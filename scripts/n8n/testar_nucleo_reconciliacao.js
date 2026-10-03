#!/usr/bin/env node
/* ============================================================================
 * Suite PURA do nucleo da RECONCILIACAO — card TRE-W3-E04-T01.
 *
 * Roda o MESMO arquivo que o Code node do n8n embute
 * (n8n/codigo/nucleo-reconciliacao.js) contra estados sinteticos, sem banco e
 * sem HTTP. Aqui se mede o que o aceite ponta a ponta nao mede barato:
 *   * o caminho SAUDAVEL fecha OK com ZERO divergencia (a comparacao nao alarma
 *     sozinha);
 *   * cada comparacao declarada (E1, E2, E4, I1..I4, P1..P4) MORDE: o estado com
 *     aquele defeito produz AQUELA divergencia, nomeada, e nao outra;
 *   * as regras de fail-closed UMA A UMA (contrato invalido, origem sem cobertura,
 *     leitura do destino nao medida em todas as formas, status de trilha fora do
 *     vocabulario, pendente sem idade) — INDETERMINADO, nunca OK;
 *   * a ORDEM do veredito (INDETERMINADO e' pior que DIVERGENTE; DIVERGENTE e'
 *     pior que OK);
 *   * a observacao informativa O1 NAO decide veredito (base sem entidade esperada
 *     e' OK com observacao, nao divergencia);
 *   * o relatorio: motivo truncado COM MARCA, nenhum payload, ordem das secoes;
 *   * o filtro do lote vazio (`in []`) NAO vira leitura da base inteira;
 *   * E4 so' reporta quando os DOIS lados tem valor (ausencia de um lado nao e'
 *     divergencia — alarme falso nao entra).
 *
 * Uso:
 *   node scripts/n8n/testar_nucleo_reconciliacao.js [--contrato <json>] [--nucleo <js>]
 *   node scripts/n8n/testar_nucleo_reconciliacao.js --workflow <json>   (mede o artefato sob teste)
 * Saida: um item por linha (OK/FALHOU) e o resumo
 *   RESULTADO: RECONCILIACAO_NUCLEO_OK|FALHOU (N itens, M falhas)
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
const CAMINHO_CONTRATO = argumento('contrato') || path.join(RAIZ, 'n8n', 'contracts', 'reconciliation-job.v1.json');
const CAMINHO_NUCLEO = argumento('nucleo') || path.join(RAIZ, 'n8n', 'codigo', 'nucleo-reconciliacao.js');
const CAMINHO_WORKFLOW = argumento('workflow');

let ITENS = 0;
let FALHAS = 0;
const ok = (t) => { ITENS++; console.log('OK    ' + t); };
const falhou = (t) => { ITENS++; FALHAS++; console.log('FALHOU ' + t); };
const verifica = (condicao, t) => (condicao ? ok(t) : falhou(t));
function resumo() {
    if (FALHAS === 0) {
        console.log(`RESULTADO: RECONCILIACAO_NUCLEO_OK (${ITENS} itens, 0 falhas)`);
        process.exit(0);
    }
    console.log(`RESULTADO: RECONCILIACAO_NUCLEO_FALHOU (${ITENS} itens, ${FALHAS} falha(s))`);
    process.exit(1);
}

/* ---------------------------------------------------------------- artefato sob teste
 * O MARCADOR e' a fronteira declarada pelo montador: antes dele esta' o nucleo
 * versionado, depois a cola do n8n. Com `--workflow`, a suite roda o MESMO texto
 * que o Code node embute (e o mesmo contrato) — e' o que faz um dente na copia do
 * workflow morder esta suite.
 * ---------------------------------------------------------------------------------- */
const MARCADOR = '/* --- adaptador do Code node (fora do nucleo versionado) --- */';

