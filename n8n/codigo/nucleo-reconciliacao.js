/* ============================================================================
 * Nucleo da RECONCILIACAO — card TRE-W3-E04-T01
 * (board transformativa-revenue-engine, card t_2ee17829).
 *
 * O QUE ESTE ARQUIVO E', E POR QUE ELE E' PURO:
 * Aqui vive a DECISAO da reconciliacao: quais entidades eram esperadas no
 * destino, onde a ida-e-volta do ID canonico quebrou, onde a fila e a trilha do
 * MESMO fato discordam, qual e' o veredito da rodada e o que o RELATORIO escreve.
 * Este arquivo NAO fala com banco, NAO fala com HTTP e NAO importa nada (o
 * sandbox de Code node do n8n nao tem `require`): quem consulta sao os dois SQL
 * (n8n/sql/reconciliacao-*.sql), quem chama a porta unica e' o workflow, e quem
 * decide e' este nucleo. E' a mesma separacao do consumidor de outbox
 * (n8n/codigo/nucleo-outbox-consumer.js) e do motor da API controlada
 * (odoo/addons/transformativa_sales_ai/api/motor.py).
 *
 * FONTE DA REGRA (nada aqui e' inventado, nada e' literal):
 *   * n8n/contracts/reconciliation-job.v1.json — o contrato versionado desta
 *     superficie: comparacoes, lote, janela de pendencia, teto de tentativas,
 *     vocabulario da trilha, identificadores fortes, regras de fail-closed e
 *     formato do relatorio. O contrato e' PARAMETRO: nao existe numero, nome de
 *     comparacao ou status neste arquivo. Trocar limiar = nova versao do
 *     contrato, nunca edicao daqui;
 *   * docs/data/DATA_CONTRACT_V1.md §4 (organizations/contacts: odoo_partner_id)
 *     e §3/§5 (IDs canonicos: organizations.id <-> res.partner.tf_company_id);
 *   * doc 06 §8 (comparar entidades esperadas, verificar IDs cruzados, verificar
 *     eventos pendentes, detectar divergencias, gerar relatorio) e §7 (retry
 *     limitado, dead-letter, idempotencia);
 *   * n8n/contracts/outbox-consumer.v1.json e n8n/contracts/odoo-events-ingest.v1.json
 *     (o vocabulario da fila e da trilha) — espelhados no contrato deste job.
 *
 * A REGRA QUE ORGANIZA TUDO — AUSENCIA DE MEDICAO NAO E' SAUDE:
 * um lado que nao se pode medir (leitura do destino sem HTTP 200, corpo sem
 * `dados.registros`, consulta sem a linha de cobertura, status de trilha fora do
 * vocabulario) fecha a rodada em INDETERMINADO, NUNCA em OK. E' a mesma regra da
 * observabilidade, pelo mesmo motivo: um numero que nao se pode medir nao autoriza
 * confiar nos outros. BASE VAZIA e' outra coisa — e' ausencia de ATIVIDADE (nao ha'
 * entidade a reconciliar) e chega como zero, nao como INDETERMINADO.
 *
 * O QUE ESTE ARQUIVO NAO FAZ (de proposito):
 *   * nao CORRIGE nada: doc 06 §8 ('nunca corrigir silenciosamente dados
 *     ambiguos') — a divergencia e' medida, nomeada e entregue; a correcao e' ato
 *     de operador;
 *   * nao escreve: nao existe caminho de escrita aqui nem no workflow (a lente
 *     estrutural reprova SQL que nao seja SELECT/WITH e no' de escrita);
 *   * nao imprime payload: o relatorio mostra IDENTIFICADOR e MOTIVO (truncado com
 *     marca) — payload pode carregar dado pessoal ou segredo e nao entra;
 *   * nao inventa divergencia a partir de ausencia de campo: a comparacao de
 *     identificador forte so' reporta quando os DOIS lados tem valor.
 *
 * VERSAO: acompanha o contrato (n8n/contracts/reconciliation-job.v1.json).
 * ==========================================================================*/

var NUCLEO_VERSAO = '1.0.0';

/* ------------------------------------------------------------------ utilidades */

function ehVazio(valor) {
    return valor === null || valor === undefined ||
        (typeof valor === 'string' && valor.trim() === '');
}

function texto(valor) {
    // Booleano NAO e' texto: o Odoo devolve `false` para campo de caracter vazio, e ler
    // `String(false)` = 'false' inventaria um valor que nao existe ("false" != cnpj). Medido no
    // aceite (estado C/D com o parceiro sem `tf_cnpj`): virava divergencia de identidade forte.
    if (valor === null || valor === undefined || typeof valor === 'boolean') return '';
    return String(valor).trim();
}

function ehObjeto(valor) {
    return valor !== null && typeof valor === 'object' && !Array.isArray(valor);
}

/* Verdadeiro lido do jeito que o driver do PostgreSQL do n8n entrega booleano
 * (true, 't', 'true', '1'). Qualquer outra coisa e' FALSO — e' o contrato que
 * manda declarar o campo como booleano no SQL, nao o nucleo que adivinha. */
function sim(valor) {
    if (valor === true) return true;
    var t = texto(valor).toLowerCase();
    return t === 't' || t === 'true' || t === '1';
}

/* Numero medido: aceita number e a representacao textual do driver. Qualquer
 * outra coisa NAO vira zero — devolve null e quem chamou decide (fail-closed). */
function numero(valor) {
    if (typeof valor === 'number' && isFinite(valor)) return valor;
    if (typeof valor === 'string' && valor.trim() !== '') {
        var n = Number(valor);
        if (isFinite(n)) return n;
    }
    return null;
}

