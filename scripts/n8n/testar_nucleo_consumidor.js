/* ============================================================================
 * Suite do nucleo do consumidor de outbox — card TRE-W3-E02-T01.
 *
 * Roda em node PURO (nao precisa de n8n): exercita o nucleo versionado
 * (n8n/codigo/nucleo-outbox-consumer.js) e, principalmente, o codigo que esta'
 * DENTRO do workflow versionado — o Code node e' executado com um shim de
 * `$input`, de modo que o que se mede e' o artefato entregue, nao a copia.
 *
 * Uso (na VPS, dentro da imagem do n8n, que traz node):
 *   docker run --entrypoint node n8nio/n8n /caminho/scripts/n8n/testar_nucleo_consumidor.js
 * Saida: OK/FALHOU por item + uma linha RESUMO; exit 0 = tudo OK, 1 = falhou, 2 = uso errado.
 * ==========================================================================*/
'use strict';

const fs = require('fs');
const path = require('path');

const RAIZ = path.resolve(__dirname, '..', '..');
const arg = (nome, padrao) => {
    const i = process.argv.indexOf('--' + nome);
    return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : padrao;
};
const ARQ_CONTRATO = arg('contrato', path.join(RAIZ, 'n8n', 'contracts', 'outbox-consumer.v1.json'));
const ARQ_NUCLEO = arg('nucleo', path.join(RAIZ, 'n8n', 'codigo', 'nucleo-outbox-consumer.js'));
const ARQ_WORKFLOW = arg('workflow', path.join(RAIZ, 'n8n', 'workflows', 'TRE-outbox-consumer.json'));
const ARQ_POLITICA = arg('politica',
    path.join(RAIZ, 'odoo', 'addons', 'transformativa_sales_ai', 'api', 'politica_api.json'));

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
const politica = JSON.parse(fs.readFileSync(ARQ_POLITICA, 'utf8'));
const nucleo = require(ARQ_NUCLEO);

/* ---------------------------------------------------------------- fixtures */

const UUID_A = '11111111-2222-3333-4444-555555555555';
const UUID_B = '99999999-8888-7777-6666-555555555555';

function eventoValido(extra) {
    const base = {
        id: UUID_A,
        aggregate_type: 'organization',
        aggregate_id: UUID_B,
        event_type: 'COMPANY_QUALIFIED',
        event_version: '1.0',
        payload: { name: 'Alfa Consultoria Ltda', domain: 'alfa.example', priority_score: 72.5 },
        attempts: 0,
        status: 'PENDING'
    };
    return Object.assign(base, extra || {});
}

/* ------------------------------------------------------- itens do contrato */

confere('contrato: esquema e versao declarados', contrato.esquema === '1' && !!contrato.versao);
confere('contrato: eventos declarados (>=1)', Array.isArray(contrato.eventos) && contrato.eventos.length >= 1);
confere('contrato: teto de tentativas e numero > 0', Number(contrato.retry.teto_de_tentativas) > 0);
confere('contrato: operacao destino declarada', contrato.destino.operacoes_declaradas.indexOf('empresa_upsert') >= 0);

/* ------------------------------------------------------------- decisao pura */

const decisao = nucleo.decidirEvento(eventoValido(), contrato);
igual('evento valido -> ENVIAR', decisao.decisao, 'ENVIAR');
igual('evento valido -> pedido.parametros.modelo e o do contrato', decisao.pedido.parametros.modelo, contrato.destino.modelo);
igual('evento valido -> valores.name do payload', decisao.pedido.parametros.valores.name, 'Alfa Consultoria Ltda');
igual('evento valido -> tf_company_id vem de aggregate_id', decisao.pedido.parametros.valores.tf_company_id,
    eventoValido().aggregate_id);
igual('evento valido -> chave derivada do evento', decisao.chave, 'outbox:' + UUID_A + ':COMPANY_QUALIFIED');
igual('evento valido -> correlacao derivada do evento', decisao.correlacao, 'outbox:' + UUID_A);
confere('evento valido -> chave casa o formato exigido pela API',
    new RegExp(contrato.trilha.chave_de_idempotencia.formato).test(decisao.chave), decisao.chave);
igual('evento valido -> operacao declarada', decisao.operacao, 'empresa_upsert');
igual('evento valido -> tentativa de entrega incrementa attempts', decisao.incrementa_tentativas, 1);

