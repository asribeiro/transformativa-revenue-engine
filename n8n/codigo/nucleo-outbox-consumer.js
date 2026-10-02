/* ============================================================================
 * Nucleo do consumidor de outbox — card TRE-W3-E02-T01 (board
 * transformativa-revenue-engine, card t_ba84b412).
 *
 * O QUE ESTE ARQUIVO E', E POR QUE ELE E' PURO:
 * Aqui vive a DECISAO do consumidor: qual evento da fila e' entregue, qual e'
 * recusado (e por que), quando a fila desiste (teto de tentativas), como o
 * evento vira pedido da API controlada do Odoo e como a RESPOSTA da API vira
 * estado final do outbox. Este arquivo NAO fala com banco, NAO fala com HTTP e
 * NAO importa nada (o sandbox de Code node do n8n nao tem `require`). Quem
 * executa e' o workflow; quem decide e' este nucleo — a mesma separacao do
 * motor puro da API controlada (odoo/addons/transformativa_sales_ai/api/motor.py).
 *
 * FONTE DA REGRA (nada aqui e' inventado):
 *   * n8n/contracts/outbox-consumer.v1.json — o contrato versionado do
 *     consumidor (eventos aceitos, mapeamento, teto de tentativas, status,
 *     classificacao HTTP, trilha). O contrato e' PARAMETRO: as funcoes recebem
 *     `contrato` e nao tem lista de eventos, mapeamento ou teto literal;
 *   * docs/data/DATA_CONTRACT_V1.md §6 (envelope com event_version obrigatorio)
 *     e §7 (idempotency_key + correlacao + retry limitado + sync_events +
 *     dead-letter; retry nao cria duplicata);
 *   * doc 06 §7-8 e doc 12 §2 (evento PG -> Odoo);
 *   * ADR-005 (nada nasce em producao — a guarda de ambiente vive na API, o
 *     consumidor so' aponta para o ambiente declarado).
 *
 * O QUE ESTE ARQUIVO NAO FAZ (lacuna declarada, de proposito):
 *   * nao implementa dedup por `idempotency_key` (mesma chave = mesmo efeito,
 *     sem repetir escrita) — card TRE-W3-E02-T02. Aqui a chave e' DERIVADA do
 *     evento, enviada, exigida pela API e registrada na trilha;
 *   * nao escreve em lugar nenhum: devolve itens de decisao/estado final;
 *   * nao gera segredo nem token: a autenticacao e' credencial do cofre do n8n.
 *
 * VERSAO: acompanha o contrato (n8n/contracts/outbox-consumer.v1.json).
 * ==========================================================================*/

var NUCLEO_VERSAO = '1.0.0';

/* ------------------------------------------------------------------ utilidades */

function ehVazio(valor) {
    return valor === null || valor === undefined ||
        (typeof valor === 'string' && valor.trim() === '');
}

function texto(valor) {
    if (valor === null || valor === undefined) return '';
    return String(valor).trim();
}

/** Le um caminho pontuado ('payload.name' / 'aggregate_id') do evento. */
function caminhoValor(objeto, caminho) {
    var partes = texto(caminho).split('.');
    var atual = objeto;
    for (var i = 0; i < partes.length; i++) {
        if (atual === null || atual === undefined) return undefined;
        atual = atual[partes[i]];
    }
    return atual;
}

/* ------------------------------------------------------------------ decisao */

/** Envelope do evento (contrato §6 / data contract §6, regra 5). */
function validarEnvelope(evento, contrato) {
    var motivos = [];
    var obrigatorios = contrato.envelope.campos_obrigatorios;
    for (var i = 0; i < obrigatorios.length; i++) {
        var campo = obrigatorios[i];
        if (ehVazio(evento[campo])) motivos.push('envelope_sem_' + campo);
    }
    var payload = evento.payload;
    if (!ehVazio(payload) && (typeof payload !== 'object' || Array.isArray(payload))) {
        motivos.push('payload_nao_e_objeto');
    }
    var versao = texto(evento.event_version);
    if (versao && contrato.envelope.versoes_suportadas.indexOf(versao) < 0) {
        motivos.push('event_version_nao_suportada:' + versao);
    }
    return { ok: motivos.length === 0, motivos: motivos };
}

function definicaoDoEvento(contrato, tipo) {
    var eventos = contrato.eventos;
    for (var i = 0; i < eventos.length; i++) {
        if (eventos[i].event_type === texto(tipo)) return eventos[i];
    }
    return null;
}

