#!/usr/bin/env node
/* ============================================================================
 * Suite PURA do nucleo da observabilidade de sync — card TRE-W3-E05-T01.
 *
 * Roda o MESMO arquivo que o Code node do n8n embute
 * (n8n/codigo/observabilidade-sync.js) contra estados sinteticos, sem banco e
 * sem HTTP. Aqui se mede o que o aceite ponta a ponta nao mede barato:
 *   * o veredito e o codigo de saida nos quatro estados (OK/ATENCAO/CRITICO/
 *     INDETERMINADO) e a ORDEM entre eles (INDETERMINADO e' pior que CRITICO);
 *   * as regras de fail-closed UMA A UMA (metrica sem linha, valor nao
 *     numerico, metrica nao declarada, limiar ausente, limiar invertido,
 *     direcao desconhecida, dimensao fora do vocabulario, valor nulo com
 *     `ausencia` declarada);
 *   * a conferencia cruzada (metrica x lista de detalhes contam a MESMA historia);
 *   * o relatorio: secoes na ordem declarada, motivo sanitizado, dead-letter sem
 *     motivo VISIVEL como "(SEM MOTIVO)" e NENHUM payload no texto.
 *
 * Uso:
 *   node scripts/n8n/testar_observabilidade_sync.js [--contrato <json>] [--nucleo <js>]
 * Saida: um item por linha (OK/FALHOU) e o resumo
 *   RESULTADO: OBSERVABILIDADE_SYNC_NUCLEO_OK|FALHOU (N itens, M falhas)
 * exit 0 = suite cumprida; 1 = falhou; 2 = uso errado.
 * ==========================================================================*/
'use strict';

const fs = require('fs');
const path = require('path');

const args = process.argv.slice(2);
function argumento(nome) {
    const i = args.indexOf('--' + nome);
    return i >= 0 ? args[i + 1] : null;
}
const RAIZ = path.resolve(__dirname, '..', '..');
const CAMINHO_CONTRATO = argumento('contrato') || path.join(RAIZ, 'n8n', 'contracts', 'observabilidade-sync.v1.json');
const CAMINHO_NUCLEO = argumento('nucleo') || path.join(RAIZ, 'n8n', 'codigo', 'observabilidade-sync.js');
const CAMINHO_WORKFLOW = argumento('workflow'); // opcional: mede o artefato SOB TESTE (workflow), nao o file solto

let ITENS = 0;
let FALHAS = 0;
const ok = (t) => { ITENS++; console.log('OK    ' + t); };
const falhou = (t) => { ITENS++; FALHAS++; console.log('FALHOU ' + t); };
const verifica = (condicao, t) => (condicao ? ok(t) : falhou(t));
function resumo() {
    if (FALHAS === 0) {
        console.log(`RESULTADO: OBSERVABILIDADE_SYNC_NUCLEO_OK (${ITENS} itens, 0 falhas)`);
        process.exit(0);
    }
    console.log(`RESULTADO: OBSERVABILIDADE_SYNC_NUCLEO_FALHOU (${ITENS} itens, ${FALHAS} falha(s))`);
    process.exit(1);
}

/* ---------------------------------------------------------------- artefato sob teste
 * O MARCADOR e' a fronteira declarada pelo montador: antes dele esta' o nucleo
 * versionado, depois a cola do n8n. Com `--workflow`, a suite roda o MESMO texto que
 * o Code node embute (e o mesmo contrato) — e' o que faz um dente na copia do
 * workflow morder esta suite.
 * ---------------------------------------------------------------------------------- */
const MARCADOR = '/* --- adaptador do Code node (fora do nucleo versionado) --- */';