const extra = nucleo.decidirEvento(eventoValido({
    payload: { name: 'Beta Ltda', domain: 'beta.example', icp_score: 91, tier: 'A+', primary_pain: 'X' }
}), contrato);
igual('mapeamento nao inventa campo (icp_score/tier/primary_pain ficam fora do pedido)',
    Object.keys(extra.pedido.parametros.valores).sort(), ['name', 'tf_company_id', 'tf_domain']);

const vazio = nucleo.decidirEvento(eventoValido({
    payload: { name: 'Gama Ltda', cnpj: '', domain: 'gama.example' }
}), contrato);
confere('campo sem valor nao vira campo vazio no pedido',
    vazio.pedido.parametros.valores.tf_cnpj === undefined, JSON.stringify(vazio.pedido.parametros.valores));

const semVersao = nucleo.decidirEvento(eventoValido({ event_version: null }), contrato);
igual('sem event_version -> RECUSAR', semVersao.decisao, 'RECUSAR');
confere('sem event_version -> motivo nomeado', /envelope_sem_event_version/.test(semVersao.motivo), semVersao.motivo);
igual('sem event_version -> pedido ausente (nao ha chamada)', semVersao.pedido, null);
igual('sem event_version -> DEAD_LETTER', semVersao.status_final, contrato.status.recusa);
igual('sem event_version -> NAO incrementa tentativas', semVersao.incrementa_tentativas, 0);

const versaoRuim = nucleo.decidirEvento(eventoValido({ event_version: '9.9' }), contrato);
igual('event_version desconhecida -> RECUSAR', versaoRuim.decisao, 'RECUSAR');
confere('event_version desconhecida -> motivo nomeado', /event_version_nao_suportada/.test(versaoRuim.motivo), versaoRuim.motivo);

const foraDoContrato = nucleo.decidirEvento(eventoValido({ event_type: 'organization.enriched' }), contrato);
igual('event_type fora do contrato -> RECUSAR', foraDoContrato.decisao, 'RECUSAR');
confere('event_type fora do contrato -> motivo nomeado',
    /event_type_fora_do_contrato/.test(foraDoContrato.motivo), foraDoContrato.motivo);

const semIdentidade = nucleo.decidirEvento(eventoValido({ aggregate_id: null }), contrato);
igual('identidade ausente -> RECUSAR', semIdentidade.decisao, 'RECUSAR');
confere('identidade ausente -> motivo nomeado', /identidade_ausente/.test(semIdentidade.motivo), semIdentidade.motivo);

const semName = nucleo.decidirEvento(eventoValido({ payload: { domain: 'delta.example' } }), contrato);
igual('campo exigido ausente -> RECUSAR', semName.decisao, 'RECUSAR');
confere('campo exigido ausente -> motivo nomeado', /campo_exigido_ausente:name/.test(semName.motivo), semName.motivo);

const esgotado = nucleo.decidirEvento(eventoValido({ attempts: contrato.retry.teto_de_tentativas, status: 'RETRY' }), contrato);
igual('teto de tentativas -> ESGOTADO', esgotado.decisao, 'ESGOTADO');
igual('teto de tentativas -> DEAD_LETTER', esgotado.status_final, contrato.status.esgotado);
igual('teto de tentativas -> nao incrementa de novo', esgotado.incrementa_tentativas, 0);
confere('teto de tentativas -> motivo nomeado', /teto_de_tentativas_atingido/.test(esgotado.last_error), esgotado.last_error);

const ignorado = nucleo.decidirEvento(eventoValido({ status: 'PROCESSED' }), contrato);
igual('status fora da fila -> IGNORAR', ignorado.decisao, 'IGNORAR');
igual('status fora da fila -> no-op (sem id, sem UPDATE)', ignorado.evento_id, null);

/* ------------------------------------------------ dedup por chave (TRE-W3-E02-T02) */

confere('contrato: dedup declarado (criterio, consulta, registro e status)',
    !!contrato.dedup && !!contrato.dedup.criterio_de_replay &&
    contrato.dedup.criterio_de_replay.coluna_de_chave === 'idempotency_key' &&
    !!contrato.dedup.consulta_da_chave && !!contrato.dedup.registro_do_replay);
