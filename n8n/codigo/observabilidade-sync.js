/* ============================================================================
 * Nucleo da OBSERVABILIDADE DA SINCRONIZACAO — card TRE-W3-E05-T01
 * (board transformativa-revenue-engine, card t_0b77a689).
 *
 * O QUE ESTE ARQUIVO E', E POR QUE ELE E' PURO:
 * Aqui vive a DECISAO da observabilidade: qual metrica foi medida, se o valor
 * cabe no limiar, qual e' o veredito da rodada e o que o RELATORIO escreve.
 * Este arquivo NAO fala com banco, NAO fala com HTTP e NAO importa nada (o
 * sandbox de Code node do n8n nao tem `require`): quem consulta e' o workflow
 * (os dois arquivos de SQL); quem decide e' este nucleo. E' a mesma separacao
 * do consumidor de outbox (n8n/codigo/nucleo-outbox-consumer.js) e do motor da
 * API controlada (odoo/addons/transformativa_sales_ai/api/motor.py).
 *
 * FONTE DA REGRA (nada aqui e' inventado, nada e' literal):
 *   * n8n/contracts/observabilidade-sync.v1.json — o contrato versionado desta
 *     superficie: metricas, unidades, LIMIARES, vocabularios, regras de
 *     fail-closed, conferencias cruzadas e formato do relatorio. O contrato e'
 *     PARAMETRO: nao existe limiar, nome de metrica ou lista de status neste
 *     arquivo. Trocar limiar = nova versao do contrato, nunca edicao daqui;
 *   * docs/data/DATA_CONTRACT_V1.md §6 (regras de integracao 1..5) e §4
 *     (outbox_events, sync_events);
 *   * doc 06 §7-8 (idempotencia, retry limitado, dead-letter COM motivo,
 *     relatorio) e doc 07 §6 (W3: sync, reconciliacao, observabilidade).
 *
 * A REGRA QUE ORGANIZA TUDO — AUSENCIA DE MEDICAO NAO E' SAUDE:
 * metrica declarada sem linha, valor nao numerico, metrica que o SQL devolve
 * sem estar declarada, limiar ausente, direcao desconhecida, dimensao fora do
 * vocabulario: todos fecham em INDETERMINADO, NUNCA em OK. Um numero que nao
 * se pode medir nao autoriza confiar nos outros. `ausencia: zero` (declarado
 * POR METRICA no contrato) e' a unica excecao, e vale so' para o valor NULO do
 * agregado — fila vazia e' informacao (zero eventos esperando), nao ausencia.
 *
 * O QUE ESTE ARQUIVO NAO FAZ (lacuna declarada, de proposito):
 *   * nao corrige nada: observabilidade relata, reconciliacao (card
 *     TRE-W3-E04-T01) e' quem compara entidade a entidade;
 *   * nao conta replay/dedup: a V1 nao guarda essa marca na trilha (o contrato
 *     declara a lacuna) e inventar a contagem seria relatar o que nao se mediu;
 *   * nao escolhe o destino do alerta: entrega o veredito e o relatorio; o
 *     canal de plantao e' decisao do dono;
 *   * nao imprime payload: o relatorio mostra identificador e MOTIVO, nunca o
 *     corpo do evento (que pode carregar dado pessoal ou segredo).
 *
 * VERSAO: acompanha o contrato (n8n/contracts/observabilidade-sync.v1.json).
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

function ehObjeto(valor) {
    return valor !== null && typeof valor === 'object' && !Array.isArray(valor);
}

/* Numero medido: aceita number e a representacao textual do driver do n8n.
 * Qualquer outra coisa NAO vira zero — vira INDETERMINADO (fail-closed). */
function numero(valor) {
    if (typeof valor === 'number' && isFinite(valor)) return valor;
    if (typeof valor === 'string' && valor.trim() !== '') {
        var n = Number(valor);
        if (isFinite(n)) return n;
    }
    return null;
}