function extrairDoWorkflow(caminho) {
    const workflow = JSON.parse(fs.readFileSync(caminho, 'utf8'));
    const no = (workflow.nodes || []).find((n) => n.type === 'n8n-nodes-base.code');
    if (!no) throw new Error('workflow sem Code node');
    const jsCode = no.parameters.jsCode;
    const corte = jsCode.indexOf(MARCADOR);
    if (corte < 0) throw new Error('Code node sem o marcador do adaptador');
    // Antes do marcador: o nucleo versionado. Depois dele: o contrato embutido e o adaptador.
    const nucleoTexto = jsCode.slice(0, corte);
    const resto = jsCode.slice(corte + MARCADOR.length);
    const inicio = resto.indexOf('const CONTRATO = ');
    if (inicio < 0) throw new Error('Code node sem o contrato embutido');
    const abertura = resto.indexOf('{', inicio);
    let profundidade = 0;
    let fim = -1;
    for (let i = abertura; i < resto.length; i++) {
        if (resto[i] === '{') profundidade++;
        else if (resto[i] === '}') {
            profundidade--;
            if (profundidade === 0) { fim = i + 1; break; }
        }
    }
    if (fim < 0) throw new Error('contrato embutido sem fim (chaves desbalanceadas)');
    const destino = path.join(require('os').tmpdir(), 'nucleo-observabilidade-sob-teste-' + process.pid + '.js');
    fs.writeFileSync(destino, nucleoTexto, 'utf8');
    return { nucleo: destino, contrato: JSON.parse(resto.slice(abertura, fim)) };
}

let contrato;
let nucleo;
if (CAMINHO_WORKFLOW) {
    const extraido = extrairDoWorkflow(CAMINHO_WORKFLOW);
    contrato = extraido.contrato;
    nucleo = require(extraido.nucleo);
} else {
    if (!fs.existsSync(CAMINHO_CONTRATO) || !fs.existsSync(CAMINHO_NUCLEO)) {
        console.error('ausente: contrato ou nucleo');
        process.exit(2);
    }
    contrato = JSON.parse(fs.readFileSync(CAMINHO_CONTRATO, 'utf8'));
    nucleo = require(CAMINHO_NUCLEO);
}
const AGORA = '2026-10-02T12:00:00.000Z';

/* ---------------------------------------------------------------- montagem de estados
 * `linhas(overrides)` monta a medicao COMPLETA declarada no contrato (todos os
 * agregados com zero e todas as combinacoes da metrica dimensional com zero) e
 * aplica os overrides por id de metrica. Assim cada caso muda UM ponto e a
 * suite mede o efeito daquele ponto — nao de um estado incompleto.
 * ---------------------------------------------------------------------------------- */
function linhas(overrides = {}) {
    const NULO = '__NULO__';
    const saida = [];
    for (const m of contrato.metricas) {
        if (m.dimensional) {
            const combos = nucleo.combinacoesDeclaradas(contrato, m);
            for (const c of combos) {
                const chave = m.id + '[' + c.rotulo + ']';
                const valor = Object.prototype.hasOwnProperty.call(overrides, chave)
                    ? overrides[chave] : 0;
                if (valor === null) continue; // combinacao OMITIDA (nao medida)
                saida.push({
                    metrica: m.id, valor: valor === NULO ? null : valor,
                    dimensao: { source_system: c.source_system, target_system: c.target_system, status: c.status }
                });
            }
            continue;
        }
        const valor = Object.prototype.hasOwnProperty.call(overrides, m.id) ? overrides[m.id] : 0;
        if (valor === null) continue; // metrica OMITIDA (linha ausente)
        saida.push({ metrica: m.id, valor: valor === NULO ? null : valor, dimensao: {} });
    }
    if (overrides.__extras) saida.push(...overrides.__extras);
    return saida;
}
const detalhes = (porTipo = {}) => {
    const saida = [];
    for (const tipo of Object.keys(porTipo)) {
        for (const item of porTipo[tipo]) saida.push(Object.assign({ tipo }, item));
    }
    return saida;
};
const copia = (obj) => JSON.parse(JSON.stringify(obj));
const metricaDe = (c, id) => c.metricas.find((m) => m.id === id);
const avaliacoesDe = (r, id) => r.metricas.filter((a) => a.id === id);

