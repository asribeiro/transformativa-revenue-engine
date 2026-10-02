# -*- coding: utf-8 -*-
"""Fila de saida (outbox) dos eventos **Odoo -> PostgreSQL** — card TRE-W3-E03-T01.

O QUE ESTE ARQUIVO ENTREGA (e a fonte de cada parte):

O Data Contract V1.0 publica, em `docs/data/data_contract_v1.json` -> `events.odoo_to_pg`, a lista
FECHADA dos sete eventos que o Odoo publica para o PostgreSQL:

    STAGE_CHANGED, ACTIVITY_COMPLETED, MEETING_CREATED, OPPORTUNITY_WON, OPPORTUNITY_LOST,
    DEAL_VALUE_CHANGED, LOSS_REASON_RECORDED

E o doc 06 §7-8 (resumido no contrato §6) exige de TODA integracao: `idempotency_key`, correlacao,
retry limitado, registro em `sync_events` e estado de erro (dead-letter) — com o envelope unico
(`event_type`, `event_version`, `timestamp`, `payload`) e a regra de que **evento sem
`event_version` e' recusado**.

Este arquivo implementa o lado ODOO dessa integracao: o FATO de negocio vira uma linha desta fila
(na MESMA transacao do fato: o evento nao pode existir sem o fato nem o fato sem o evento) e um
remetente entrega a fila pela PORTA UNICA (`POST <base>/webhook/tre/odoo-eventos`, o webhook do
n8n), classificando a resposta em `SENT` / `RETRY` / `DEAD_LETTER`.

DECISOES DECLARADAS (medidas, nao presumidas):

  1. **Nada de conexao direta com o PostgreSQL.** O modulo NAO importa driver de banco, NAO
     conhece o schema `sales_intelligence` e NAO escreve SQL: quem escreve no `sales_intelligence`
     e' o n8n (a mesma regra do doc 02 §3 que vale no sentido contrario, conferida pela lente
     estrutural). A unica saida e' HTTP, por um unico caminho.
  2. **Idempotencia por CONTEUDO do fato**, nao por token aleatorio: a chave e'
     `odoo:<event_type>:<modelo>:<res_id>:<sha1 do envelope SEM o instante>`. Reenvio do MESMO fato
     (retry, replay do remetente, entrega duplicada da porta) reusa a chave e nao cria segunda
     linha — nem aqui (UNIQUE) nem na trilha do PostgreSQL (UNIQUE em `sync_events`). Dois fatos
     DIFERENTES do mesmo tipo tem payload diferente e portanto chave diferente. LACUNA DECLARADA:
     dois fatos do mesmo tipo com payload identico (ex.: estagio A->B repetido no mesmo segundo)
     colapsam por desenho — e' o preco da chave derivada de conteudo, e esta' declarado no runbook.
  3. **Remetente fail-closed:** sem `ingest_url`/`ingest_token` configurados o remetente NAO
     inventa destino e NAO toca a fila (a fila fica como esta', visivel). Nenhum host, porta ou
     segredo no codigo versionado: os dois vem de `ir.config_parameter` (configuracao de ambiente).
  4. **A fila nunca derruba o negocio:** a deteccao escreve com `sudo()` (o usuario que muda o
     estagio nao precisa de ACL na fila) e o remetente isola a falha por evento — um evento que
     nao entrega nao impede os outros de sair.

O QUE ESTE ARQUIVO **NAO** FAZ (declarado, para nao vender mais do que entrega):

  * nao materializa o fato em tabela de negocio do PostgreSQL: o contrato V1 nao tem tabela de
    historico de funil e `sync_events` e' a trilha que o contrato da' ao PostgreSQL — o payload
    integral vai nela, entao a materializacao (ex.: `interactions`) e' derivavel por quem precisar;
  * nao reconcilia (comparar entidades esperadas, IDs cruzados, pendentes) — card `TRE-W3-E04-T01`;
  * nao observa/mede a latencia da sincronizacao — card `TRE-W3-E05-T01`;
  * nao deduplica a ENTREGA do lado do consumidor n8n PG->Odoo — card `TRE-W3-E02-T02`;
  * nao liga a agenda: o `ir.cron` deste modulo nasce **inativo** (nada nasce em producao, ADR-005);
    ligar e' decisao de operacao, registrada no runbook.
"""
import hashlib
import json
import logging