function lista(valor) {
    return Array.isArray(valor) ? valor : [];
}

/* Motivo: uma linha, sem controle, com o teto declarado no contrato (o motivo e'
 * nomeado e curto; texto longo e' truncado COM MARCA — nunca cortado em silencio). */
function sanitizar(motivo, teto) {
    var limite = numero(teto);
    var t = texto(motivo).replace(/[\r\n\t]+/g, ' ').replace(/[\u0000-\u001f\u007f]/g, ' ');
    t = t.replace(/\s{2,}/g, ' ');
    if (limite === null || limite <= 0) return t;
    if (t.length > limite) return t.slice(0, limite) + '...';
    return t;
}

/* ------------------------------------------------------------------ contrato */

function contratoValido(contrato) {
    if (!ehObjeto(contrato)) return false;
    if (!Array.isArray(contrato.comparacoes) || contrato.comparacoes.length === 0) return false;
    if (!ehObjeto(contrato.veredito) || !Array.isArray(contrato.veredito.ordem)) return false;
    if (!ehObjeto(contrato.veredito.saida)) return false;
    if (!ehObjeto(contrato.regras_de_fail_closed)) return false;
    if (!Array.isArray(contrato.leituras_do_destino) || contrato.leituras_do_destino.length === 0) return false;
    if (!ehObjeto(contrato.campos_do_vinculo)) return false;
    if (!ehObjeto(contrato.vinculo_da_trilha)) return false;
    if (!ehObjeto(contrato.status_da_trilha)) return false;
    if (!ehObjeto(contrato.status_da_fila)) return false;
    if (!ehObjeto(contrato.envelope_da_leitura)) return false;
    if (!Array.isArray(contrato.identificadores_fortes) || contrato.identificadores_fortes.length === 0) return false;
    for (var fi = 0; fi < contrato.identificadores_fortes.length; fi++) {
        var forte = contrato.identificadores_fortes[fi];
        if (!ehObjeto(forte) || ehVazio(forte.destino) || ehVazio(forte.origem)) return false;
    }
    for (var li = 0; li < contrato.leituras_do_destino.length; li++) {
        var declarada = contrato.leituras_do_destino[li];
        if (!ehObjeto(declarada) || ehVazio(declarada.id) || ehVazio(declarada.campo_de_filtro) ||
            ehVazio(declarada.operador) || ehVazio(declarada.valores_de)) return false;
    }
    // Os numeros que decidem a rodada sao PARAMETRO declarado (nao ha numero de
    // reserva no nucleo): contrato sem eles nao decide nada — fecha INDETERMINADO.
    if (numero(contrato.lote && contrato.lote.limite_de_entidades) === null) return false;
    if (numero(contrato.lote && contrato.lote.limite_de_eventos) === null) return false;
    if (numero(contrato.janela_de_pendencia_s) === null) return false;
    if (numero(contrato.teto_de_tentativas) === null) return false;
    if (numero(contrato.relatorio && contrato.relatorio.tamanho_maximo_do_motivo) === null) return false;
    // Cada comparacao declarada tem de ser nomeavel: id, tipo e motivo. Comparacao
    // sem motivo nomeado nao se relata (e o relatorio e' o produto do job).
    for (var i = 0; i < contrato.comparacoes.length; i++) {
        var c = contrato.comparacoes[i];
        if (!ehObjeto(c) || ehVazio(c.id) || ehVazio(c.tipo) || ehVazio(c.motivo)) return false;
    }
    var observacoes = lista(contrato.observacoes);
    for (var j = 0; j < observacoes.length; j++) {
        var o = observacoes[j];
        if (!ehObjeto(o) || ehVazio(o.id) || ehVazio(o.tipo) || ehVazio(o.motivo)) return false;
    }
    return true;
}

function comparacaoPorId(contrato, id) {
    var comparacoes = lista(contrato.comparacoes);
    for (var i = 0; i < comparacoes.length; i++) {
        if (texto(comparacoes[i].id) === texto(id)) return comparacoes[i];
    }
    return null;
}

function observacaoPorId(contrato, id) {
    var observacoes = lista(contrato.observacoes);
    for (var i = 0; i < observacoes.length; i++) {
        if (texto(observacoes[i].id) === texto(id)) return observacoes[i];
    }
    return null;
}

function leituraPorId(contrato, id) {
    var leituras = lista(contrato.leituras_do_destino);
    for (var i = 0; i < leituras.length; i++) {
        if (texto(leituras[i].id) === texto(id)) return leituras[i];
    }
    return null;
}

/* ------------------------------------------------------------------ itens do n8n */

function corpoDoItem(item) {
    if (item === null || item === undefined) return null;
    var json = (ehObjeto(item) && !ehVazio(item.json) && ehObjeto(item.json)) ? item.json : item;
    return json;
}

function itensParaLista(itens) {
    var saida = [];
    for (var i = 0; i < lista(itens).length; i++) {
        var corpo = corpoDoItem(itens[i]);
        if (ehObjeto(corpo)) saida.push(corpo);
    }
    return saida;
}

/* ------------------------------------------------------------------ linhas de SQL */

function linhasPorTipo(itens, tipo) {
    var todas = itensParaLista(itens);
    var saida = [];
    for (var i = 0; i < todas.length; i++) {
        if (texto(todas[i].tipo) === texto(tipo)) saida.push(todas[i]);
    }
    return saida;
}

function linhaDeCobertura(itens) {
    var linhas = linhasPorTipo(itens, 'cobertura');
    return linhas.length > 0 ? linhas[0] : null;
}