igual('contrato: dedup.ordem_da_decisao nomeia as cinco decisoes',
    contrato.dedup.ordem_da_decisao.slice().sort(),
    ['ENVIAR', 'ESGOTADO', 'IGNORAR', 'RECUSAR', 'REPLAY'].sort());
igual('contrato: o replay nao incrementa tentativas', contrato.dedup.incrementa_tentativas, 0);

const eventoEntregue = eventoValido();
const chaveEntregue = 'outbox:' + UUID_A + ':COMPANY_QUALIFIED';
const trilhaCompleta = {};
trilhaCompleta[chaveEntregue] = {
    idempotency_key: chaveEntregue,
    trilha_id: 'trilha-1',
    status: contrato.dedup.criterio_de_replay.status_trilha,
    completed_at: '2026-10-02 00:00:00+00',
    response_payload: { ok: true, dados: { acao_efetiva: 'criar', id: 42 } }
};

igual('chavesDoLote deriva uma chave por evento (sem repetir)',
    nucleo.chavesDoLote([{ json: eventoEntregue }, { json: eventoEntregue }], contrato), [chaveEntregue]);
igual('chavesDoLote ignora item vazio (e o no de consulta emite item vazio quando nao ha trilha)',
    nucleo.chavesDoLote([{ json: {} }, { json: null }], contrato), []);
igual('campos da chave vem da DERIVACAO do contrato (nao ha lista literal no nucleo)',
    nucleo.camposDaChaveDeIdempotencia(contrato), ['id', 'event_type']);
igual('evento sem os componentes da chave nao gera chave (outbox:: nao e chave de nada)',
    nucleo.chavesDoLote([{ json: eventoValido({ event_type: null }) }, { json: eventoValido({ id: null }) }], contrato), []);
igual('trilhaPorChave indexa pela chave e descarta linha sem chave',
    Object.keys(nucleo.trilhaPorChave([{ json: {} }, { json: trilhaCompleta[chaveEntregue] }])), [chaveEntregue]);
igual('trilhaPorChave aceita consulta vazia sem inventar registro', nucleo.trilhaPorChave([]).hasOwnProperty(chaveEntregue), false);
igual('registroDeReplay aceita o registro de status de sucesso',
    nucleo.registroDeReplay(eventoEntregue, contrato, trilhaCompleta).trilha_id, 'trilha-1');
igual('registroDeReplay recusa registro de trilha FAILED',
    nucleo.registroDeReplay(eventoEntregue, contrato,
        { [chaveEntregue]: Object.assign({}, trilhaCompleta[chaveEntregue], { status: 'FAILED' }) }), null);
igual('registroDeReplay recusa registro de trilha REFUSED',
    nucleo.registroDeReplay(eventoEntregue, contrato,
        { [chaveEntregue]: Object.assign({}, trilhaCompleta[chaveEntregue], { status: 'REFUSED' }) }), null);
igual('registroDeReplay recusa registro cuja chave nao e a do evento',
    nucleo.registroDeReplay(eventoEntregue, contrato,
        { [chaveEntregue]: Object.assign({}, trilhaCompleta[chaveEntregue], { idempotency_key: 'outbox:outro' }) }), null);
igual('registroDeReplay sem trilha (consulta vazia) e null', nucleo.registroDeReplay(eventoEntregue, contrato, {}), null);

const replay = nucleo.decidirEvento(eventoEntregue, contrato, trilhaCompleta);
igual('chave ja entregue -> REPLAY', replay.decisao, 'REPLAY');
igual('REPLAY -> status final do contrato', replay.status_final, contrato.dedup.status_final);
igual('REPLAY -> status de trilha do contrato (reaproveitado)', replay.status_trilha, contrato.dedup.status_trilha);
igual('REPLAY -> NAO incrementa tentativas', replay.incrementa_tentativas, 0);
igual('REPLAY -> sem pedido (nao ha chamada a porta unica)', replay.pedido, null);
igual('REPLAY -> sem operacao (nao ha caminho para o POST)', replay.operacao, null);
confere('REPLAY -> motivo nomeado com a chave', replay.motivo === 'chave_ja_entregue:' + chaveEntregue, replay.motivo);
igual('REPLAY -> id do evento preservado (ha UPDATE, ao contrario do IGNORAR)', replay.evento_id, UUID_A);
igual('REPLAY -> resposta registrada e a da trilha (mesmo efeito)', replay.response_payload.ok, true);
igual('REPLAY -> trilha reaproveitada (id e instante da conclusao preservados)',
    replay.trilha_reaproveitada, { id: 'trilha-1', completed_at: '2026-10-02 00:00:00+00' });