/** Mapeia o evento para os VALORES da operacao, seguindo o mapeamento declarado. */
function montarValores(evento, definicao) {
    var valores = {};
    for (var i = 0; i < definicao.mapeamento.length; i++) {
        var regra = definicao.mapeamento[i];
        var valor = caminhoValor(evento, regra.origem);
        if (ehVazio(valor)) continue;              // campo sem valor nao vira campo vazio na API
        valores[regra.destino] = valor;
    }
    return valores;
}

function chaveDeIdempotencia(evento, contrato) {
    var modelo = contrato.trilha.chave_de_idempotencia.derivacao;
    return modelo
        .replace('<outbox_events.id>', texto(evento.id))
        .replace('<event_type>', texto(evento.event_type));
}

function correlacaoDoEvento(evento, contrato) {
    return contrato.trilha.correlacao.derivacao.replace('<outbox_events.id>', texto(evento.id));
}

/** Corpo do POST na porta unica — so' o que a politica da API declara. */
function montarPedido(evento, definicao, contrato) {
    return {
        idempotency_key: chaveDeIdempotencia(evento, contrato),
        correlation_id: correlacaoDoEvento(evento, contrato),
        parametros: {
            modelo: contrato.destino.modelo,
            valores: montarValores(evento, definicao)
        }
    };
}

/** Item de estado final, comum aos dois caminhos (entrega e recusa). */
function itemFinal(evento, contrato, campos) {
    var base = {
        evento_id: ehVazio(evento.evento_id) ? texto(evento.id) : evento.evento_id,
        event_type: texto(evento.event_type),
        event_version: ehVazio(evento.event_version) ? null : evento.event_version,
        aggregate_type: ehVazio(evento.aggregate_type) ? null : evento.aggregate_type,
        aggregate_id: ehVazio(evento.aggregate_id) ? null : evento.aggregate_id,
        chave: ehVazio(evento.chave) ? chaveDeIdempotencia(evento, contrato) : evento.chave,
        correlacao: ehVazio(evento.correlacao) ? correlacaoDoEvento(evento, contrato) : evento.correlacao,
        operacao: null,
        pedido: null,
        http: null,
        codigo: null,
        response_payload: null
    };
    for (var chave in campos) {
        if (Object.prototype.hasOwnProperty.call(campos, chave)) base[chave] = campos[chave];
    }
    return base;
}

/**
 * DECISAO de um evento da fila. Devolve sempre o mesmo formato de item, com
 * `decisao` em ENVIAR | RECUSAR | ESGOTADO | IGNORAR:
 *   ENVIAR   -> envelope valido, evento no contrato, identidade e campos exigidos presentes;
 *   RECUSAR  -> recusa DEFINITIVA por envelope/contrato/identidade (sem tentativa de entrega);
 *   ESGOTADO -> ja' tentou o teto de vezes (a fila desiste, dead-letter nomeada);
 *   IGNORAR  -> status fora da fila (defensivo; o SQL so' le PENDING/RETRY) — vira no-op.
 */