/* ---------------------------------------------------------------- contrato declarado */
console.log('--- declaracao do contrato (nada implicito) ---');
verifica(nucleo.contratoValido(contrato), 'o contrato em disco e valido para o nucleo');
verifica(contrato.metricas.length >= 10, `contrato declara ${contrato.metricas.length} metricas (>= 10)`);
{
    const semDeclaracao = contrato.metricas.filter((m) => !m.direcao || !m.ausencia || !m.unidade || !m.id || !m.titulo);
    verifica(semDeclaracao.length === 0,
        `toda metrica declara id/titulo/unidade/direcao/ausencia (faltando: ${semDeclaracao.map((m) => m.id).join(',') || 'nenhuma'})`);
}
{
    const informativasSemNull = contrato.metricas.filter((m) => m.informativa === true && m.limites !== null);
    verifica(informativasSemNull.length === 0, 'metrica informativa declara limites null (nao decide veredito)');
}
{
    const decidem = contrato.metricas.filter((m) => m.informativa !== true);
    const semLimites = decidem.filter((m) => !m.limites || typeof m.limites.alerta !== 'number' || typeof m.limites.critico !== 'number');
    verifica(semLimites.length === 0, `toda metrica que decide veredito declara alerta e critico (faltando: ${semLimites.map((m) => m.id).join(',') || 'nenhuma'})`);
}
{
    const invertidos = contrato.metricas.filter((m) => m.limites && m.limites.critico < m.limites.alerta);
    verifica(invertidos.length === 0, 'nenhum limiar invertido (critico >= alerta)');
}
{
    const dimensional = contrato.metricas.filter((m) => m.dimensional);
    verifica(dimensional.length >= 1, `ha metrica dimensional declarada (${dimensional.map((m) => m.id).join(',')})`);
    const todosComAusenciaZero = dimensional.every((m) => m.ausencia === 'zero' && m.dimensional.combinacao_ausente === 0);
    verifica(todosComAusenciaZero, 'metrica dimensional declara ausencia zero e combinacao_ausente 0 (ausencia e informacao)');
}
const REGRAS = contrato.regras_de_fail_closed;
verifica(REGRAS && REGRAS.ausencia && Array.isArray(REGRAS.ausencia.valores) && REGRAS.ausencia.valores.indexOf('erro') >= 0,
    'o contrato declara a regra de ausencia com o padrao fail-closed (erro) entre os valores');
verifica(nucleo.direcoesDeclaradas(contrato).length === 2,
    `vocabulario da trilha declara ${nucleo.direcoesDeclaradas(contrato).length} direcoes (as DUAS portas)`);

/* ---------------------------------------------------------------- veredito e saida */
console.log('--- veredito e codigo de saida nos quatro estados ---');
{
    const r = nucleo.avaliar(contrato, linhas(), [], AGORA);
    verifica(r.veredito === 'OK' && r.saida === 0, `medicao zerada fecha OK/exit 0 (${r.veredito}/${r.saida})`);
    verifica(r.indeterminados.length === 0, 'medicao zerada nao tem indeterminado');
    verifica(r.metricas.length === contrato.metricas.length + nucleo.combinacoesDeclaradas(contrato, metricaDe(contrato, 'trilha_por_status')).length - 1,
        `a rodada mede TODAS as metricas declaradas (${r.metricas.length} avaliacoes, escalares + combinacoes)`);
}
{
    const r = nucleo.avaliar(contrato, linhas({ dead_letter_total: 1 }), [], AGORA);
    verifica(r.veredito === 'ATENCAO' && r.saida === 1, `dead_letter_total = alerta(1) fecha ATENCAO/exit 1 (${r.veredito}/${r.saida})`);
}
{
    const r = nucleo.avaliar(contrato, linhas({ dead_letter_total: 10 }), [], AGORA);
    verifica(r.veredito === 'CRITICO' && r.saida === 2, `dead_letter_total = critico(10) fecha CRITICO/exit 2 (${r.veredito}/${r.saida})`);
}
{
    const r = nucleo.avaliar(contrato, linhas({ fila_retry: 5, dead_letter_total: 1 }), [], AGORA);
    verifica(r.veredito === 'ATENCAO', 'o veredito e o PIOR entre as metricas (retry no alerta + dead-letter no alerta = ATENCAO)');
}
{
    const r = nucleo.avaliar(contrato, linhas({ fila_pendentes: 500, dead_letter_total: 0 }), [], AGORA);
    verifica(r.veredito === 'CRITICO', 'fila_pendentes acima do critico fecha CRITICO mesmo com o resto zerado');
}
{
    const r = nucleo.avaliar(contrato, linhas({ fila_no_teto: 1 }), [], AGORA);
    verifica(r.veredito === 'CRITICO', 'evento na fila sem tentativa restante fecha CRITICO (limiar declarado 0/1)');
}
{
    const r = nucleo.avaliar(contrato, linhas({ outbox_processado_total: 40, 'trilha_por_status[postgres->odoo/COMPLETED]': 40 }), [], AGORA);
    verifica(r.veredito === 'OK', 'metrica informativa nao decide veredito (processados 40 = OK)');
    verifica(avaliacoesDe(r, 'outbox_processado_total')[0].avaliacao === 'INFORMATIVA', 'metrica informativa e relatada como INFORMATIVA');
}
verifica(nucleo.piorVeredito('OK', 'CRITICO') === 'CRITICO' && nucleo.piorVeredito('CRITICO', 'INDETERMINADO') === 'INDETERMINADO',
    'a ordem do veredito e declarada e INDETERMINADO e pior que CRITICO');

