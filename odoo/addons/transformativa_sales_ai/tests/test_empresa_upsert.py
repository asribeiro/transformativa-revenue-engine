# -*- coding: utf-8 -*-
"""Aceite da operacao de escrita de negocio `empresa_upsert` — card TRE-W3-E01-T02.

O QUE ESTA SUITE PROVA (item por item, contra o Odoo de verdade e por HTTP de verdade):

  AC1 operacao declarada: `empresa_upsert` aparece nas capacidades da API com o tipo/teto que a
      POLITICA declara (o teste le o arquivo da politica — nao ha' versao literal aqui, senao o
      item expira a cada versao nova);
  AC2 identidade declarada: sem NENHUM identificador com valor -> 422 `identificador_ausente` e
      nada escrito;
  AC3 upsert idempotente: a mesma empresa cria UMA vez e atualiza depois (`acao_efetiva`), a
      contagem por identidade nao cresce em chamadas repetidas, e a identidade funciona pelo
      canonico (`tf_company_id`) e pelos fortes do contrato §5 (CNPJ -> dominio -> LinkedIn);
  AC4 ambiguidade: identificadores do mesmo pedido apontando para registros DIFERENTES -> 409
      `valor_ambiguo`, sem criar e sem alterar ninguem (contrato §5: ambiguidade e' reportada);
  AC5 semantica de empresa: `is_company` e' valor FIXO declarado — o parceiro nasce empresa,
      continua empresa na atualizacao, e o chamador que tentar decidir esse valor leva 422
      `campo_fixo_divergente`;
  AC6 contrato de integracao: `idempotency_key` exigida e validada; `dry_run` descreve e NAO
      escreve (nem cria nem atualiza); `tf_company_id` fora do formato UUID e' recusado pelo ORM;
      campo fora da declaracao -> 422; `correlation_id` ecoado;
  AC7 guarda de ambiente + rastro: ambiente fora da politica -> 503 (nada escrito) e a escrita
      bem-sucedida deixa UMA linha `TF_API_AUDIT` com os ids, sem payload e sem token.

O QUE ESTA SUITE NAO PROVA (declarado, para nao vender mais do que mede):
  * que o consumidor externo (n8n) funciona — e' a fase HTTP por `curl` do verificador;
  * a dedup por `idempotency_key` (retry/replay, card TRE-W3-E02-T02): aqui a chave e' exigida e
    validada; a garantia de "nao duplicar" medida aqui vem da IDENTIDADE;
  * merge de empresas ja' duplicadas na base — e' do TRE-W3-E04-T01 (reconciliacao).
"""

from datetime import datetime, timedelta
import json

from odoo.modules.module import get_module_path
from odoo.tests import HttpCase, new_test_user, tagged

MODULO = "transformativa_sales_ai"
OPERACAO = "empresa_upsert"

UUID_1 = "3f1c0a52-6f0e-4a2b-9d3e-000000000001"
UUID_2 = "3f1c0a52-6f0e-4a2b-9d3e-000000000002"
UUID_3 = "3f1c0a52-6f0e-4a2b-9d3e-000000000003"


