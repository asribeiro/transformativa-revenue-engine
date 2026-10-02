/* ============================================================================
 * Nucleo da PORTA de ingestao de eventos Odoo -> PostgreSQL — card
 * TRE-W3-E03-T01 (board transformativa-revenue-engine, card t_85cb2838).
 *
 * O QUE ESTE ARQUIVO E', E POR QUE ELE E' PURO:
 * Aqui vive a DECISAO da porta: qual envelope e' aceito, qual e' RECUSADO (com
 * motivo NOMEADO) e como o evento vira parametros da trilha
 * (`sales_intelligence.sync_events`). Este arquivo NAO fala com banco, NAO fala
 * com HTTP e NAO importa nada (o sandbox de Code node do n8n nao tem
 * `require`). Quem executa e' o workflow; quem decide e' este nucleo — a mesma
 * separacao do motor puro da API controlada do Odoo
 * (odoo/addons/transformativa_sales_ai/api/motor.py).
 *
 * FONTE DA REGRA (nada aqui e' inventado):
 *   * n8n/contracts/odoo-events-ingest.v1.json — o contrato versionado da porta
 *     (envelope, eventos aceitos, ordem da validacao, trilha, credenciais). O
 *     contrato e' PARAMETRO: as funcoes recebem `contrato` e nao tem lista de
 *     eventos, campos exigidos ou ordem de validacao literais;
 *   * docs/data/DATA_CONTRACT_V1.md §6 (envelope com `event_version`
 *     obrigatorio; regras 1..5 das integracoes) e §4 (`sync_events` como trilha
 *     de sincronizacao e idempotencia);
 *   * doc 06 §6-8 e doc 02 §3 (Outbox Pattern, idempotency key, dead-letter
 *     visivel, reconciliacao fora do caminho principal).
 *
 * O QUE ESTE ARQUIVO NAO FAZ (lacuna declarada, de proposito):
 *   * nao retenta nada: aceita ou recusa UMA vez e responde — o retry com teto
 *     e' do produtor (os dois lados estao neste card, mas a decisao de retry
 *     vive no modulo do Odoo);
 *   * nao materializa o evento em tabela de NEGOCIO (o contrato V1 nao tem
 *     tabela de historico de funil): a trilha guarda o payload integral;
 *   * nao gera nem valida segredo: o token e' credencial do cofre do n8n,
 *     checada pelo proprio webhook ANTES deste nucleo.
 *
 * VERSAO: acompanha o contrato (n8n/contracts/odoo-events-ingest.v1.json).
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

/** Formato do ID canonico do contrato §3 (`id UUID`) — flexivel na versao, de proposito. */
function ehUuid(valor) {
    return /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/
        .test(texto(valor));
}

/** Hash FNV-1a (NAO criptografico): serve so' para NOMEAR uma recusa de forma reproduzivel. */
function fnv1a(txt) {
    var h = 0x811c9dc5;
    for (var i = 0; i < txt.length; i++) {
        h ^= txt.charCodeAt(i) & 0xff;
        h = (h * 0x01000193) >>> 0;
    }
    return ('00000000' + h.toString(16)).slice(-8);
}

/** Definicao declarada do evento no contrato (ou null: evento fora da lista fechada). */
function definicaoDoEvento(contrato, eventType) {
    var eventos = (contrato && contrato.eventos) || [];
    for (var i = 0; i < eventos.length; i++) {
        if (eventos[i].event_type === texto(eventType)) return eventos[i];
    }
    return null;
}

/** Chave da linha de RECUSA quando o envelope nao trouxe chave (falha continua VISIVEL). */
function chaveDeRecusa(corpoBruto) {
    var material = typeof corpoBruto === 'string' ? corpoBruto : JSON.stringify(corpoBruto || null);
    return 'odoo:recusado:' + fnv1a(material || '');
}

/** Identidade canonica que acompanha o fato (payload.oportunidade_id > payload.organizacao_id). */
function identidadeCanonica(payload) {
    var candidatos = [
        { campo: 'oportunidade_id', tipo: 'oportunidade' },
        { campo: 'organizacao_id', tipo: 'organizacao' }
    ];
    for (var i = 0; i < candidatos.length; i++) {
        var valor = payload ? payload[candidatos[i].campo] : null;
        if (!ehVazio(valor)) {
            return {
                id: texto(valor),
                tipo: candidatos[i].tipo,
                valida: ehUuid(valor),
                campo: candidatos[i].campo
            };
        }
    }
    return { id: null, tipo: 'vazia', valida: true, campo: null };
}

/* ------------------------------------------------------------------ validacao */

/**
 * Valida o envelope NA ORDEM DECLARADA no contrato. Devolve {ok, motivo} — o
 * motivo e' nomeado, nunca um "erro generico".
 */