/* ------------------------------------------------------------------ montagem do lote */

function entidadeNormalizada(linha) {
    return {
        id: texto(linha.entidade_id),
        nome: texto(linha.nome),
        odoo_partner_id: ehVazio(linha.odoo_partner_id) ? null : texto(linha.odoo_partner_id),
        cnpj: texto(linha.cnpj),
        domain: texto(linha.domain),
        linkedin_url: texto(linha.linkedin_url),
        status: texto(linha.status),
        esperada: sim(linha.esperada),
        operacao_do_espelho: ehVazio(linha.operacao_do_espelho) ? null : texto(linha.operacao_do_espelho),
        espelho_entregue: sim(linha.espelho_entregue),
        // identificadores fortes: o nome do campo no DESTINO vem do contrato
        // (`identificadores_fortes[].destino` -> `origem` na origem). Mapa literal aqui
        // esconderia do contrato qual campo se compara com qual.
        fortes: {}
    };
}

function montarLote(contrato, origemItens) {
    var linhas = linhasPorTipo(origemItens, 'entidade');
    var cobertura = linhaDeCobertura(origemItens);
    var entidades = [];
    var fortes = lista(contrato.identificadores_fortes);
    for (var i = 0; i < linhas.length; i++) {
        var entidade = entidadeNormalizada(linhas[i]);
        for (var f = 0; f < fortes.length; f++) {
            var destino = texto(fortes[f].destino);
            var origem = texto(fortes[f].origem);
            if (ehVazio(destino) || ehVazio(origem)) continue;
            entidade.fortes[destino] = texto(linhas[i][origem]);
        }
        entidades.push(entidade);
    }
    return {
        entidades: entidades,
        cobertura: cobertura === null ? null : {
            total_de_organizacoes: numero(cobertura.total_de_organizacoes),
            limite: numero(cobertura.limite)
        }
    };
}

/* Pedidos da PORTA UNICA (a unica forma de ler o destino): um pedido por leitura
 * declarada, com os campos, o filtro, a ordem e o limite do contrato.
 *
 * SEM ENTIDADE O FILTRO NAO VIRA LEITURA DA BASE INTEIRA: o operador `in` com
 * lista vazia nao casa registro nenhum — e' isso que impede a rodada de uma base
 * recem-criada de varrer o CRM inteiro pelas costas (o aceite mede exatamente
 * isso). */
function montarPedidos(contrato, origemItens) {
    var lote = montarLote(contrato, origemItens);
    var ids_de_parceiro = [];
    var ids_de_organizacao = [];
    for (var i = 0; i < lote.entidades.length; i++) {
        var e = lote.entidades[i];
        ids_de_organizacao.push(e.id);
        if (!ehVazio(e.odoo_partner_id)) ids_de_parceiro.push(e.odoo_partner_id);
    }
    var pedidos = {};
    var leituras = lista(contrato.leituras_do_destino);
    for (var l = 0; l < leituras.length; l++) {
        var leitura = leituras[l];
        // O que entra no filtro e' DERIVADO do campo declarado (`valores_de`): filtrar pelos IDs de
        // PARCEIRO e' perguntar "o parceiro que o PostgreSQL aponta existe?" (a ponta da ida);
        // filtrar pelos IDs de ORGANIZACAO e' perguntar "quantos parceiros se dizem o espelho desta
        // organizacao?" (a ponta da volta). Nada e' literal aqui: vem do contrato.
        var valores = texto(leitura.valores_de) === 'odoo_partner_id' ? ids_de_parceiro : ids_de_organizacao;
        var pedido = {
            modelo: texto(contrato.fontes && contrato.fontes.destino ? contrato.fontes.destino.modelo : ''),
            filtro: [[texto(leitura.campo_de_filtro), texto(leitura.operador), valores]],
            campos: lista(leitura.campos),
            ordem: texto(leitura.ordem),
            limite: numero(leitura.limite)
        };
        // Parametros EXTRAS declarados pela leitura (ex.: `incluir_arquivados`): entram no pedido
        // por DECLARACAO, nao por literal — o nucleo nao conhece o vocabulario da porta unica.
        var extras = leitura.parametros;
        if (extras && typeof extras === 'object') {
            for (var chave in extras) {
                if (Object.prototype.hasOwnProperty.call(extras, chave)) pedido[chave] = extras[chave];
            }
        }
        pedidos[texto(leitura.id)] = pedido;
    }
    return {
        entidades: lote.entidades.length,
        ids_de_parceiro: ids_de_parceiro.length,
        ids_de_organizacao: ids_de_organizacao.length,
        cobertura: lote.cobertura,
        pedidos: pedidos
    };
}

/* ------------------------------------------------------------------ leitura do destino */

/**
 * Le a resposta de UMA leitura da porta unica. Devolve sempre o mesmo formato:
 *   { id, medido, motivo, registros }
 * `medido` so' e' verdadeiro com HTTP 2xx E corpo com `ok` verdadeiro E
 * `dados.registros` lista. Resposta que nao se entende NAO vira "sem divergencia".
 */
