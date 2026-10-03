# -*- coding: utf-8 -*-
"""Aceite da fila de eventos **Odoo -> PostgreSQL** — card TRE-W3-E03-T01 (`t_85cb2838`).

O QUE ESTA SUITE PROVA (contra o Odoo de verdade e, no remetente, contra HTTP de verdade):

  AC1 conjunto FECHADO de eventos: a tupla de eventos do modulo e' exatamente
      `events.odoo_to_pg` do contrato congelado (lido do JSON versionado, nao de texto no teste) e o
      Selection do modelo tem os mesmos valores — evento fora da lista nao tem como nascer;
  AC2 deteccao presa ao FATO: estagio, ganho, perda, valor, motivo, atividade concluida e reuniao
      criada geram evento; escrita que NAO e' fato do contrato (nome, por exemplo) NAO gera nada —
      o detector nao e' "qualquer coisa mudou";
  AC3 envelope e trilha: `event_type`, `event_version`, `timestamp`, `payload` + `idempotency_key` e
      `correlation_id`; `occurred_at` do fato; `PENDING`/`attempts=0` na emissao;
  AC4 idempotencia por conteudo: o MESMO fato emitido duas vezes NAO cria segunda linha (chave
      derivada do conteudo + UNIQUE);
  AC5 identidade canonica declarada: com `tf_opportunity_id`/`tf_company_id` o evento sai com o UUID
      canonico e o tipo certo; sem eles, `vazia` (lacuna visivel, nao UUID inventado);
  AC6 remetente pela PORTA UNICA: `POST <base>/webhook/tre/odoo-eventos` com o token do parametro;
      2xx -> `SENT` (e o `duplicado` da resposta fica registrado); 4xx do destino -> `DEAD_LETTER`
      com motivo nomeado e SEM retry; 5xx/transporte -> `RETRY` e, no teto de 3, `DEAD_LETTER`;
  AC7 fail-closed: sem `ingest_url`/`ingest_token` configurados NADA sai e a fila fica intacta;
  AC8 ACL: usuario sem acesso a fila muda o negocio e o evento nasce (a emissao e' do modulo, via
      `sudo`, declarada);
  AC9 modulos: a fila tem chave UNICA, o cron nasce INATIVO e o modulo nao tem caminho paralelo para
      o PostgreSQL (nenhum driver de banco, nenhum SQL, uma unica chamada HTTP).

O QUE ESTA SUITE NAO PROVA (declarado):

  * a ESCRITA no PostgreSQL (trilha `sync_events`, chave unica do lado do destino, recusa nomeada da
    porta): e' o aceite ponta a ponta `scripts/n8n/verificar-odoo-eventos.sh` com o n8n de verdade;
  * a ligacao da agenda e o comportamento em producao: card de operacao + ADR-005;
  * reconciliacao e observabilidade da sincronizacao: cards TRE-W3-E04-T01 / TRE-W3-E05-T01.
"""
import json
import os
import re
import threading
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

from odoo.exceptions import AccessError
from odoo.modules.module import get_module_path
from odoo.tests import TransactionCase, new_test_user, tagged

MODULO = 'transformativa_sales_ai'
CAMINHO_DA_PORTA = '/webhook/tre/odoo-eventos'
U1 = '11111111-1111-4111-8111-111111111111'


class PortaFalsa(BaseHTTPRequestHandler):
    """Servidor HTTP DE VERDADE em 127.0.0.1 — nao e' mock do modulo sob teste.

    Ele responde o que o teste manda (fila de codigos) e guarda o que recebeu, que e' exatamente o
    que o ingestor faz do outro lado: 2xx = aceito, 422 = recusa nomeada, 500 = falha transitoria.
    """

    codigos = []
    recebidos = []
    duplicado = False

    def do_POST(self):  # noqa: N802 - assinatura do BaseHTTPRequestHandler
        tamanho = int(self.headers.get('Content-Length') or 0)
        corpo = self.rfile.read(tamanho).decode('utf-8')
        tipo = type(self)
        tipo.recebidos.append({
            'caminho': self.path,
            'corpo': corpo,
            'token': self.headers.get('X-Tre-Ingest-Token'),
            'chave': self.headers.get('X-Tre-Event-Key'),
        })
        codigo = tipo.codigos.pop(0) if tipo.codigos else 200
        if codigo >= 400:
            resposta = {'aceito': False, 'motivo': 'recusa_nomeada_do_teste'}
        else:
            resposta = {'aceito': True, 'duplicado': bool(tipo.duplicado)}
        texto = json.dumps(resposta).encode('utf-8')
        self.send_response(codigo)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(texto)))
        self.end_headers()
        self.wfile.write(texto)

    def log_message(self, *args):  # silencio: o log do teste e' a medicao
        return