/* Motivo: uma linha, sem controle, com teto declarado no contrato (o motivo e'
 * nomeado e curto; texto longo e' truncado COM MARCA — nunca cortado em silencio).
 * Teto invalido NAO vira numero implicito: sai o texto inteiro (ja' sanitizado). O
 * teto e' exigido por `contratoValido`, entao este ramo so' existe para o caso do
 * contrato invalido — e nele cortar em silencio esconderia o motivo, que e' o que a
 * observabilidade existe para mostrar. */
function sanitizar(motivo, teto) {
    var limite = numero(teto);
    var t = texto(motivo).replace(/[\r\n\t]+/g, ' ').replace(/[\u0000-\u001f\u007f]/g, ' ');
    t = t.replace(/\s{2,}/g, ' ');
    if (limite === null || limite <= 0) return t;
    if (t.length > limite) return t.slice(0, limite) + '...';
    return t;
}

/* ------------------------------------------------------------------ contrato */

function metricaDeclarada(contrato, id) {
    var metricas = (contrato && contrato.metricas) || [];
    for (var i = 0; i < metricas.length; i++) {
        if (metricas[i].id === texto(id)) return metricas[i];
    }
    return null;
}

function contratoValido(contrato) {
    if (!ehObjeto(contrato)) return false;
    if (!Array.isArray(contrato.metricas) || contrato.metricas.length === 0) return false;
    if (!ehObjeto(contrato.veredito) || !Array.isArray(contrato.veredito.ordem)) return false;
    if (!ehObjeto(contrato.veredito.saida)) return false;
    if (!ehObjeto(contrato.detalhes) || !ehObjeto(contrato.detalhes.tipos)) return false;
    if (!ehObjeto(contrato.regras_de_fail_closed)) return false;
    // O teto do motivo no relatorio e' parametro DECLARADO (nao ha numero de reserva
    // no nucleo): contrato sem ele nao decide nada — fecha INDETERMINADO.
    if (numero(contrato.relatorio && contrato.relatorio.tamanho_maximo_do_motivo) === null) return false;
    return true;
}

/* ------------------------------------------------------------------ limiares */

/**
 * Limites DECLARADOS da metrica. Devolve um objeto com o diagnostico em vez de
 * "null": metrica que decide veredito sem limiar declarado e' contrato
 * invalido — nao existe limiar implicito (o defeito que a casa cobra).
 */
function limitesDaMetrica(contrato, metrica) {
    var informativa = metrica.informativa === true;
    if (metrica.limites === null || metrica.limites === undefined) {
        if (informativa) return { informativa: true, limites: null, erro: null };
        return { informativa: false, limites: null, erro: 'limiar_ausente' };
    }
    if (!ehObjeto(metrica.limites)) return { informativa: false, limites: null, erro: 'limiar_ausente' };
    var alerta = numero(metrica.limites.alerta);
    var critico = numero(metrica.limites.critico);
    if (alerta === null || critico === null) return { informativa: false, limites: null, erro: 'limiar_ausente' };
    if (critico < alerta) return { informativa: false, limites: null, erro: 'limiar_invertido' };
    return { informativa: false, limites: { alerta: alerta, critico: critico }, erro: null };
}

/* Direcao declarada da metrica: a v1 so' conhece `maior_pior`. Direcao inferida
 * e' proibida (o nucleo nao decide o sentido de um numero por conta propria). */
function direcaoValida(metrica) {
    return texto(metrica.direcao) === 'maior_pior';
}

/** Avaliacao de UM valor contra os limites declarados. */
function avaliarValor(valor, limites) {
    if (valor >= limites.critico) return 'CRITICO';
    if (valor >= limites.alerta) return 'ATENCAO';
    return 'OK';
}

/* ------------------------------------------------------------------ metricas */

function avaliacaoDaMetrica(id, titulo, unidade, avaliacao, valor, limites, observacao) {
    return {
        id: id,
        titulo: titulo || '',
        unidade: unidade || '',
        avaliacao: avaliacao,
        valor: valor,
        limites: limites || null,
        observacao: observacao || null
    };
}