import urllib.error
import urllib.request

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Lista FECHADA do contrato (`events.odoo_to_pg`). Fonte unica aqui; o conferidor
# `scripts/odoo/conferir_eventos_no_contrato.py` compara esta tupla com o JSON congelado.
EVENTOS_ODOO_PARA_PG = (
    'STAGE_CHANGED',
    'ACTIVITY_COMPLETED',
    'MEETING_CREATED',
    'OPPORTUNITY_WON',
    'OPPORTUNITY_LOST',
    'DEAL_VALUE_CHANGED',
    'LOSS_REASON_RECORDED',
)

VERSAO_DO_EVENTO = '1.0'

# Caminho da PORTA UNICA (webhook do n8n). O que e' declarado aqui e' o CAMINHO; o host/base vem
# de `ir.config_parameter` — host literal no artefato versionado e' justamente o que a lente
# estrutural reprova.
CAMINHO_DO_EVENTO = '/webhook/tre/odoo-eventos'
PARAM_URL = 'transformativa_sales_ai.ingest_url'
PARAM_TOKEN = 'transformativa_sales_ai.ingest_token'
TIMEOUT_DE_ENVIO = 20

# Retry limitado do contrato: apos o teto o evento vira DEAD_LETTER (visivel), nunca fica girando.
TETO_DE_TENTATIVAS = 3

# Classificacao da resposta da porta (espelha `classificacao_http` do contrato do ingestor):
#   * 2xx                     -> SENT
#   * recusa NOMEADA (4xx)    -> DEAD_LETTER (retentar nao muda o veredito; o motivo e' guardado)
#   * resto (5xx, 408, 429)   -> RETRY, ate o teto
CODIGOS_TERMINAIS_DO_DESTINO = (400, 401, 403, 404, 405, 409, 410, 422)


