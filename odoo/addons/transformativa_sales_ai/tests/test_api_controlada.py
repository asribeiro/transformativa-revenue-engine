# -*- coding: utf-8 -*-
"""Aceite da API controlada (`POST /tf/api/v1/<operacao>`) — card TRE-W3-E01-T01 (`t_e0489efc`).

O QUE ESTA SUITE PROVA (item por item, contra o Odoo de verdade, por HTTP de verdade):

  AC1 superficie fechada: operacao nao declarada -> 404; verbo diferente de POST -> 405;
      politica invalida -> 500 (nada e' servido com regra inventada);
  AC3 campo declarado: campo fora da lista -> 422, campo de filtro fora da lista -> 422,
      teto de registros -> 422, chave desconhecida no corpo -> 400;
  AC4 credencial: sem header -> 401; chave invalida -> 401;
  AC5 ambiente: ambiente nao declarado -> 503; ambiente fora da politica -> 503; producao sem
      aprovacao registrada -> 503; producao COM aprovacao valida -> 200 (a guarda e' portao);
      aprovacao vencida -> 503;
  AC6 contrato: escrita sem `idempotency_key` -> 422; `dry_run` nao escreve; upsert por
      identidade cria uma vez e atualiza depois (sem duplicar);
  AC7 envelope: toda resposta (inclusive recusa) traz `correlation_id`; a auditoria por chamada
      e' medida no log do container pelo verificador (`TF_API_AUDIT`).

COMO ELE OBTEM A CHAVE (mesmo caminho do teste do proprio Odoo, `api_doc/tests/test_doc.py`):
`res.users.apikeys._generate(scope='rpc', ...)` para o usuario de integracao — o header
`Authorization: Bearer <chave>` e' resolvido pelo `auth='bearer'` do Odoo 19.

O QUE ESTA SUITE NAO PROVA (declarado, para nao vender mais do que mede):
  * que o token nao vaza para o log — isso e' item do verificador, no log bruto do container;
  * que o consumidor externo (n8n/curl) funciona — idem, fase de HTTP externo do verificador;
  * deduplicacao por chave de idempotencia (card TRE-W3-E02-T02) — aqui a chave e' exigida e
    validada; o motor de dedup nao existe ainda. As operacoes de ESCRITA de negocio entram na
    politica pelos cards TRE-W3-E01-T02..T05 (a primeira foi `oportunidade_upsert`, E01-T04); os
    itens desta suite que falam da LISTA de operacoes leem o proprio artefato (ANCORA:ITEM_DATADO).
"""

import json
from datetime import date, datetime, timedelta

from odoo.modules.module import get_module_path
from odoo.tests import HttpCase, new_test_user, tagged

MODULO = "transformativa_sales_ai"