/** Dimensao de uma linha: as chaves declaradas da metrica, presentes e conhecidas. */
function dimensaoDaMetrica(contrato, metrica, dimensao) {
    var declaracao = metrica.dimensional || {};
    var valores = declaracao.valores || {};
    var campos = declaracao.campos || [];
    var chaves = [];
    var rotulos = [];
    for (var i = 0; i < campos.length; i++) {
        var campo = campos[i];
        var valor = ehObjeto(dimensao) ? texto(dimensao[campo]) : '';
        if (valores[campo] && valores[campo].indexOf(valor) < 0) {
            return { erro: 'dimensao_fora_do_vocabulario:' + campo + '=' + (valor || '(vazio)'), chaves: null, rotulo: null };
        }
        chaves.push(valor);
        rotulos.push(campo + '=' + valor);
    }
    // O PAR (source_system, target_system) tem de ser uma das DIRECOES declaradas:
    // cada campo solto pode estar no vocabulario e o par nao ser uma porta
    // (`odoo->odoo` passaria campo a campo). Direcao declarada, nao inferida.
    if (campos.indexOf('source_system') >= 0 && campos.indexOf('target_system') >= 0) {
        var origem = ehObjeto(dimensao) ? texto(dimensao.source_system) : '';
        var destino = ehObjeto(dimensao) ? texto(dimensao.target_system) : '';
        var direcoes = direcoesDeclaradas(contrato);
        var achada = false;
        for (var d = 0; d < direcoes.length; d++) {
            if (direcoes[d].source_system === origem && direcoes[d].target_system === destino) achada = true;
        }
        if (!achada) {
            return { erro: 'dimensao_fora_do_vocabulario:direcao=' + (origem || '(vazio)') + '->' + (destino || '(vazio)'), chaves: null, rotulo: null };
        }
    }
    return { erro: null, chaves: chaves.join('|'), rotulo: rotulos.join('/') };
}

/** Direcoes declaradas do contrato, no formato `<source>-><target>`. */
function direcoesDeclaradas(contrato) {
    var lista = (contrato && contrato.vocabulario_da_trilha && contrato.vocabulario_da_trilha.direcoes_declaradas) || [];
    var saida = [];
    for (var i = 0; i < lista.length; i++) {
        saida.push({ id: lista[i].id, source_system: lista[i].source_system, target_system: lista[i].target_system });
    }
    return saida;
}

/* Combinacoes declaradas da metrica dimensional (direcao x status), na ordem do
 * contrato. A combinacao AUSENTE na consulta vale o `combinacao_ausente`
 * declarado (0): nada aconteceu naquela direcao/status — e' informacao, nao
 * ausencia de medicao (por isso `ausencia: zero` na metrica). */
function combinacoesDeclaradas(contrato, metrica) {
    var declaracao = metrica.dimensional || {};
    var valores = declaracao.valores || {};
    var status = valores.status || [];
    var combinacoes = [];
    var direcoes = direcoesDeclaradas(contrato);
    for (var i = 0; i < direcoes.length; i++) {
        for (var j = 0; j < status.length; j++) {
            combinacoes.push({
                source_system: direcoes[i].source_system,
                target_system: direcoes[i].target_system,
                status: status[j],
                rotulo: direcoes[i].id + '/' + status[j]
            });
        }
    }
    return combinacoes;
}

/**
 * Mede UMA metrica declarada a partir das linhas devolvidas pelo SQL.
 * Devolve {avaliacoes: [...], indeterminados: [...]} — o mesmo formato que o
 * veredito consome, de modo que metrica "que decide" e "informativa" passam
 * pelo MESMO caminho e nenhuma some do relatorio.
 */
