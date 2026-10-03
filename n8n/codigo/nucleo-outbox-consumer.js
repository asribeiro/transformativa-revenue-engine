/* ============================================================================
 * Nucleo do consumidor de outbox — cards TRE-W3-E02-T01 (consumo) e
 * TRE-W3-E02-T02 (dedup por chave); board transformativa-revenue-engine
 * (cards t_ba84b412 e t_3bde06ab).
 *
 * O QUE ESTE ARQUIVO E', E POR QUE ELE E' PURO:
 * Aqui vive a DECISAO do consumidor: qual evento da fila e' entregue, qual e'
 * recusado (e por que), quando a fila desiste (teto de tentativas), quando o
 * evento e' um REPLAY (a chave ja' foi entregue: mesma chave = mesmo efeito,
 * sem repetir escrita), como o evento vira pedido da API controlada do Odoo e
 * como a RESPOSTA da API vira estado final do outbox. Este arquivo NAO fala com
 * banco, NAO fala com HTTP e NAO importa nada (o sandbox de Code node do n8n nao
 * tem `require`). Quem executa e' o workflow; quem decide e' este nucleo — a
 * mesma separacao do motor puro da API controlada
 * (odoo/addons/transformativa_sales_ai/api/motor.py).
 *
 * FONTE DA REGRA (nada aqui e' inventado):
 *   * n8n/contracts/outbox-consumer.v1.json — o contrato versionado do
 *     consumidor (eventos aceitos, mapeamento, teto de tentativas, status,
 *     classificacao HTTP, trilha, `dedup`). O contrato e' PARAMETRO: as funcoes
 *     recebem `contrato` e nao tem lista de eventos, mapeamento, teto ou
 *     criterio de replay literal;
 *   * docs/data/DATA_CONTRACT_V1.md §6 (envelope com event_version obrigatorio)
 *     e §7 (idempotency_key + correlacao + retry limitado + sync_events +
 *     dead-letter; retry nao cria duplicata);
 *   * doc 06 §7-8, doc 08 §5 (reprocessar mesmo event_id / mesma chave /
 *     timeout apos sucesso remoto / retry nao cria registro adicional) e doc 12 §2;
 *   * ADR-005 (nada nasce em producao — a guarda de ambiente vive na API, o
 *     consumidor so' aponta para o ambiente declarado).
 *
 * O QUE ESTE ARQUIVO NAO FAZ (lacuna declarada, de proposito):
 *   * nao acessa banco nem rede: recebe a TRILHA ja' lida (quem le e' o no
 *     Postgres com n8n/sql/ler-trilha.sql) e devolve itens de decisao/estado final;
 *   * nao cria linha de trilha no replay: `n8n/sql/registrar-replay.sql` finaliza
 *     o evento reaproveitando o registro — a trilha nao e' tocada;
 *   * nao gera segredo nem token: a autenticacao e' credencial do cofre do n8n.
 *
 * VERSAO: acompanha o contrato (n8n/contracts/outbox-consumer.v1.json -> 1.1.0).
 * ==========================================================================*/

var NUCLEO_VERSAO = '1.1.0';

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

/* ------------------------------------------------- dedup por chave (T02)
 * A chave de idempotencia e' do EVENTO e e' deterministica: o mesmo evento
 * reexecutado produz a mesma chave. A trilha e' o REGISTRO dessa chave (UNIQUE
 * no contrato); o replay e' reconhecido por consulta, nunca por heuristica.
 * ------------------------------------------------------------------------ */

/** Chaves do lote, derivadas do PROPRIO evento (uma por evento, sem repeticao). */
function chavesDoLote(itens, contrato) {
    var vistas = {};
    var chaves = [];
    var componentes = camposDaChaveDeIdempotencia(contrato);
    for (var i = 0; i < itens.length; i++) {
        var evento = (itens[i] && itens[i].json) ? itens[i].json : itens[i];
        if (ehVazio(evento) || typeof evento !== 'object') continue;
        if (chaveDoEvento(evento, contrato, componentes) === '') continue;
        var chave = chaveDeIdempotencia(evento, contrato);
        if (vistas[chave]) continue;
        vistas[chave] = true;
        chaves.push(chave);
    }
    return chaves;
}

/** Campos do evento que COMPOEM a chave, lidos da propria derivacao declarada no contrato. */
function camposDaChaveDeIdempotencia(contrato) {
    var achados = contrato.trilha.chave_de_idempotencia.derivacao.match(/<([^>]+)>/g) || [];
    var campos = [];
    for (var i = 0; i < achados.length; i++) {
        var nome = achados[i].replace(/[<>]/g, '').split('.').pop();
        if (nome && campos.indexOf(nome) < 0) campos.push(nome);
    }
    return campos;
}

/**
 * Chave do evento BEM FORMADA, ou '' quando o evento nao a tem.
 * Evento sem os componentes da derivacao produziria `outbox::` — chave de nada: o formato do
 * contrato sozinho nao pega isso, porque `:` e' caractere valido. Chave assim NAO e' consultada
 * nem pode autorizar replay (o registro da trilha e' por chave do EVENTO).
 */
function chaveDoEvento(evento, contrato, componentes) {
    for (var i = 0; i < componentes.length; i++) {
        if (ehVazio(evento[componentes[i]])) return '';
    }
    return chaveDeIdempotencia(evento, contrato);
}