/* ---------------------------------------------------------------- fail-closed, um por um */
console.log('--- fail-closed: um por um ---');
{
    const r = nucleo.avaliar(contrato, linhas({ fila_pendentes: null }), [], AGORA);
    verifica(r.veredito === 'INDETERMINADO' && r.saida === 3, `metrica declarada sem linha fecha INDETERMINADO/exit 3 (${r.veredito}/${r.saida})`);
    verifica(r.indeterminados.some((i) => i.motivo === 'metrica_declarada_sem_linha'), 'o motivo do indeterminado e nomeado (metrica_declarada_sem_linha)');
}
{
    const r = nucleo.avaliar(contrato, linhas({ fila_pendentes: 'nao-e-numero' }), [], AGORA);
    verifica(r.veredito === 'INDETERMINADO' && r.indeterminados.some((i) => i.motivo.startsWith('valor_nao_numerico')),
        'valor nao numerico fecha INDETERMINADO (o dado ruim nao vira zero)');
}
{
    const r = nucleo.avaliar(contrato, linhas({ __extras: [{ metrica: 'metrica_que_ninguem_declarou', valor: 0, dimensao: {} }] }), [], AGORA);
    verifica(r.veredito === 'INDETERMINADO' && r.indeterminados.some((i) => i.motivo === 'metrica_nao_declarada'),
        'metrica devolvida pelo SQL e nao declarada no contrato fecha INDETERMINADO (os dois lados tem de bater)');
}
{
    const r = nucleo.avaliar(contrato, linhas({ fila_pendentes: 0, fila_retry: 0 }), [], AGORA);
    verifica(r.veredito === 'OK', 'estado OK serve de controle do fail-closed (nao ha indeterminado escondido)');
}
{
    const c = copia(contrato);
    metricaDe(c, 'fila_pendentes').limites = null;         // deixa de declarar limiar
    const r = nucleo.avaliar(c, linhas({ fila_pendentes: 999 }), [], AGORA);
    verifica(r.veredito === 'INDETERMINADO' && r.indeterminados.some((i) => i.motivo === 'limiar_ausente'),
        'metrica que decide veredito sem limiar declarado fecha INDETERMINADO (nao existe limiar implicito)');
}
{
    const c = copia(contrato);
    metricaDe(c, 'fila_pendentes').limites = { alerta: 200, critico: 50 };
    const r = nucleo.avaliar(c, linhas(), [], AGORA);
    verifica(r.veredito === 'INDETERMINADO' && r.indeterminados.some((i) => i.motivo === 'limiar_invertido'),
        'limiar invertido (critico < alerta) fecha INDETERMINADO');
}
{
    const c = copia(contrato);
    metricaDe(c, 'fila_pendentes').direcao = 'menor_pior';
    const r = nucleo.avaliar(c, linhas(), [], AGORA);
    verifica(r.veredito === 'INDETERMINADO' && r.indeterminados.some((i) => i.motivo.startsWith('direcao_desconhecida')),
        'direcao fora da declarada fecha INDETERMINADO (sentido do numero nao se infere)');
}
{
    const r = nucleo.avaliar(contrato, linhas({
        'trilha_por_status[postgres->odoo/COMPLETED]': null,
        __extras: [{ metrica: 'trilha_por_status', valor: 1, dimensao: { source_system: 'odoo', target_system: 'odoo', status: 'COMPLETED' } }]
    }), [], AGORA);
    verifica(r.veredito === 'INDETERMINADO' && r.indeterminados.some((i) => i.motivo.startsWith('dimensao_fora_do_vocabulario')),
        'linha de trilha com PAR de direcao fora das portas declaradas fecha INDETERMINADO na metrica dimensional');
}
{
    const r = nucleo.avaliar(contrato, linhas({ 'trilha_por_status[odoo->postgres/COMPLETED]': null, 'trilha_por_status[postgres->odoo/COMPLETED]': 3 }), [], AGORA);
    const combo = avaliacoesDe(r, 'trilha_por_status').find((a) => a.observacao === 'odoo->postgres/COMPLETED');
    verifica(combo && combo.valor === 0, 'combinacao ausente da metrica dimensional vale 0 (declarado: ausencia e informacao)');
    verifica(r.veredito === 'OK', 'combinacao ausente nao vira indeterminado nem critico');
}
{
    const c = copia(contrato);
    metricaDe(c, 'fila_idade_maxima_s').ausencia = 'erro';
    const r = nucleo.avaliar(c, linhas({ fila_idade_maxima_s: '__NULO__' }), [], AGORA);
    verifica(r.veredito === 'INDETERMINADO' && r.indeterminados.some((i) => i.motivo === 'valor_nulo'),
        'valor nulo com ausencia declarada como erro fecha INDETERMINADO');
    verifica(r.indeterminados.some((i) => i.motivo === 'valor_nulo') && !r.indeterminados.some((i) => i.motivo === 'metrica_declarada_sem_linha'),
        'valor nulo e LINHA AUSENTE sao casos distintos (e o nucleo diz qual foi)');
    const r2 = nucleo.avaliar(contrato, linhas({ fila_idade_maxima_s: '__NULO__' }), [], AGORA);
    verifica(r2.veredito === 'OK' && avaliacoesDe(r2, 'fila_idade_maxima_s')[0].valor === 0,
        'valor nulo com ausencia declarada como zero vale 0 (fila vazia e informacao)');
}
{
    const r = nucleo.avaliar(null, [], [], AGORA);
    verifica(r.veredito === 'INDETERMINADO' && r.relatorio.indexOf('contrato invalido') >= 0,
        'contrato invalido fecha INDETERMINADO com motivo (fail-closed na entrada)');
}