igual('sem a trilha nao existe replay (decisao do T01 preservada)',
    nucleo.decidirEvento(eventoEntregue, contrato).decisao, 'ENVIAR');

const replayNoTeto = nucleo.decidirEvento(
    eventoValido({ attempts: contrato.retry.teto_de_tentativas, status: 'RETRY' }), contrato, trilhaCompleta);
igual('ordem da decisao: REPLAY vem ANTES do teto de tentativas', replayNoTeto.decisao, 'REPLAY');
const replayEnvelopeRuim = nucleo.decidirEvento(eventoValido({ event_version: null }), contrato, trilhaCompleta);
igual('ordem da decisao: REPLAY vem ANTES da recusa de envelope (o fato JA foi entregue)',
    replayEnvelopeRuim.decisao, 'REPLAY');
igual('ordem da decisao: IGNORAR vem ANTES do REPLAY (status fora da fila e no-op)',
    nucleo.decidirEvento(eventoValido({ status: 'PROCESSED' }), contrato, trilhaCompleta).decisao, 'IGNORAR');
const trilhaFalha = {};
trilhaFalha[chaveEntregue] = Object.assign({}, trilhaCompleta[chaveEntregue], { status: 'FAILED' });
igual('trilha FAILED nao vira replay: o evento volta a ser entregue (a escrita pode nao ter acontecido)',
    nucleo.decidirEvento(eventoEntregue, contrato, trilhaFalha).decisao, 'ENVIAR');
igual('trilha REFUSED nao vira replay: a recusa e do evento, nao da chave',
    nucleo.decidirEvento(eventoValido({ event_version: null }), contrato,
        { [chaveEntregue]: Object.assign({}, trilhaCompleta[chaveEntregue], { status: 'REFUSED' }) }).decisao, 'RECUSAR');

/* --------------------------------------------------- resposta da API -> estado */

igual('200 -> SUCESSO', nucleo.classificarResposta(200, { ok: true }, contrato).resultado, 'SUCESSO');
igual('422 -> RECUSA definitiva', nucleo.classificarResposta(422, { codigo: 'campo_obrigatorio_ausente' }, contrato).resultado, 'RECUSA');
igual('409 -> RECUSA definitiva', nucleo.classificarResposta(409, { codigo: 'valor_ambiguo' }, contrato).resultado, 'RECUSA');
igual('503 ambiente_nao_permitido -> RECUSA (codigo terminal)',
    nucleo.classificarResposta(503, { codigo: 'ambiente_nao_permitido' }, contrato).resultado, 'RECUSA');
igual('500 erro_interno -> FALHA_TRANSITORIA',
    nucleo.classificarResposta(500, { codigo: 'erro_interno' }, contrato).resultado, 'FALHA_TRANSITORIA');
igual('sem resposta (transporte) -> FALHA_TRANSITORIA', nucleo.classificarResposta(null, null, contrato).resultado,
    'FALHA_TRANSITORIA');

const decisaoEntrega = nucleo.decidirEvento(eventoValido(), contrato);
const respOk = nucleo.resultadoDasRespostas(
    [{ json: { statusCode: 200, body: { ok: true, correlation_id: decisaoEntrega.correlacao } } }],
    [{ json: decisaoEntrega }], contrato)[0].json;
igual('resposta 200 casada -> PROCESSED', respOk.status_final, contrato.status.sucesso);
igual('resposta 200 casada -> trilha COMPLETED', respOk.status_trilha, contrato.trilha.status.sucesso);
igual('resposta 200 casada -> payload da resposta na trilha', respOk.response_payload.ok, true);
igual('resposta 200 casada -> chave preservada da decisao', respOk.chave, decisaoEntrega.chave);
igual('resposta 200 casada -> id do evento preservado', respOk.evento_id, UUID_A);

const respTrocada = nucleo.resultadoDasRespostas(
    [{ json: { statusCode: 200, body: { ok: true, correlation_id: 'outbox:outro-evento' } } }],
    [{ json: decisaoEntrega }], contrato)[0].json;