def chave_de_idempotencia(event_type, odoo_model, odoo_res_id, payload):
    """Chave de idempotencia DERIVADA DO FATO (decisao 2 do cabecalho).

    O instante NAO entra no hash: reenvio do mesmo fato tem de reusar a chave. O que entra e' o
    conteudo que distingue um fato do outro (tipo, registro de origem e payload).
    """
    material = json.dumps(
        {
            'event_type': event_type,
            'event_version': VERSAO_DO_EVENTO,
            'odoo_model': odoo_model,
            'odoo_res_id': int(odoo_res_id or 0),
            'payload': payload,
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(',', ':'),
    )
    digest = hashlib.sha1(material.encode('utf-8')).hexdigest()
    return 'odoo:%s:%s:%s:%s' % (event_type, odoo_model, int(odoo_res_id or 0), digest)


def classificar_resposta(status_http):
    """`2xx` -> SENT · recusa nomeada do destino -> DEAD_LETTER · resto -> RETRY."""
    if 200 <= int(status_http) < 300:
        return 'SENT'
    if int(status_http) in CODIGOS_TERMINAIS_DO_DESTINO:
        return 'DEAD_LETTER'
    return 'RETRY'


def serializar(envelope):
    """JSON canonico do envelope — o MESMO texto que vai no corpo do POST (e no `envelope`)."""
    return json.dumps(envelope, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


class TfEventoOutbox(models.Model):
    """A fila de saida dos eventos Odoo -> PostgreSQL (uma linha por fato)."""

    _name = 'tf.evento.outbox'
    _description = 'Evento Odoo -> PostgreSQL (outbox do Sales AI)'
    _order = 'occurred_at, id'

    # O que impede retry de virar duplicata (contrato §3/§6). O UNIQUE e' a garantia DURA; a
    # busca-antes-de-criar em `_tf_emitir` e' so' o caminho barato para nao depender de excecao.
    _chave_de_idempotencia_unica = models.Constraint(
        'UNIQUE(idempotency_key)',
        'A chave de idempotencia do evento Odoo->PostgreSQL e unica por fato (contrato §3/§6).',
    )

    name = fields.Char(
        string='Evento',
        compute='_compute_name',
        store=True,
        help='Rotulo de leitura: `<event_type> em <modelo>#<id>`.',
    )
    event_type = fields.Selection(
        selection=tuple((valor, valor) for valor in EVENTOS_ODOO_PARA_PG),
        string='Tipo do evento',
        required=True,
        index=True,
        help='Um dos sete eventos de `events.odoo_to_pg` do contrato (secao 6). Tipo fora da lista '
             'nao tem como nascer: o campo e um Selection fechado, e o ingestor tambem recusa.',
    )
    event_version = fields.Char(
        string='Versao do evento',
        required=True,
        default=VERSAO_DO_EVENTO,
        help='Obrigatorio em todo evento (contrato §6). Evento sem versao e recusado pelo ingestor '
             '— nao se interpreta versao por suposicao.',
    )
    occurred_at = fields.Datetime(
        string='Quando o fato aconteceu',
        required=True,
        default=fields.Datetime.now,
        index=True,
        help='Instante do FATO em Odoo (nao o instante do envio). E o `timestamp` do envelope.',
    )
    odoo_model = fields.Char(string='Modelo de origem', required=True, index=True)
    odoo_res_id = fields.Integer(string='Registro de origem', required=True, index=True)
    entidade_canonica_tipo = fields.Selection(
        selection=[
            ('oportunidade', 'Oportunidade (crm.lead.tf_opportunity_id)'),
            ('organizacao', 'Organizacao (res.partner.tf_company_id)'),
            ('vazia', 'Nao resolvida no lado Odoo'),
        ],
        string='Tipo do ID canonico',
        default='vazia',
        help='Qual ID canonico do contrato (§3) acompanha o fato. `vazia` e uma resposta legitima: '
             'nem todo lead tem UUID canonico preenchido — o evento sai com a identidade de Odoo '
             'declarada e a lacuna fica visivel (nao se inventa UUID).',
    )
    entidade_canonica_id = fields.Char(
        string='ID canonico',
        index=True,
        help='UUID canonico do contrato (§3) que acompanha o fato, quando existir. E o `entity_id` '
             'da trilha `sync_events` no PostgreSQL — nunca substitui o id interno do Odoo.',
    )
    correlation_id = fields.Char(
        string='Correlacao',
        index=True,
        help='Correlacao da integracao (contrato §3/§6). Quando o fato nasceu de uma chamada da API '
             'controlada, e o `tf_correlation_id` do documento; quando nasceu na interface do Odoo, '
             'e derivada do proprio fato (`odoo-ui:<modelo>:<id>:<write_date>`).',
    )
    payload = fields.Json(
        string='Payload',
        required=True,
        help='Os valores do fato (antes/depois, ids, contexto). Vai integral no envelope.',
    )
    envelope = fields.Text(
        string='Envelope enviado',
        required=True,
        help='JSON canonico do envelope (contrato §6: event_type, event_version, timestamp, payload '
             '+ idempotency_key/correlation_id das regras de integracao). E o corpo exato do POST.',
    )
    idempotency_key = fields.Char(string='Chave de idempotencia', required=True, index=True)
    status = fields.Selection(
        selection=[
            ('PENDING', 'Pendente'),
            ('RETRY', 'Retentar'),
            ('SENT', 'Entregue'),
            ('DEAD_LETTER', 'Dead-letter'),
        ],
        string='Estado',
        default='PENDING',
        required=True,
        index=True,
    )
    attempts = fields.Integer(
        string='Tentativas',
        default=0,
        help='Contadas a cada tentativa de entrega (a bem-sucedida conta). No teto (%s) o evento '
             'vira DEAD_LETTER.' % TETO_DE_TENTATIVAS,
    )
    http_status = fields.Char(string='HTTP da porta')
    last_error = fields.Text(
        string='Motivo do ultimo erro',
        help='Motivo nomeado, visivel: recusa do destino ou falha de transporte. Falha nao e '
             'engolida (contrato §6 regra 3).',
    )
    response_summary = fields.Char(
        string='Resposta da porta (resumo)',
        help='Trecho limitado da resposta do ingestor (sem payload de volta).',
    )
    duplicado_no_destino = fields.Boolean(
        string='Duplicado no destino',
        help='A porta respondeu que a chave JA existia na trilha do PostgreSQL: a entrega foi '
             'idempotente (retry nao criou segunda linha).',
    )
    sent_at = fields.Datetime(string='Entregue em')

    @api.depends('event_type', 'odoo_model', 'odoo_res_id')
    def _compute_name(self):
        for evento in self:
            evento.name = '%s em %s#%s' % (
                evento.event_type or '?',
                evento.odoo_model or '?',
                evento.odoo_res_id or 0,
            )

    # ------------------------------------------------------------------ emissao do fato
    @api.model
    def _tf_emitir(
        self,
        event_type,
        odoo_model,
        odoo_res_id,
        payload,
        occurred_at=None,
        correlation_id=False,
        entidade_canonica_id=False,
        entidade_canonica_tipo='vazia',
    ):
        """Grava o FATO na fila. Idempotente por conteudo: mesmo fato nao vira duas linhas.

        `sudo()` e' deliberado e declarado (decisao 4): o usuario que mudou o estagio do lead nao
        tem — e nao precisa ter — acesso a esta fila; sem o sudo, mudar o negocio quebraria no
        controle de acesso da fila.
        """
        if event_type not in EVENTOS_ODOO_PARA_PG:
            raise ValueError(
                'evento %r nao pertence a events.odoo_to_pg do contrato — a fila nao aceita evento '
                'fora da lista fechada' % (event_type,)
            )
        ocorrido = occurred_at or fields.Datetime.now()
        chave = chave_de_idempotencia(event_type, odoo_model, odoo_res_id, payload)
        existente = self.sudo().search([('idempotency_key', '=', chave)], limit=1)
        if existente:
            _logger.info(
                'TRE outbox: fato ja emitido (mesma chave de conteudo), nada criado: %s', chave
            )
            return existente
        envelope = {
            'event_type': event_type,
            'event_version': VERSAO_DO_EVENTO,
            'timestamp': fields.Datetime.to_string(ocorrido),
            'idempotency_key': chave,
            'correlation_id': correlation_id or '',
            'payload': payload,
        }
        valores = {
            'event_type': event_type,
            'event_version': VERSAO_DO_EVENTO,
            'occurred_at': ocorrido,
            'odoo_model': odoo_model,
            'odoo_res_id': int(odoo_res_id),
            'entidade_canonica_id': entidade_canonica_id or False,
            'entidade_canonica_tipo': entidade_canonica_tipo if entidade_canonica_id else 'vazia',
            'correlation_id': correlation_id or False,
            'payload': payload,
            'envelope': serializar(envelope),
            'idempotency_key': chave,
            'status': 'PENDING',
        }
        # savepoint: se a chave ja existir (corrida entre duas transacoes), a insercao morre AQUI
        # sem envenenar a transacao do fato de negocio que esta' sendo gravado.
        try:
            with self.env.cr.savepoint():
                return self.sudo().create(valores)
        except Exception as erro:  # noqa: BLE001 - chave duplicada em corrida
            _logger.warning('TRE outbox: corrida na chave %s (%s)', chave, erro)
            return self.sudo().search([('idempotency_key', '=', chave)], limit=1)

    # ------------------------------------------------------------------ configuracao da porta
    @api.model
    def _tf_configuracao_da_porta(self):
        parametros = self.env['ir.config_parameter'].sudo()
        return {
            'url': (parametros.get_param(PARAM_URL) or '').strip(),
            'token': (parametros.get_param(PARAM_TOKEN) or '').strip(),
        }

    # ------------------------------------------------------------------ envio
    @api.model
    def _tf_enviar_pendentes(self, limite=50):
        """Entrega a fila pela porta unica. Nao levanta: devolve o resumo do ciclo.

        Falha de um evento NAO impede os outros (decisao 4) e nenhum evento e' apagado: o estado
        da fila e' o proprio registro de erro (contrato §6 regra 3).
        """
        resumo = {
            'na_fila': 0,
            'SENT': 0,
            'RETRY': 0,
            'DEAD_LETTER': 0,
            'duplicados_no_destino': 0,
            'erro': False,
        }
        fila = self.sudo().search(
            [('status', 'in', ('PENDING', 'RETRY'))], order='occurred_at, id', limit=int(limite)
        )
        resumo['na_fila'] = len(fila)
        if not fila:
            return resumo
        porta = self._tf_configuracao_da_porta()
        if not porta['url'] or not porta['token']:
            # Fail-closed: sem destino declarado nada sai. Nao e' erro de evento — e' ambiente
            # incompleto, e o resumo diz exatamente isso.
            resumo['erro'] = 'porta_nao_configurada'
            _logger.warning(
                'TRE outbox: %s sem %s/%s — fila intacta (%s eventos)',
                'porta nao configurada',
                PARAM_URL,
                PARAM_TOKEN,
                len(fila),
            )
            return resumo
        for evento in fila:
            estado = evento._tf_enviar_um(porta['url'], porta['token'])
            resumo[estado] = resumo.get(estado, 0) + 1
            if estado == 'SENT' and evento.duplicado_no_destino:
                resumo['duplicados_no_destino'] += 1
        return resumo

    def _tf_enviar_um(self, url, token):
        """Uma tentativa de entrega. Devolve o estado final do evento."""
        self.ensure_one()
        destino = '%s%s' % (url.rstrip('/'), CAMINHO_DO_EVENTO)
        pedido = urllib.request.Request(
            destino,
            data=self.envelope.encode('utf-8'),
            method='POST',
            headers={
                'Content-Type': 'application/json',
                # Token da porta: vem de `ir.config_parameter` (nunca do artefato versionado).
                'X-Tre-Ingest-Token': token,
                'X-Tre-Event-Key': self.idempotency_key,
            },
        )
        tentativas = self.attempts + 1
        try:
            with urllib.request.urlopen(pedido, timeout=TIMEOUT_DE_ENVIO) as resposta:
                self._tf_registrar_resposta(resposta.status, resposta.read(2000), tentativas)
        except urllib.error.HTTPError as erro:
            self._tf_registrar_resposta(erro.code, erro.read(2000), tentativas)
        except Exception as erro:  # noqa: BLE001 - transporte (DNS, recusa de conexao, timeout)
            self._tf_registrar_transporte(erro, tentativas)
        return self.status

    def _tf_registrar_resposta(self, status_http, corpo, tentativas):
        self.ensure_one()
        estado = classificar_resposta(status_http)
        texto = (corpo or b'')
        if isinstance(texto, bytes):
            texto = texto.decode('utf-8', 'replace')
        motivo = ''
        duplicado = False
        try:
            resposta = json.loads(texto) if texto else {}
            if isinstance(resposta, dict):
                motivo = str(resposta.get('motivo') or '')
                duplicado = bool(resposta.get('duplicado'))
        except ValueError:
            motivo = ''
        valores = {
            'attempts': tentativas,
            'http_status': str(status_http),
            'response_summary': texto[:500],
            'duplicado_no_destino': duplicado,
        }
        if estado == 'SENT':
            valores.update({'status': 'SENT', 'sent_at': fields.Datetime.now(), 'last_error': False})
        elif estado == 'DEAD_LETTER':
            valores.update({
                'status': 'DEAD_LETTER',
                'last_error': 'recusa nomeada do destino (HTTP %s): %s'
                % (status_http, motivo or texto[:200] or 'sem motivo declarado'),
            })
        else:
            valores.update(self._tf_estado_de_retentativa(tentativas, 'HTTP %s' % status_http))
        self.sudo().write(valores)

    def _tf_registrar_transporte(self, erro, tentativas):
        self.ensure_one()
        self.sudo().write(dict(
            {'attempts': tentativas, 'http_status': False, 'response_summary': False},
            **self._tf_estado_de_retentativa(tentativas, 'transporte: %s' % erro)
        ))

    def _tf_estado_de_retentativa(self, tentativas, motivo):
        """Abaixo do teto: RETRY. No teto: DEAD_LETTER com o motivo (visivel, nao engolido)."""
        if tentativas >= TETO_DE_TENTATIVAS:
            return {
                'status': 'DEAD_LETTER',
                'last_error': 'teto de %s tentativas atingido (%s)' % (TETO_DE_TENTATIVAS, motivo),
            }
        return {'status': 'RETRY', 'last_error': motivo}

    # ------------------------------------------------------------------ operacao
    def action_tf_enviar_agora(self):
        """Botao/acao manual de operacao: entrega a fila e devolve o resumo do ciclo."""
        resumo = self.env['tf.evento.outbox']._tf_enviar_pendentes()
        _logger.info('TRE outbox: ciclo manual -> %s', resumo)
        return True

    def action_tf_reenfileirar(self):
        """Devolve eventos DEAD_LETTER para a fila (decisao consciente de operacao)."""
        alvos = self.filtered(lambda evento: evento.status == 'DEAD_LETTER')
        alvos.sudo().write({'status': 'RETRY', 'attempts': 0, 'last_error': False})
        return True