/* ---------------------------------------------------------------- conferencia cruzada e detalhes */
console.log('--- detalhes, motivo e conferencia cruzada ---');
const MORTO = {
    id: 'aaaaaaaa-1111-4111-8111-111111111111', event_type: 'COMPANY_QUALIFIED', direcao: 'postgres->odoo',
    status: 'DEAD_LETTER', tentativas: 0, quando: '2026-10-02T11:00:00.000Z', motivo: 'api_controlada: HTTP 422 campo_fixo_divergente'
};
const MORTO_SEM_MOTIVO = {
    id: 'bbbbbbbb-2222-4222-8222-222222222222', event_type: 'COMPANY_UPDATED', direcao: 'postgres->odoo',
    status: 'DEAD_LETTER', tentativas: 1, quando: '2026-10-02T11:05:00.000Z', motivo: null
};
{
    const r = nucleo.avaliar(contrato, linhas({ dead_letter_total: 1, dead_letter_sem_motivo: 0 }), detalhes({ outbox_dead_letter: [MORTO] }), AGORA);
    verifica(r.veredito === 'ATENCAO', 'dead-letter com motivo entra no relatorio e nao e divergencia');
    verifica(r.relatorio.indexOf(MORTO.motivo) >= 0, 'o MOTIVO aparece no relatorio (falha visivel com motivo)');
    verifica(r.relatorio.indexOf('DEAD_LETTER') >= 0, 'a linha do detalhe mostra o estado do evento');
}
{
    const r = nucleo.avaliar(contrato, linhas({ dead_letter_total: 2, dead_letter_sem_motivo: 1 }), detalhes({ outbox_dead_letter: [MORTO, MORTO_SEM_MOTIVO] }), AGORA);
    verifica(r.veredito === 'CRITICO' && r.saida === 2, `dead-letter SEM motivo fecha CRITICO (limiar 0/1) — ${r.veredito}`);
    verifica(r.relatorio.indexOf('(SEM MOTIVO)') >= 0, 'dead-letter sem motivo e VISIVEL como (SEM MOTIVO) no relatorio');
}
{
    // conferencia cruzada: metrica diz 0 mas a lista tem um item sem motivo
    const r = nucleo.avaliar(contrato, linhas({ dead_letter_total: 1, dead_letter_sem_motivo: 0 }), detalhes({ outbox_dead_letter: [MORTO, MORTO_SEM_MOTIVO] }), AGORA);
    verifica(r.veredito === 'INDETERMINADO' && r.indeterminados.some((i) => i.motivo.startsWith('conferencia_cruzada_divergente')),
        'metrica e lista de detalhes divergentes fecham INDETERMINADO (nao se escolhe a mais bonita)');
}
{
    const r = nucleo.avaliar(contrato, linhas({ trilha_falhas: 1, trilha_recusas: 1 }),
        detalhes({
            trilha_falha: [{ id: 'c1', event_type: 'empresa_upsert', direcao: 'postgres->odoo', status: 'FAILED', motivo: 'transporte: sem resposta' }],
            trilha_recusa: [{ id: 'c2', event_type: 'STAGE_CHANGED', direcao: 'odoo->postgres', status: 'REFUSED', motivo: 'campo_exigido_ausente:lead_id' }]
        }), AGORA);
    verifica(r.veredito === 'ATENCAO', 'falhas e recusas da trilha sao relatadas com a direcao (serve o E02 e o E03)');
    verifica(r.relatorio.indexOf('odoo->postgres') >= 0 && r.relatorio.indexOf('campo_exigido_ausente:lead_id') >= 0,
        'o detalhe da trilha mostra a direcao e o motivo nomeado');
}
{
    // `alwaysOutputData`: rodada SAUDAVEL entrega UM item VAZIO como placeholder da consulta
    // sem linhas. Ausencia de detalhe NAO e' detalhe com tipo desconhecido — sem isso toda
    // rodada saudavel fecharia INDETERMINADO e o relatorio gritaria quando esta' tudo bem.
    const r = nucleo.avaliar(contrato, linhas(), [{}], AGORA);
    verifica(r.veredito === 'OK' && r.indeterminados.length === 0,
        'linha VAZIA do alwaysOutputData nao vira indeterminado (rodada saudavel tem zero detalhe)');
    const r2 = nucleo.avaliar(contrato, linhas(), [{ tipo: '', id: 'x', motivo: null }], AGORA);
    verifica(r2.veredito === 'INDETERMINADO' && r2.indeterminados.some((i) => i.motivo.startsWith('tipo_de_detalhe_nao_declarado')),
        'linha de detalhe COM dado e tipo vazio continua fechando INDETERMINADO (placeholder e linha quebrada sao coisas diferentes)');
}
{
    const r = nucleo.avaliar(contrato, linhas(), detalhes({ tipo_que_ninguem_declarou: [{ id: 'x', motivo: 'y' }] }), AGORA);
    verifica(r.veredito === 'INDETERMINADO' && r.indeterminados.some((i) => i.motivo.startsWith('tipo_de_detalhe_nao_declarado')),
        'tipo de detalhe nao declarado no contrato fecha INDETERMINADO (nada e ignorado em silencio)');
}