function medirMetrica(contrato, metrica, linhas) {
    var indeterminados = [];
    var avaliacoes = [];
    var declaracao = limitesDaMetrica(contrato, metrica);

    if (!direcaoValida(metrica)) {
        indeterminados.push({ id: metrica.id, motivo: 'direcao_desconhecida:' + texto(metrica.direcao) });
        return { avaliacoes: avaliacoes, indeterminados: indeterminados };
    }
    if (declaracao.erro) {
        indeterminados.push({ id: metrica.id, motivo: declaracao.erro });
        return { avaliacoes: avaliacoes, indeterminados: indeterminados };
    }

    var minhas = [];
    for (var i = 0; i < (linhas || []).length; i++) {
        if (texto(linhas[i] && linhas[i].metrica) === metrica.id) minhas.push(linhas[i]);
    }

    // LINHA AUSENTE e' sempre INDETERMINADO: todo agregado devolve uma linha
    // (a dimensional devolve as combinacoes existentes); nao devolver significa
    // que a superficie quebrou.
    if (minhas.length === 0) {
        indeterminados.push({ id: metrica.id, motivo: 'metrica_declarada_sem_linha' });
        return { avaliacoes: avaliacoes, indeterminados: indeterminados };
    }

    if (metrica.dimensional) {
        var por_combinacao = {};
        for (var k = 0; k < minhas.length; k++) {
            var dim = dimensaoDaMetrica(contrato, metrica, minhas[k].dimensao);
            if (dim.erro) {
                indeterminados.push({ id: metrica.id, motivo: dim.erro });
                return { avaliacoes: avaliacoes, indeterminados: indeterminados };
            }
            por_combinacao[dim.chaves] = { valor: minhas[k].valor, rotulo: dim.rotulo };
        }
        var combinacoes = combinacoesDeclaradas(contrato, metrica);
        for (var c = 0; c < combinacoes.length; c++) {
            var chave = [combinacoes[c].source_system, combinacoes[c].target_system, combinacoes[c].status].join('|');
            var achada = por_combinacao[chave];
            var valor_bruto = achada ? achada.valor : (metrica.dimensional.combinacao_ausente || 0);
            var medido = numero(valor_bruto);
            if (medido === null && valor_bruto !== null) {
                indeterminados.push({ id: metrica.id, motivo: 'valor_nao_numerico:' + combinacoes[c].rotulo });
                continue;
            }
            if (medido === null) medido = metrica.dimensional.combinacao_ausente || 0;
            avaliacoes.push(avaliacaoDaMetrica(
                metrica.id, metrica.titulo, metrica.unidade,
                declaracao.informativa ? 'INFORMATIVA' : avaliarValor(medido, declaracao.limites),
                medido, declaracao.limites, combinacoes[c].rotulo));
        }
        return { avaliacoes: avaliacoes, indeterminados: indeterminados };
    }

    if (minhas.length > 1) {
        indeterminados.push({ id: metrica.id, motivo: 'metrica_escalar_com_multiplas_linhas:' + minhas.length });
        return { avaliacoes: avaliacoes, indeterminados: indeterminados };
    }

    var bruto = minhas[0].valor;
    var valor = numero(bruto);
    if (valor === null) {
        // valor NULO do agregado: governado por `ausencia` (declarado por metrica)
        if (bruto === null || bruto === undefined || bruto === '') {
            if (texto(metrica.ausencia) === 'zero') {
                valor = 0;
            } else {
                indeterminados.push({ id: metrica.id, motivo: 'valor_nulo' });
                return { avaliacoes: avaliacoes, indeterminados: indeterminados };
            }
        } else {
            indeterminados.push({ id: metrica.id, motivo: 'valor_nao_numerico:' + texto(bruto).slice(0, 40) });
            return { avaliacoes: avaliacoes, indeterminados: indeterminados };
        }
    }
    avaliacoes.push(avaliacaoDaMetrica(
        metrica.id, metrica.titulo, metrica.unidade,
        declaracao.informativa ? 'INFORMATIVA' : avaliarValor(valor, declaracao.limites),
        valor, declaracao.limites, null));
    return { avaliacoes: avaliacoes, indeterminados: indeterminados };
}

/* ------------------------------------------------------------------ veredito */

function piorVeredito(a, b) {
    var ordem = ['OK', 'ATENCAO', 'CRITICO', 'INDETERMINADO'];
    return ordem.indexOf(a) >= ordem.indexOf(b) ? a : b;
}