function validarEnvelope(envelope, contrato) {
    var ordem = (contrato && contrato.envelope && contrato.envelope.ordem_da_validacao) || [];
    var exigidos = (contrato && contrato.envelope && contrato.envelope.campos_obrigatorios) || [];
    var suportadas = (contrato && contrato.envelope && contrato.envelope.versoes_suportadas) || [];
    var formatoChave = /^[A-Za-z0-9._:-]{8,255}$/;

    if (!ehObjeto(envelope)) {
        return { ok: false, motivo: 'corpo_invalido' };
    }
    for (var i = 0; i < ordem.length; i++) {
        var regra = ordem[i];
        if (regra === 'corpo_objeto') {
            if (!ehObjeto(envelope)) return { ok: false, motivo: 'corpo_invalido' };
        } else if (regra === 'event_type_presente') {
            if (ehVazio(envelope.event_type)) return { ok: false, motivo: 'evento_ausente' };
        } else if (regra === 'event_type_declarado') {
            if (!definicaoDoEvento(contrato, envelope.event_type)) {
                return { ok: false, motivo: 'evento_fora_do_contrato' };
            }
        } else if (regra === 'event_version_presente') {
            if (ehVazio(envelope.event_version)) return { ok: false, motivo: 'envelope_sem_versao' };
        } else if (regra === 'event_version_suportada') {
            if (suportadas.indexOf(texto(envelope.event_version)) < 0) {
                return { ok: false, motivo: 'versao_nao_suportada' };
            }
        } else if (regra === 'timestamp_presente') {
            if (ehVazio(envelope.timestamp)) return { ok: false, motivo: 'timestamp_ausente' };
        } else if (regra === 'payload_objeto') {
            if (!ehObjeto(envelope.payload)) return { ok: false, motivo: 'payload_ausente' };
        } else if (regra === 'idempotency_key_presente') {
            if (ehVazio(envelope.idempotency_key)) {
                return { ok: false, motivo: 'idempotency_key_ausente' };
            }
        } else if (regra === 'idempotency_key_formato') {
            if (!formatoChave.test(texto(envelope.idempotency_key))) {
                return { ok: false, motivo: 'idempotency_key_invalida' };
            }
        } else if (regra === 'campos_exigidos') {
            var definicao = definicaoDoEvento(contrato, envelope.event_type);
            var exigidosDoEvento = (definicao && definicao.exigidos) || [];
            for (var j = 0; j < exigidosDoEvento.length; j++) {
                var campo = exigidosDoEvento[j];
                if (ehVazio(envelope.payload[campo])) {
                    return { ok: false, motivo: 'campo_exigido_ausente:' + campo };
                }
            }
        } else if (regra === 'identidade_canonica') {
            var identidade = identidadeCanonica(envelope.payload);
            if (!identidade.valida) {
                return { ok: false, motivo: 'identidade_invalida:' + identidade.campo };
            }
        }
    }
    // Os campos obrigatorios do envelope sao conferidos pelo nome (a ordem declarada acima e' a
    // narrativa; esta linha e' a garantia dura de que nenhum deles passou batido).
    for (var k = 0; k < exigidos.length; k++) {
        if (ehVazio(envelope[exigidos[k]])) {
            return { ok: false, motivo: 'campo_obrigatorio_ausente:' + exigidos[k] };
        }
    }
    return { ok: true, motivo: null };
}

/* ------------------------------------------------------------------ decisao */

/**
 * Decide a ingestao de UM envelope e devolve o item que o workflow consome:
 * o estado (aceito/motivo/codigo HTTP), os dados da trilha e a LISTA de
 * parametros do SQL (`queryReplacement`), na ordem declarada nos arquivos
 * `n8n/sql/*.sql`:
 *   $1 idempotency_key · $2 operation (event_type) · $3 entity_type ·
 *   $4 entity_id (uuid|NULL) · $5 source_version · $6 request_payload (jsonb) ·
 *   $7 status da trilha · $8 error_message
 */
function decidir(envelope, corpoBruto, contrato) {
    var validacao = validarEnvelope(envelope, contrato);
    var definicao = definicaoDoEvento(contrato, envelope && envelope.event_type);
    var identidade = identidadeCanonica(envelope && envelope.payload);
    var chave = validacao.ok || !ehVazio(envelope && envelope.idempotency_key)
        ? texto(envelope && envelope.idempotency_key)
        : chaveDeRecusa(corpoBruto);
    var corpo = typeof envelope === 'string' ? envelope : JSON.stringify(envelope);
    var parametros = [
        chave,
        texto(envelope && envelope.event_type),
        definicao ? definicao.modelo_origem : null,
        identidade.id,
        texto(envelope && envelope.event_version),
        corpo,
        validacao.ok ? 'COMPLETED' : 'REFUSED',
        validacao.ok ? null : validacao.motivo
    ];
    return {
        aceito: validacao.ok,
        motivo: validacao.motivo,
        codigo_http: validacao.ok ? 200 : 422,
        idempotency_key: chave,
        event_type: validacao.ok ? texto(envelope.event_type) : null,
        entity_type: definicao ? definicao.modelo_origem : null,
        entity_id: identidade.id,
        source_version: texto(envelope && envelope.event_version),
        parametros: parametros
    };
}

/* O n8n injeta este arquivo dentro do Code node; o aceite roda o MESMO arquivo no
 * node puro. `module` so' existe no segundo caso. */
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        NUCLEO_VERSAO: NUCLEO_VERSAO,
        ehVazio: ehVazio,
        texto: texto,
        ehObjeto: ehObjeto,
        ehUuid: ehUuid,
        fnv1a: fnv1a,
        definicaoDoEvento: definicaoDoEvento,
        chaveDeRecusa: chaveDeRecusa,
        identidadeCanonica: identidadeCanonica,
        validarEnvelope: validarEnvelope,
        decidir: decidir
    };
}