function extrairDoWorkflow(caminho) {
    const workflow = JSON.parse(fs.readFileSync(caminho, 'utf8'));
    const no = (workflow.nodes || []).find((n) => n.type === 'n8n-nodes-base.code' && n.name === 'Avaliar');
    if (!no) throw new Error('workflow sem o Code node "Avaliar"');
    const jsCode = no.parameters.jsCode;
    const corte = jsCode.indexOf(MARCADOR);
    if (corte < 0) throw new Error('Code node sem o marcador do adaptador');
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
    const destino = path.join(require('os').tmpdir(), 'nucleo-reconciliacao-sob-teste-' + process.pid + '.js');
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

/* ---------------------------------------------------------------- mundo sintetico */

const ORG_A = '11111111-1111-4111-8111-111111111111';
const ORG_B = '22222222-2222-4222-8222-222222222222';
const ORG_C = '33333333-3333-4333-8333-333333333333';
const PARCEIRO_A = 71;
const PARCEIRO_B = 72;

/** Linha de entidade da consulta de origem (o que n8n/sql/reconciliacao-origem.sql devolve). */
function entidade(overrides) {
    return Object.assign({
        tipo: 'entidade',
        entidade_id: ORG_A,
        nome: 'Alfa Consultoria Ltda',
        odoo_partner_id: PARCEIRO_A,
        cnpj: '11.222.333/0001-81',
        domain: 'alfa.example',
        linkedin_url: null,
        status: 'QUALIFIED',
        esperada: true,
        operacao_do_espelho: 'COMPANY_QUALIFIED',
        espelho_entregue: true
    }, overrides || {});
}

/** Linha de cobertura da consulta de origem. */
function cobertura(total, limite) {
    return { tipo: 'cobertura', total_de_organizacoes: total === undefined ? 1 : total, limite: limite === undefined ? 200 : limite };
}

/** Linha de evento na fila (o que n8n/sql/reconciliacao-pendentes.sql devolve). */
function pendente(overrides) {
    return Object.assign({
        tipo: 'pendente',
        evento_id: 'e1111111-1111-4111-8111-111111111111',
        chave: 'outbox:e1111111-1111-4111-8111-111111111111:COMPANY_QUALIFIED',
        event_type: 'COMPANY_QUALIFIED',
        aggregate_id: ORG_A,
        status: 'PENDING',
        attempts: 0,
        idade_s: 5,
        motivo: null,
        status_da_trilha: null,
        tem_trilha: false
    }, overrides || {});
}

function coberturaDaFila(pending, retry) {
    return { tipo: 'cobertura', total_pending: pending === undefined ? 0 : pending, total_retry: retry === undefined ? 0 : retry };
}

/** Resposta cruda de UMA leitura da porta unica, do jeito que o no' HTTP entrega. */
function resposta(registros, status, corpoForcado) {
    const corpo = corpoForcado !== undefined ? corpoForcado
        : { ok: true, dados: { registros: registros || [], total: (registros || []).length } };
    return [{ json: { statusCode: status === undefined ? 200 : status, body: corpo } }];
}

function avaliar(origem, fila, leituras) {
    return nucleo.avaliar(contrato, origem, fila, leituras, AGORA);
}

/** Estado SAUDAVEL: uma organizacao esperada, espelho existente, ida-e-volta certa, fila vazia. */
function mundoSaudavel(overridesOrigem, overridesLeituras) {
    const origem = overridesOrigem || [entidade(), cobertura(1)];
    const leituras = Object.assign({
        por_id: resposta([{
            id: PARCEIRO_A, name: 'Alfa Consultoria Ltda', active: true, tf_company_id: ORG_A,
            tf_cnpj: '11.222.333/0001-81', tf_domain: 'alfa.example', tf_linkedin_url: null
        }]),
        por_company_id: resposta([{
            id: PARCEIRO_A, name: 'Alfa Consultoria Ltda', active: true, tf_company_id: ORG_A
        }])
    }, overridesLeituras || {});
    return avaliar(origem, [coberturaDaFila(0, 0)], leituras);
}

function tipos(resultado) {
    return (resultado.divergencias || []).map((d) => d.tipo).sort().join(',');
}
function regras(resultado) {
    return (resultado.indeterminacoes || []).map((i) => i.regra).sort().join(',');
}

/* ---------------------------------------------------------------- 1. caminho saudavel */
console.log('--- caminho saudavel e as observacoes ---');
{
    // MEDIDO no aceite (estado G): com a porta unica FORA DO AR as duas leituras voltam NAO MEDIDAS e
    // a rodada reportava E1 para o espelho esperado — divergencia inventada a partir de ausencia de
    // medicao. O item separa os dois mundos: nao medido NAO gera E1.
    const r = mundoSaudavel(undefined, {
        por_id: [{ json: { error: 'connect ECONNREFUSED' } }],
        por_company_id: [{ json: { error: 'connect ECONNREFUSED' } }]
    });
    verifica(r.veredito === 'INDETERMINADO' && r.divergencias.length === 0,
        'porta unica fora do ar: NENHUMA divergencia de entidade (ausencia de medicao nao vira E1) -> '
        + r.veredito + ' / ' + tipos(r));
    verifica(regras(r).includes('leitura_do_destino_nao_medida')
        && regras(r).includes('comparacoes_de_entidade_puladas'),
        'porta unica fora do ar: a leitura nao medida E o pulo das comparacoes ficam nomeados -> ' + regras(r));
}
{
    const r = mundoSaudavel();
    verifica(r.veredito === 'OK', 'mundo saudavel fecha OK (medido: ' + r.veredito + ')');
    verifica(r.divergencias.length === 0, 'mundo saudavel sem divergencia (medidas: ' + r.divergencias.length + ')');
    verifica(r.indeterminacoes.length === 0, 'mundo saudavel sem indeterminacao (medidas: ' + r.indeterminacoes.length + ')');
    verifica(r.cobertura.entidades_lidas === 1 && r.cobertura.entidades_esperadas === 1
        && r.cobertura.janela_completa === true, 'cobertura medida: lidas=1 esperadas=1 janela_completa=true');
    verifica(r.cobertura.leituras.por_id.medido === true && r.cobertura.leituras.por_id.registros === 1,
        'a leitura por id aparece na cobertura como medida com 1 registro');
}
{
    const r = avaliar([entidade({ esperada: false, odoo_partner_id: null, espelho_entregue: false, operacao_do_espelho: null }), cobertura(1)],
        [coberturaDaFila(0, 0)],
        { por_id: resposta([]), por_company_id: resposta([]) });
    verifica(r.veredito === 'OK', 'lote sem entidade esperada fecha OK (medido: ' + r.veredito + ')');
    verifica(r.observacoes.length === 1 && r.observacoes[0].id === 'O1',
        'a observacao informativa O1 e relatada (medidas: ' + r.observacoes.length + ')');
    verifica(r.observacoes[0].informativa === true && r.divergencias.length === 0,
        'O1 NAO decide veredito: observacao informativa, zero divergencia');
}
{
    const r = avaliar([cobertura(0)], [coberturaDaFila(0, 0)], { por_id: resposta([]), por_company_id: resposta([]) });
    verifica(r.veredito === 'OK' && r.cobertura.entidades_lidas === 0 && r.cobertura.janela_completa === true,
        'BASE VAZIA e informacao (OK com 0 entidade lida), nao indeterminacao');
}

/* ---------------------------------------------------------------- 2. cada comparacao morde */
console.log('--- cada comparacao declarada morde ---');
{
    const r = avaliar([entidade(), cobertura(1)], [coberturaDaFila(0, 0)],
        { por_id: resposta([]), por_company_id: resposta([]) });
    verifica(r.veredito === 'DIVERGENTE' && tipos(r) === 'entidade_esperada_ausente_no_destino',
        'E1: entidade esperada ausente nas duas leituras -> ' + tipos(r));
}
{
    const r = mundoSaudavel(undefined, {
        por_id: resposta([{ id: PARCEIRO_A, name: 'Alfa', active: false, tf_company_id: ORG_A }])
    });
    verifica(r.veredito === 'DIVERGENTE' && tipos(r).indexOf('entidade_arquivada_no_destino') >= 0,
        'E2: espelho arquivado (active=false) -> ' + tipos(r));
}
{
    const r = mundoSaudavel(undefined, {
        por_id: resposta([{ id: PARCEIRO_A, name: 'Alfa', active: true, tf_company_id: ORG_A, tf_cnpj: '99.999.999/0001-99' }])
    });
    verifica(r.veredito === 'DIVERGENTE' && tipos(r) === 'identidade_forte_divergente',
        'E4: identificador forte divergente -> ' + tipos(r));
}
{
    const r = avaliar([entidade(), entidade({ entidade_id: ORG_B, odoo_partner_id: PARCEIRO_B, nome: 'Beta' }), cobertura(2)],
        [pendente({ idade_s: 5, status: 'PROCESSED' }), coberturaDaFila(0, 0)],
        {
            por_id: resposta([
                { id: PARCEIRO_A, name: 'Alfa', active: true, tf_company_id: ORG_B, tf_cnpj: '11.222.333/0001-81' },
                { id: PARCEIRO_B, name: 'Beta', active: true, tf_company_id: ORG_B }
            ]),
            por_company_id: resposta([
                { id: PARCEIRO_A, name: 'Alfa', active: true, tf_company_id: ORG_B },
                { id: PARCEIRO_B, name: 'Beta', active: true, tf_company_id: ORG_B }
            ])
        });
    verifica(r.veredito === 'DIVERGENTE' && tipos(r).indexOf('id_cruzado_divergente') >= 0,
        'I1: espelho aponta para OUTRA organizacao DO LOTE -> ' + tipos(r));
}
{
    const r = mundoSaudavel(undefined, {
        por_id: resposta([{ id: PARCEIRO_A, name: 'Alfa', active: true, tf_company_id: null }]),
        por_company_id: resposta([])
    });
    verifica(r.veredito === 'DIVERGENTE' && tipos(r).indexOf('id_cruzado_divergente') >= 0,
        'I1: espelho sem ID canonico (tf_company_id vazio) -> ' + tipos(r));
}
{
    const r = mundoSaudavel(undefined, {
        por_id: resposta([{ id: '9191919191', name: 'Alfa (outro)', active: true, tf_company_id: ORG_A }]),
        por_company_id: resposta([{ id: '9191919191', name: 'Alfa (outro)', active: true, tf_company_id: ORG_A }])
    });
    verifica(r.veredito === 'DIVERGENTE' && tipos(r).indexOf('id_cruzado_sem_volta_no_pg') >= 0,
        'I2: parceiro se declara o espelho e a ponta do PG nao volta -> ' + tipos(r));
}
{
    const r = mundoSaudavel(undefined, {
        por_company_id: resposta([
            { id: PARCEIRO_A, name: 'Alfa', active: true, tf_company_id: ORG_A },
            { id: 999, name: 'Alfa (duplicado)', active: true, tf_company_id: ORG_A }
        ])
    });
    verifica(r.veredito === 'DIVERGENTE' && tipos(r).indexOf('id_cruzado_duplicado_no_destino') >= 0,
        'I3: dois parceiros com o mesmo tf_company_id -> ' + tipos(r));
}
{
    const r = mundoSaudavel(undefined, {
        por_id: resposta([{ id: PARCEIRO_A, name: 'Alfa', active: true, tf_company_id: ORG_C, tf_cnpj: '11.222.333/0001-81' }]),
        por_company_id: resposta([])
    });
    verifica(r.veredito === 'DIVERGENTE' && tipos(r).indexOf('id_cruzado_aponta_para_fora_do_lote') >= 0,
        'I4: espelho aponta para organizacao FORA do lote -> ' + tipos(r));
}
{
    const r = avaliar([entidade(), cobertura(1)], [pendente({ idade_s: 901 }), coberturaDaFila(1, 0)],
        { por_id: resposta([{ id: PARCEIRO_A, name: 'Alfa', active: true, tf_company_id: ORG_A }]), por_company_id: resposta([{ id: PARCEIRO_A, tf_company_id: ORG_A }]) });
    verifica(r.veredito === 'DIVERGENTE' && tipos(r) === 'evento_pendente_alem_da_janela',
        'P1: evento na fila alem da janela declarada -> ' + tipos(r));
}
{
    const r = avaliar([entidade(), cobertura(1)],
        [pendente({ tem_trilha: true, status_da_trilha: 'COMPLETED' }), coberturaDaFila(1, 0)],
        { por_id: resposta([{ id: PARCEIRO_A, active: true, tf_company_id: ORG_A }]), por_company_id: resposta([{ id: PARCEIRO_A, tf_company_id: ORG_A }]) });
    verifica(r.veredito === 'DIVERGENTE' && tipos(r).indexOf('evento_entregue_ainda_na_fila') >= 0,
        'P2: trilha diz entregue e o evento continua na fila -> ' + tipos(r));
}
{
    const r = avaliar([entidade(), cobertura(1)], [pendente({ attempts: contrato.teto_de_tentativas }), coberturaDaFila(1, 0)],
        { por_id: resposta([{ id: PARCEIRO_A, active: true, tf_company_id: ORG_A }]), por_company_id: resposta([{ id: PARCEIRO_A, tf_company_id: ORG_A }]) });
    verifica(r.veredito === 'DIVERGENTE' && tipos(r).indexOf('evento_no_teto_ainda_na_fila') >= 0,
        'P3: evento na fila no teto de tentativas -> ' + tipos(r));
}
{
    const r = avaliar([entidade(), cobertura(1)],
        [pendente({ tem_trilha: true, status_da_trilha: contrato.status_da_trilha.recusa }), coberturaDaFila(1, 0)],
        { por_id: resposta([{ id: PARCEIRO_A, active: true, tf_company_id: ORG_A }]), por_company_id: resposta([{ id: PARCEIRO_A, tf_company_id: ORG_A }]) });
    verifica(r.veredito === 'DIVERGENTE' && tipos(r).indexOf('recusa_ainda_na_fila') >= 0,
        'P4: trilha com recusa definitiva e o evento continua na fila -> ' + tipos(r));
}
{
    const r = avaliar([entidade(), cobertura(1)],
        [pendente({ tem_trilha: true, status_da_trilha: contrato.status_da_trilha.falha_transitoria, idade_s: 5 }), coberturaDaFila(1, 0)],
        { por_id: resposta([{ id: PARCEIRO_A, active: true, tf_company_id: ORG_A }]), por_company_id: resposta([{ id: PARCEIRO_A, tf_company_id: ORG_A }]) });
    verifica(r.veredito === 'OK', 'falha transitoria recente na fila NAO e divergencia (retry e normal)');
}

/* ---------------------------------------------------------------- 3. fail-closed */
console.log('--- fail-closed: ausencia de medicao nunca e saude ---');
{
    const r = avaliar([entidade()], [coberturaDaFila(0, 0)], { por_id: resposta([]), por_company_id: resposta([]) });
    verifica(r.veredito === 'INDETERMINADO' && regras(r).indexOf('consulta_de_origem_sem_cobertura') >= 0,
        'origem sem linha de cobertura fecha INDETERMINADO -> ' + regras(r));
}
{
    const r = avaliar([entidade(), cobertura(1)], [coberturaDaFila(0, 0)], { por_id: resposta([], 500), por_company_id: resposta([], 500) });
    verifica(r.veredito === 'INDETERMINADO' && regras(r).indexOf('leitura_do_destino_nao_medida') >= 0
        && regras(r).indexOf('entidade_esperada_sem_leitura') >= 0,
        'HTTP 500 nas leituras fecha INDETERMINADO (e nomeia a entidade esperada sem leitura) -> ' + regras(r));
}
{
    const r = avaliar([entidade(), cobertura(1)], [coberturaDaFila(0, 0)],
        { por_id: resposta([], 200, { ok: false, codigo: 'operacao_nao_declarada' }), por_company_id: resposta([], 200, { ok: false, codigo: 'operacao_nao_declarada' }) });
    verifica(r.veredito === 'INDETERMINADO' && regras(r).indexOf('leitura_do_destino_nao_medida') >= 0,
        'resposta RECUSADA (ok=false) nao vira "sem divergencia" -> ' + regras(r));
}
{
    const r = avaliar([entidade(), cobertura(1)], [coberturaDaFila(0, 0)],
        { por_id: resposta([], 200, { ok: true, dados: {} }), por_company_id: resposta([], 200, { ok: true, dados: {} }) });
    verifica(r.veredito === 'INDETERMINADO' && regras(r).indexOf('leitura_do_destino_nao_medida') >= 0,
        'corpo sem `dados.registros` nao vira "sem divergencia" -> ' + regras(r));
}
{
    const r = avaliar([entidade(), cobertura(1)], [pendente({ tem_trilha: true, status_da_trilha: 'EXPIRED' }), coberturaDaFila(1, 0)],
        { por_id: resposta([{ id: PARCEIRO_A, active: true, tf_company_id: ORG_A }]), por_company_id: resposta([{ id: PARCEIRO_A, tf_company_id: ORG_A }]) });
    verifica(r.veredito === 'INDETERMINADO' && regras(r).indexOf('trilha_sem_status_conhecido') >= 0,
        'status de trilha fora do vocabulario fecha INDETERMINADO -> ' + regras(r));
}
{
    const r = avaliar([entidade(), cobertura(1)], [pendente({ idade_s: null }), coberturaDaFila(1, 0)],
        { por_id: resposta([{ id: PARCEIRO_A, active: true, tf_company_id: ORG_A }]), por_company_id: resposta([{ id: PARCEIRO_A, tf_company_id: ORG_A }]) });
    verifica(r.veredito === 'INDETERMINADO' && regras(r).indexOf('pendente_sem_idade') >= 0,
        'pendente sem idade medivel fecha INDETERMINADO -> ' + regras(r));
}
{
    const r = avaliar([entidade(), cobertura(1)], [{ tipo: 'pendente' }], { por_id: resposta([]), por_company_id: resposta([]) });
    verifica(r.veredito === 'INDETERMINADO' && regras(r).indexOf('consulta_de_pendentes_sem_cobertura') >= 0,
        'fila sem linha de cobertura fecha INDETERMINADO -> ' + regras(r));
}
{
    const r = avaliar([entidade(), cobertura(1)], [coberturaDaFila(0, 0)], {});
    verifica(r.veredito === 'INDETERMINADO' && regras(r).indexOf('leitura_do_destino_nao_medida') >= 0,
        'leituras AUSENTES (no que nao rodou) fecham INDETERMINADO, nao OK');
}
{
    const r = nucleo.avaliar(null, [entidade(), cobertura(1)], [coberturaDaFila(0, 0)], {}, AGORA);
    verifica(r.veredito === 'INDETERMINADO' && regras(r) === 'contrato_invalido',
        'contrato invalido/ausente fecha INDETERMINADO sem decidir nada');
}
{
    const semNumeros = JSON.parse(JSON.stringify(contrato));
    semNumeros.janela_de_pendencia_s = null;
    const r = nucleo.avaliar(semNumeros, [entidade(), cobertura(1)], [coberturaDaFila(0, 0)], {}, AGORA);
    verifica(r.veredito === 'INDETERMINADO' && regras(r) === 'contrato_invalido',
        'contrato sem a janela declarada NAO decide (nao existe limiar implicito no nucleo)');
}

/* ---------------------------------------------------------------- 3b. janelas declaradas */
console.log('--- janela do lote e janela da fila ---');
{
    const r = avaliar([entidade(), cobertura(5000)], [coberturaDaFila(0, 0)],
        { por_id: resposta([{ id: PARCEIRO_A, active: true, tf_company_id: ORG_A }]), por_company_id: resposta([{ id: PARCEIRO_A, tf_company_id: ORG_A }]) });
    verifica(r.veredito === 'OK' && r.cobertura.janela_completa === false
        && r.linha.indexOf('janela_parcial') > 0,
        'janela do lote PARCIAL e declarada como parcial na linha-resumo (' + r.linha + ')');
    verifica(r.relatorio.indexOf('total_de_organizacoes=5000') > 0,
        'a cobertura do relatorio mostra a base inteira medida (5000) e nao so o recorte');
}
{
    const r = avaliar([entidade(), cobertura(1)], [{ tipo: 'cobertura', total_pending: null, total_retry: null }],
        { por_id: resposta([{ id: PARCEIRO_A, active: true, tf_company_id: ORG_A }]), por_company_id: resposta([{ id: PARCEIRO_A, tf_company_id: ORG_A }]) });
    verifica(r.veredito === 'INDETERMINADO' && regras(r).indexOf('limite_da_fila_sem_medicao') >= 0,
        'fila sem o total por status NAO pode ser declarada completa nem parcial -> ' + regras(r));
}
{
    const r = avaliar([entidade(), cobertura(1)], [coberturaDaFila(5000, 0)],
        { por_id: resposta([{ id: PARCEIRO_A, active: true, tf_company_id: ORG_A }]), por_company_id: resposta([{ id: PARCEIRO_A, tf_company_id: ORG_A }]) });
    verifica(r.veredito === 'OK' && r.cobertura.fila_leitura_completa === false
        && r.cobertura.total_na_fila === 5000 && r.linha.indexOf('fila_parcial') > 0,
        'fila maior que o teto de leitura: a rodada diz que leu um recorte (' + r.linha + ')');
}

/* ---------------------------------------------------------------- 4. ordem do veredito */
console.log('--- a ordem do veredito e a nao-vacuidade do relatorio ---');
{
    const divergente = mundoSaudavel(undefined, { por_id: resposta([]), por_company_id: resposta([]) });
    const indeterminado = avaliar([entidade(), cobertura(1)], [coberturaDaFila(0, 0)], {});
    verifica(divergente.veredito === 'DIVERGENTE' && indeterminado.veredito === 'INDETERMINADO',
        'INDETERMINADO e pior que DIVERGENTE (o lado nao medido nao vira veredito bom)');
    verifica(contrato.veredito.ordem.indexOf('INDETERMINADO') > contrato.veredito.ordem.indexOf('DIVERGENTE')
        && contrato.veredito.ordem.indexOf('DIVERGENTE') > contrato.veredito.ordem.indexOf('OK'),
        'a ordem declarada e OK < DIVERGENTE < INDETERMINADO');
}
{
    const motivoLongo = 'x'.repeat(500);
    const r = avaliar([entidade(), cobertura(1)],
        [{ tipo: 'pendente', evento_id: 'e1', chave: 'k', event_type: 'COMPANY_QUALIFIED', status: 'PENDING', attempts: 0, idade_s: 9999, motivo: motivoLongo, tem_trilha: false }, coberturaDaFila(1, 0)],
        { por_id: resposta([{ id: PARCEIRO_A, active: true, tf_company_id: ORG_A }]), por_company_id: resposta([{ id: PARCEIRO_A, tf_company_id: ORG_A }]) });
    const motivo = r.divergencias[0].motivo;
    verifica(motivo.length <= contrato.relatorio.tamanho_maximo_do_motivo + 3 && motivo.slice(-3) === '...',
        'motivo longo e TRUNCADO COM MARCA (nunca cortado em silencio), teto declarado=' + contrato.relatorio.tamanho_maximo_do_motivo);
    verifica(r.relatorio.indexOf('request_payload') < 0 && r.relatorio.indexOf('response_payload') < 0
        && r.relatorio.indexOf('payload') < 0,
        'o relatorio NAO carrega payload');
    verifica(r.relatorio.indexOf('DIVERGENCIAS (') > 0 && r.relatorio.split('\n')[0].indexOf('RECONCILIACAO') === 0
        && r.relatorio.split('\n')[1] === r.linha,
        'o relatorio tem a linha-resumo na segunda linha e a secao de divergencias');
    verifica(r.linha.indexOf('VEREDITO: DIVERGENTE') === 0 && r.linha.indexOf('janela_completa') > 0,
        'a linha-resumo e declarada com veredito e cobertura: ' + r.linha);
}
{
    const r = mundoSaudavel([entidade({ cnpj: null, domain: 'alfa.example' }), cobertura(1)]);
    verifica(r.veredito === 'OK', 'E4 NAO inventa divergencia a partir de ausencia (origem sem cnpj, espelho sem cnpj)');
}
{
    // MEDIDO em execucao real (estado C/D do aceite): o Odoo devolve `false` para campo de
    // caracter vazio. Ler `String(false)` = 'false' inventaria um valor que nao existe.
    const r = mundoSaudavel(undefined, {
        por_id: resposta([{ id: PARCEIRO_A, name: 'Alfa', active: true, tf_company_id: ORG_A, tf_cnpj: false, tf_domain: false }])
    });
    verifica(r.veredito === 'OK' && r.divergencias.length === 0,
        'campo do Odoo vazio (false) NAO e valor: nao vira divergencia de identidade forte -> ' + tipos(r));
}

/* ---------------------------------------------------------------- 5. pedidos da porta unica */
console.log('--- pedidos da porta unica ---');
{
    const pedidos = nucleo.montarPedidos(contrato, [entidade(), cobertura(1)]);
    const porId = pedidos.pedidos.por_id;
    const porCompany = pedidos.pedidos.por_company_id;
    verifica(porId.filtro[0][0] === 'id' && porId.filtro[0][1] === 'in' && porId.filtro[0][2].length === 1
        && String(porId.filtro[0][2][0]) === String(PARCEIRO_A),
        'leitura por id filtra pelos IDs de PARCEIRO da origem: ' + JSON.stringify(porId.filtro));
    verifica(porCompany.filtro[0][0] === 'tf_company_id' && porCompany.filtro[0][2].length === 1
        && porCompany.filtro[0][2][0] === ORG_A,
        'leitura por tf_company_id filtra pelos IDs de ORGANIZACAO: ' + JSON.stringify(porCompany.filtro));
    const leituraDeclarada = contrato.leituras_do_destino.find((l) => l.id === 'por_id');
    verifica(porId.limite === leituraDeclarada.limite && porId.campos.length === leituraDeclarada.campos.length
        && porId.ordem === leituraDeclarada.ordem,
        'limite, campos e ordem do pedido vem do contrato (limite=' + porId.limite + ')');
    verifica(porId.modelo === contrato.fontes.destino.modelo, 'o modelo do pedido e o do contrato (' + porId.modelo + ')');
    verifica(porId.incluir_arquivados === true && porCompany.incluir_arquivados === true,
        'os parametros EXTRAS declarados por leitura entram no pedido (incluir_arquivados=true)');
    const pedidoSemExtras = nucleo.montarPedidos(
        Object.assign({}, contrato, {leituras_do_destino: contrato.leituras_do_destino.map((l) => {
            const copia = JSON.parse(JSON.stringify(l)); delete copia.parametros; return copia;
        })}), [entidade(), cobertura(1)]);
    verifica(pedidoSemExtras.pedidos.por_id.incluir_arquivados === undefined,
        'sem parametro declarado o pedido NAO leva chave inventada (o nucleo nao conhece o vocabulario da porta unica)');
}
{
    const pedidos = nucleo.montarPedidos(contrato, [cobertura(0)]);
    verifica(pedidos.entidades === 0 && pedidos.pedidos.por_id.filtro[0][2].length === 0
        && pedidos.pedidos.por_company_id.filtro[0][2].length === 0,
        'lote VAZIO: o filtro vai com lista vazia (in []) — nao vira leitura da base inteira');
}
{
    const pedidos = nucleo.montarPedidos(contrato, [entidade({ odoo_partner_id: null, esperada: false }), cobertura(1)]);
    verifica(pedidos.pedidos.por_id.filtro[0][2].length === 0 && pedidos.pedidos.por_company_id.filtro[0][2].length === 1,
        'sem ponta gravada no PG a leitura por id vai vazia e a por tf_company_id vai com o ID da organizacao');
}

/* ---------------------------------------------------------------- 6. leitura da resposta */
console.log('--- leitura da resposta da porta unica ---');
{
    const comoTexto = nucleo.lerResposta(contrato, 'por_id', [{ json: { statusCode: 200, body: JSON.stringify({ ok: true, dados: { registros: [{ id: 1 }], total: 1 } }) } }]);
    verifica(comoTexto.medido === true && comoTexto.registros.length === 1,
        'corpo como TEXTO JSON e lido como medicao (o n8n pode entregar string)');
    const semOk = nucleo.lerResposta(contrato, 'por_id', [{ json: { statusCode: 200, body: { dados: { registros: [] } } } }]);
    verifica(semOk.medido === false && semOk.motivo === 'leitura_sem_ok', 'corpo sem `ok:true` nao e medicao (' + semOk.motivo + ')');
    const semResposta = nucleo.lerResposta(contrato, 'por_id', []);
    verifica(semResposta.medido === false && semResposta.motivo === 'leitura_sem_resposta', 'sem item do no de leitura nao e medicao');
    const erroDeTransporte = nucleo.lerResposta(contrato, 'por_id', [{ json: { statusCode: 0, body: null } }]);
    verifica(erroDeTransporte.medido === false, 'falha de transporte nao e medicao');
}

/* ---------------------------------------------------------------- 7. leitura de contrato */
{
    verifica(nucleo.contratoValido(contrato) === true, 'o contrato versionado e valido para o nucleo');
    verifica(nucleo.contratoValido(JSON.parse(JSON.stringify(Object.assign({}, contrato, { comparacoes: [] })))) === false,
        'contrato sem comparacao declarada e invalido (nao decide nada)');
    const semMotivo = JSON.parse(JSON.stringify(contrato));
    semMotivo.comparacoes[0].motivo = '';
    verifica(nucleo.contratoValido(semMotivo) === false, 'comparacao sem motivo nomeado torna o contrato invalido');
}

resumo();