/**
 * Avaliacao dos detalhes (dead-letter, falha de trilha, recusa de trilha):
 * sanitizada e agrupada por TIPO declarado no contrato. Tipo que o contrato nao
 * declara nao e' ignorado em silencio: entra no relatorio como divergencia.
 */
function avaliarDetalhes(contrato, linhas) {
    // Teto do motivo: valor DECLARADO no contrato (exigido por `contratoValido`), sem
    // numero de reserva no codigo — limiar/parametro que decide nao mora aqui.
    var teto = contrato.relatorio.tamanho_maximo_do_motivo;
    var tipos = (contrato.detalhes && contrato.detalhes.tipos) || {};
    var por_tipo = {};
    var divergencias = [];
    var total = 0;
    for (var i = 0; i < (linhas || []).length; i++) {
        var linha = linhas[i] || {};
        var tipo = texto(linha.tipo);
        if (!tipos[tipo]) {
            divergencias.push('tipo_de_detalhe_nao_declarado:' + (tipo || '(vazio)'));
            tipo = 'tipo_nao_declarado';
        }
        if (!por_tipo[tipo]) por_tipo[tipo] = [];
        por_tipo[tipo].push({
            id: texto(linha.id),
            event_type: texto(linha.event_type),
            direcao: texto(linha.direcao),
            status: texto(linha.status),
            tentativas: numero(linha.tentativas),
            quando: texto(linha.quando),
            motivo: sanitizar(linha.motivo, teto),
            com_motivo: !ehVazio(linha.motivo)
        });
        total++;
    }
    return { por_tipo: por_tipo, divergencias: divergencias, total: total };
}

/**
 * Conferencias cruzadas declaradas (contrato -> conferencias_cruzadas): metrica
 * e lista de detalhes tem de contar a MESMA historia. Divergencia = INDETERMINADO
 * (uma das duas consultas esta' errada e nao se escolhe a mais bonita).
 */
function conferenciasCruzadas(contrato, avaliacoes, detalhes) {
    var indeterminados = [];
    var conferencias = (contrato && contrato.conferencias_cruzadas) || [];
    var por_id = {};
    for (var i = 0; i < avaliacoes.length; i++) {
        if (!por_id[avaliacoes[i].id]) por_id[avaliacoes[i].id] = avaliacoes[i];
    }
    for (var c = 0; c < conferencias.length; c++) {
        var conf = conferencias[c];
        var metrica = por_id[conf.metrica];
        if (!metrica) continue; // metrica sem medida ja' esta' indeterminada por conta propria
        var itens = (detalhes.por_tipo[conf.tipo_de_detalhe]) || [];
        var sem_motivo = 0;
        for (var k = 0; k < itens.length; k++) if (!itens[k].com_motivo) sem_motivo++;
        if (sem_motivo !== metrica.valor) {
            indeterminados.push({
                id: conf.id,
                motivo: 'conferencia_cruzada_divergente:' + conf.metrica + '=' + metrica.valor +
                    ' x ' + conf.tipo_de_detalhe + ' sem motivo=' + sem_motivo
            });
        }
    }
    return indeterminados;
}

/**
 * Avalia uma rodada: metricas do SQL + detalhes + conferencias cruzadas.
 * Devolve o veredito, a lista de avaliacoes e o relatorio em texto.
 */