igual('correlation_id divergente NAO vira sucesso', respTrocada.status_final, contrato.status.falha_transitoria);
confere('correlation_id divergente -> motivo nomeado', /resposta_nao_corresponde_a_decisao/.test(respTrocada.last_error),
    respTrocada.last_error);

const respTransporte = nucleo.resultadoDasRespostas(
    [{ json: { error: { message: 'connect ECONNREFUSED' } } }], [{ json: decisaoEntrega }], contrato)[0].json;
igual('falha de transporte -> RETRY', respTransporte.status_final, contrato.status.falha_transitoria);
igual('falha de transporte -> trilha FAILED', respTransporte.status_trilha, contrato.trilha.status.falha_transitoria);

/* ------------------------------- o que esta' DENTRO do workflow versionado */

const nos = workflow.nodes;
const noNucleo = nos.filter(n => n.name === 'Nucleo: validar e decidir')[0];
const noClassificar = nos.filter(n => n.name === 'Classificar resposta')[0];
const noChaves = nos.filter(n => n.name === 'Chaves do lote (nucleo)')[0];
const noTrilha = nos.filter(n => n.name === 'Ler trilha (chaves entregues)')[0];
confere('workflow tem os Code nodes e o no da trilha do dedup',
    !!noNucleo && !!noClassificar && !!noChaves && !!noTrilha);

const jsNucleo = noNucleo.parameters.jsCode;
const partesNucleo = jsNucleo.split(MARCADOR);
confere('Code node do nucleo usa o marcador do adaptador', partesNucleo.length === 2);
igual('Code node do nucleo embute o arquivo versionado (byte a byte, a menos do espaco final)',
    partesNucleo[0].replace(/\s+$/, ''), nucleoTexto.replace(/\s+$/, ''));
confere('adaptador do nucleo le os eventos do no da fila e passa a trilha lida',
    /\$\(\s*'Ler pendentes \(outbox\)'\s*\)\.all\(\)/.test(partesNucleo[1]) &&
    /trilhaPorChave\(\$input\.all\(\)\)/.test(partesNucleo[1]), partesNucleo[1].trim());

const jsClassificar = noClassificar.parameters.jsCode;
const partesClassificar = jsClassificar.split(MARCADOR);
confere('Code node da classificacao usa o marcador do adaptador', partesClassificar.length === 2);
igual('Code node da classificacao embute o MESMO arquivo versionado',
    partesClassificar[0].replace(/\s+$/, ''), nucleoTexto.replace(/\s+$/, ''));
confere('adaptador da classificacao passa as decisoes para o casamento',
    /resultadoDasRespostas\(\$input\.all\(\), \$\('Nucleo: validar e decidir'\)\.all\(\), CONTRATO\)/.test(partesClassificar[1]),
    partesClassificar[1].trim());

const jsChaves = noChaves.parameters.jsCode;
const partesChaves = jsChaves.split(MARCADOR);
igual('Code node das chaves embute o MESMO arquivo versionado',
    partesChaves[0].replace(/\s+$/, ''), nucleoTexto.replace(/\s+$/, ''));
confere('adaptador das chaves deriva a lista do lote',
    /chavesDoLoteComoItem\(\$input\.all\(\), CONTRATO\)/.test(partesChaves[1]), partesChaves[1].trim());
confere('o no da trilha emite saida mesmo sem chave entregue (alwaysOutputData)',
    noTrilha.alwaysOutputData === true);

function contratoDoCodeNode(partes) {
    const m = partes[1].match(/const CONTRATO = ([\s\S]*?);\n/);
    return m ? JSON.parse(m[1]) : null;
}
igual('contrato embutido no Code node do nucleo == contrato versionado', contratoDoCodeNode(partesNucleo), contrato);
igual('contrato embutido no Code node da classificacao == contrato versionado', contratoDoCodeNode(partesClassificar), contrato);

/* o codigo EMBUTIDO executa: shim de $input, como o n8n entrega */
function executarComoCodeNode(jsCode, itens, referencias) {
    const $input = { all: () => itens };
    const fn = new Function('$input', '$', 'JSON', 'items', jsCode);
    const $ = (nome) => ({ all: () => (referencias && referencias[nome]) || [] });
    return fn($input, $, JSON, itens);
}
const saidaEmbutida = executarComoCodeNode(jsNucleo, [{ json: {} }],
    { 'Ler pendentes (outbox)': [{ json: eventoValido() }] });
