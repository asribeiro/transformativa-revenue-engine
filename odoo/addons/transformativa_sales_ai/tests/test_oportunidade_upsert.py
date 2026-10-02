# -*- coding: utf-8 -*-
"""Aceite da operacao de negocio `oportunidade_upsert` — card TRE-W3-E01-T04 (`t_8b2ed1b7`).

O QUE ESTA SUITE PROVA (item por item, contra o Odoo de verdade, por HTTP de verdade):

  AC1 operacao declarada: `oportunidade_upsert` e' servida pela MESMA porta unica do E01-T01, na
      versao da politica em vigor (lida do proprio artefato), como escrita com chave exigida;
  AC2 identidade canonica: casa pelo UUID `tf_opportunity_id` (contrato §3), nunca por nome; sem o
      UUID no pedido -> 422 com recusa NOMEADA; UUID fora do formato -> 422 `valor_invalido`
      (constraint do modelo, via ORM);
  AC3 upsert: N chamadas com o mesmo UUID -> UM registro (contagem medida no banco); `criar` na
      primeira e `atualizar` depois; atualizacao e' PARCIAL (campo nao enviado permanece);
  AC4 fronteira de dono (contrato §2): campo de dono do Odoo (`stage_id`, `expected_revenue`,
      `probability`) -> 422 `campo_nao_declarado`; medido que um upsert do espelho NAO altera
      estagio nem valor do lead; `tf_priority_tier` nao e' escrevivel (derivado do score);
  AC5 contrato de integracao (doc 06 §7): `idempotency_key` exigida/validada, `dry_run` descreve
      sem escrever, `correlation_id` ecoado, rastro gravado no espelho (`tf_correlation_id`,
      `tf_idempotency_key`, `tf_last_sync_at`, `tf_last_event_type`);
  AC6 guarda de ambiente do ADR-005 tambem na ESCRITA: `homologacao` fora da politica -> 503;
      `producao` sem aprovacao -> 503 e COM aprovacao valida -> 200 (a guarda e' portao);
  AC7 rastro: UMA linha `TF_API_AUDIT` por chamada (inclusive recusa), com operacao/acao/ids e SEM
      token e SEM payload (lida do logger da API);
  AC8 vinculo e dono medidos: `partner_id` liga o lead a empresa do espelho e o lead criado nasce
      sob o usuario de integracao (sem dono comercial — atribuicao nao e' desta operacao).

O QUE ESTA SUITE NAO PROVA (declarado, para nao vender mais do que mede):
  * que o token nao vaza para o log do servidor — isso e' item do verificador, no log bruto;
  * que o consumidor externo (n8n/curl) funciona — idem, fase de HTTP externo do verificador;
  * deduplicacao por `idempotency_key` (card TRE-W3-E02-T02) — aqui a chave e' exigida, validada e
    registrada; quem garante "nao duplicar" e' a IDENTIDADE canonica;
  * adocao de lead criado a mao no CRM (sem `tf_opportunity_id`): a duplicata e' REPORTADA pela
    reconciliacao (E04-T01), nunca corrigida em silencio (contrato §5/§8).
"""

import json
import os
import tempfile
from datetime import date, datetime, timedelta

from odoo.modules.module import get_module_path
from odoo.tests import HttpCase, new_test_user, tagged

MODULO = "transformativa_sales_ai"

# UUIDs canonicos usados pelos itens (fixos de proposito: erro ilegivel em uuid4 nao ajuda ninguem).
UUID_A = "11111111-2222-4333-8444-555555555555"
UUID_B = "66666666-7777-4888-8999-000000000000"
UUID_C = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
UUID_D = "dddddddd-eeee-4fff-8000-111111111111"
# UUIDs proprios do item de identidade (nomes iguais de proposito) e do dry-run em registro
# existente: nao reaproveitam os das outras provas (cada prova mede o que ela mesma semeia).
UUID_E = "14141414-3636-4767-8787-929292929292"
UUID_F = "15151515-3737-4868-8787-939393939393"
UUID_G = "16161616-3838-4979-8787-949494949494"