/* ---------------------------------------------------------------- relatorio */
console.log('--- relatorio: secoes, sanitizacao e ausencia de payload ---');
{
    const motivoLongo = 'linha 1\nlinha 2\tcom\tcontrole ' + 'x'.repeat(400);
    const r = nucleo.avaliar(contrato, linhas(), detalhes({ trilha_falha: [{ id: 'z', status: 'FAILED', motivo: motivoLongo }] }), AGORA);
    const linhaDoMotivo = r.relatorio.split('\n').find((l) => l.indexOf('motivo=') >= 0);
    verifica(linhaDoMotivo && linhaDoMotivo.indexOf('\n') < 0 && linhaDoMotivo.indexOf('\t') < 0,
        'motivo sanitizado: o relatorio continua tendo UMA linha por item');
    verifica(linhaDoMotivo && linhaDoMotivo.length < 300 && linhaDoMotivo.indexOf('...') >= 0,
        'motivo acima do teto declarado e truncado COM MARCA (nunca cortado em silencio)');
    verifica(r.relatorio.indexOf('x'.repeat(400)) < 0, 'o texto integral do motivo nao vaza para o relatorio');
}
{
    const SEGREDO = 'Bearer abcdefghijklmnopqrstuvwxyz0123456789';
    const r = nucleo.avaliar(contrato,
        linhas({ __extras: [{ metrica: 'fila_pendentes', valor: 0, dimensao: {}, payload: { event_version: '1.0', payload: { secret_marker: SEGREDO } } }] }),
        detalhes({ outbox_dead_letter: [Object.assign({}, MORTO, { payload: { secret_marker: SEGREDO } })] }), AGORA);
    // a linha extra duplica a metrica escalar -> indeterminado; o que se mede aqui e' o TEXTO
    verifica(r.relatorio.indexOf(SEGREDO) < 0 && r.relatorio.indexOf('secret_marker') < 0,
        'nem payload nem marcador de segredo aparecem no relatorio (o relatorio e texto de medicao, nao despejo de dado)');
    verifica(r.veredito === 'INDETERMINADO', 'linha escalar duplicada fecha INDETERMINADO (metrica_escalar_com_multiplas_linhas)');
}
{
    const r = nucleo.avaliar(contrato, linhas({ dead_letter_total: 10 }), [], AGORA);
    const textoRel = r.relatorio;
    const posMetricas = textoRel.indexOf('METRICAS');
    const posDetalhes = textoRel.indexOf('DETALHES');
    const posVeredito = textoRel.lastIndexOf('VEREDITO:');
    verifica(posMetricas >= 0 && posDetalhes > posMetricas && posVeredito > posDetalhes,
        'as secoes do relatorio saem na ordem declarada (METRICAS > DETALHES > VEREDITO)');
    verifica(textoRel.startsWith('OBSERVABILIDADE DE SYNC — contrato ' + contrato.id + ' v' + contrato.versao),
        'o cabecalho do relatorio identifica contrato e versao');
    verifica(textoRel.indexOf('VEREDITO: CRITICO') === posVeredito && r.veredito === 'CRITICO',
        `a linha do veredito e a declarada no contrato (${textoRel.split('\n').pop().slice(0, 60)}...)`);
    verifica(/VEREDITO: \w+ — \d+ metricas declaradas, \d+ medidas, \d+ indeterminadas; pior metrica: \S+/.test(textoRel.split('\n').pop()),
        'a linha do veredito carrega contagens e a pior metrica (relatorio fiscalizavel)');
}
{
    const r = nucleo.avaliar(contrato, linhas({ fila_pendentes: null, dead_letter_total: 10 }), [], AGORA);
    verifica(r.veredito === 'INDETERMINADO', 'INDETERMINADO ganha de CRITICO no mesmo estado, e o relatorio diz os dois');
    verifica(r.relatorio.indexOf('INDETERMINADO') >= 0 && r.relatorio.indexOf('CRITICO') >= 0 && r.relatorio.indexOf('metrica_declarada_sem_linha') >= 0,
        'o relatorio nomeia o motivo do indeterminado junto das metricas criticas');
}

resumo();
