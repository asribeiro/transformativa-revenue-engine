/* ============================================================================
 * Suite do nucleo da PORTA de ingestao de eventos Odoo -> PostgreSQL — card
 * TRE-W3-E03-T01.
 *
 * Roda em node PURO (nao precisa de n8n): exercita o nucleo versionado
 * (n8n/codigo/nucleo-ingest-eventos.js) e, principalmente, o codigo que esta'
 * DENTRO do workflow versionado — o Code node e' executado com um shim de
 * `$input`, de modo que o que se mede e' o artefato entregue, nao a copia.
 *
 * Uso (na VPS, dentro da imagem do n8n, que traz node):
 *   docker run --entrypoint node n8nio/n8n /caminho/scripts/n8n/testar_nucleo_ingest.js
 * Saida: OK/FALHOU por item + uma linha RESUMO; exit 0 = tudo OK, 1 = falhou.
 * ==========================================================================*/
'use strict';

const fs = require('fs');
const path = require('path');

const RAIZ = path.resolve(__dirname, '..', '..');
const arg = (nome, padrao) => {
    const i = process.argv.indexOf('--' + nome);
    return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : padrao;
};
const ARQ_CONTRATO = arg('contrato', path.join(RAIZ, 'n8n', 'contracts', 'odoo-events-ingest.v1.json'));
const ARQ_NUCLEO = arg('nucleo', path.join(RAIZ, 'n8n', 'codigo', 'nucleo-ingest-eventos.js'));
const ARQ_WORKFLOW = arg('workflow', path.join(RAIZ, 'n8n', 'workflows', 'TRE-odoo-events-ingest.json'));

const MARCADOR = '/* --- adaptador do Code node (fora do nucleo versionado) --- */';

let ITENS = 0, FALHAS = 0;
function ok(nome) { ITENS++; console.log('OK    ' + nome); }
function falhou(nome, detalhe) {
    ITENS++; FALHAS++;
    console.log('FALHOU ' + nome + (detalhe ? '  [' + detalhe + ']' : ''));
}
function igual(nome, obtido, esperado) {
    const a = JSON.stringify(obtido), b = JSON.stringify(esperado);
    if (a === b) ok(nome); else falhou(nome, 'obtido=' + a + ' esperado=' + b);
}
function confere(nome, condicao, detalhe) {
    if (condicao) ok(nome); else falhou(nome, detalhe);
}

const contrato = JSON.parse(fs.readFileSync(ARQ_CONTRATO, 'utf8'));
const nucleoTexto = fs.readFileSync(ARQ_NUCLEO, 'utf8');
const workflow = JSON.parse(fs.readFileSync(ARQ_WORKFLOW, 'utf8'));
const nucleo = require(ARQ_NUCLEO);

/* ---------------------------------------------------------------- shim do Code node */

function executarComoCodeNode(codigo, itens, nos) {
    const $input = {
        first: () => itens[0],
        all: () => itens,
        item: itens[0]
    };
    const $items = (nome) => (nos || {})[nome] || [];
    const $ = (nome) => ({ first: () => ($items(nome)[0] || { json: {} }), all: () => $items(nome) });
    const $json = itens[0] ? itens[0].json : {};
    const funcao = new Function('$input', '$json', '$', '$items', codigo);
    return funcao($input, $json, $, $items);
}

function codigoDoNo(nome) {
    const no = workflow.nodes.filter((candidato) => candidato.name === nome)[0];
    if (!no) return null;
    return (no.parameters || {}).jsCode || null;
}

/* ---------------------------------------------------------------- envelopes de teste */

const U1 = '11111111-1111-4111-8111-111111111111';
const U2 = '22222222-2222-4222-8222-222222222222';

function payloadDoEvento(eventType) {
    const base = {
        modelo_origem: 'crm.lead',
        lead_id: 42,
        oportunidade_id: U2,
        organizacao_id: U1,
        parceiro_id: 7,
        estagio: 'Negociacao'
    };
    if (eventType === 'STAGE_CHANGED') {
        return Object.assign(base, {
            estagio_anterior: { id: 1, nome: 'Qualificado' },
            estagio_novo: { id: 2, nome: 'Proposta' },
            probabilidade: 60
        });
    }
    if (eventType === 'OPPORTUNITY_WON') {
        return Object.assign(base, { valor: 2500, moeda: 'BRL', probabilidade: 100 });
    }
    if (eventType === 'OPPORTUNITY_LOST') {
        return Object.assign(base, { valor: 2500, moeda: 'BRL', motivo: 'Preco' });
    }
    if (eventType === 'DEAL_VALUE_CHANGED') {
        return Object.assign(base, { valor_anterior: 1000, valor_novo: 2500, moeda: 'BRL' });
    }
    if (eventType === 'LOSS_REASON_RECORDED') {
        return Object.assign(base, {
            motivo: { id: 3, nome: 'Preco' },
            motivo_anterior: { id: 0, nome: '' }
        });
    }
    if (eventType === 'ACTIVITY_COMPLETED') {
        return {
            atividade_id: 91,
            tipo: 'Ligacao',
            resumo: 'Ligar para o diretor',
            prazo: '2026-10-03',
            documento: { modelo: 'crm.lead', id: 42 },
            usuario_responsavel: 'Comercial',
            com_feedback: true,
            oportunidade_id: U2,
            organizacao_id: U1
        };
    }
    return {
        reuniao_id: 123,
        nome: 'Diagnostico',
        inicio: '2026-10-04 10:00:00',
        fim: '2026-10-04 11:00:00',
        dia_inteiro: false,
        duracao_horas: 1,
        participantes: 2,
        oportunidade_id: U2,
        lead_id: 42,
        organizacao_id: U1
    };
}