igual('Code node EMBUTIDO decide igual ao nucleo versionado', saidaEmbutida[0].json.decisao, 'ENVIAR');
igual('Code node EMBUTIDO monta o mesmo pedido', saidaEmbutida[0].json.pedido, decisao.pedido);

const saidaClassificada = executarComoCodeNode(jsClassificar,
    [{ json: { statusCode: 200, body: { ok: true, correlation_id: decisaoEntrega.correlacao } } }],
    { 'Nucleo: validar e decidir': [{ json: decisaoEntrega }] });
igual('Code node EMBUTIDO classifica a resposta em PROCESSED', saidaClassificada[0].json.status_final, contrato.status.sucesso);

const saidaChavesEmbutida = executarComoCodeNode(jsChaves, [{ json: eventoValido() }], null);
igual('Code node EMBUTIDO das chaves devolve a lista de chaves do lote',
    saidaChavesEmbutida[0].json.chaves, [chaveEntregue]);
const saidaReplayEmbutida = executarComoCodeNode(jsNucleo, [{ json: trilhaCompleta[chaveEntregue] }],
    { 'Ler pendentes (outbox)': [{ json: eventoEntregue }] });
igual('Code node EMBUTIDO decide REPLAY com a trilha que o no de consulta entregou',
    saidaReplayEmbutida[0].json.decisao, 'REPLAY');
igual('Code node EMBUTIDO do replay nao monta pedido (nenhuma chance de POST)',
    saidaReplayEmbutida[0].json.pedido, null);
const saidaSemTrilhaEmbutida = executarComoCodeNode(jsNucleo, [{ json: {} }],
    { 'Ler pendentes (outbox)': [{ json: eventoEntregue }] });
igual('Code node EMBUTIDO com consulta vazia entrega normalmente (nao inventa replay)',
    saidaSemTrilhaEmbutida[0].json.decisao, 'ENVIAR');

/* --------------------- o consumidor fala com a API controlada que EXISTE */

function operacaoDaPolitica(nome) {
    return (politica.operacoes || []).filter(o => o.nome === nome)[0] || null;
}
for (const evento of contrato.eventos) {
    const op = operacaoDaPolitica(evento.operacao);
    confere('politica da API declara a operacao ' + evento.operacao, !!op);
    if (!op) continue;
    confere('operacao ' + evento.operacao + ' e de escrita', op.tipo === 'escrita');
    confere('operacao ' + evento.operacao + ' exige idempotency_key', op.requer_idempotency_key === true);
    const declaracao = op.modelos[contrato.destino.modelo];
    confere('operacao ' + evento.operacao + ' declara o modelo ' + contrato.destino.modelo, !!declaracao);
    if (!declaracao) continue;
    const identidades = declaracao.campos_de_identidade || [declaracao.campo_de_identidade];
    let identidadeMapeada = false;
    for (const regra of evento.mapeamento) {
        if (regra.origem === evento.exige_identidade) {
            identidadeMapeada = identidades.indexOf(regra.destino) >= 0;
        }
        confere('mapeamento ' + regra.origem + ' -> ' + regra.destino + ' declarado na politica da API',
            declaracao.campos.indexOf(regra.destino) >= 0);
    }
    confere('a identidade do evento (' + evento.exige_identidade + ') chega a um campo de identidade da politica',
        identidadeMapeada);
    for (const exigido of (evento.exigidos || [])) {
        confere('campo exigido ' + exigido + ' e' + ' campo obrigatorio da politica da API',
            (declaracao.campos_obrigatorios || []).indexOf(exigido) >= 0);
    }
    confere('o corpo do pedido so tem as chaves do contrato',
        JSON.stringify(Object.keys(nucleo.montarPedido(eventoValido(), evento, contrato)).sort()) ===
        JSON.stringify(['correlation_id', 'idempotency_key', 'parametros'].sort()));
}

if (FALHAS === 0) {
    console.log('RESULTADO: NUCLEO_CONSUMIDOR_OK (' + ITENS + ' itens, 0 falhas)');
    process.exit(0);
}
console.log('RESULTADO: NUCLEO_CONSUMIDOR_FALHOU (' + ITENS + ' itens, ' + FALHAS + ' falha(s))');
process.exit(1);