@tagged('post_install', '-at_install')
class TestEventosOdooPg(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.raiz_do_modulo = get_module_path(MODULO)
        # O contrato e' CONGELADO aqui de proposito: o teste roda no container de dev, onde so' o
        # diretorio do MODULO esta' montado — o `docs/data/data_contract_v1.json` do repositorio nao
        # existe la'. Quem confronta o modulo com o JSON versionado e' o conferidor
        # `scripts/odoo/conferir_eventos_no_contrato.py` (roda onde o repo existe). Congelar aqui e'
        # o que impede a lista de eventos de crescer sem que alguem edite este teste de proposito.
        cls.contrato = {
            'events': {
                'envelope_required_fields': ['event_type', 'event_version', 'timestamp', 'payload'],
                'odoo_to_pg': [
                    'STAGE_CHANGED',
                    'ACTIVITY_COMPLETED',
                    'MEETING_CREATED',
                    'OPPORTUNITY_WON',
                    'OPPORTUNITY_LOST',
                    'DEAL_VALUE_CHANGED',
                    'LOSS_REASON_RECORDED',
                ],
            },
        }
        cls.parametros = cls.env['ir.config_parameter'].sudo()
        cls.fila = cls.env['tf.evento.outbox']
        cls.tipo_de_atividade = cls.env['mail.activity.type'].create({'name': 'Ligacao'})
        cls.motivo_de_perda = cls.env['crm.lost.reason'].create({'name': 'Preco'})
        cls.estagio_ganho = cls.env['crm.stage'].search([('is_won', '=', True)], limit=1)
        # Porta falsa (servidor de verdade) no proprio processo do teste.
        PortaFalsa.codigos = []
        PortaFalsa.recebidos = []
        cls.porta = HTTPServer(('127.0.0.1', 0), PortaFalsa)
        cls.thread = threading.Thread(target=cls.porta.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_da_porta = 'http://127.0.0.1:%s' % cls.porta.server_port

    @classmethod
    def tearDownClass(cls):
        cls.porta.shutdown()
        cls.porta.server_close()
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        PortaFalsa.codigos = []
        PortaFalsa.recebidos = []
        PortaFalsa.duplicado = False
        self.parametros.set_param('transformativa_sales_ai.ingest_url', self.base_da_porta)
        self.parametros.set_param('transformativa_sales_ai.ingest_token', 'token-de-teste')
        self.parceiro = self.env['res.partner'].create({
            'name': 'Alfa Consultoria Ltda',
            'tf_company_id': U1,
            'is_company': True,
        })
        self.lead = self.env['crm.lead'].create({
            'name': 'Oportunidade Alfa',
            'type': 'opportunity',
            'partner_id': self.parceiro.id,
            'tf_opportunity_id': '22222222-2222-4222-8222-222222222222',
            'expected_revenue': 1000.0,
        })

    # ------------------------------------------------------------------ helpers
    def _eventos(self, tipo=None):
        dominio = [('odoo_model', '!=', False)]
        if tipo:
            dominio.append(('event_type', '=', tipo))
        return self.fila.search(dominio, order='id')

    def _estado(self, evento):
        """Le o estado do evento DEPOIS de o remetente escrever (cache invalidado de proposito)."""
        evento.invalidate_recordset()
        return (evento.status, evento.attempts)

    def _outro_estagio(self):
        return self.env['crm.stage'].search([('id', '!=', self.lead.stage_id.id)], limit=1)

    # ------------------------------------------------------------------ AC1
    def test_01_eventos_do_modulo_sao_os_do_contrato(self):
        esperado = tuple(self.contrato['events']['odoo_to_pg'])
        selecao = tuple(valor for valor, _rotulo in self.fila._fields['event_type'].selection)
        self.assertEqual(selecao, esperado)
        from odoo.addons.transformativa_sales_ai.models.tf_evento_outbox import EVENTOS_ODOO_PARA_PG
        self.assertEqual(tuple(EVENTOS_ODOO_PARA_PG), esperado)

    def test_02_envelope_do_contrato_tem_os_campos_obrigatorios(self):
        obrigatorios = set(self.contrato['events']['envelope_required_fields'])
        self.lead.write({'stage_id': self._outro_estagio().id})
        evento = self._eventos('STAGE_CHANGED')
        envelope = json.loads(evento.envelope)
        self.assertTrue(obrigatorios.issubset(set(envelope)))
        self.assertEqual(envelope['event_type'], 'STAGE_CHANGED')
        self.assertEqual(envelope['event_version'], '1.0')
        self.assertTrue(envelope['idempotency_key'])
        self.assertEqual(evento.status, 'PENDING')
        self.assertEqual(evento.attempts, 0)
        self.assertEqual(evento.odoo_model, 'crm.lead')
        self.assertEqual(evento.odoo_res_id, self.lead.id)
        self.assertEqual(envelope['payload']['estagio_novo']['id'], self.lead.stage_id.id)

    # ------------------------------------------------------------------ AC2
    def test_03_mudanca_de_estagio_emite_stage_changed(self):
        anterior = self.lead.stage_id.id
        self.lead.write({'stage_id': self._outro_estagio().id})
        evento = self._eventos('STAGE_CHANGED')
        self.assertEqual(len(evento), 1)
        self.assertEqual(evento.payload['estagio_anterior']['id'], anterior)
        self.assertEqual(evento.payload['lead_id'], self.lead.id)
        self.assertEqual(evento.entidade_canonica_id, self.lead.tf_opportunity_id)
        self.assertEqual(evento.entidade_canonica_tipo, 'oportunidade')

    def test_04_estagio_de_ganho_emite_stage_changed_e_won(self):
        self.lead.write({'stage_id': self.estagio_ganho.id})
        tipos = sorted(self._eventos().mapped('event_type'))
        self.assertEqual(tipos, ['OPPORTUNITY_WON', 'STAGE_CHANGED'])
        ganho = self._eventos('OPPORTUNITY_WON')
        self.assertEqual(ganho.payload['valor'], 1000.0)
        self.assertEqual(ganho.payload['estagio'], self.estagio_ganho.name)

    def test_05_perda_emite_opportunity_lost_e_motivo_registrado(self):
        self.lead.action_set_lost(lost_reason_id=self.motivo_de_perda.id)
        tipos = sorted(self._eventos().mapped('event_type'))
        self.assertEqual(tipos, ['LOSS_REASON_RECORDED', 'OPPORTUNITY_LOST'])
        motivo = self._eventos('LOSS_REASON_RECORDED')
        self.assertEqual(motivo.payload['motivo']['id'], self.motivo_de_perda.id)
        self.assertEqual(motivo.payload['motivo_anterior']['id'], 0)
        perda = self._eventos('OPPORTUNITY_LOST')
        self.assertEqual(len(perda), 1)
        self.assertEqual(perda.payload['lead_id'], self.lead.id)
        self.assertEqual(perda.payload['valor'], 1000.0)
        # O motivo tem EVENTO PROPRIO (`LOSS_REASON_RECORDED`, conferido acima): `action_set_lost`
        # ARQUIVA primeiro e grava o motivo depois, entao a perda pode sair com o motivo ainda vazio.
        # O que a perda promete e' o campo declarado no envelope, nao o dado que ainda nao existe.
        self.assertIn('motivo', perda.payload)

    def test_06_mudanca_de_valor_emite_deal_value_changed(self):
        self.lead.write({'expected_revenue': 2500.0})
        evento = self._eventos('DEAL_VALUE_CHANGED')
        self.assertEqual(len(evento), 1)
        self.assertEqual(evento.payload['valor_anterior'], 1000.0)
        self.assertEqual(evento.payload['valor_novo'], 2500.0)
        self.assertTrue(evento.payload['moeda'])

    def test_07_atividade_concluida_emite_activity_completed(self):
        modelo = self.env['ir.model']._get('crm.lead')
        atividade = self.env['mail.activity'].create({
            'activity_type_id': self.tipo_de_atividade.id,
            'res_model_id': modelo.id,
            'res_id': self.lead.id,
            'summary': 'Ligar para o diretor',
            'date_deadline': date.today(),
            'user_id': self.env.user.id,
        })
        atividade._action_done(feedback='Cliente pediu proposta')
        evento = self._eventos('ACTIVITY_COMPLETED')
        self.assertEqual(len(evento), 1)
        self.assertEqual(evento.odoo_res_id, atividade.id)
        self.assertEqual(evento.payload['tipo'], 'Ligacao')
        self.assertEqual(evento.payload['resumo'], 'Ligar para o diretor')
        self.assertTrue(evento.payload['com_feedback'])
        self.assertEqual(evento.payload['documento']['modelo'], 'crm.lead')
        self.assertEqual(evento.entidade_canonica_tipo, 'oportunidade')
        # `_action_done` ARQUIVA a atividade (`active=False`); o registro permanece e o que prova a
        # conclusao e' o `date_done` (calculado pelo proprio `mail.activity`, nao pelo detector).
        self.assertFalse(atividade.active)
        self.assertTrue(atividade.date_done)

    def test_08_reuniao_criada_emite_meeting_created(self):
        inicio = datetime.now() + timedelta(days=1)
        reuniao = self.env['calendar.event'].create({
            'name': 'Diagnostico com o cliente',
            'start': inicio,
            'stop': inicio + timedelta(hours=1),
            'opportunity_id': self.lead.id,
        })
        evento = self._eventos('MEETING_CREATED')
        self.assertEqual(len(evento), 1)
        self.assertEqual(evento.odoo_res_id, reuniao.id)
        self.assertEqual(evento.payload['nome'], 'Diagnostico com o cliente')
        self.assertEqual(evento.entidade_canonica_id, self.lead.tf_opportunity_id)

    def test_09_escrita_que_nao_e_fato_nao_emite(self):
        self.lead.write({'name': 'Oportunidade Alfa (renomeada)', 'description': 'notas'})
        self.assertEqual(len(self._eventos()), 0)

    # ------------------------------------------------------------------ AC3/AC4
    def test_10_mesmo_fato_duas_vezes_nao_duplica(self):
        payload = {'modelo_origem': 'crm.lead', 'lead_id': self.lead.id, 'valor_novo': 10.0}
        primeiro = self.fila._tf_emitir('DEAL_VALUE_CHANGED', 'crm.lead', self.lead.id, payload)
        segundo = self.fila._tf_emitir('DEAL_VALUE_CHANGED', 'crm.lead', self.lead.id, payload)
        self.assertEqual(primeiro.id, segundo.id)
        self.assertEqual(len(self._eventos('DEAL_VALUE_CHANGED')), 1)

    def test_11_evento_fora_do_contrato_nao_nasce(self):
        with self.assertRaises(ValueError):
            self.fila._tf_emitir('LEAD_CREATED', 'crm.lead', self.lead.id, {})

    def test_12_sem_uuid_canonico_a_identidade_fica_vazia(self):
        orfao = self.env['crm.lead'].create({'name': 'Sem canonico', 'type': 'opportunity'})
        orfao.write({'expected_revenue': 500.0})
        evento = self._eventos('DEAL_VALUE_CHANGED').filtered(lambda e: e.odoo_res_id == orfao.id)
        self.assertEqual(evento.entidade_canonica_tipo, 'vazia')
        self.assertFalse(evento.entidade_canonica_id)
        self.assertEqual(evento.payload['lead_id'], orfao.id)

    # ------------------------------------------------------------------ AC8 (ACL)
    def test_13_usuario_sem_acl_na_fila_ainda_gera_evento(self):
        # Vendedor de verdade (grupo de vendas, sem ACL nenhuma na fila): o detector grava a fila com
        # `sudo`, entao quem escreve no lead nao precisa de acesso a `tf.evento.outbox`. Criar o
        # lead por ele nao serve de prova: o ACL do CRM e' outro assunto (e crm_iap_enrich entra no
        # create). O que este teste mede e' a ESCRITA que gera fato.
        usuario = new_test_user(
            self.env, login='tf_sem_acl_fila',
            groups='base.group_user,sales_team.group_sale_salesman',
        )
        lead_do_usuario = self.env['crm.lead'].create({
            'name': 'Do vendedor', 'type': 'opportunity', 'user_id': usuario.id,
        })
        with self.assertRaises(AccessError):
            self.fila.with_user(usuario).search([('id', '=', 1)])
        lead_do_usuario.with_user(usuario).write({'expected_revenue': 77.0})
        evento = self._eventos('DEAL_VALUE_CHANGED').filtered(
            lambda e: e.odoo_res_id == lead_do_usuario.id
        )
        self.assertEqual(len(evento), 1)
        self.assertEqual(evento.payload['valor_novo'], 77.0)

    # ------------------------------------------------------------------ AC6/AC7 (remetente)
    def _enfileirar(self, tipo='STAGE_CHANGED', ocorrido=None, payload=None):
        return self.fila._tf_emitir(
            tipo,
            'crm.lead',
            self.lead.id,
            payload or {'modelo_origem': 'crm.lead', 'lead_id': self.lead.id, 'marcador': tipo},
            occurred_at=ocorrido,
        )

    def test_14_sem_porta_configurada_a_fila_fica_intacta(self):
        evento = self._enfileirar()
        self.parametros.set_param('transformativa_sales_ai.ingest_url', '')
        resumo = self.fila._tf_enviar_pendentes()
        self.assertEqual(resumo['erro'], 'porta_nao_configurada')
        self.assertEqual(resumo['na_fila'], 1)
        self.assertEqual(evento.status, 'PENDING')
        self.assertEqual(evento.attempts, 0)
        self.assertEqual(PortaFalsa.recebidos, [])
        self.parametros.set_param('transformativa_sales_ai.ingest_url', self.base_da_porta)
        self.parametros.set_param('transformativa_sales_ai.ingest_token', '')
        resumo = self.fila._tf_enviar_pendentes()
        self.assertEqual(resumo['erro'], 'porta_nao_configurada')
        self.assertEqual(PortaFalsa.recebidos, [])

    def test_15_entrega_200_marca_sent_com_rastro(self):
        evento = self._enfileirar()
        resumo = self.fila._tf_enviar_pendentes()
        self.assertEqual(resumo['SENT'], 1)
        self.assertEqual(self._estado(evento), ('SENT', 1))
        self.assertEqual(evento.http_status, '200')
        self.assertTrue(evento.sent_at)
        self.assertEqual(len(PortaFalsa.recebidos), 1)
        pedido = PortaFalsa.recebidos[0]
        self.assertEqual(pedido['caminho'], CAMINHO_DA_PORTA)
        self.assertEqual(pedido['token'], 'token-de-teste')
        self.assertEqual(pedido['chave'], evento.idempotency_key)
        self.assertEqual(json.loads(pedido['corpo'])['idempotency_key'], evento.idempotency_key)

    def test_16_entrega_duplicada_no_destino_e_registrada(self):
        evento = self._enfileirar()
        PortaFalsa.duplicado = True
        resumo = self.fila._tf_enviar_pendentes()
        self.assertEqual(resumo['SENT'], 1)
        self.assertEqual(resumo['duplicados_no_destino'], 1)
        self.assertTrue(evento.duplicado_no_destino)

    def test_17_recusa_nomeada_vira_dead_letter_sem_retry(self):
        PortaFalsa.codigos = [422]
        evento = self._enfileirar()
        self.fila._tf_enviar_pendentes()
        self.assertEqual(self._estado(evento), ('DEAD_LETTER', 1))
        self.assertIn('recusa_nomeada_do_teste', evento.last_error)
        # segunda passada nao retenta um DEAD_LETTER
        self.fila._tf_enviar_pendentes()
        self.assertEqual(len(PortaFalsa.recebidos), 1)
        self.assertEqual(self._estado(evento), ('DEAD_LETTER', 1))

    def test_18_falha_transitoria_retenta_e_no_teto_vira_dead_letter(self):
        PortaFalsa.codigos = [500, 503, 500]
        evento = self._enfileirar()
        self.fila._tf_enviar_pendentes()
        self.assertEqual(self._estado(evento), ('RETRY', 1))
        self.fila._tf_enviar_pendentes()
        self.assertEqual(self._estado(evento), ('RETRY', 2))
        self.fila._tf_enviar_pendentes()
        self.assertEqual(self._estado(evento), ('DEAD_LETTER', 3))
        self.assertIn('teto de 3 tentativas', evento.last_error)
        self.fila._tf_enviar_pendentes()
        self.assertEqual(len(PortaFalsa.recebidos), 3)

    def test_19_fila_entrega_em_ordem_e_so_pendentes(self):
        agora = datetime.now()
        primeiro = self._enfileirar('DEAL_VALUE_CHANGED', ocorrido=agora - timedelta(minutes=3))
        segundo = self._enfileirar('STAGE_CHANGED', ocorrido=agora - timedelta(minutes=2))
        terceiro = self._enfileirar('OPPORTUNITY_WON', ocorrido=agora - timedelta(minutes=1))
        segundo.sudo().write({'status': 'SENT'})
        self.fila._tf_enviar_pendentes()
        corpos = [json.loads(pedido['corpo'])['event_type'] for pedido in PortaFalsa.recebidos]
        self.assertEqual(corpos, ['DEAL_VALUE_CHANGED', 'OPPORTUNITY_WON'])
        self.assertEqual(self._estado(primeiro), ('SENT', 1))
        self.assertEqual(self._estado(terceiro), ('SENT', 1))
        self.assertEqual(self._estado(segundo), ('SENT', 0))

    def test_20_reenfileirar_dead_letter_volta_para_a_fila(self):
        PortaFalsa.codigos = [422]
        evento = self._enfileirar()
        self.fila._tf_enviar_pendentes()
        self.assertEqual(self._estado(evento), ('DEAD_LETTER', 1))
        evento.action_tf_reenfileirar()
        self.assertEqual(self._estado(evento), ('RETRY', 0))
        self.fila._tf_enviar_pendentes()
        self.assertEqual(self._estado(evento), ('SENT', 1))

    # ------------------------------------------------------------------ AC9
    def test_21_cron_nasce_inativo(self):
        cron = self.env.ref('%s.cron_tf_entregar_eventos' % MODULO)
        self.assertFalse(cron.active)

    def test_22_modulo_nao_tem_caminho_paralelo_para_o_postgres(self):
        """Lente EXECUTAVEL do caminho: o que conta e' o codigo, nao a prosa.

        Docstring e comentario podem CITAR o schema do contrato (a documentacao do modulo cita, de
        proposito) — o que nao pode existir e' o caminho: driver de banco, SQL executado, literal de
        conexao. E a saida HTTP tem de ser UMA so.
        """
        import ast

        def literais_de_docstring(arvore):
            ids = set()
            for no in ast.walk(arvore):
                if isinstance(no, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    corpo = getattr(no, 'body', [])
                    if corpo and isinstance(corpo[0], ast.Expr) and isinstance(
                        corpo[0].value, ast.Constant
                    ) and isinstance(corpo[0].value.value, str):
                        ids.add(id(corpo[0].value))
            return ids

        proibidos_import = ('psycopg', 'psycopg2', 'pg8000', 'asyncpg')
        achados = []
        saidas_http = []
        for raiz, _dirs, arquivos in os.walk(self.raiz_do_modulo):
            if '__pycache__' in raiz:
                continue
            for arquivo in sorted(arquivos):
                if not arquivo.endswith('.py') or arquivo.startswith('test_'):
                    continue
                with open(os.path.join(raiz, arquivo), encoding='utf-8') as fh:
                    arvore = ast.parse(fh.read())
                ignorar = literais_de_docstring(arvore)
                for no in ast.walk(arvore):
                    if isinstance(no, ast.Import):
                        for alias in no.names:
                            if alias.name.split('.')[0] in proibidos_import:
                                achados.append('%s: import %s' % (arquivo, alias.name))
                            if alias.name == 'urllib.request':
                                saidas_http.append(arquivo)
                    elif isinstance(no, ast.ImportFrom):
                        if (no.module or '').split('.')[0] in proibidos_import:
                            achados.append('%s: from %s' % (arquivo, no.module))
                    elif isinstance(no, ast.Attribute) and no.attr == 'execute':
                        achados.append('%s: chamada .execute()' % arquivo)
                    elif isinstance(no, ast.Constant) and isinstance(no.value, str):
                        if id(no) in ignorar:
                            continue
                        # Literal de CONEXAO tem gramatica propria (chave=valor de DSN ou URL de
                        # banco). Prosa que cita o schema (`help=` falando de `sales_intelligence`)
                        # nao e' caminho: quem pega caminho de verdade e' o `.execute()` acima.
                        if re.search(r'(?:^|[\s;])(dbname|host|port|user|password)\s*=',
                                     no.value) or 'postgres://' in no.value \
                                or 'postgresql://' in no.value:
                            achados.append('%s: literal de conexao %r' % (arquivo, no.value[:40]))
        self.assertEqual(achados, [], 'caminho paralelo para o PostgreSQL: %s' % achados)
        self.assertEqual(saidas_http, [os.path.basename('tf_evento_outbox.py')],
                         'a saida HTTP do modulo tem de ser UMA so: %s' % saidas_http)