function avaliar(contrato, linhas, detalhes, agora) {
    var indeterminados = [];
    var avaliacoes = [];

    if (!contratoValido(contrato)) {
        return {
            veredito: 'INDETERMINADO',
            versao_contrato: null,
            nucleo_versao: NUCLEO_VERSAO,
            metricas: [],
            indeterminados: [{ id: 'contrato', motivo: 'contrato_invalido' }],
            relatorio: 'OBSERVABILIDADE DE SYNC — INDETERMINADO: contrato invalido (sem metricas/veredito/detalhes/regras declarados)',
            saida: null
        };
    }

    var metricas = contrato.metricas;
    for (var i = 0; i < metricas.length; i++) {
        var medida = medirMetrica(contrato, metricas[i], linhas);
        avaliacoes = avaliacoes.concat(medida.avaliacoes);
        indeterminados = indeterminados.concat(medida.indeterminados);
    }

    // Metrica nao declarada no contrato: os dois lados (SQL e contrato) tem de bater.
    for (var k = 0; k < (linhas || []).length; k++) {
        var id = texto(linhas[k] && linhas[k].metrica);
        if (id !== '' && !metricaDeclarada(contrato, id)) {
            indeterminados.push({ id: id, motivo: 'metrica_nao_declarada' });
        }
    }

    var lidos = avaliarDetalhes(contrato, detalhes);
    for (var d = 0; d < lidos.divergencias.length; d++) {
        indeterminados.push({ id: 'detalhes', motivo: lidos.divergencias[d] });
    }
    indeterminados = indeterminados.concat(conferenciasCruzadas(contrato, avaliacoes, lidos));

    var veredito = 'OK';
    for (var a = 0; a < avaliacoes.length; a++) {
        if (avaliacoes[a].avaliacao !== 'INFORMATIVA') veredito = piorVeredito(veredito, avaliacoes[a].avaliacao);
    }
    for (var u = 0; u < indeterminados.length; u++) veredito = piorVeredito(veredito, 'INDETERMINADO');

    var saida = contrato.veredito.saida[veredito];
    var pior = null;
    for (var p = 0; p < avaliacoes.length; p++) {
        if (avaliacoes[p].avaliacao === veredito) { pior = pior || avaliacoes[p]; }
    }
    var relatorio = formatarRelatorio(contrato, agora, avaliacoes, lidos, indeterminados, veredito, pior);

    return {
        veredito: veredito,
        versao_contrato: texto(contrato.versao),
        nucleo_versao: NUCLEO_VERSAO,
        metricas: avaliacoes,
        detalhes: lidos.por_tipo,
        indeterminados: indeterminados,
        relatorio: relatorio,
        saida: typeof saida === 'number' ? saida : null
    };
}

/* ------------------------------------------------------------------ relatorio */

function linhaDeVeredito(contrato, veredito, avaliacoes, indeterminados, pior) {
    var declaradas = (contrato && contrato.metricas) || [];
    var modelo = (contrato && contrato.veredito && contrato.veredito.linha) || 'VEREDITO: <veredito>';
    return modelo
        .replace('<veredito>', veredito)
        .replace('<n> metricas declaradas', declaradas.length + ' metricas declaradas')
        .replace('<n> medidas', avaliacoes.length + ' medidas')
        .replace('<n> indeterminadas', indeterminados.length + ' indeterminadas')
        .replace('<id>', pior ? pior.id : (indeterminados.length ? indeterminados[0].id : '-'));
}

function formatarRelatorio(contrato, agora, avaliacoes, detalhes, indeterminados, veredito, pior) {
    var linhas = [];
    var carimbo = agora || new Date().toISOString();
    linhas.push('OBSERVABILIDADE DE SYNC — contrato ' + texto(contrato.id) + ' v' + texto(contrato.versao) +
        ' · nucleo ' + NUCLEO_VERSAO + ' · ' + carimbo);
    linhas.push('METRICAS');
    for (var i = 0; i < avaliacoes.length; i++) {
        var av = avaliacoes[i];
        var limites = av.limites
            ? ' (alerta >= ' + av.limites.alerta + ', critico >= ' + av.limites.critico + ')'
            : ' (informativa)';
        linhas.push('  [' + av.avaliacao + '] ' + av.id + (av.observacao ? '[' + av.observacao + ']' : '') +
            ' = ' + av.valor + ' ' + av.unidade + limites);
    }
    linhas.push('DETALHES');
    var tipos = contrato.detalhes.tipos;
    var nomes = Object.keys(tipos);
    for (var t = 0; t < nomes.length; t++) {
        var itens = detalhes.por_tipo[nomes[t]] || [];
        linhas.push('  ' + nomes[t] + ' (' + itens.length + ')');
        for (var k = 0; k < itens.length; k++) {
            linhas.push('    ' + (itens[k].id || '(sem id)') +
                ' status=' + (itens[k].status || '-') +
                ' direcao=' + (itens[k].direcao || '-') +
                ' event_type=' + (itens[k].event_type || '-') +
                (itens[k].tentativas !== null ? ' tentativas=' + itens[k].tentativas : '') +
                ' motivo=' + (itens[k].com_motivo ? itens[k].motivo : '(SEM MOTIVO)'));
        }
    }
    var naoDeclarados = detalhes.por_tipo['tipo_nao_declarado'] || [];
    if (naoDeclarados.length) linhas.push('  tipo_nao_declarado (' + naoDeclarados.length + ')');
    if (indeterminados.length) {
        linhas.push('INDETERMINADO');
        for (var u = 0; u < indeterminados.length; u++) {
            linhas.push('  ' + indeterminados[u].id + ': ' + indeterminados[u].motivo);
        }
    }
    linhas.push(linhaDeVeredito(contrato, veredito, avaliacoes, indeterminados, pior));
    return linhas.join('\n');
}