function lerResposta(contrato, id, itens) {
    var corpo = null;
    var status = null;
    var todos = itensParaLista(itens);
    var bruto = todos.length > 0 ? todos[0] : null;
    if (bruto !== null) {
        if (!ehVazio(bruto.statusCode) || !ehVazio(bruto.body)) {
            status = numero(bruto.statusCode);
            corpo = ehVazio(bruto.body) ? null : bruto.body;
        } else {
            corpo = bruto;
        }
    }
    var caminho = texto(contrato.envelope_da_leitura.caminho_dos_registros).split('.');
    var chave = texto(contrato.envelope_da_leitura.chave_do_sucesso);
    if (corpo === null) {
        return { id: texto(id), medido: false, motivo: 'leitura_sem_resposta', registros: [] };
    }
    if (typeof corpo === 'string') {
        try { corpo = JSON.parse(corpo); } catch (erro) { corpo = null; }
    }
    if (!ehObjeto(corpo)) {
        return { id: texto(id), medido: false, motivo: 'corpo_da_leitura_ilegivel', registros: [] };
    }
    if (status !== null && (status < 200 || status >= 300)) {
        var codigo = texto(corpo.codigo);
        return {
            id: texto(id), medido: false, registros: [],
            motivo: 'leitura_recusada:http_' + String(status) + (ehVazio(codigo) ? '' : ':' + codigo)
        };
    }
    if (corpo[chave] !== true) {
        return { id: texto(id), medido: false, motivo: 'leitura_sem_ok', registros: [] };
    }
    var alvo = corpo;
    for (var i = 0; i < caminho.length; i++) {
        if (!ehObjeto(alvo)) return { id: texto(id), medido: false, motivo: 'leitura_sem_dados', registros: [] };
        alvo = alvo[caminho[i]];
    }
    if (!Array.isArray(alvo)) {
        return { id: texto(id), medido: false, motivo: 'leitura_sem_registros', registros: [] };
    }
    return { id: texto(id), medido: true, motivo: '', registros: alvo };
}

/* ------------------------------------------------------------------ avaliacao */

function indeterminacao(problemas, regra, motivo) {
    problemas.push({ regra: texto(regra), motivo: texto(motivo) });
}

function divergencia(contrato, id, campos) {
    var comparacao = comparacaoPorId(contrato, id);
    if (comparacao === null) return null;
    var saida = {
        id: texto(comparacao.id),
        comparacao: texto(comparacao.id),
        grupo: texto(comparacao.grupo),
        tipo: texto(comparacao.tipo)
    };
    for (var chave in campos) {
        if (Object.prototype.hasOwnProperty.call(campos, chave)) saida[chave] = campos[chave];
    }
    return saida;
}

function observacao(contrato, id, campos) {
    var declarada = observacaoPorId(contrato, id);
    if (declarada === null) return null;
    var saida = {
        id: texto(declarada.id),
        tipo: texto(declarada.tipo),
        informativa: declarada.informativa !== false
    };
    for (var chave in campos) {
        if (Object.prototype.hasOwnProperty.call(campos, chave)) saida[chave] = campos[chave];
    }
    return saida;
}

function pior(listaDeVereditos, ordem) {
    var piorAte = null;
    for (var i = 0; i < listaDeVereditos.length; i++) {
        var atual = texto(listaDeVereditos[i]);
        var posicaoAtual = ordem.indexOf(atual);
        if (posicaoAtual < 0) continue;
        if (piorAte === null || posicaoAtual > ordem.indexOf(piorAte)) piorAte = atual;
    }
    return piorAte;
}

/**
 * A DECISAO da rodada. Recebe:
 *   contrato       — o contrato versionado (parametro, nao fonte de literal);
 *   origem itens   — linhas de n8n/sql/reconciliacao-origem.sql;
 *   pendentesItens — linhas de n8n/sql/reconciliacao-pendentes.sql;
 *   leituras       — { <id da leitura>: itens crus da porta unica };
 *   agoraIso       — instante da rodada (o aceite injeta para o relatorio ser comparavel).
 *
 * Devolve o RELATORIO da rodada: veredito, cobertura, contagens, divergencias,
 * observacoes, indeterminacoes, linha-resumo e o relatorio em texto.
 */