function eventoValido(eventType) {
    return {
        event_type: eventType,
        event_version: contrato.envelope.versoes_suportadas[0],
        timestamp: '2026-10-02 12:00:00',
        idempotency_key: 'odoo:' + eventType + ':crm.lead:42:deadbeefdeadbeefdeadbeefdeadbeefdeadbeef',
        correlation_id: 'odoo-ui:crm.lead:42:2026-10-02 12:00:00',
        payload: payloadDoEvento(eventType)
    };
}

/* ---------------------------------------------------------------- itens 1..4 */

igual('nucleo declara a versao do contrato', nucleo.NUCLEO_VERSAO, contrato.versao);
confere('nucleo exporta a decisao e a validacao',
    typeof nucleo.decidir === 'function' && typeof nucleo.validarEnvelope === 'function');
igual('contrato declara os 7 eventos de events.odoo_to_pg',
    contrato.eventos.map((evento) => evento.event_type).sort(),
    ['ACTIVITY_COMPLETED', 'DEAL_VALUE_CHANGED', 'LOSS_REASON_RECORDED', 'MEETING_CREATED',
        'OPPORTUNITY_LOST', 'OPPORTUNITY_WON', 'STAGE_CHANGED']);
confere('o nucleo puro nao fala com banco nem com HTTP',
    !/\brequire\s*\(|\bfetch\s*\(|\bSELECT\b|\bINSERT\b|\bUPDATE\b/.test(nucleoTexto));

/* ---------------------------------------------------------------- aceite dos 7 eventos */

for (const definicao of contrato.eventos) {
    const decisao = nucleo.decidir(eventoValido(definicao.event_type), '{}', contrato);
    confere('aceita ' + definicao.event_type, decisao.aceito === true, decisao.motivo);
    igual('codigo HTTP 200 em ' + definicao.event_type, decisao.codigo_http, 200);
    igual('trilha COMPLETED em ' + definicao.event_type, decisao.parametros[6], 'COMPLETED');
    igual('sem motivo em ' + definicao.event_type, decisao.parametros[7], null);
    igual('entity_type de ' + definicao.event_type + ' e o modelo declarado',
        decisao.parametros[2], definicao.modelo_origem);
}

/* ---------------------------------------------------------------- recusas nomeadas */

const CASOS_RECUSA = [
    ['corpo_invalido', 'nao sou json', null],
    ['evento_ausente', Object.assign(eventoValido('STAGE_CHANGED'), { event_type: '' }), null],
    ['evento_fora_do_contrato',
        Object.assign(eventoValido('STAGE_CHANGED'), { event_type: 'LEAD_CREATED' }), null],
    ['envelope_sem_versao', Object.assign(eventoValido('STAGE_CHANGED'), { event_version: '' }), null],
    ['versao_nao_suportada', Object.assign(eventoValido('STAGE_CHANGED'), { event_version: '2.0' }), null],
    ['timestamp_ausente', Object.assign(eventoValido('STAGE_CHANGED'), { timestamp: '' }), null],
    ['payload_ausente', Object.assign(eventoValido('STAGE_CHANGED'), { payload: null }), null],
    ['idempotency_key_ausente', Object.assign(eventoValido('STAGE_CHANGED'), { idempotency_key: '' }), null],
    ['idempotency_key_invalida', Object.assign(eventoValido('STAGE_CHANGED'), { idempotency_key: 'curta' }), null]
];

for (const [motivo, entrada, _] of CASOS_RECUSA) {
    const decisao = nucleo.decidir(entrada, JSON.stringify(entrada), contrato);
    igual('recusa nomeada: ' + motivo, decisao.motivo, motivo);
    igual('recusa 422: ' + motivo, decisao.codigo_http, 422);
    igual('trilha REFUSED: ' + motivo, decisao.parametros[6], 'REFUSED');
}

/* matriz campo-exigido-ausente: remover CADA campo exigido de CADA evento reprova */
for (const definicao of contrato.eventos) {
    for (const campo of definicao.exigidos) {
        const envelope = eventoValido(definicao.event_type);
        delete envelope.payload[campo];
        const decisao = nucleo.decidir(envelope, JSON.stringify(envelope), contrato);
        igual('campo exigido ausente: ' + definicao.event_type + '/' + campo,
            decisao.motivo, 'campo_exigido_ausente:' + campo);
    }
}

/* identidade canonica */
{
    const envelope = eventoValido('STAGE_CHANGED');
    envelope.payload.oportunidade_id = 'nao-e-uuid';
    const decisao = nucleo.decidir(envelope, JSON.stringify(envelope), contrato);
    igual('identidade malformada e recusada', decisao.motivo, 'identidade_invalida:oportunidade_id');
}
{
    const envelope = eventoValido('MEETING_CREATED');
    delete envelope.payload.oportunidade_id;
    const decisao = nucleo.decidir(envelope, JSON.stringify(envelope), contrato);
    igual('sem oportunidade o entity_id e a organizacao', decisao.parametros[3], U1);
    confere('e a recusa nao acontece por falta de identidade', decisao.aceito === true);
}
{
    const envelope = eventoValido('MEETING_CREATED');
    delete envelope.payload.oportunidade_id;
    delete envelope.payload.organizacao_id;
    const decisao = nucleo.decidir(envelope, JSON.stringify(envelope), contrato);
    igual('sem identidade canonica o entity_id e NULL', decisao.parametros[3], null);
    confere('evento sem identidade canonica continua aceito', decisao.aceito === true);
}
{
    const envelope = eventoValido('STAGE_CHANGED');
    const decisao = nucleo.decidir(envelope, '{}', contrato);
    igual('a chave da trilha e a do envelope (aceito)', decisao.parametros[0], envelope.idempotency_key);
    const corpo = JSON.parse(decisao.parametros[5]);
    igual('o request_payload da trilha e o envelope', corpo.event_type, 'STAGE_CHANGED');
    igual('a versao da trilha e a do envelope', decisao.parametros[4], envelope.event_version);
}
{
    const envelope = eventoValido('STAGE_CHANGED');
    delete envelope.idempotency_key;
    const texto = JSON.stringify(envelope);
    const a = nucleo.decidir(envelope, texto, contrato);
    const b = nucleo.decidir(envelope, texto, contrato);
    igual('a chave da recusa e reprodutivel (mesmo corpo, mesma chave)', a.parametros[0], b.parametros[0]);
    confere('a chave da recusa e nomeada como recusa', a.parametros[0].indexOf('odoo:recusado:') === 0);
    const outro = nucleo.decidir(envelope, texto + ' ', contrato);
    confere('corpo diferente muda a chave da recusa', outro.parametros[0] !== a.parametros[0]);
}

/* ---------------------------------------------------------------- o Code node DO WORKFLOW */

const codigoDecisao = codigoDoNo('Nucleo: decidir ingestao');
confere('o workflow tem o Code node "Nucleo: decidir ingestao"', !!codigoDecisao);
if (codigoDecisao) {
    confere('o nucleo versionado esta EMBUTIDO no Code node',
        codigoDecisao.indexOf(nucleoTexto) === 0);
    confere('o Code node tem o adaptador declarado (fora do nucleo)',
        codigoDecisao.indexOf(MARCADOR) > 0);
    const envelope = eventoValido('OPPORTUNITY_WON');
    const itens = [{ json: { body: envelope, headers: {}, query: {}, params: {} } }];
    const saida = executarComoCodigo(codigoDecisao, itens);
    igual('Code node EMBUTIDO aceita o envelope entregue pelo Odoo', saida[0].json.aceito, true);
    igual('Code node EMBUTIDO devolve os parametros da trilha', saida[0].json.parametros.length, 8);
    const saidaRecusa = executarComoCodigo(codigoDecisao,
        [{ json: { body: { event_type: 'STAGE_CHANGED' }, headers: {}, query: {}, params: {} } }]);
    igual('Code node EMBUTIDO recusa envelope sem versao', saidaRecusa[0].json.motivo, 'envelope_sem_versao');
}

function executarComoCodigo(codigo, itens) {
    // O adaptador do Code node le `$input.first().json` (o corpo que o webhook entrega).
    const $input = { first: () => itens[0], all: () => itens, item: itens[0] };
    const funcao = new Function('$input', '$json', '$', '$items', codigo);
    return funcao($input, itens[0].json, () => ({ first: () => ({ json: {} }), all: () => [] }), () => []);
}

if (FALHAS === 0) {
    console.log('RESULTADO: NUCLEO_INGEST_OK (' + ITENS + ' itens, 0 falhas)');
    process.exit(0);
}
console.log('RESULTADO: NUCLEO_INGEST_FALHOU (' + ITENS + ' itens, ' + FALHAS + ' falha(s))');
process.exit(1);