/**
 * Trilha do lote indexada pela chave — o que a consulta devolveu, cru.
 * Linha sem chave (inclusive a linha vazia que o no de consulta emite quando nao
 * ha nada a devolver) e' DESCARTADA: o registro do replay so' se apoia em
 * registro de trilha com chave. Consulta vazia nunca vira "ja' entregue".
 */
function trilhaPorChave(itensTrilha) {
    var porChave = {};
    if (!itensTrilha) return porChave;
    for (var i = 0; i < itensTrilha.length; i++) {
        var linha = (itensTrilha[i] && itensTrilha[i].json) ? itensTrilha[i].json : itensTrilha[i];
        if (ehVazio(linha) || typeof linha !== 'object') continue;
        var chave = texto(linha.idempotency_key);
        if (ehVazio(chave)) continue;
        porChave[chave] = linha;
    }
    return porChave;
}

/**
 * Registro de trilha que AUTORIZA o replay, ou null.
 * O criterio e' o declarado no contrato (nome da coluna da chave + status de
 * sucesso da trilha) — nao ha status literal aqui. Trilha de outro status
 * (FAILED, REFUSED) NAO autoriza: falha transitoria pode nao ter escrito nada
 * no destino, e recusa definitiva e' do evento, nao da chave.
 */
function registroDeReplay(evento, contrato, trilha) {
    var criterio = contrato.dedup.criterio_de_replay;
    var chave = chaveDeIdempotencia(evento, contrato);
    var registro = (trilha && trilha[chave]) ? trilha[chave] : null;
    if (ehVazio(registro)) return null;
    if (texto(registro[criterio.coluna_de_chave]) !== chave) return null;
    if (texto(registro.status) !== texto(criterio.status_trilha)) return null;
    return registro;
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
 * `decisao` em REPLAY | ENVIAR | RECUSAR | ESGOTADO | IGNORAR — a ORDEM abaixo e'
 * a ordem declarada no contrato (`dedup.ordem_da_decisao`):
 *   IGNORAR  -> status fora da fila (defensivo; o SQL so' le PENDING/RETRY) — vira no-op;
 *   REPLAY   -> a chave do evento JA' esta' na trilha com status de sucesso: mesma chave =
 *               mesmo efeito, sem repetir escrita (sem chamada, sem nova linha de trilha);
 *   ESGOTADO -> ja' tentou o teto de vezes (a fila desiste, dead-letter nomeada);
 *   RECUSAR  -> recusa DEFINITIVA por envelope/contrato/identidade (sem tentativa de entrega);
 *   ENVIAR   -> envelope valido, evento no contrato, identidade e campos exigidos presentes.
 *
 * `trilha` (opcional) e' a trilha do lote indexada pela chave (`trilhaPorChave`). Sem ela
 * nao existe replay — o nucleo nao inventa "ja' entregue" a partir de nada.
 */
function decidirEvento(evento, contrato, trilha) {
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
    var registro = registroDeReplay(evento, contrato, trilha);
    if (registro) {
        // Replay: o efeito no destino JA' aconteceu (a trilha guarda o pedido e a resposta da
        // entrega que o produziu). Nao ha' chamada, nao ha' incremento de tentativa e a trilha
        // NAO e' tocada — o registro e' reaproveitado por n8n/sql/registrar-replay.sql, que
        // exige a trilha de sucesso (fail-closed) para finalizar o evento.
        return itemFinal(evento, contrato, {
            decisao: 'REPLAY',
            motivo: 'chave_ja_entregue:' + chaveDeIdempotencia(evento, contrato),
            incrementa_tentativas: 0,
            status_final: contrato.dedup.status_final,
            status_trilha: contrato.dedup.status_trilha,
            last_error: null,
            request_payload: null,
            response_payload: ehVazio(registro.response_payload) ? null : registro.response_payload,
            trilha_reaproveitada: {
                id: ehVazio(registro.trilha_id) ? null : registro.trilha_id,
                completed_at: ehVazio(registro.completed_at) ? null : registro.completed_at
            }
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

/**
 * Adaptador do Code node "Nucleo: validar e decidir". A trilha do lote chega pelo
 * $input (a consulta de chaves rodou imediatamente antes) e os eventos vem do no de
 * leitura da fila: e' a MESMA decisao pura, com a trilha como parametro.
 */
function decisaoDoLote(itens, contrato, trilha) {
    var saida = [];
    for (var i = 0; i < itens.length; i++) {
        var evento = (itens[i] && itens[i].json) ? itens[i].json : itens[i];
        saida.push({ json: decidirEvento(evento, contrato, trilha) });
    }
    return saida;
}

/** Adaptador do Code node "Chaves do lote": uma UNICA consulta de trilha por ciclo. */
function chavesDoLoteComoItem(itens, contrato) {
    return [{ json: { chaves: chavesDoLote(itens, contrato) } }];
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
        chavesDoLote: chavesDoLote,
        camposDaChaveDeIdempotencia: camposDaChaveDeIdempotencia,
        chaveDoEvento: chaveDoEvento,
        trilhaPorChave: trilhaPorChave,
        registroDeReplay: registroDeReplay,
        montarPedido: montarPedido,
        decidirEvento: decidirEvento,
        classificarResposta: classificarResposta,
        corpoDaResposta: corpoDaResposta,
        decisaoDoLote: decisaoDoLote,
        chavesDoLoteComoItem: chavesDoLoteComoItem,
        resultadoDasRespostas: resultadoDasRespostas
    };
}