function avaliar(contrato, origemItens, pendentesItens, leituras, agoraIso) {
    var tetoDoMotivo = contrato && contrato.relatorio ? contrato.relatorio.tamanho_maximo_do_motivo : null;
    var problemas = [];
    var divergencias = [];
    var observacoes = [];

    if (!contratoValido(contrato)) {
        return {
            card: 'TRE-W3-E04-T01',
            nucleo_versao: NUCLEO_VERSAO,
            versao_do_contrato: null,
            rodada: texto(agoraIso),
            veredito: texto(contrato && contrato.veredito && contrato.veredito.ordem
                ? contrato.veredito.ordem[contrato.veredito.ordem.length - 1] : 'INDETERMINADO'),
            cobertura: {},
            contagens: { divergencias: 0, observacoes: 0, indeterminacoes: 1, por_tipo: {} },
            divergencias: [],
            observacoes: [],
            indeterminacoes: [{ regra: 'contrato_invalido', motivo: 'contrato da reconciliacao ausente ou incompleto: nada a decidir' }],
            linha: 'VEREDITO: INDETERMINADO — contrato da reconciliacao invalido',
            relatorio: 'RECONCILIACAO INDETERMINADA — contrato da reconciliacao invalido.'
        };
    }
    var ordem = lista(contrato.veredito.ordem);
    var teto = numero(contrato.teto_de_tentativas);
    var janela = numero(contrato.janela_de_pendencia_s);

    /* -- origem ---------------------------------------------------------- */
    var lote = montarLote(contrato, origemItens);
    if (lote.cobertura === null) {
        indeterminacao(problemas, 'consulta_de_origem_sem_cobertura',
            'a consulta de origem nao devolveu a linha de cobertura: nao se sabe qual era o lote nem se a janela estava completa');
    }
    var totalDeOrganizacoes = lote.cobertura && lote.cobertura.total_de_organizacoes !== null
        ? lote.cobertura.total_de_organizacoes : null;
    var limite = lote.cobertura && lote.cobertura.limite !== null ? lote.cobertura.limite : null;
    if (limite === null) {
        indeterminacao(problemas, 'limite_do_lote_sem_medicao',
            'a linha de cobertura nao trouxe o limite do lote: a janela nao pode ser declarada completa nem parcial');
    }

    /* -- leituras do destino --------------------------------------------- */
    var medidas = {};
    var itensDeLeitura = ehObjeto(leituras) ? leituras : {};
    var declaradas = lista(contrato.leituras_do_destino);
    for (var i = 0; i < declaradas.length; i++) {
        var id = texto(declaradas[i].id);
        var resultado = lerResposta(contrato, id, itensDeLeitura[id]);
        medidas[id] = resultado;
        if (!resultado.medido) {
            indeterminacao(problemas, 'leitura_do_destino_nao_medida',
                'leitura `' + id + '`: ' + resultado.motivo);
        }
    }
    var esperadas = 0;
    for (var e1 = 0; e1 < lote.entidades.length; e1++) {
        if (lote.entidades[e1].esperada) esperadas++;
    }
    var algumaLeituraMedida = false;
    for (var m in medidas) {
        if (Object.prototype.hasOwnProperty.call(medidas, m) && medidas[m].medido) algumaLeituraMedida = true;
    }
    if (esperadas > 0 && !algumaLeituraMedida) {
        indeterminacao(problemas, 'entidade_esperada_sem_leitura',
            'ha ' + String(esperadas) + ' entidade(s) esperada(s) no destino e NENHUMA leitura do destino foi medida: nao se compara o que nao se mediu');
    }

    /* indices do destino — os IDs das leituras vem do CONTRATO (`valores_de`), nao de literal */
    var idDaLeituraDeParceiro = null;
    var idDaLeituraDeOrganizacao = null;
    for (var dl = 0; dl < declaradas.length; dl++) {
        if (texto(declaradas[dl].valores_de) === 'odoo_partner_id') idDaLeituraDeParceiro = texto(declaradas[dl].id);
        else idDaLeituraDeOrganizacao = texto(declaradas[dl].id);
    }
    var porId = {};
    var registrosPorId = idDaLeituraDeParceiro && medidas[idDaLeituraDeParceiro]
        ? medidas[idDaLeituraDeParceiro].registros : [];
    for (var p = 0; p < registrosPorId.length; p++) {
        var idParceiro = texto(registrosPorId[p].id);
        if (!ehVazio(idParceiro) && !porId[idParceiro]) porId[idParceiro] = registrosPorId[p];
    }
    var porCompanyId = {};
    var registrosPorCompany = idDaLeituraDeOrganizacao && medidas[idDaLeituraDeOrganizacao]
        ? medidas[idDaLeituraDeOrganizacao].registros : [];
    for (var q = 0; q < registrosPorCompany.length; q++) {
        var company = texto(registrosPorCompany[q].tf_company_id);
        if (ehVazio(company)) continue;
        if (!porCompanyId[company]) porCompanyId[company] = [];
        porCompanyId[company].push(registrosPorCompany[q]);
    }
    var idsDoLote = {};
    for (var x = 0; x < lote.entidades.length; x++) idsDoLote[lote.entidades[x].id] = true;

    /* Leitura NAO medida nao e' medicao: sem as leituras do destino medidas, "ausente" seria
       ausencia de MEDICAO disfarcada de divergencia. MEDIDO no aceite (estado G, porta unica
       parada): a rodada reportava E1 para todo espelho esperado, com o INDETERMINADO convivendo
       com a divergencia inventada. Aqui a comparacao de entidade e' declaradamente PULADA e o pulo
       fica NOMEADO nas indeterminacoes — silencio nao serve a um job cujo defeito pior e' ler
       ausencia de medicao como ausencia de problema. */
    var leiturasDeEntidadeMedidas = true;
    for (var lm = 0; lm < declaradas.length; lm++) {
        var medidaDaLm = medidas[texto(declaradas[lm].id)];
        if (!medidaDaLm || !medidaDaLm.medido) leiturasDeEntidadeMedidas = false;
    }
    if (!leiturasDeEntidadeMedidas) {
        indeterminacao(problemas, 'comparacoes_de_entidade_puladas',
            'sem as duas leituras do destino MEDIDAS nenhuma entidade e comparada: '
            + 'ausencia de medicao nao vira divergencia');
    }

    /* -- comparacoes de entidade ----------------------------------------- */
    for (var n = 0; n < lote.entidades.length; n++) {
        var entidade = lote.entidades[n];
        if (!entidade.esperada) continue;
        if (!leiturasDeEntidadeMedidas) continue;
        var parceiroPorId = entidade.odoo_partner_id !== null && porId[entidade.odoo_partner_id]
            ? porId[entidade.odoo_partner_id] : null;
        var parceirosPorCompany = porCompanyId[entidade.id] || [];
        var presente = parceiroPorId !== null ? parceiroPorId : (parceirosPorCompany.length > 0 ? parceirosPorCompany[0] : null);

        if (presente === null) {
            divergencias.push(divergencia(contrato, 'E1', {
                entidade: entidade.id,
                nome: entidade.nome,
                operacao_do_espelho: entidade.operacao_do_espelho,
                odoo_partner_id: entidade.odoo_partner_id,
                motivo: sanitizar('espelho esperado (' + (entidade.espelho_entregue
                    ? 'evento de espelho entregue: ' + (entidade.operacao_do_espelho || 'operacao declarada')
                    : 'ponta gravada em organizations.odoo_partner_id=' + (entidade.odoo_partner_id || '-'))
                    + ') e ausente nas duas leituras do destino', tetoDoMotivo)
            }));
        } else {
            if (!sim(presente.active)) {
                divergencias.push(divergencia(contrato, 'E2', {
                    entidade: entidade.id,
                    nome: entidade.nome,
                    parceiro_id: texto(presente.id),
                    motivo: sanitizar('o espelho existe no CRM mas esta arquivado (active=false)', tetoDoMotivo)
                }));
            }

            if (parceiroPorId !== null) {
                var canonico = texto(parceiroPorId.tf_company_id);
                if (canonico !== entidade.id) {
                    if (ehVazio(canonico)) {
                        divergencias.push(divergencia(contrato, 'I1', {
                            entidade: entidade.id,
                            nome: entidade.nome,
                            parceiro_id: texto(parceiroPorId.id),
                            motivo: sanitizar('o parceiro apontado por organizations.odoo_partner_id=' + entidade.odoo_partner_id
                                + ' nao declara o ID canonico da organizacao (tf_company_id vazio)', tetoDoMotivo)
                        }));
                    } else if (!idsDoLote[canonico]) {
                        divergencias.push(divergencia(contrato, 'I4', {
                            entidade: entidade.id,
                            nome: entidade.nome,
                            parceiro_id: texto(parceiroPorId.id),
                            tf_company_id: canonico,
                            motivo: sanitizar('o espelho aponta para a organizacao ' + canonico
                                + ', que nao esta no lote desta rodada (janela; com a janela completa isto e um orfao)', tetoDoMotivo)
                        }));
                    } else {
                        divergencias.push(divergencia(contrato, 'I1', {
                            entidade: entidade.id,
                            nome: entidade.nome,
                            parceiro_id: texto(parceiroPorId.id),
                            tf_company_id: canonico,
                            motivo: sanitizar('o espelho apontado por organizations.odoo_partner_id=' + entidade.odoo_partner_id
                                + ' declara tf_company_id=' + canonico + ' (outra organizacao)', tetoDoMotivo)
                        }));
                    }
                }

                var fortes = lista(contrato.identificadores_fortes);
                for (var f = 0; f < fortes.length; f++) {
                    var campoDestino = texto(fortes[f].destino);
                    var valorOrigem = texto(entidade.fortes[campoDestino]);
                    var valorDestino = texto(parceiroPorId[campoDestino]);
                    if (ehVazio(valorOrigem) || ehVazio(valorDestino)) continue;
                    if (valorOrigem === valorDestino) continue;
                    divergencias.push(divergencia(contrato, 'E4', {
                        entidade: entidade.id,
                        nome: entidade.nome,
                        parceiro_id: texto(parceiroPorId.id),
                        campo: campoDestino,
                        motivo: sanitizar('identificador forte ' + campoDestino + ' difere entre origem e espelho', tetoDoMotivo)
                    }));
                }
            }

            if (parceirosPorCompany.length > 0) {
                var declarado = entidade.odoo_partner_id;
                for (var v = 0; v < parceirosPorCompany.length; v++) {
                    if (texto(parceirosPorCompany[v].id) !== texto(declarado)) {
                        divergencias.push(divergencia(contrato, 'I2', {
                            entidade: entidade.id,
                            nome: entidade.nome,
                            parceiro_id: texto(parceirosPorCompany[v].id),
                            odoo_partner_id: declarado,
                            motivo: sanitizar('o parceiro ' + texto(parceirosPorCompany[v].id)
                                + ' se declara o espelho desta organizacao e organizations.odoo_partner_id '
                                + (ehVazio(declarado) ? 'esta vazio' : 'aponta para ' + declarado), tetoDoMotivo)
                        }));
                    }
                }
                if (parceirosPorCompany.length > 1) {
                    divergencias.push(divergencia(contrato, 'I3', {
                        entidade: entidade.id,
                        nome: entidade.nome,
                        parceiros: parceirosPorCompany.length,
                        motivo: sanitizar(String(parceirosPorCompany.length)
                            + ' parceiros declaram o mesmo tf_company_id: o ID canonico tem mais de uma ponta no CRM', tetoDoMotivo)
                    }));
                }
            }
        }
    }

    /* -- comparacoes de pendencia ---------------------------------------- */
    var pendentes = linhasPorTipo(pendentesItens, 'pendente');
    var coberturaDaFila = linhaDeCobertura(pendentesItens);
    if (coberturaDaFila === null) {
        indeterminacao(problemas, 'consulta_de_pendentes_sem_cobertura',
            'a consulta da fila nao devolveu a linha de cobertura: nao se sabe se a fila estava vazia ou se a consulta quebrou');
    }
    var vocabulario = {
        sucesso: texto(contrato.status_da_trilha.sucesso),
        falha: texto(contrato.status_da_trilha.falha_transitoria),
        recusa: texto(contrato.status_da_trilha.recusa)
    };
    var statusConhecidos = [vocabulario.sucesso, vocabulario.falha, vocabulario.recusa];
    for (var k = 0; k < pendentes.length; k++) {
        var pendente = pendentes[k];
        var idade = numero(pendente.idade_s);
        if (idade === null) {
            indeterminacao(problemas, 'pendente_sem_idade',
                'evento ' + texto(pendente.evento_id) + ' na fila sem idade medivel: a janela de pendencia nao pode ser aplicada');
        } else if (janela !== null && idade >= janela) {
            divergencias.push(divergencia(contrato, 'P1', {
                entidade: texto(pendente.aggregate_id) || texto(pendente.evento_id),
                evento_id: texto(pendente.evento_id),
                event_type: texto(pendente.event_type),
                idade_s: idade,
                motivo: sanitizar('evento na fila ha ' + String(Math.round(idade)) + 's (janela declarada: '
                    + String(janela) + 's)'
                    + (ehVazio(pendente.motivo) ? ' e sem motivo nomeado' : '; motivo: ' + texto(pendente.motivo)), tetoDoMotivo)
            }));
        }

        var tentativas = numero(pendente.attempts);
        if (tentativas !== null && teto !== null && tentativas >= teto) {
            divergencias.push(divergencia(contrato, 'P3', {
                entidade: texto(pendente.aggregate_id) || texto(pendente.evento_id),
                evento_id: texto(pendente.evento_id),
                event_type: texto(pendente.event_type),
                attempts: tentativas,
                motivo: sanitizar('evento na fila com ' + String(tentativas) + ' tentativa(s) — no teto declarado ('
                    + String(teto) + ') ele deveria ter saido da fila como ' + texto(contrato.status_da_fila.recusa), tetoDoMotivo)
            }));
        }

        if (sim(pendente.tem_trilha)) {
            var statusTrilha = texto(pendente.status_da_trilha);
            if (statusTrilha === vocabulario.sucesso) {
                divergencias.push(divergencia(contrato, 'P2', {
                    entidade: texto(pendente.aggregate_id) || texto(pendente.evento_id),
                    evento_id: texto(pendente.evento_id),
                    event_type: texto(pendente.event_type),
                    chave: texto(pendente.chave),
                    motivo: sanitizar('a trilha da chave diz entregue (' + vocabulario.sucesso + ') e o evento continua na fila como '
                        + texto(pendente.status), tetoDoMotivo)
                }));
            } else if (statusTrilha === vocabulario.recusa) {
                divergencias.push(divergencia(contrato, 'P4', {
                    entidade: texto(pendente.aggregate_id) || texto(pendente.evento_id),
                    evento_id: texto(pendente.evento_id),
                    event_type: texto(pendente.event_type),
                    chave: texto(pendente.chave),
                    motivo: sanitizar('a trilha da chave registra recusa definitiva (' + vocabulario.recusa + ') e o evento continua na fila como '
                        + texto(pendente.status), tetoDoMotivo)
                }));
            } else if (statusConhecidos.indexOf(statusTrilha) < 0) {
                indeterminacao(problemas, 'trilha_sem_status_conhecido',
                    'a trilha da chave ' + texto(pendente.chave) + ' tem status fora do vocabulario declarado: '
                    + (ehVazio(statusTrilha) ? 'vazio' : statusTrilha));
            }
        }
    }

    /* -- observacoes ------------------------------------------------------ */
    if (esperadas === 0) {
        var observada = observacao(contrato, 'O1', {
            entidade: null,
            motivo: sanitizar('o lote tem ' + String(lote.entidades.length)
                + ' organizacao(oes) e nenhuma esperada no destino: nao houve ida-e-volta a medir nesta rodada', tetoDoMotivo)
        });
        if (observada !== null) observacoes.push(observada);
    }

    /* -- veredito -------------------------------------------------------- */
    var janelaCompleta = (totalDeOrganizacoes !== null && limite !== null)
        ? totalDeOrganizacoes <= limite : null;
    var totalDaFila = (coberturaDaFila !== null
        && coberturaDaFila.total_pending !== undefined && coberturaDaFila.total_pending !== null
        && coberturaDaFila.total_retry !== undefined && coberturaDaFila.total_retry !== null)
        ? Number(coberturaDaFila.total_pending) + Number(coberturaDaFila.total_retry) : null;
    var limiteDaFila = numero(contrato.lote && contrato.lote.limite_de_eventos);
    if (totalDaFila === null || limiteDaFila === null) {
        indeterminacao(problemas, 'limite_da_fila_sem_medicao',
            'a linha de cobertura da fila nao trouxe o total por status (ou o contrato nao declara o limite): a leitura da fila nao pode ser declarada completa nem parcial');
    }
    var filaLeituraCompleta = (totalDaFila !== null && limiteDaFila !== null)
        ? totalDaFila <= limiteDaFila : null;
    var veredito = 'OK';
    if (problemas.length > 0) veredito = 'INDETERMINADO';
    else if (divergencias.length > 0) veredito = 'DIVERGENTE';

    var coberturaDoResultado = {
        entidades_lidas: lote.entidades.length,
        entidades_esperadas: esperadas,
        total_de_organizacoes: totalDeOrganizacoes,
        limite: limite,
        janela_completa: janelaCompleta,
        eventos_na_fila_lidos: pendentes.length,
        total_pending: coberturaDaFila ? numero(coberturaDaFila.total_pending) : null,
        total_retry: coberturaDaFila ? numero(coberturaDaFila.total_retry) : null,
        total_na_fila: totalDaFila,
        limite_de_eventos: limiteDaFila,
        fila_leitura_completa: filaLeituraCompleta,
        leituras: {}
    };
    for (var m2 in medidas) {
        if (Object.prototype.hasOwnProperty.call(medidas, m2)) {
            coberturaDoResultado.leituras[m2] = { medido: medidas[m2].medido, registros: medidas[m2].registros.length };
        }
    }

    var porTipo = {};
    for (var d = 0; d < divergencias.length; d++) {
        var tipo = texto(divergencias[d].tipo);
        porTipo[tipo] = (porTipo[tipo] || 0) + 1;
    }
    var rotuloDaJanela = janelaCompleta === true ? 'completa'
        : (janelaCompleta === false ? 'parcial' : 'nao_medida');
    var rotuloDaFila = filaLeituraCompleta === true ? 'completa'
        : (filaLeituraCompleta === false ? 'parcial' : 'nao_medida');
    var linha = 'VEREDITO: ' + veredito + ' — ' + String(lote.entidades.length) + ' entidades medidas, '
        + String(pendentes.length) + ' eventos na fila lidos, ' + String(divergencias.length)
        + ' divergencias; cobertura: janela_' + rotuloDaJanela + ', fila_' + rotuloDaFila;

    /* -- relatorio em texto ---------------------------------------------- */
    var linhasDoRelatorio = [];
    linhasDoRelatorio.push('RECONCILIACAO — rodada ' + texto(agoraIso) + ' (contrato '
        + texto(contrato.versao) + ', nucleo ' + NUCLEO_VERSAO + ')');
    linhasDoRelatorio.push(linha);
    linhasDoRelatorio.push('COBERTURA: entidades_lidas=' + String(coberturaDoResultado.entidades_lidas)
        + ' esperadas=' + String(coberturaDoResultado.entidades_esperadas)
        + ' total_de_organizacoes=' + (totalDeOrganizacoes === null ? 'nao_medido' : String(totalDeOrganizacoes))
        + ' limite=' + (limite === null ? 'nao_medido' : String(limite))
        + ' janela=' + rotuloDaJanela
        + ' eventos_na_fila=' + String(coberturaDoResultado.eventos_na_fila_lidos)
        + ' fila=' + rotuloDaFila + ' (total=' + (totalDaFila === null ? 'nao_medido' : String(totalDaFila))
        + ' limite=' + (limiteDaFila === null ? 'nao_medido' : String(limiteDaFila)) + ')'
        + ' destino=' + Object.keys(coberturaDoResultado.leituras).map(function (chaveDeLeitura) {
            return chaveDeLeitura + ':' + (coberturaDoResultado.leituras[chaveDeLeitura].medido
                ? String(coberturaDoResultado.leituras[chaveDeLeitura].registros) + ' registro(s)' : 'NAO MEDIDO');
        }).join(' '));
    if (problemas.length > 0) {
        linhasDoRelatorio.push('INDETERMINADO (' + String(problemas.length) + '):');
        for (var pr = 0; pr < problemas.length; pr++) {
            linhasDoRelatorio.push('  [' + problemas[pr].regra + '] ' + problemas[pr].motivo);
        }
    }
    linhasDoRelatorio.push('DIVERGENCIAS (' + String(divergencias.length) + '):');
    if (divergencias.length === 0) linhasDoRelatorio.push('  nenhuma');
    for (var dv = 0; dv < divergencias.length; dv++) {
        var textoDaDivergencia = '  [' + divergencias[dv].id + '] ' + divergencias[dv].tipo;
        if (!ehVazio(divergencias[dv].entidade)) textoDaDivergencia += ' — entidade ' + divergencias[dv].entidade;
        if (!ehVazio(divergencias[dv].evento_id)) textoDaDivergencia += ' — evento ' + divergencias[dv].evento_id;
        if (!ehVazio(divergencias[dv].motivo)) textoDaDivergencia += ': ' + divergencias[dv].motivo;
        linhasDoRelatorio.push(textoDaDivergencia);
    }
    if (observacoes.length > 0) {
        linhasDoRelatorio.push('OBSERVACOES (' + String(observacoes.length) + '):');
        for (var ob = 0; ob < observacoes.length; ob++) {
            linhasDoRelatorio.push('  [' + observacoes[ob].id + '] ' + observacoes[ob].tipo + ': ' + observacoes[ob].motivo);
        }
    }

    return {
        card: 'TRE-W3-E04-T01',
        nucleo_versao: NUCLEO_VERSAO,
        versao_do_contrato: texto(contrato.versao),
        rodada: texto(agoraIso),
        veredito: veredito,
        cobertura: coberturaDoResultado,
        contagens: {
            divergencias: divergencias.length,
            observacoes: observacoes.length,
            indeterminacoes: problemas.length,
            por_tipo: porTipo
        },
        divergencias: divergencias,
        observacoes: observacoes,
        indeterminacoes: problemas,
        linha: linha,
        relatorio: linhasDoRelatorio.join('\n')
    };
}