@tagged("post_install", "-at_install")
class TestEmpresaUpsert(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        raiz = get_module_path(MODULO)
        cls.caminho_politica = raiz + "/api/politica_api.json"
        with open(cls.caminho_politica, "r", encoding="utf-8") as fh:
            cls.politica = json.load(fh)
        cls.icp = cls.env["ir.config_parameter"].sudo()
        cls.usuario = new_test_user(
            cls.env,
            login="tf_api_empresa",
            groups="base.group_user,%s.group_tf_sales_ai_user,sales_team.group_sale_salesman"
            % MODULO,
        )
        cls.chave = cls.env["res.users.apikeys"].with_user(cls.usuario)._generate(
            scope="rpc",
            name="teste-empresa-upsert",
            expiration_date=datetime.now() + timedelta(hours=12),
        )

    def setUp(self):
        super().setUp()
        self.icp.set_param("tf.api.ambiente", "dev")
        self.icp.set_param("tf.api.politica", self.caminho_politica)
        self.icp.set_param("tf.api.aprovacao", "")

    # ------------------------------------------------------------------ utilidades
    def _post(self, corpo, operacao=OPERACAO, chave="__padrao__"):
        cabecalhos = {}
        if chave != "__sem__":
            cabecalhos["Authorization"] = "Bearer %s" % (
                self.chave if chave == "__padrao__" else chave
            )
        return self.url_open(
            "/tf/api/v1/%s" % operacao, json=corpo, headers=cabecalhos, method="POST"
        )

    def _upsert(self, valores, chave_idempotencia="tre-e01-t02-teste-0001", **extra):
        corpo = {
            "idempotency_key": chave_idempotencia,
            "parametros": {"valores": valores},
        }
        corpo.update(extra)
        return self._post(corpo)

    def _codigo(self, resposta):
        self.assertIn(resposta.status_code, range(400, 600), resposta.text)
        return resposta.json()["codigo"]

    def _parceiro(self, campo, valor):
        return self.env["res.partner"].search([(campo, "=", valor)])

    # ------------------------------------------------------------------ AC1 operacao declarada
    def test_01_operacao_declarada_nas_capacidades(self):
        """A API serve a operacao e o que ela declara e' o que a POLITICA em vigor declara."""
        declarada = {
            op["nome"]: op
            for op in self.politica["operacoes"]
        }[OPERACAO]
        resposta = self._post({"correlation_id": "tre-e01-t02-capacidades"}, operacao="sistema_capacidades")
        self.assertEqual(resposta.status_code, 200, resposta.text)
        corpo = resposta.json()
        self.assertEqual(corpo["politica_versao"], self.politica["versao"], corpo)
        capacidades = {
            op["nome"]: op for op in corpo["dados"]["capacidades"]["operacoes"]
        }
        self.assertIn(OPERACAO, capacidades)
        self.assertEqual(capacidades[OPERACAO]["tipo"], declarada["tipo"])
        self.assertEqual(
            capacidades[OPERACAO]["requer_idempotency_key"],
            declarada["requer_idempotency_key"],
        )
        self.assertEqual(capacidades[OPERACAO]["modelos"], sorted(declarada["modelos"]))
        self.assertEqual(declarada["tipo"], "escrita")
        self.assertTrue(declarada["requer_idempotency_key"])

    # ------------------------------------------------------------------ AC3 cria e atualiza
    def test_02_cria_a_empresa_na_primeira_chamada(self):
        resposta = self._upsert(
            {
                "name": "Alfa Consultoria Ltda",
                "tf_company_id": UUID_1,
                "tf_cnpj": "11.222.333/0001-81",
                "tf_domain": "alfa.example",
                "tf_priority_score": 72.5,
            }
        )
        self.assertEqual(resposta.status_code, 200, resposta.text)
        dados = resposta.json()["dados"]
        self.assertEqual(dados["acao_efetiva"], "criar")
        self.assertEqual(len(dados["ids"]), 1)
        parceiro = self._parceiro("tf_company_id", UUID_1)
        self.assertEqual(len(parceiro), 1)
        self.assertEqual(parceiro.name, "Alfa Consultoria Ltda")
        self.assertTrue(parceiro.is_company)
        self.assertEqual(parceiro.tf_cnpj, "11.222.333/0001-81")
        self.assertEqual(parceiro.tf_domain, "alfa.example")
        self.assertEqual(parceiro.tf_priority_score, 72.5)
        self.assertEqual(parceiro.id, dados["ids"][0])

    def test_03_mesma_identidade_atualiza_sem_duplicar(self):
        self._upsert({"name": "Beta Servicos", "tf_company_id": UUID_1},
                     chave_idempotencia="tre-e01-t02-teste-0002")
        self.assertEqual(len(self._parceiro("tf_company_id", UUID_1)), 1)
        resposta = self._upsert(
            {"name": "Beta Servicos (novo nome)", "tf_company_id": UUID_1,
             "tf_priority_score": 91.0},
            chave_idempotencia="tre-e01-t02-teste-0003",
        )
        self.assertEqual(resposta.status_code, 200, resposta.text)
        dados = resposta.json()["dados"]
        self.assertEqual(dados["acao_efetiva"], "atualizar")
        parceiro = self._parceiro("tf_company_id", UUID_1)
        self.assertEqual(len(parceiro), 1)
        self.assertEqual(parceiro.ids, dados["ids"])
        self.assertEqual(parceiro.name, "Beta Servicos (novo nome)")
        self.assertEqual(parceiro.tf_priority_score, 91.0)
        # `is_company` e' valor fixo declarado: continua empresa depois da atualizacao (AC5).
        self.assertTrue(parceiro.is_company)

    def test_04_chamadas_repetidas_nao_duplicam(self):
        for indice in range(3):
            resposta = self._upsert(
                {"name": "Gama Tecnologia", "tf_company_id": UUID_2,
                 "tf_priority_score": 50.0 + indice},
                chave_idempotencia="tre-e01-t02-teste-repeticao-%d" % indice,
            )
            self.assertEqual(resposta.status_code, 200, resposta.text)
            acao_esperada = "criar" if indice == 0 else "atualizar"
            self.assertEqual(resposta.json()["dados"]["acao_efetiva"], acao_esperada)
        registros = self._parceiro("tf_company_id", UUID_2)
        self.assertEqual(len(registros), 1, "upsert por identidade duplicou a empresa")
        self.assertEqual(registros.tf_priority_score, 52.0)

    def test_05_identidade_por_cnpj_sem_o_canonico(self):
        cnpj = "22.333.444/0001-95"
        primeira = self._upsert({"name": "Delta Industria", "tf_cnpj": cnpj},
                                chave_idempotencia="tre-e01-t02-teste-0005")
        self.assertEqual(primeira.status_code, 200, primeira.text)
        self.assertEqual(primeira.json()["dados"]["acao_efetiva"], "criar")
        segunda = self._upsert({"name": "Delta Industria S.A.", "tf_cnpj": cnpj},
                               chave_idempotencia="tre-e01-t02-teste-0006")
        self.assertEqual(segunda.status_code, 200, segunda.text)
        self.assertEqual(segunda.json()["dados"]["acao_efetiva"], "atualizar")
        registros = self._parceiro("tf_cnpj", cnpj)
        self.assertEqual(len(registros), 1)
        self.assertEqual(registros.name, "Delta Industria S.A.")

    def test_06_identidade_por_dominio_e_por_linkedin(self):
        dominio = "epsilon.example"
        self._upsert({"name": "Epsilon", "tf_domain": dominio},
                     chave_idempotencia="tre-e01-t02-teste-0007")
        resposta = self._upsert({"name": "Epsilon Holding", "tf_domain": dominio},
                                chave_idempotencia="tre-e01-t02-teste-0008")
        self.assertEqual(resposta.json()["dados"]["acao_efetiva"], "atualizar")
        self.assertEqual(len(self._parceiro("tf_domain", dominio)), 1)
        linkedin = "https://www.linkedin.com/company/zeta-consultoria"
        self._upsert({"name": "Zeta", "tf_linkedin_url": linkedin},
                     chave_idempotencia="tre-e01-t02-teste-0009")
        resposta = self._upsert({"name": "Zeta Consultoria", "tf_linkedin_url": linkedin},
                                chave_idempotencia="tre-e01-t02-teste-0010")
        self.assertEqual(resposta.json()["dados"]["acao_efetiva"], "atualizar")
        self.assertEqual(len(self._parceiro("tf_linkedin_url", linkedin)), 1)
        self.assertEqual(self._parceiro("tf_linkedin_url", linkedin).name, "Zeta Consultoria")

    # ------------------------------------------------------------------ AC2 identidade obrigatoria
    def test_07_sem_identificador_recusa_422_e_nao_escreve(self):
        antes = self.env["res.partner"].search_count([])
        resposta = self._upsert(
            {"name": "Empresa sem identificador"},
            chave_idempotencia="tre-e01-t02-teste-0011",
        )
        self.assertEqual(resposta.status_code, 422, resposta.text)
        self.assertEqual(self._codigo(resposta), "identificador_ausente")
        self.assertEqual(self.env["res.partner"].search_count([]), antes)

    # ------------------------------------------------------------------ AC4 ambiguidade
    def test_08_identificadores_apontando_para_registros_diferentes_recusa_409(self):
        cnpj = "33.444.555/0001-06"
        dominio = "theta.example"
        primeiro = self.env["res.partner"].create(
            {"name": "Theta Um", "is_company": True, "tf_cnpj": cnpj}
        )
        segundo = self.env["res.partner"].create(
            {"name": "Theta Dois", "is_company": True, "tf_domain": dominio}
        )
        antes = self.env["res.partner"].search_count([])
        resposta = self._upsert(
            {"name": "Theta Fundida", "tf_cnpj": cnpj, "tf_domain": dominio},
            chave_idempotencia="tre-e01-t02-teste-0012",
        )
        self.assertEqual(resposta.status_code, 409, resposta.text)
        self.assertEqual(self._codigo(resposta), "valor_ambiguo")
        self.assertEqual(self.env["res.partner"].search_count([]), antes)
        self.assertEqual(primeiro.name, "Theta Um")
        self.assertEqual(segundo.name, "Theta Dois")
        self.assertFalse(primeiro.tf_domain)
        self.assertFalse(segundo.tf_cnpj)

    def test_09_dois_registros_com_o_mesmo_identificador_recusa_409(self):
        cnpj = "44.555.666/0001-17"
        self.env["res.partner"].create({"name": "Iota A", "is_company": True, "tf_cnpj": cnpj})
        self.env["res.partner"].create({"name": "Iota B", "is_company": True, "tf_cnpj": cnpj})
        resposta = self._upsert(
            {"name": "Iota C", "tf_cnpj": cnpj},
            chave_idempotencia="tre-e01-t02-teste-0013",
        )
        self.assertEqual(resposta.status_code, 409, resposta.text)
        self.assertEqual(self._codigo(resposta), "valor_ambiguo")
        self.assertEqual(len(self._parceiro("tf_cnpj", cnpj)), 2)

    def test_10_um_identificador_e_o_canonico_juntos_atualizam_o_mesmo_registro(self):
        """Identificadores do mesmo pedido casando O MESMO registro nao sao ambiguidade."""
        self.env["res.partner"].create(
            {"name": "Kappa", "is_company": True, "tf_company_id": UUID_3,
             "tf_cnpj": "55.666.777/0001-28"}
        )
        resposta = self._upsert(
            {"name": "Kappa Renomeada", "tf_company_id": UUID_3,
             "tf_cnpj": "55.666.777/0001-28"},
            chave_idempotencia="tre-e01-t02-teste-0014",
        )
        self.assertEqual(resposta.status_code, 200, resposta.text)
        self.assertEqual(resposta.json()["dados"]["acao_efetiva"], "atualizar")
        self.assertEqual(len(self._parceiro("tf_company_id", UUID_3)), 1)
        self.assertEqual(self._parceiro("tf_company_id", UUID_3).name, "Kappa Renomeada")

    # ------------------------------------------------------------------ AC5 valor fixo
    def test_11_valor_fixo_divergente_recusa_422(self):
        antes = self.env["res.partner"].search_count([])
        resposta = self._upsert(
            {"name": "Lambda Pessoa", "is_company": False, "tf_company_id": UUID_1},
            chave_idempotencia="tre-e01-t02-teste-0015",
        )
        self.assertEqual(resposta.status_code, 422, resposta.text)
        self.assertEqual(self._codigo(resposta), "campo_fixo_divergente")
        self.assertEqual(self.env["res.partner"].search_count([]), antes)

    # ------------------------------------------------------------------ AC6 contrato de integracao
    def test_12_escrita_sem_idempotency_key_recusa_422(self):
        resposta = self._post({"parametros": {"valores": {"name": "Sem chave", "tf_company_id": UUID_1}}})
        self.assertEqual(resposta.status_code, 422, resposta.text)
        self.assertEqual(self._codigo(resposta), "idempotency_key_ausente")

    def test_13_idempotency_key_fora_do_formato_recusa_422(self):
        resposta = self._post(
            {
                "idempotency_key": "curta!!",
                "parametros": {"valores": {"name": "Chave torta", "tf_company_id": UUID_1}},
            }
        )
        self.assertEqual(resposta.status_code, 422, resposta.text)
        self.assertEqual(self._codigo(resposta), "idempotency_key_invalida")

    def test_14_dry_run_descreve_sem_criar_e_sem_atualizar(self):
        nova = self._upsert(
            {"name": "Mu Nova", "tf_company_id": UUID_2, "tf_priority_score": 10.0},
            chave_idempotencia="tre-e01-t02-teste-0016",
            dry_run=True,
        )
        self.assertEqual(nova.status_code, 200, nova.text)
        corpo = nova.json()
        self.assertTrue(corpo["dry_run"])
        self.assertEqual(corpo["dados"]["acao_efetiva"], "criar")
        self.assertIn("is_company", corpo["dados"]["criaria"])
        self.assertEqual(len(self._parceiro("tf_company_id", UUID_2)), 0)
        # agora a empresa existe: dry_run tem de dizer "atualizar" e nao mudar o nome
        self._upsert({"name": "Mu Existente", "tf_company_id": UUID_2},
                     chave_idempotencia="tre-e01-t02-teste-0017")
        existente = self._upsert(
            {"name": "Mu Nome Que Nao Deve Entrar", "tf_company_id": UUID_2},
            chave_idempotencia="tre-e01-t02-teste-0018",
            dry_run=True,
        )
        self.assertEqual(existente.status_code, 200, existente.text)
        dados = existente.json()["dados"]
        self.assertEqual(dados["acao_efetiva"], "atualizar")
        self.assertEqual(dados["id"], self._parceiro("tf_company_id", UUID_2).id)
        self.assertEqual(self._parceiro("tf_company_id", UUID_2).name, "Mu Existente")

    def test_15_uuid_canonico_fora_do_formato_recusa_422(self):
        antes = self.env["res.partner"].search_count([])
        resposta = self._upsert(
            {"name": "Nu Invalida", "tf_company_id": "nao-e-uuid"},
            chave_idempotencia="tre-e01-t02-teste-0019",
        )
        self.assertEqual(resposta.status_code, 422, resposta.text)
        self.assertEqual(self._codigo(resposta), "valor_invalido")
        self.assertEqual(self.env["res.partner"].search_count([]), antes)

    def test_16_campo_fora_da_declaracao_recusa_422(self):
        resposta = self._upsert(
            {"name": "Xi", "tf_company_id": UUID_1, "email": "xi@example.com"},
            chave_idempotencia="tre-e01-t02-teste-0020",
        )
        self.assertEqual(resposta.status_code, 422, resposta.text)
        self.assertEqual(self._codigo(resposta), "campo_nao_declarado")

    def test_17_envelope_ecoa_correlation_id_e_idempotency_key(self):
        resposta = self._upsert(
            {"name": "Omicron", "tf_company_id": UUID_3},
            chave_idempotencia="tre-e01-t02-teste-0021",
            correlation_id="tre-e01-t02-correlacao",
        )
        self.assertEqual(resposta.status_code, 200, resposta.text)
        corpo = resposta.json()
        self.assertEqual(corpo["correlation_id"], "tre-e01-t02-correlacao")
        self.assertEqual(corpo["idempotency_key"], "tre-e01-t02-teste-0021")
        self.assertEqual(corpo["operacao"], OPERACAO)
        self.assertEqual(corpo["ambiente"], "dev")
        self.assertFalse(corpo["dry_run"])

    # ------------------------------------------------------------------ AC7 ambiente + rastro
    def test_18_ambiente_fora_da_politica_recusa_503_e_nao_escreve(self):
        self.icp.set_param("tf.api.ambiente", "homologacao")
        antes = self.env["res.partner"].search_count([])
        resposta = self._upsert(
            {"name": "Pi", "tf_company_id": UUID_1},
            chave_idempotencia="tre-e01-t02-teste-0022",
        )
        self.assertEqual(resposta.status_code, 503, resposta.text)
        self.assertEqual(self._codigo(resposta), "ambiente_nao_permitido")
        self.assertEqual(self.env["res.partner"].search_count([]), antes)

    def test_19_auditoria_da_escrita_registra_ids_sem_payload_e_sem_token(self):
        with self.assertLogs("transformativa_sales_ai.api", level="INFO") as captura:
            self._upsert(
                {"name": "Rho Marcada", "tf_company_id": UUID_1},
                chave_idempotencia="tre-e01-t02-teste-0023",
                correlation_id="tre-e01-t02-auditoria",
            )
        linhas = [linha for linha in captura.output if "TF_API_AUDIT" in linha]
        self.assertEqual(len(linhas), 1, captura.output)
        linha = linhas[0]
        self.assertIn('"resultado": "ok"', linha)
        self.assertIn('"correlation_id": "tre-e01-t02-auditoria"', linha)
        self.assertIn('"operacao": "empresa_upsert"', linha)
        self.assertIn('"acao": "upsert"', linha)
        self.assertIn('"modelo": "res.partner"', linha)
        self.assertIn('"idempotency_key": "tre-e01-t02-teste-0023"', linha)
        self.assertNotIn("Bearer", linha)
        self.assertNotIn("Rho Marcada", linha)
        ids_do_banco = self._parceiro("tf_company_id", UUID_1).ids
        self.assertIn('"ids": %s' % json.dumps(ids_do_banco), linha)