function decidirEvento(evento, contrato) {
    var status = texto(evento.status);
    var teto = Number(contrato.retry.teto_de_tentativas);
    var tentativas = Number(ehVazio(evento.attempts) ? 0 : evento.attempts);

    if (contrato.status.entrada.indexOf(status) < 0) {
        return itemFinal(evento, contrato, {
            decisao: 'IGNORAR',
            evento_id: null,                       // no-op medido: sem id nao ha UPDATE nem trilha
            motivo: 'status_fora_da_fila:' + (status || 'vazio'),
            incrementa_tentativas: 0,
            status_final: null,
            status_trilha: null,
            last_error: null,
            request_payload: null
        });
    }
    if (tentativas >= teto) {
        return itemFinal(evento, contrato, {
            decisao: 'ESGOTADO',
            motivo: 'teto_de_tentativas_atingido:' + tentativas,
            incrementa_tentativas: 0,
            status_final: contrato.status.esgotado,
            status_trilha: contrato.trilha.status.recusa,
            last_error: 'teto_de_tentativas_atingido:' + tentativas,
            request_payload: evento.payload || null
        });
    }
    var envelope = validarEnvelope(evento, contrato);
    if (!envelope.ok) {
        return itemFinal(evento, contrato, {
            decisao: 'RECUSAR',
            motivo: envelope.motivos.join('; '),
            incrementa_tentativas: 0,
            status_final: contrato.status.recusa,
            status_trilha: contrato.trilha.status.recusa,
            last_error: envelope.motivos.join('; '),
            request_payload: evento.payload || null
        });
    }
    var definicao = definicaoDoEvento(contrato, evento.event_type);
    if (!definicao) {
        return itemFinal(evento, contrato, {
            decisao: 'RECUSAR',
            motivo: 'event_type_fora_do_contrato:' + texto(evento.event_type),
            incrementa_tentativas: 0,
            status_final: contrato.status.recusa,
            status_trilha: contrato.trilha.status.recusa,
            last_error: 'event_type_fora_do_contrato:' + texto(evento.event_type),
            request_payload: evento.payload || null
        });
    }
    if (ehVazio(caminhoValor(evento, definicao.exige_identidade))) {
        return itemFinal(evento, contrato, {
            decisao: 'RECUSAR',
            motivo: 'identidade_ausente:' + definicao.exige_identidade,
            incrementa_tentativas: 0,
            status_final: contrato.status.recusa,
            status_trilha: contrato.trilha.status.recusa,
            last_error: 'identidade_ausente:' + definicao.exige_identidade,
            request_payload: evento.payload || null
        });
    }
    var valores = montarValores(evento, definicao);
    var exigidos = definicao.exigidos || [];
    for (var i = 0; i < exigidos.length; i++) {
        if (ehVazio(valores[exigidos[i]])) {
            return itemFinal(evento, contrato, {
                decisao: 'RECUSAR',
                motivo: 'campo_exigido_ausente:' + exigidos[i],
                incrementa_tentativas: 0,
                status_final: contrato.status.recusa,
                status_trilha: contrato.trilha.status.recusa,
                last_error: 'campo_exigido_ausente:' + exigidos[i],
                request_payload: evento.payload || null
            });
        }
    }
    var pedido = montarPedido(evento, definicao, contrato);
    return itemFinal(evento, contrato, {
        decisao: 'ENVIAR',
        motivo: '',
        incrementa_tentativas: 1,
        status_final: null,                        // decidido pela resposta da API
        status_trilha: null,
        last_error: null,
        operacao: definicao.operacao,
        pedido: pedido,
        request_payload: pedido
    });
}

/* --------------------------------------------------- resposta da API -> estado */

/**
 * Classifica a resposta da porta unica em SUCESSO | RECUSA | FALHA_TRANSITORIA.
 * `status` nulo/indefinido = a chamada nem chegou a responder (transporte).
 */
function classificarResposta(status, corpo, contrato) {
    var codigo = '';
    if (corpo && typeof corpo === 'object' && !Array.isArray(corpo)) codigo = texto(corpo.codigo);
    if (status === null || status === undefined || status === 0) {
        return { resultado: 'FALHA_TRANSITORIA', codigo: codigo, motivo: 'falha_de_transporte' };
    }
    var numero = Number(status);
    if (numero >= 200 && numero < 300) {
        return { resultado: 'SUCESSO', codigo: codigo, motivo: '' };
    }
    var terminais = contrato.classificacao_http.codigos_terminais_do_erro_da_api;
    if (numero >= 400 && numero < 500) {
        return { resultado: 'RECUSA', codigo: codigo, motivo: 'recusa_da_api:' + (codigo || String(numero)) };
    }
    if (numero >= 500) {
        if (codigo && terminais.indexOf(codigo) >= 0) {
            return { resultado: 'RECUSA', codigo: codigo, motivo: 'recusa_da_api:' + codigo };
        }
        return { resultado: 'FALHA_TRANSITORIA', codigo: codigo, motivo: 'falha_do_servidor:' + (codigo || String(numero)) };
    }
    return { resultado: 'FALHA_TRANSITORIA', codigo: codigo, motivo: 'resposta_inesperada:' + String(numero) };
}

/** Corpo da resposta em objeto (o n8n pode entregar texto ou objeto ja' parseado). */
function corpoDaResposta(valor) {
    if (valor === null || valor === undefined) return null;
    if (typeof valor === 'object') return valor;
    if (typeof valor === 'string') {
        try { return JSON.parse(valor); } catch (erro) { return { texto: valor }; }
    }
    return { texto: String(valor) };
}

/* ------------------------------------------------------------------ adaptadores
 * Os adaptadores sao a UNICA parte escrita para o n8n (`$input`). O corpo acima
 * e' puro e roda igual no n8n e no node do aceite (scripts/n8n).
 * --------------------------------------------------------------------------- */