/* O n8n injeta este arquivo dentro do Code node; o aceite roda o MESMO arquivo no
 * node puro. `module` so' existe no segundo caso. */
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        NUCLEO_VERSAO: NUCLEO_VERSAO,
        ehVazio: ehVazio,
        texto: texto,
        ehObjeto: ehObjeto,
        numero: numero,
        sanitizar: sanitizar,
        metricaDeclarada: metricaDeclarada,
        contratoValido: contratoValido,
        limitesDaMetrica: limitesDaMetrica,
        direcaoValida: direcaoValida,
        avaliarValor: avaliarValor,
        dimensaoDaMetrica: dimensaoDaMetrica,
        direcoesDeclaradas: direcoesDeclaradas,
        combinacoesDeclaradas: combinacoesDeclaradas,
        medirMetrica: medirMetrica,
        piorVeredito: piorVeredito,
        avaliarDetalhes: avaliarDetalhes,
        conferenciasCruzadas: conferenciasCruzadas,
        avaliar: avaliar,
        linhaDeVeredito: linhaDeVeredito,
        formatarRelatorio: formatarRelatorio
    };
}

/* ---------------------------------------------------------------------------
 * Adaptador de linha de comando (FORA do nucleo versionado) — usado pelo aceite
 * e por quem quiser rodar a medicao a mao:
 *   node n8n/codigo/observabilidade-sync.js --contrato <json> --metricas <json> --detalhes <json>
 * Le os tres arquivos, imprime o relatorio e sai com o codigo declarado no
 * contrato (veredito.saida). Erro de leitura = INDETERMINADO (exit 3).
 * ------------------------------------------------------------------------- */
if (typeof require === 'function' && typeof module !== 'undefined' && module.exports &&
    typeof process !== 'undefined' && process.argv && require.main === module) {
    var fs = require('fs');
    var argumentos = process.argv.slice(2);
    var caminhos = {};
    for (var arg = 0; arg < argumentos.length; arg++) {
        if (argumentos[arg].indexOf('--') === 0) {
            caminhos[argumentos[arg].slice(2)] = argumentos[arg + 1];
            arg++;
        }
    }
    var lerJson = function (caminho) {
        return JSON.parse(fs.readFileSync(caminho, 'utf8'));
    };
    var resultado;
    try {
        var contratoLido = lerJson(caminhos.contrato);
        var metricasLidas = lerJson(caminhos.metricas);
        var detalhesLidos = lerJson(caminhos.detalhes);
        resultado = avaliar(contratoLido, metricasLidas, detalhesLidos, new Date().toISOString());
    } catch (e) {
        process.stdout.write('OBSERVABILIDADE DE SYNC — INDETERMINADO: leitura da medicao falhou (' +
            sanitizar(e && e.message, 200) + ')\n');
        process.exit(3);
    }
    process.stdout.write(resultado.relatorio + '\n');
    process.exit(typeof resultado.saida === 'number' ? resultado.saida : 3);
}