@tagged("post_install", "-at_install")
class TestOportunidadeUpsert(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        raiz = get_module_path(MODULO)
        cls.politica_real = raiz + "/api/politica_api.json"
        cls.icp = cls.env["ir.config_parameter"].sudo()
        # Login proprio da suite (nao reaproveita o da suite do E01-T01): as duas rodam no mesmo
        # banco de testes e login duplicado entre classes e' fonte de falha obscura.
        cls.usuario = new_test_user(
            cls.env,
            login="tf_api_integracao_oportunidade",
            groups="base.group_user,%s.group_tf_sales_ai_user,sales_team.group_sale_salesman"
            % MODULO,
        )
        cls.chave = cls.env["res.users.apikeys"].with_user(cls.usuario)._generate(
            scope="rpc",
            name="teste-oportunidade-upsert",
            expiration_date=datetime.now() + timedelta(days=0.5),
        )
        with open(cls.politica_real, encoding="utf-8") as fh:
            cls.politica = json.load(fh)

    def setUp(self):
        super().setUp()
        self.icp.set_param("tf.api.ambiente", "dev")
        self.icp.set_param("tf.api.politica", self.politica_real)
        self.icp.set_param("tf.api.aprovacao", "")

    # ------------------------------------------------------------------ utilidades
    def _post(self, operacao, corpo, chave="__padrao__"):
        """POST de verdade na rota.

        `method="POST"` e' explicito de proposito: sem ele, `url_open` com corpo vazio (ou
        `json={}`) vira GET e o Odoo responde **405 Method Not Allowed** — barrado no roteador,
        ANTES do controlador (nao mede nada do item). Defeito medido na rodada 1 desta suite.
        """
        cabecalhos = {}
        if chave != "__sem__":
            cabecalhos["Authorization"] = "Bearer %s" % (
                self.chave if chave == "__padrao__" else chave
            )
        return self.url_open(
            "/tf/api/v1/%s" % operacao, json=corpo, headers=cabecalhos, method="POST"
        )

    def _codigo(self, resposta):
        self.assertIn(resposta.status_code, range(400, 600), resposta.text)
        return resposta.json()["codigo"]

    def _upsert(self, valores, chave_http, **extra):
        corpo = {"idempotency_key": chave_http, "parametros": {"valores": valores}}
        corpo.update(extra)
        resposta = self._post("oportunidade_upsert", corpo)
        self.assertEqual(resposta.status_code, 200, resposta.text)
        return resposta.json()["dados"]

    def _lead(self, uuid):
        """Le o lead por identidade canonica com cache invalidado (a escrita vem de outro env)."""
        leads = self.env["crm.lead"].search([("tf_opportunity_id", "=", uuid)])
        leads.invalidate_recordset()
        return leads

    def _politica_com_producao(self):
        """Copia a politica REAL num arquivo temporario liberando 'producao' (fixture do teste).

        Nao edita o artefato versionado nem o fixture compartilhado: o que se mede e' a guarda do
        ADR-005 com a operacao REAL declarada, e para isso basta a lista de ambientes permitidos.
        """
        dados = dict(self.politica)
        dados["ambientes_permitidos"] = ["dev", "producao"]
        caminho = os.path.join(tempfile.mkdtemp(prefix="tre-e01-t04-politica-"), "politica.json")
        with open(caminho, "w", encoding="utf-8") as fh:
            json.dump(dados, fh, ensure_ascii=False, indent=2)
        return caminho

    # ------------------------------------------------------------------ AC1 operacao declarada
    def test_01_sem_token_recusa_401(self):
        """Sem token nao ha' operacao: o item chega na rota e a autenticacao o barra.

        Corpo com `parametros` de proposito: corpo vazio vira GET e o roteador responde 405 antes
        de qualquer autenticacao (medido na rodada 1) — o item mediria a coisa errada.
        """
        resposta = self._post(
            "oportunidade_upsert",
            {"parametros": {"valores": {"name": "Sem token", "tf_opportunity_id": UUID_A}}},
            chave="__sem__",
        )
        self.assertEqual(resposta.status_code, 401, resposta.text)

    def test_02_operacao_aparece_nas_capacidades_como_escrita(self):
        """A declaracao e' lida do ARTEFATO (nao de literal no teste): o que se cobra e' a coerencia."""
        resposta = self._post("sistema_capacidades", {"correlation_id": "tre-e01-t04-capacidades"})
        self.assertEqual(resposta.status_code, 200, resposta.text)
        corpo = resposta.json()
        self.assertEqual(corpo["politica_versao"], self.politica["versao"])
        operacoes = {op["nome"]: op for op in corpo["dados"]["capacidades"]["operacoes"]}
        declarada = next(
            (op for op in self.politica["operacoes"] if op["nome"] == "oportunidade_upsert"), None
        )
        self.assertIsNotNone(declarada, "a politica real nao declara oportunidade_upsert")
        self.assertIn("oportunidade_upsert", operacoes)
        self.assertEqual(operacoes["oportunidade_upsert"]["tipo"], "escrita")
        self.assertTrue(operacoes["oportunidade_upsert"]["requer_idempotency_key"])
        self.assertEqual(operacoes["oportunidade_upsert"]["modelos"], ["crm.lead"])
        self.assertEqual(
            sorted(operacoes), sorted(op["nome"] for op in self.politica["operacoes"])
        )

    # ------------------------------------------------------------------ AC5 chave de idempotencia
    def test_03_escrita_sem_idempotency_key_422(self):
        resposta = self._post(
            "oportunidade_upsert",
            {"parametros": {"valores": {"name": "Sem chave", "tf_opportunity_id": UUID_A}}},
        )
        self.assertEqual(resposta.status_code, 422, resposta.text)
        self.assertEqual(self._codigo(resposta), "idempotency_key_ausente")

    def test_04_idempotency_key_fora_do_formato_422(self):
        resposta = self._post(
            "oportunidade_upsert",
            {
                "idempotency_key": "curta!!",
                "parametros": {"valores": {"name": "Chave torta", "tf_opportunity_id": UUID_A}},
            },
        )
        self.assertEqual(resposta.status_code, 422, resposta.text)
        self.assertEqual(self._codigo(resposta), "idempotency_key_invalida")

    def test_05_sem_uuid_canonico_422(self):
        """Sem identidade o espelho nao sabe de QUE oportunidade fala: recusa nomeada, nada escrito.

        ANCORA:CODIGO_EM_TRANSICAO — o codigo e' `campo_obrigatorio_ausente` na forma do E01-T01 e
        passa a `identificador_ausente` com a forma do E01-T02 (lista ordenada de identidades). A
        garantia (422 nomeado + nada escrito) e' a mesma nos dois; o item cobra a garantia.
        """
        antes = self.env["crm.lead"].search_count([])
        resposta = self._post(
            "oportunidade_upsert",
            {"idempotency_key": "tre-e01-t04-http-0005",
             "parametros": {"valores": {"name": "Sem identidade"}}},
        )
        self.assertEqual(resposta.status_code, 422, resposta.text)
        self.assertIn(
            self._codigo(resposta), ("campo_obrigatorio_ausente", "identificador_ausente")
        )
        self.assertEqual(self.env["crm.lead"].search_count([]), antes)

    def test_06_uuid_fora_do_formato_422(self):
        resposta = self._post(
            "oportunidade_upsert",
            {"idempotency_key": "tre-e01-t04-http-0006",
             "parametros": {"valores": {"name": "UUID torto", "tf_opportunity_id": "nao-e-uuid"}}},
        )
        self.assertEqual(resposta.status_code, 422, resposta.text)
        self.assertEqual(self._codigo(resposta), "valor_invalido")

    def test_07_name_obrigatorio_422(self):
        """`name` vem declarado como obrigatorio: ausente = 422 nomeado (e nao um nome computado).

        O campo `crm.lead.name` e' `required=True` E `compute='_compute_name'` no Odoo 19: sem o
        item, um payload "so' com UUID" criaria um lead com o nome que o Odoo computar — valor fora
        do contrato do espelho. A declaracao fecha isso antes do ORM (medicao do ORM no runbook).
        """
        resposta = self._post(
            "oportunidade_upsert",
            {"idempotency_key": "tre-e01-t04-http-0007",
             "parametros": {"valores": {"tf_opportunity_id": UUID_A}}},
        )
        self.assertEqual(resposta.status_code, 422, resposta.text)
        self.assertEqual(self._codigo(resposta), "campo_obrigatorio_ausente")

    # ------------------------------------------------------------------ AC3 upsert por identidade
    def test_08_primeira_chamada_cria_e_grava_o_espelho(self):
        dados = self._upsert(
            {
                "name": "Oportunidade HTTP Um",
                "type": "opportunity",
                "tf_opportunity_id": UUID_A,
                "tf_priority_score": 82.5,
                "tf_icp_score": 80.0,
                "tf_automation_fit_score": 85.0,
                "tf_buying_signal_score": 90.0,
                "tf_data_quality_score": 70.0,
                "tf_score_version": "v1",
                "tf_next_best_action": "FOLLOW_UP",
                "tf_correlation_id": "tre-e01-t04-http-correl-1",
                "tf_idempotency_key": "tre-e01-t04-http-0008",
                "tf_last_sync_at": "2026-10-02 03:00:00",
                "tf_last_event_type": "OPPORTUNITY_RECOMMENDED",
            },
            "tre-e01-t04-http-0008",
        )
        self.assertEqual(dados["acao_efetiva"], "criar")
        lead = self._lead(UUID_A)
        self.assertEqual(len(lead), 1)
        self.assertEqual(dados["ids"], [lead.id])
        self.assertEqual(lead.name, "Oportunidade HTTP Um")
        self.assertEqual(lead.type, "opportunity")
        self.assertEqual(lead.tf_priority_score, 82.5)
        self.assertEqual(lead.tf_next_best_action, "FOLLOW_UP")
        self.assertEqual(lead.tf_idempotency_key, "tre-e01-t04-http-0008")
        self.assertEqual(lead.tf_correlation_id, "tre-e01-t04-http-correl-1")
        self.assertEqual(lead.tf_last_event_type, "OPPORTUNITY_RECOMMENDED")
        self.assertTrue(lead.tf_last_sync_at)

    def test_09_segunda_chamada_atualiza_o_mesmo_registro(self):
        self._upsert(
            {"name": "Oportunidade Dois A", "tf_opportunity_id": UUID_B}, "tre-e01-t04-http-0009a"
        )
        lead = self._lead(UUID_B)
        self.assertEqual(len(lead), 1)
        dados = self._upsert(
            {"name": "Oportunidade Dois B", "tf_opportunity_id": UUID_B},
            "tre-e01-t04-http-0009b",
        )
        self.assertEqual(dados["acao_efetiva"], "atualizar")
        self.assertEqual(dados["ids"], [lead.id])
        self.assertEqual(len(self._lead(UUID_B)), 1)
        self.assertEqual(lead.name, "Oportunidade Dois B")

    def test_10_atualizacao_e_parcial_campo_nao_enviado_permanece(self):
        self._upsert(
            {"name": "Parcial", "tf_opportunity_id": UUID_C, "tf_priority_score": 61.0,
             "tf_next_best_action": "WAIT"},
            "tre-e01-t04-http-0010a",
        )
        self._upsert(
            {"name": "Parcial atualizada", "tf_opportunity_id": UUID_C},
            "tre-e01-t04-http-0010b",
        )
        lead = self._lead(UUID_C)
        self.assertEqual(lead.name, "Parcial atualizada")
        self.assertEqual(lead.tf_priority_score, 61.0, "campo nao enviado foi apagado/zerado")
        self.assertEqual(lead.tf_next_best_action, "WAIT", "campo nao enviado foi apagado/zerado")

    def test_11_cinco_chamadas_um_unico_registro(self):
        for indice in range(5):
            self._upsert(
                {"name": "Repetida %d" % indice, "tf_opportunity_id": UUID_D,
                 "tf_priority_score": 50.0 + indice},
                "tre-e01-t04-http-0011-%d" % indice,
            )
        leads = self._lead(UUID_D)
        self.assertEqual(len(leads), 1, "a identidade canonica duplicou o registro")
        self.assertEqual(leads.name, "Repetida 4")
        self.assertEqual(leads.tf_priority_score, 54.0)

    def test_12_casa_pelo_uuid_e_nao_pelo_nome(self):
        """NOMES iguais nao sao identidade: quem casa e' o UUID canonico (contrato §3).

        As duas sementes nascem PELA PROPRIA API (nao pelo ORM da suite): a conexao que serve a
        requisicao HTTP so' enxerga o que esta' commitado, entao semente de teste nao commitada
        seria invisivel e o item mediria outra coisa (defeito medido na rodada 1).
        """
        mesmo_nome = "Oportunidade de nome repetido"
        primeira = self._upsert(
            {"name": mesmo_nome, "type": "opportunity", "tf_opportunity_id": UUID_E},
            "tre-e01-t04-http-0012a",
        )
        segunda = self._upsert(
            {"name": mesmo_nome, "type": "opportunity", "tf_opportunity_id": UUID_F},
            "tre-e01-t04-http-0012b",
        )
        self.assertNotEqual(primeira["ids"], segunda["ids"], "dois UUIDs viraram o mesmo registro")
        self._upsert(
            {"name": "So' a segunda muda", "tf_opportunity_id": UUID_F},
            "tre-e01-t04-http-0012c",
        )
        self.assertEqual(
            self._lead(UUID_E).name, mesmo_nome, "o upsert casou pelo NOME, nao pelo UUID"
        )
        self.assertEqual(self._lead(UUID_F).name, "So' a segunda muda")
        self.assertEqual(len(self._lead(UUID_E)), 1)
        self.assertEqual(len(self._lead(UUID_F)), 1)

    # ------------------------------------------------------------------ AC4 fronteira de dono
    def test_13_campo_de_estagio_recusado_422(self):
        resposta = self._post(
            "oportunidade_upsert",
            {"idempotency_key": "tre-e01-t04-http-0013",
             "parametros": {"valores": {"name": "Com estagio", "tf_opportunity_id": UUID_A,
                                        "stage_id": 1}}},
        )
        self.assertEqual(resposta.status_code, 422, resposta.text)
        self.assertEqual(self._codigo(resposta), "campo_nao_declarado")

    def test_14_campo_de_valor_recusado_422(self):
        """`expected_revenue` e' do ODOO (contrato §2: valor -> Odoo). O espelho nao escreve valor."""
        resposta = self._post(
            "oportunidade_upsert",
            {"idempotency_key": "tre-e01-t04-http-0014",
             "parametros": {"valores": {"name": "Com valor", "tf_opportunity_id": UUID_A,
                                        "expected_revenue": 99999.0}}},
        )
        self.assertEqual(resposta.status_code, 422, resposta.text)
        self.assertEqual(self._codigo(resposta), "campo_nao_declarado")

    def test_15_campo_derivado_nao_e_escrevivel_422(self):
        """`tf_priority_tier` e' computado do score (TRE-W2-E04-T02): escrever nele e' recusa."""
        resposta = self._post(
            "oportunidade_upsert",
            {"idempotency_key": "tre-e01-t04-http-0015",
             "parametros": {"valores": {"name": "Com tier", "tf_opportunity_id": UUID_A,
                                        "tf_priority_tier": "A+"}}},
        )
        self.assertEqual(resposta.status_code, 422, resposta.text)
        self.assertEqual(self._codigo(resposta), "campo_nao_declarado")

    def test_16_upsert_do_espelho_nao_toca_os_campos_de_dono_do_odoo(self):
        """A fronteira do contrato §2 medida no REGISTRO, nao so' na recusa do payload.

        O lead tem de nascer pela API (semente de teste nao commitada e' invisivel para a conexao
        que serve o HTTP). Compara-se o registro INTEIRO antes e depois do segundo upsert: o que o
        espelho toca sao os campos que ele declara.
        """
        DONOS = ["stage_id", "expected_revenue", "probability", "date_deadline", "date_closed",
                 "user_id", "team_id", "partner_id"]
        self._upsert(
            {"name": "Oportunidade do funil", "type": "opportunity", "tf_opportunity_id": UUID_A,
             "tf_priority_score": 40.0, "tf_next_best_action": "WAIT"},
            "tre-e01-t04-http-0016a",
        )
        lead = self._lead(UUID_A)
        self.assertEqual(len(lead), 1)
        antes = lead.read(DONOS)[0]
        self.assertTrue(antes["stage_id"], "o lead nasceu sem estagio — medicao vazia (dono: Odoo)")
        self.assertEqual(antes["expected_revenue"], 0.0)
        self._upsert(
            {"name": "Oportunidade do funil (espelho)", "tf_opportunity_id": UUID_A,
             "tf_priority_score": 77.0, "tf_next_best_action": "CREATE_MEETING"},
            "tre-e01-t04-http-0016b",
        )
        lead.invalidate_recordset()
        depois = lead.read(DONOS)[0]
        self.assertEqual(depois, antes, "o espelho tocou em campo de dono do Odoo")
        self.assertEqual(lead.name, "Oportunidade do funil (espelho)")
        self.assertEqual(lead.tf_priority_score, 77.0)
        self.assertEqual(lead.tf_next_best_action, "CREATE_MEETING")
        self.assertEqual(len(self._lead(UUID_A)), 1)

    def test_17_tier_acompanha_o_score_do_espelho(self):
        self._upsert(
            {"name": "Tier A+", "tf_opportunity_id": UUID_B, "tf_priority_score": 95.0},
            "tre-e01-t04-http-0017",
        )
        lead = self._lead(UUID_B)
        self.assertEqual(lead.tf_priority_tier, "A+")
        self._upsert(
            {"name": "Tier C", "tf_opportunity_id": UUID_B, "tf_priority_score": 55.0},
            "tre-e01-t04-http-0018",
        )
        self.assertEqual(lead.tf_priority_tier, "C", "a faixa nao acompanhou o score escrito")

    # ------------------------------------------------------------------ AC5 dry-run e envelope
    def test_18_dry_run_em_lead_novo_nao_escreve(self):
        antes = self.env["crm.lead"].search_count(
            [("tf_opportunity_id", "=", UUID_C)]
        )
        resposta = self._post(
            "oportunidade_upsert",
            {"idempotency_key": "tre-e01-t04-http-0019", "dry_run": True,
             "parametros": {"valores": {"name": "Dry run", "tf_opportunity_id": UUID_C}}},
        )
        self.assertEqual(resposta.status_code, 200, resposta.text)
        corpo = resposta.json()
        self.assertTrue(corpo["dry_run"])
        self.assertEqual(corpo["dados"]["acao_efetiva"], "criar")
        self.assertEqual(corpo["dados"]["criaria"], ["name", "tf_opportunity_id"])
        depois = self.env["crm.lead"].search_count([("tf_opportunity_id", "=", UUID_C)])
        self.assertEqual((antes, depois), (0, 0))

    def test_19_dry_run_em_lead_existente_descreve_sem_alterar(self):
        """Com o registro existente, o dry-run descreve a ATUALIZACAO e nao escreve nada."""
        criado = self._upsert(
            {"name": "Existe", "type": "opportunity", "tf_opportunity_id": UUID_G},
            "tre-e01-t04-http-0019a",
        )
        resposta = self._post(
            "oportunidade_upsert",
            {"idempotency_key": "tre-e01-t04-http-0019b", "dry_run": True,
             "parametros": {"valores": {"name": "Nao deve entrar", "tf_opportunity_id": UUID_G}}},
        )
        self.assertEqual(resposta.status_code, 200, resposta.text)
        dados = resposta.json()["dados"]
        self.assertEqual(dados["acao_efetiva"], "atualizar")
        self.assertEqual(dados["id"], criado["ids"][0])
        self.assertEqual(dados["atualizaria"], ["name", "tf_opportunity_id"])
        self.assertEqual(self._lead(UUID_G).name, "Existe")

    def test_20_envelope_traz_chave_e_correlacao(self):
        """O envelope ecoa o que o chamador mandou e o rastro declarado chega no espelho.

        `tf_idempotency_key` e' campo DECLARADO: vai no `valores` (quem preenche e' o produtor do
        fato). A chave do ENVELOPE nao e' escrita no CRM por conta propria — ela entra na trilha de
        auditoria (item AC7), que e' onde replay/retry e' investigado (dedup e' do E02-T02).
        """
        resposta = self._post(
            "oportunidade_upsert",
            {"idempotency_key": "tre-e01-t04-http-0021",
             "correlation_id": "tre-e01-t04-http-correl-2",
             "parametros": {"valores": {"name": "Envelope", "tf_opportunity_id": UUID_A,
                                        "tf_idempotency_key": "tre-e01-t04-http-0021",
                                        "tf_correlation_id": "tre-e01-t04-http-correl-2"}}},
        )
        self.assertEqual(resposta.status_code, 200, resposta.text)
        corpo = resposta.json()
        self.assertEqual(corpo["idempotency_key"], "tre-e01-t04-http-0021")
        self.assertEqual(corpo["correlation_id"], "tre-e01-t04-http-correl-2")
        self.assertEqual(corpo["politica_versao"], self.politica["versao"])
        self.assertEqual(corpo["acao"], "upsert")
        self.assertEqual(self._lead(UUID_A).tf_idempotency_key, "tre-e01-t04-http-0021")

    # ------------------------------------------------------------------ AC1/AC6 guarda de ambiente
    def test_21_escrita_em_homologacao_recusa_503(self):
        self.icp.set_param("tf.api.ambiente", "homologacao")
        resposta = self._post(
            "oportunidade_upsert",
            {"idempotency_key": "tre-e01-t04-http-0022",
             "parametros": {"valores": {"name": "Homolog", "tf_opportunity_id": UUID_A}}},
        )
        self.assertEqual(resposta.status_code, 503, resposta.text)
        self.assertEqual(self._codigo(resposta), "ambiente_nao_permitido")
        self.assertEqual(self._lead(UUID_A), self.env["crm.lead"], "escreveu fora do ambiente")

    def test_22_producao_sem_aprovacao_recusa_503(self):
        self.icp.set_param("tf.api.politica", self._politica_com_producao())
        self.icp.set_param("tf.api.ambiente", "producao")
        resposta = self._post(
            "oportunidade_upsert",
            {"idempotency_key": "tre-e01-t04-http-0023",
             "parametros": {"valores": {"name": "Producao", "tf_opportunity_id": UUID_A}}},
        )
        self.assertEqual(resposta.status_code, 503, resposta.text)
        self.assertEqual(self._codigo(resposta), "aprovacao_ausente")

    def test_23_producao_com_aprovacao_valida_atende(self):
        """A guarda e' PORTÃO, nao parede: com aprovacao registrada a escrita atende (ADR-005)."""
        self.icp.set_param("tf.api.politica", self._politica_com_producao())
        self.icp.set_param("tf.api.ambiente", "producao")
        self.icp.set_param(
            "tf.api.aprovacao",
            "card=TRE-W3-E01-T04,aprovador=Anderson Ribeiro,validade=%s"
            % (date.today() + timedelta(days=30)).isoformat(),
        )
        resposta = self._post(
            "oportunidade_upsert",
            {"idempotency_key": "tre-e01-t04-http-0024",
             "parametros": {"valores": {"name": "Producao aprovada", "tf_opportunity_id": UUID_A}}},
        )
        self.assertEqual(resposta.status_code, 200, resposta.text)
        self.assertEqual(resposta.json()["ambiente"], "producao")
        self.assertEqual(self._lead(UUID_A).name, "Producao aprovada")

    # ------------------------------------------------------------------ AC7 auditoria
    def test_24_auditoria_registra_sucesso_e_recusa_sem_payload(self):
        with self.assertLogs("transformativa_sales_ai.api", level="INFO") as captura:
            self._post(
                "oportunidade_upsert",
                {"idempotency_key": "tre-e01-t04-http-0025",
                 "correlation_id": "tre-e01-t04-auditoria",
                 "parametros": {"valores": {"name": "Auditada", "tf_opportunity_id": UUID_B}}},
            )
            self._post(
                "oportunidade_upsert",
                {"idempotency_key": "tre-e01-t04-http-0026",
                 "parametros": {"valores": {"name": "Recusada-MARCADOR",
                                            "tf_opportunity_id": UUID_B, "stage_id": 1}}},
            )
        linhas = [linha for linha in captura.output if "TF_API_AUDIT" in linha]
        self.assertEqual(len(linhas), 2, captura.output)
        self.assertTrue(any('"operacao": "oportunidade_upsert"' in linha for linha in linhas))
        self.assertTrue(any('"acao": "upsert"' in linha for linha in linhas))
        self.assertTrue(any('"resultado": "recusado"' in linha for linha in linhas))
        self.assertTrue(any('"codigo": "campo_nao_declarado"' in linha for linha in linhas))
        for linha in linhas:
            self.assertNotIn("Bearer", linha)
            self.assertNotIn("Recusada-MARCADOR", linha, "payload entrou na trilha")

    # ------------------------------------------------------------------ AC8 vinculo e dono medidos
    def test_25_partner_id_liga_o_lead_a_empresa_do_espelho(self):
        empresa = self.env["res.partner"].create(
            {"name": "Empresa do espelho", "is_company": True, "tf_cnpj": "12.345.678/0001-95"}
        )
        self._upsert(
            {"name": "Oportunidade da empresa", "tf_opportunity_id": UUID_C,
             "partner_id": empresa.id},
            "tre-e01-t04-http-0027",
        )
        lead = self._lead(UUID_C)
        self.assertEqual(lead.partner_id, empresa)
        self.assertEqual(lead.partner_id.tf_cnpj, "12.345.678/0001-95")

    def test_26_lead_criado_nasce_sob_o_usuario_de_integracao(self):
        """Medido e DECLARADO: a atribuicao de vendedor nao e' desta operacao.

        O lead criado pela API pertence ao usuario dono da chave (nao ha' campo de dono comercial
        declarado — atribuir vendedor e' decisao do CRM/humano, nao do espelho da inteligencia).
        """
        self._upsert(
            {"name": "Sem dono comercial", "tf_opportunity_id": UUID_D}, "tre-e01-t04-http-0028"
        )
        self.assertEqual(self._lead(UUID_D).user_id, self.usuario)

    def test_27_campo_de_outro_modelo_recusado_422(self):
        """O corpo nao e' "um dicionario de campos do Odoo": e' o que a operacao declara."""
        resposta = self._post(
            "oportunidade_upsert",
            {"idempotency_key": "tre-e01-t04-http-0029",
             "parametros": {"valores": {"name": "Com is_company", "tf_opportunity_id": UUID_A,
                                        "is_company": True}}},
        )
        self.assertEqual(resposta.status_code, 422, resposta.text)
        self.assertEqual(self._codigo(resposta), "campo_nao_declarado")