/** Adaptador do Code node "Nucleo: validar e decidir". */
function decisaoDoLote(itens, contrato) {
    var saida = [];
    for (var i = 0; i < itens.length; i++) {
        var evento = (itens[i] && itens[i].json) ? itens[i].json : itens[i];
        saida.push({ json: decidirEvento(evento, contrato) });
    }
    return saida;
}

/**
 * Adaptador do Code node "Classificar resposta": casa cada resposta com a
 * decisao que a originou. O casamento e' pelo `correlation_id` ecoado pela API
 * (contrato: a API devolve o eco no envelope de sucesso E no de recusa); se o
 * eco nao conferir, o item NAO vira sucesso — vira falha nomeada. A ordem serve
 * apenas de candidato: e' conferida, nunca presumida.
 */
function resultadoDasRespostas(itensResposta, itensDecisao, contrato) {
    var decisoes = [];
    for (var d = 0; d < itensDecisao.length; d++) {
        var json = (itensDecisao[d] && itensDecisao[d].json) ? itensDecisao[d].json : itensDecisao[d];
        if (json && json.decisao === 'ENVIAR') decisoes.push(json);
    }
    var saida = [];
    for (var i = 0; i < itensResposta.length; i++) {
        var bruto = (itensResposta[i] && itensResposta[i].json) ? itensResposta[i].json : itensResposta[i];
        var decisao = decisoes[i] || null;
        var status = null;
        var corpo = null;
        var problema = '';
        if (bruto && bruto.error) {
            problema = 'falha_de_transporte:' + texto(typeof bruto.error === 'object' ? (bruto.error.message || bruto.error.code) : bruto.error);
        } else {
            status = bruto ? bruto.statusCode : null;
            corpo = corpoDaResposta(bruto ? bruto.body : null);
            if (decisao && corpo && typeof corpo === 'object' && !Array.isArray(corpo)) {
                var eco = texto(corpo.correlation_id);
                if (eco && eco !== texto(decisao.correlacao)) {
                    problema = 'resposta_nao_corresponde_a_decisao:' + eco;
                }
            }
        }
        if (!decisao) {
            saida.push({ json: { decisao: 'IGNORAR', evento_id: null, motivo: 'resposta_sem_decisao_correspondente',
                incrementa_tentativas: 0, status_final: null, status_trilha: null, last_error: null,
                request_payload: null, response_payload: null, http: status, codigo: null } });
            continue;
        }
        var classificacao = problema
            ? { resultado: 'FALHA_TRANSITORIA', codigo: null, motivo: problema }
            : classificarResposta(status, corpo, contrato);
        var conteudo = {
            decisao: 'ENTREGUE',
            resultado: classificacao.resultado,
            motivo: classificacao.motivo,
            codigo: classificacao.codigo || null,
            http: status,
            incrementa_tentativas: 1,
            response_payload: corpo,
            request_payload: decisao.pedido
        };
        if (classificacao.resultado === 'SUCESSO') {
            conteudo.status_final = contrato.status.sucesso;
            conteudo.status_trilha = contrato.trilha.status.sucesso;
            conteudo.last_error = null;
        } else if (classificacao.resultado === 'RECUSA') {
            conteudo.status_final = contrato.status.recusa;
            conteudo.status_trilha = contrato.trilha.status.recusa;
            conteudo.last_error = classificacao.motivo;
        } else {
            conteudo.status_final = contrato.status.falha_transitoria;
            conteudo.status_trilha = contrato.trilha.status.falha_transitoria;
            conteudo.last_error = classificacao.motivo;
        }
        saida.push({ json: itemFinal(decisao, contrato, conteudo) });
    }
    return saida;
}

/* O n8n injeta este arquivo dentro do Code node; o aceite roda o MESMO arquivo
 * no node puro. `module` so' existe no segundo caso. */
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        NUCLEO_VERSAO: NUCLEO_VERSAO,
        ehVazio: ehVazio,
        texto: texto,
        caminhoValor: caminhoValor,
        validarEnvelope: validarEnvelope,
        definicaoDoEvento: definicaoDoEvento,
        montarValores: montarValores,
        chaveDeIdempotencia: chaveDeIdempotencia,
        correlacaoDoEvento: correlacaoDoEvento,
        montarPedido: montarPedido,
        decidirEvento: decidirEvento,
        classificarResposta: classificarResposta,
        corpoDaResposta: corpoDaResposta,
        decisaoDoLote: decisaoDoLote,
        resultadoDasRespostas: resultadoDasRespostas
    };
}