@tagged("post_install", "-at_install")
class TestApiControlada(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        raiz = get_module_path(MODULO)
        cls.politica_real = raiz + "/api/politica_api.json"
        cls.politica_de_teste = raiz + "/tests/politicas/politica_de_teste.json"
        cls.politica_invalida = raiz + "/tests/politicas/politica_invalida.json"
        cls.icp = cls.env["ir.config_parameter"].sudo()
        cls.usuario = new_test_user(
            cls.env,
            login="tf_api_integracao",
            groups="base.group_user,%s.group_tf_sales_ai_user,sales_team.group_sale_salesman" % MODULO,
        )
        cls.chave = cls.env["res.users.apikeys"].with_user(cls.usuario)._generate(
            scope="rpc",
            name="teste-api-controlada",
            # Mesma duracao curta usada pelo teste do proprio Odoo (api_doc): a validade da chave
            # nao pode passar do teto do grupo do usuario (`api_key_duration`, padrao 1 dia).
            expiration_date=datetime.now() + timedelta(days=0.5),
        )

    def setUp(self):
        super().setUp()
        self.icp.set_param("tf.api.ambiente", "dev")
        self.icp.set_param("tf.api.politica", self.politica_real)
        self.icp.set_param("tf.api.aprovacao", "")

    # ------------------------------------------------------------------ utilidades
    def _post(self, operacao, corpo, chave="__padrao__", headers=None, metodo="POST"):
        cabecalhos = dict(headers or {})
        if chave != "__sem__":
            cabecalhos["Authorization"] = "Bearer %s" % (self.chave if chave == "__padrao__" else chave)
        return self.url_open(
            "/tf/api/v1/%s" % operacao,
            json=corpo,
            headers=cabecalhos,
            method=metodo,
        )

    def _codigo(self, resposta):
        self.assertIn(resposta.status_code, range(400, 600), resposta.text)
        return resposta.json()["codigo"]

    def _aprovacao(self, validade=None, aprovador="Anderson Ribeiro"):
        validade = validade or (date.today() + timedelta(days=30)).isoformat()
        return "card=TRE-W3-E01-T01,aprovador=%s,validade=%s" % (aprovador, validade)

    # ------------------------------------------------------------------ AC4 credencial
    def test_01_sem_token_recusa_401(self):
        resposta = self._post("sistema_capacidades", {}, chave="__sem__")
        self.assertEqual(resposta.status_code, 401, resposta.text)

    def test_02_token_invalido_recusa_401(self):
        resposta = self._post("sistema_capacidades", {}, chave="chave-que-nao-existe-000000")
        self.assertEqual(resposta.status_code, 401, resposta.text)

    # ------------------------------------------------------------------ AC1 superficie fechada
    def test_03_operacao_nao_declarada_404(self):
        resposta = self._post("partner_upsert_livre", {})
        self.assertEqual(resposta.status_code, 404, resposta.text)
        corpo = resposta.json()
        self.assertEqual(corpo["codigo"], "operacao_nao_declarada")
        self.assertFalse(corpo["ok"])
        self.assertTrue(corpo["correlation_id"])

    def test_04_verbo_get_recusado_405(self):
        resposta = self.url_open(
            "/tf/api/v1/sistema_capacidades",
            headers={"Authorization": "Bearer %s" % self.chave},
        )
        self.assertEqual(resposta.status_code, 405, resposta.text)

    def test_05_politica_invalida_nao_serve_500(self):
        self.icp.set_param("tf.api.politica", self.politica_invalida)
        resposta = self._post("sistema_capacidades", {})
        self.assertEqual(resposta.status_code, 500, resposta.text)
        self.assertEqual(resposta.json()["codigo"], "politica_invalida")

    # ------------------------------------------------------------------ AC1 operacao declarada
    def test_06_operacao_declarada_responde_capacidades(self):
        resposta = self._post("sistema_capacidades", {})
        self.assertEqual(resposta.status_code, 200, resposta.text)
        corpo = resposta.json()
        self.assertTrue(corpo["ok"])
        self.assertEqual(corpo["operacao"], "sistema_capacidades")
        self.assertEqual(corpo["ambiente"], "dev")
        # ANCORA:ITEM_DATADO — a versao da politica e a LISTA de operacoes sao lidas do proprio
        # artefato, nao fixadas aqui: cada card da onda W3-E01 (E01-T02..T05) acrescenta a sua
        # operacao de negocio e sobe a versao. O item que nao expira e' a COERENCIA entre o que a
        # politica declara e o que a API serve (cobrar o literal reprovava o card seguinte).
        with open(self.politica_real, encoding="utf-8") as fh:
            politica = json.load(fh)
        self.assertEqual(corpo["politica_versao"], politica["versao"])
        self.assertTrue(corpo["correlation_id"])
        nomes = [op["nome"] for op in corpo["dados"]["capacidades"]["operacoes"]]
        self.assertEqual(sorted(nomes), sorted(op["nome"] for op in politica["operacoes"]))
        self.assertEqual(corpo["dados"]["capacidades"]["ambientes_permitidos"], ["dev"])

    def test_07_correlation_id_do_chamador_e_ecoado(self):
        resposta = self._post(
            "sistema_capacidades", {"correlation_id": "tre-e01-t01-teste-correlacao"}
        )
        self.assertEqual(resposta.status_code, 200, resposta.text)
        self.assertEqual(resposta.json()["correlation_id"], "tre-e01-t01-teste-correlacao")

    # ------------------------------------------------------------------ AC5 guarda de ambiente
    def test_08_ambiente_nao_declarado_503(self):
        self.icp.set_param("tf.api.ambiente", "")
        resposta = self._post("sistema_capacidades", {})
        self.assertEqual(resposta.status_code, 503, resposta.text)
        self.assertEqual(self._codigo(resposta), "ambiente_nao_declarado")

    def test_09_ambiente_fora_da_politica_503(self):
        self.icp.set_param("tf.api.ambiente", "homologacao")
        resposta = self._post("sistema_capacidades", {})
        self.assertEqual(resposta.status_code, 503, resposta.text)
        self.assertEqual(self._codigo(resposta), "ambiente_nao_permitido")

    def test_10_ambiente_desconhecido_503(self):
        self.icp.set_param("tf.api.ambiente", "staging")
        resposta = self._post("sistema_capacidades", {})
        self.assertEqual(resposta.status_code, 503, resposta.text)
        self.assertEqual(self._codigo(resposta), "ambiente_nao_declarado")

    def test_11_producao_sem_aprovacao_503(self):
        self.icp.set_param("tf.api.politica", self.politica_de_teste)
        self.icp.set_param("tf.api.ambiente", "producao")
        resposta = self._post("sistema_capacidades", {})
        self.assertEqual(resposta.status_code, 503, resposta.text)
        self.assertEqual(self._codigo(resposta), "aprovacao_ausente")

    def test_12_producao_com_aprovacao_valida_atende(self):
        self.icp.set_param("tf.api.politica", self.politica_de_teste)
        self.icp.set_param("tf.api.ambiente", "producao")
        self.icp.set_param("tf.api.aprovacao", self._aprovacao())
        resposta = self._post("sistema_capacidades", {})
        self.assertEqual(resposta.status_code, 200, resposta.text)
        self.assertEqual(resposta.json()["ambiente"], "producao")

    def test_13_producao_com_aprovacao_vencida_503(self):
        self.icp.set_param("tf.api.politica", self.politica_de_teste)
        self.icp.set_param("tf.api.ambiente", "producao")
        self.icp.set_param(
            "tf.api.aprovacao", self._aprovacao(validade="2020-01-01")
        )
        resposta = self._post("sistema_capacidades", {})
        self.assertEqual(resposta.status_code, 503, resposta.text)
        self.assertEqual(self._codigo(resposta), "aprovacao_ausente")

    def test_14_aprovacao_sem_um_dos_campos_nao_vale_503(self):
        self.icp.set_param("tf.api.politica", self.politica_de_teste)
        self.icp.set_param("tf.api.ambiente", "producao")
        self.icp.set_param("tf.api.aprovacao", "aprovador=Anderson Ribeiro,validade=2099-01-01")
        resposta = self._post("sistema_capacidades", {})
        self.assertEqual(resposta.status_code, 503, resposta.text)
        self.assertEqual(self._codigo(resposta), "aprovacao_ausente")

    # ------------------------------------------------------------------ AC3 campo declarado
    def test_15_campo_nao_declarado_na_leitura_422(self):
        resposta = self._post(
            "crm_registros_ler",
            {"parametros": {"modelo": "res.partner", "campos": ["id", "email"]}},
        )
        self.assertEqual(resposta.status_code, 422, resposta.text)
        self.assertEqual(self._codigo(resposta), "campo_nao_declarado")

    def test_16_campo_de_filtro_nao_declarado_422(self):
        resposta = self._post(
            "crm_registros_ler",
            {"parametros": {"modelo": "res.partner", "filtro": [["email", "=", "x@y.z"]]}},
        )
        self.assertEqual(resposta.status_code, 422, resposta.text)
        self.assertEqual(self._codigo(resposta), "campo_nao_declarado")

    def test_17_operador_nao_permitido_422(self):
        resposta = self._post(
            "crm_registros_ler",
            {"parametros": {"modelo": "crm.lead", "filtro": [["tf_opportunity_id", "like", "x"]]}},
        )
        self.assertEqual(resposta.status_code, 422, resposta.text)
        self.assertEqual(self._codigo(resposta), "valor_invalido")

    def test_18_limite_acima_do_teto_422(self):
        resposta = self._post(
            "crm_registros_ler",
            {"parametros": {"modelo": "res.partner", "limite": 5000}},
        )
        self.assertEqual(resposta.status_code, 422, resposta.text)
        self.assertEqual(self._codigo(resposta), "limite_excedido")

    def test_19_modelo_nao_declarado_422(self):
        resposta = self._post(
            "crm_registros_ler", {"parametros": {"modelo": "res.users"}}
        )
        self.assertEqual(resposta.status_code, 422, resposta.text)
        self.assertEqual(self._codigo(resposta), "modelo_nao_declarado")

    def test_20_chave_desconhecida_no_corpo_400(self):
        resposta = self._post("sistema_capacidades", {"forcar": True})
        self.assertEqual(resposta.status_code, 400, resposta.text)
        self.assertEqual(self._codigo(resposta), "payload_invalido")

    # ------------------------------------------------------------------ leitura declarada
    def test_21_leitura_declarada_devolve_registros(self):
        parceiro = self.env["res.partner"].create(
            {"name": "Parceiro da API", "tf_cnpj": "11.222.333/0001-81"}
        )
        resposta = self._post(
            "crm_registros_ler",
            {
                "parametros": {
                    "modelo": "res.partner",
                    "filtro": [["tf_cnpj", "=", "11.222.333/0001-81"]],
                    "campos": ["id", "name", "tf_cnpj"],
                    "limite": 5,
                }
            },
        )
        self.assertEqual(resposta.status_code, 200, resposta.text)
        dados = resposta.json()["dados"]
        self.assertEqual(dados["total"], 1)
        self.assertEqual(dados["registros"][0]["id"], parceiro.id)
        self.assertEqual(set(dados["registros"][0]), {"id", "name", "tf_cnpj"})

    def test_22_leitura_declarada_respeita_a_acl_do_usuario_da_chave(self):
        """AC4: a leitura por `POST /tf/api/v1/*` roda com as ACLs do dono da chave — nao por sudo.

        O CRM tem regra propria: o vendedor so' enxerga o lead DELE. Entao o mesmo pedido devolve o
        lead proprio e nao devolve o alheio — e' a ACL do Odoo valendo, nao um filtro meu. O item
        existe porque a primeira rodada mediu `[] != [1]` exatamente aqui: o teste antigo criava o
        lead sem `user_id` e o usuario de integracao (restrito) nao o via.
        """
        proprio = self.env["crm.lead"].create(
            {
                "name": "Oportunidade do usuario da chave",
                "type": "opportunity",
                "user_id": self.usuario.id,
                "tf_opportunity_id": "8f14e45f-ceea-467f-a0e0-000000000001",
            }
        )
        alheio = self.env["crm.lead"].create(
            {
                "name": "Oportunidade de OUTRO vendedor",
                "type": "opportunity",
                "tf_opportunity_id": "8f14e45f-ceea-467f-a0e0-000000000002",
            }
        )
        resposta = self._post(
            "crm_registros_ler",
            {
                "parametros": {
                    "modelo": "crm.lead",
                    "filtro": [["tf_opportunity_id", "=", proprio.tf_opportunity_id]],
                }
            },
        )
        self.assertEqual(resposta.status_code, 200, resposta.text)
        registros = resposta.json()["dados"]["registros"]
        self.assertEqual([r["id"] for r in registros], [proprio.id])
        self.assertIn("tf_priority_tier", registros[0])
        fora = self._post(
            "crm_registros_ler",
            {
                "parametros": {
                    "modelo": "crm.lead",
                    "filtro": [["tf_opportunity_id", "=", alheio.tf_opportunity_id]],
                }
            },
        )
        self.assertEqual(fora.status_code, 200, fora.text)
        self.assertEqual(fora.json()["dados"]["registros"], [])

    # ------------------------------------------------------------------ AC6 contrato de escrita
    def test_23_escrita_sem_idempotency_key_422(self):
        self.icp.set_param("tf.api.politica", self.politica_de_teste)
        resposta = self._post(
            "teste_criar_parceiro",
            {"parametros": {"valores": {"name": "Sem chave"}}},
        )
        self.assertEqual(resposta.status_code, 422, resposta.text)
        self.assertEqual(self._codigo(resposta), "idempotency_key_ausente")

    def test_24_escrita_com_idempotency_key_fora_do_formato_422(self):
        self.icp.set_param("tf.api.politica", self.politica_de_teste)
        resposta = self._post(
            "teste_criar_parceiro",
            {
                "idempotency_key": "curta!!",
                "parametros": {"valores": {"name": "Chave torta"}},
            },
        )
        self.assertEqual(resposta.status_code, 422, resposta.text)
        self.assertEqual(self._codigo(resposta), "idempotency_key_invalida")

    def test_25_escrita_com_campo_nao_declarado_422(self):
        self.icp.set_param("tf.api.politica", self.politica_de_teste)
        resposta = self._post(
            "teste_criar_parceiro",
            {
                "idempotency_key": "tre-e01-t01-teste-campo-nao-declarado",
                "parametros": {"valores": {"name": "Com extra", "email": "x@y.z"}},
            },
        )
        self.assertEqual(resposta.status_code, 422, resposta.text)
        self.assertEqual(self._codigo(resposta), "campo_nao_declarado")

    def test_26_escrita_sem_campo_obrigatorio_422(self):
        """`valores` com campo declarado mas sem o obrigatorio: 422, nao 400.

        `valores: {}` (vazio) e' outra coisa — payload vazio, 400 `payload_invalido` (medido na
        rodada 1, quando este item esperava 422 e o motor devolveu 400 por desenho).
        """
        self.icp.set_param("tf.api.politica", self.politica_de_teste)
        vazio = self._post(
            "teste_criar_parceiro",
            {"idempotency_key": "tre-e01-t01-teste-valores-vazios", "parametros": {"valores": {}}},
        )
        self.assertEqual(vazio.status_code, 400, vazio.text)
        self.assertEqual(self._codigo(vazio), "payload_invalido")
        resposta = self._post(
            "teste_criar_parceiro",
            {
                "idempotency_key": "tre-e01-t01-teste-sem-obrigatorio",
                "parametros": {"valores": {"tf_cnpj": "33.222.111/0001-00"}},
            },
        )
        self.assertEqual(resposta.status_code, 422, resposta.text)
        self.assertEqual(self._codigo(resposta), "campo_obrigatorio_ausente")

    def test_27_dry_run_nao_escreve(self):
        self.icp.set_param("tf.api.politica", self.politica_de_teste)
        antes = self.env["res.partner"].search_count([("tf_cnpj", "=", "99.888.777/0001-66")])
        resposta = self._post(
            "teste_upsert_parceiro",
            {
                "idempotency_key": "tre-e01-t01-teste-dry-run",
                "dry_run": True,
                "parametros": {"valores": {"name": "Dry run", "tf_cnpj": "99.888.777/0001-66"}},
            },
        )
        self.assertEqual(resposta.status_code, 200, resposta.text)
        corpo = resposta.json()
        self.assertTrue(corpo["dry_run"])
        self.assertEqual(corpo["dados"]["acao_efetiva"], "criar")
        self.assertIn("criaria", corpo["dados"])
        depois = self.env["res.partner"].search_count([("tf_cnpj", "=", "99.888.777/0001-66")])
        self.assertEqual((antes, depois), (0, 0))

    def test_28_upsert_cria_uma_vez_e_atualiza_depois(self):
        self.icp.set_param("tf.api.politica", self.politica_de_teste)
        cnpj = "77.666.555/0001-44"
        primeira = self._post(
            "teste_upsert_parceiro",
            {
                "idempotency_key": "tre-e01-t01-teste-upsert-1",
                "parametros": {"valores": {"name": "Upsert um", "tf_cnpj": cnpj}},
            },
        )
        self.assertEqual(primeira.status_code, 200, primeira.text)
        self.assertEqual(primeira.json()["dados"]["acao_efetiva"], "criar")
        criado = self.env["res.partner"].search([("tf_cnpj", "=", cnpj)])
        self.assertEqual(len(criado), 1)
        segunda = self._post(
            "teste_upsert_parceiro",
            {
                "idempotency_key": "tre-e01-t01-teste-upsert-2",
                "parametros": {"valores": {"name": "Upsert dois", "tf_cnpj": cnpj}},
            },
        )
        self.assertEqual(segunda.status_code, 200, segunda.text)
        self.assertEqual(segunda.json()["dados"]["acao_efetiva"], "atualizar")
        self.assertEqual(segunda.json()["dados"]["ids"], [criado.id])
        self.assertEqual(len(self.env["res.partner"].search([("tf_cnpj", "=", cnpj)])), 1)
        self.assertEqual(criado.name, "Upsert dois")

    def test_29_escrita_respondente_traz_idempotency_key_e_correlation(self):
        self.icp.set_param("tf.api.politica", self.politica_de_teste)
        resposta = self._post(
            "teste_criar_parceiro",
            {
                "idempotency_key": "tre-e01-t01-teste-envelope",
                "correlation_id": "tre-e01-t01-envelope",
                "parametros": {"valores": {"name": "Envelope", "tf_cnpj": "55.444.333/0001-22"}},
            },
        )
        self.assertEqual(resposta.status_code, 200, resposta.text)
        corpo = resposta.json()
        self.assertEqual(corpo["idempotency_key"], "tre-e01-t01-teste-envelope")
        self.assertEqual(corpo["correlation_id"], "tre-e01-t01-envelope")
        self.assertEqual(corpo["dados"]["acao_efetiva"], "criar")

    def test_30_auditoria_dispara_as_duas_linhas_no_log(self):
        """A trilha existe para sucesso E recusa — lida do logger da API, nao do log do arquivo."""
        with self.assertLogs("transformativa_sales_ai.api", level="INFO") as captura:
            self._post("sistema_capacidades", {"correlation_id": "tre-e01-t01-auditoria"})
            self._post("operacao_que_nao_existe", {})
        linhas = [linha for linha in captura.output if "TF_API_AUDIT" in linha]
        self.assertEqual(len(linhas), 2, captura.output)
        self.assertTrue(all("tre-e01-t01-auditoria" in linha or "recusado" in linha for linha in linhas))
        self.assertTrue(any('"resultado": "ok"' in linha for linha in linhas))
        self.assertTrue(any('"resultado": "recusado"' in linha for linha in linhas))
        self.assertTrue(any('"codigo": "operacao_nao_declarada"' in linha for linha in linhas))
        # AC7: a trilha nao carrega o token nem o payload
        for linha in linhas:
            self.assertNotIn("Bearer", linha)
            self.assertNotIn("chave-que-nao-existe", linha)

    def test_31_dry_run_na_leitura_nao_consulta_o_dado(self):
        """`dry_run: true` vale para leitura tambem: descreve a consulta e NAO a executa (AC6)."""
        resposta = self._post(
            "crm_registros_ler",
            {
                "dry_run": True,
                "parametros": {"modelo": "res.partner", "campos": ["id", "name"], "limite": 2},
            },
        )
        self.assertEqual(resposta.status_code, 200, resposta.text)
        dados = resposta.json()["dados"]
        self.assertTrue(resposta.json()["dry_run"])
        self.assertTrue(dados["dry_run"])
        self.assertNotIn("registros", dados)
        self.assertEqual(dados["consultaria"]["modelo"], "res.partner")
        self.assertEqual(dados["consultaria"]["limite"], 2)

    def test_32_dry_run_onde_a_politica_nao_aceita_422(self):
        """`aceita_dry_run: false` na declaracao vale: pedir dry_run ali e' recusa nomeada."""
        self.icp.set_param("tf.api.politica", self.politica_de_teste)
        resposta = self._post(
            "teste_ler_sem_dry_run", {"dry_run": True, "parametros": {"modelo": "res.partner"}}
        )
        self.assertEqual(resposta.status_code, 422, resposta.text)
        self.assertEqual(self._codigo(resposta), "dry_run_nao_suportado")