/* ------------------------------------------------------------------ adaptadores
 * Os adaptadores sao a UNICA parte escrita para o n8n (`$input` / `$`). O corpo
 * acima e' puro e roda igual no n8n e no node do aceite (scripts/n8n).
 * --------------------------------------------------------------------------- */

/** Adaptador do Code node "Preparar leitura (nucleo)": monta os pedidos da porta unica. */
function adaptadorPrepararLeitura(contrato, itens) {
    return [{ json: montarPedidos(contrato, itens) }];
}

/**
 * Adaptador do Code node "Avaliar": junta as TRES fontes por REFERENCIA de no'
 * (a origem, a fila e as duas leituras da porta unica) e roda o nucleo.
 */
function adaptadorAvaliar(contrato, origem, pendentes, leituras, agoraIso) {
    return [{ json: avaliar(contrato, origem, pendentes, leituras, agoraIso) }];
}

/* Exportacao para o node do aceite (no n8n nao existe `require`/`module`). */
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        NUCLEO_VERSAO: NUCLEO_VERSAO,
        contratoValido: contratoValido,
        montarPedidos: montarPedidos,
        montarLote: montarLote,
        lerResposta: lerResposta,
        avaliar: avaliar,
        adaptadorPrepararLeitura: adaptadorPrepararLeitura,
        adaptadorAvaliar: adaptadorAvaliar
    };
}
