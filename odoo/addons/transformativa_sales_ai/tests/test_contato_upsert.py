# -*- coding: utf-8 -*-
"""Aceite da operacao de escrita de negocio `contato_upsert` — card TRE-W3-E01-T03.

O QUE ESTA SUITE PROVA (item por item, contra o Odoo de verdade e por HTTP de verdade):

  AC1 operacao declarada: `contato_upsert` aparece nas capacidades da API com o tipo que a
      POLITICA em vigor declara (o teste le o arquivo da politica — nao ha' versao literal aqui,
      senao o item expira a cada versao nova);
  AC2 identidade declarada: o valor de identidade (e-mail) ausente -> 422 `identificador_ausente`
      e nada escrito; `identificador` escalar (forma da identidade por UM campo) NAO substitui a
      identidade declarada por lista — e' recusado, nunca descartado em silencio;
  AC3 upsert idempotente: o mesmo contato cria UMA vez e atualiza depois (`acao_efetiva`), a
      contagem por identidade nao cresce em chamadas repetidas;
  AC4 ambiguidade: mais de um parceiro com o mesmo e-mail -> 409 `valor_ambiguo`, sem criar e sem
      alterar ninguem (contrato §5: ambiguidade e' reportada, nunca resolvida por heuristica);
  AC5 semantica de contato (PESSOA): `is_company` e' valor FIXO declarado — o parceiro nasce
      pessoa, e o chamador que tentar decidir esse valor leva 422 `campo_fixo_divergente`;
  AC6 contrato de integracao: `idempotency_key` exigida e validada; `dry_run` descreve e NAO
      escreve (nem cria nem atualiza); campo fora da declaracao -> 422; `correlation_id` ecoado;
  AC7 guarda de ambiente + rastro: ambiente fora da politica -> 503 (nada escrito) e a escrita
      bem-sucedida deixa UMA linha `TF_API_AUDIT` com os ids, sem payload e sem token;
  AC9 escopo declarado do compliance: os campos de opt-out / `legal_basis` / `preferred_channel`
      NAO existem no espelho (sao do schema do PostgreSQL, contrato §9) e envia-los e' recusa.

O QUE ESTA SUITE NAO PROVA (declarado, para nao vender mais do que mede):
  * que o consumidor externo (n8n) funciona — e' a fase HTTP por `curl` do verificador;
  * a dedup por `idempotency_key` (retry/replay, card TRE-W3-E02-T02): aqui a chave e' exigida e
    validada; a garantia de "nao duplicar" medida aqui vem da IDENTIDADE;
  * merge de contatos ja' duplicados na base — e' do TRE-W3-E04-T01 (reconciliacao);
  * normalizacao do e-mail: igual a' operacao de empresa, o contrato V1 nao define normalizacao —
    o valor e' guardado e casado COMO RECEBIDO (risco residual registrado no runbook do card).
"""

from datetime import datetime, timedelta
import json

from odoo.modules.module import get_module_path
from odoo.tests import HttpCase, new_test_user, tagged

MODULO = "transformativa_sales_ai"
OPERACAO = "contato_upsert"

EMAIL_1 = "ana.souza@exemplo.example"
EMAIL_2 = "bruno.lima@exemplo.example"
EMAIL_3 = "carla.mendes@exemplo.example"

# Campos do escopo de COMPLIANCE (contrato §9): vivem no PostgreSQL (`sales_intelligence.contacts`).
# A suite confere que eles NAO existem no espelho operacional — lacuna declarada, nunca silenciosa.
CAMPOS_DE_COMPLIANCE = (
    "do_not_contact",
    "opt_out_email",
    "opt_out_whatsapp",
    "preferred_channel",
    "legal_basis",
)


@tagged("post_install", "-at_install")
class TestContatoUpsert(HttpCase):
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
            login="tf_api_contato",
            groups="base.group_user,%s.group_tf_sales_ai_user,sales_team.group_sale_salesman"
            % MODULO,
        )
        cls.chave = cls.env["res.users.apikeys"].with_user(cls.usuario)._generate(
            scope="rpc",
            name="teste-contato-upsert",
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

    def _upsert(self, valores, chave_idempotencia="tre-e01-t03-teste-0001", **extra):
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
        declarada = {op["nome"]: op for op in self.politica["operacoes"]}[OPERACAO]
        resposta = self._post(
            {"correlation_id": "tre-e01-t03-capacidades"}, operacao="sistema_capacidades"
        )
        self.assertEqual(resposta.status_code, 200, resposta.text)
        corpo = resposta.json()
        self.assertEqual(corpo["politica_versao"], self.politica["versao"], corpo)
        capacidades = {op["nome"]: op for op in corpo["dados"]["capacidades"]["operacoes"]}
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
    def test_02_cria_o_contato_na_primeira_chamada(self):
        resposta = self._upsert(
            {
                "name": "Ana Souza",
                "email": EMAIL_1,
                "function": "Diretora de Operacoes",
                "phone": "+55 11 90000-0001",
            }
        )
        self.assertEqual(resposta.status_code, 200, resposta.text)
        dados = resposta.json()["dados"]
        self.assertEqual(dados["acao_efetiva"], "criar")
        self.assertEqual(len(dados["ids"]), 1)
        contato = self._parceiro("email", EMAIL_1)
        self.assertEqual(len(contato), 1)
        self.assertEqual(contato.name, "Ana Souza")
        self.assertEqual(contato.function, "Diretora de Operacoes")
        self.assertEqual(contato.phone, "+55 11 90000-0001")
        # AC5: o parceiro nasce PESSOA — medido no banco, nao no envelope.
        self.assertFalse(contato.is_company)
        self.assertEqual(contato.id, dados["ids"][0])

    def test_03_mesma_identidade_atualiza_sem_duplicar(self):
        self._upsert({"name": "Bruno Lima", "email": EMAIL_1},
                     chave_idempotencia="tre-e01-t03-teste-0002")
        self.assertEqual(len(self._parceiro("email", EMAIL_1)), 1)
        resposta = self._upsert(
            {"name": "Ana Souza Lima", "email": EMAIL_1, "function": "COO"},
            chave_idempotencia="tre-e01-t03-teste-0003",
        )
        self.assertEqual(resposta.status_code, 200, resposta.text)
        dados = resposta.json()["dados"]
        self.assertEqual(dados["acao_efetiva"], "atualizar")
        contato = self._parceiro("email", EMAIL_1)
        self.assertEqual(len(contato), 1)
        self.assertEqual(contato.ids, dados["ids"])
        self.assertEqual(contato.name, "Ana Souza Lima")
        self.assertEqual(contato.function, "COO")
        # `is_company` e' valor fixo declarado: continua PESSOA depois da atualizacao (AC5).
        self.assertFalse(contato.is_company)

    def test_04_chamadas_repetidas_nao_duplicam(self):
        for indice in range(3):
            resposta = self._upsert(
                {"name": "Carla Mendes %d" % indice, "email": EMAIL_2},
                chave_idempotencia="tre-e01-t03-teste-repeticao-%d" % indice,
            )
            self.assertEqual(resposta.status_code, 200, resposta.text)
            acao_esperada = "criar" if indice == 0 else "atualizar"
            self.assertEqual(resposta.json()["dados"]["acao_efetiva"], acao_esperada)
        registros = self._parceiro("email", EMAIL_2)
        self.assertEqual(len(registros), 1, "upsert por identidade duplicou o contato")
        self.assertEqual(registros.name, "Carla Mendes 2")

    # ------------------------------------------------------------------ AC2 identidade obrigatoria
    def test_05_sem_email_recusa_422_e_nao_escreve(self):
        antes = self.env["res.partner"].search_count([])
        resposta = self._upsert(
            {"name": "Contato sem e-mail"},
            chave_idempotencia="tre-e01-t03-teste-0005",
        )
        self.assertEqual(resposta.status_code, 422, resposta.text)
        self.assertEqual(self._codigo(resposta), "identificador_ausente")
        self.assertEqual(self.env["res.partner"].search_count([]), antes)

    def test_06_identificador_escalar_nao_substitui_a_identidade_declarada(self):
        """O parametro da forma 'um campo' e' RECUSADO (nao descartado em silencio) — portao do E01-T03."""
        antes = self.env["res.partner"].search_count([])
        resposta = self._post(
            {
                "idempotency_key": "tre-e01-t03-teste-0006",
                "parametros": {
                    "identificador": EMAIL_3,
                    "valores": {"name": "Contato por identificador escalar"},
                },
            }
        )
        self.assertEqual(resposta.status_code, 400, resposta.text)
        self.assertEqual(self._codigo(resposta), "payload_invalido")
        self.assertEqual(self.env["res.partner"].search_count([]), antes)
        self.assertEqual(len(self._parceiro("email", EMAIL_3)), 0)

    # ------------------------------------------------------------------ AC4 ambiguidade
    def test_07_dois_registros_com_o_mesmo_email_recusa_409(self):
        self.env["res.partner"].create({"name": "Dora A", "is_company": False, "email": EMAIL_3})
        self.env["res.partner"].create({"name": "Dora B", "is_company": False, "email": EMAIL_3})
        antes = self.env["res.partner"].search_count([])
        resposta = self._upsert(
            {"name": "Dora C", "email": EMAIL_3},
            chave_idempotencia="tre-e01-t03-teste-0007",
        )
        self.assertEqual(resposta.status_code, 409, resposta.text)
        self.assertEqual(self._codigo(resposta), "valor_ambiguo")
        self.assertEqual(self.env["res.partner"].search_count([]), antes)
        registros = self._parceiro("email", EMAIL_3)
        self.assertEqual(len(registros), 2)
        self.assertEqual(sorted(registros.mapped("name")), ["Dora A", "Dora B"])

    def test_08_email_ja_existente_atualiza_o_mesmo_registro(self):
        self.env["res.partner"].create({"name": "Eva Ribeiro", "is_company": False,
                                        "email": "eva.ribeiro@exemplo.example"})
        resposta = self._upsert(
            {"name": "Eva Ribeiro Goncalves", "email": "eva.ribeiro@exemplo.example"},
            chave_idempotencia="tre-e01-t03-teste-0008",
        )
        self.assertEqual(resposta.status_code, 200, resposta.text)
        self.assertEqual(resposta.json()["dados"]["acao_efetiva"], "atualizar")
        registros = self._parceiro("email", "eva.ribeiro@exemplo.example")
        self.assertEqual(len(registros), 1)
        self.assertEqual(registros.name, "Eva Ribeiro Goncalves")

    # ------------------------------------------------------------------ AC5 valor fixo
    def test_09_valor_fixo_divergente_recusa_422(self):
        antes = self.env["res.partner"].search_count([])
        resposta = self._upsert(
            {"name": "Fabio Empresa", "email": "fabio@exemplo.example", "is_company": True},
            chave_idempotencia="tre-e01-t03-teste-0009",
        )
        self.assertEqual(resposta.status_code, 422, resposta.text)
        self.assertEqual(self._codigo(resposta), "campo_fixo_divergente")
        self.assertEqual(self.env["res.partner"].search_count([]), antes)

    def test_10_valor_fixo_tambem_e_aplicado_na_atualizacao(self):
        """Comportamento DECLARADO e medido: registro que ja' e' empresa e casa por e-mail vira pessoa.

        E' consequencia direta de `is_company` ser valor FIXO da operacao (a operacao e' de pessoa) e
        de a identidade ser o e-mail. Restringir a identidade a pessoas exigiria declarar ESCOPO de
        identidade — que o contrato V1 nao define. Risco residual registrado no runbook do card
        (`docs/runbooks/odoo-contato-upsert.md`, secao Lacunas), com card de decisao proprio: o que
        este item garante e' que o comportamento nao e' SILENCIOSO.
        """
        empresa = self.env["res.partner"].create(
            {"name": "Empresa Com Email", "is_company": True, "email": "contato@empresa.example"}
        )
        self.assertTrue(empresa.is_company)
        resposta = self._upsert(
            {"name": "Contato Da Empresa", "email": "contato@empresa.example"},
            chave_idempotencia="tre-e01-t03-teste-0010",
        )
        self.assertEqual(resposta.status_code, 200, resposta.text)
        self.assertEqual(resposta.json()["dados"]["acao_efetiva"], "atualizar")
        self.assertEqual(resposta.json()["dados"]["ids"], [empresa.id])
        self.assertFalse(empresa.is_company, "o valor fixo declarado tem de ser aplicado na atualizacao")

    # ------------------------------------------------------------------ AC6 contrato de integracao
    def test_11_escrita_sem_idempotency_key_recusa_422(self):
        resposta = self._post(
            {"parametros": {"valores": {"name": "Sem chave", "email": EMAIL_1}}}
        )
        self.assertEqual(resposta.status_code, 422, resposta.text)
        self.assertEqual(self._codigo(resposta), "idempotency_key_ausente")

    def test_12_idempotency_key_fora_do_formato_recusa_422(self):
        resposta = self._post(
            {
                "idempotency_key": "curta!!",
                "parametros": {"valores": {"name": "Chave torta", "email": EMAIL_1}},
            }
        )
        self.assertEqual(resposta.status_code, 422, resposta.text)
        self.assertEqual(self._codigo(resposta), "idempotency_key_invalida")

    def test_13_dry_run_descreve_sem_criar_e_sem_atualizar(self):
        nova = self._upsert(
            {"name": "Gisele Nova", "email": "gisele.nova@exemplo.example"},
            chave_idempotencia="tre-e01-t03-teste-0013",
            dry_run=True,
        )
        self.assertEqual(nova.status_code, 200, nova.text)
        corpo = nova.json()
        self.assertTrue(corpo["dry_run"])
        self.assertEqual(corpo["dados"]["acao_efetiva"], "criar")
        self.assertIn("is_company", corpo["dados"]["criaria"])
        self.assertEqual(len(self._parceiro("email", "gisele.nova@exemplo.example")), 0)
        # agora o contato existe: dry_run tem de dizer "atualizar" e nao mudar o nome
        self._upsert({"name": "Gisele Existente", "email": "gisele.nova@exemplo.example"},
                     chave_idempotencia="tre-e01-t03-teste-0014")
        existente = self._upsert(
            {"name": "Gisele Nome Que Nao Deve Entrar", "email": "gisele.nova@exemplo.example"},
            chave_idempotencia="tre-e01-t03-teste-0015",
            dry_run=True,
        )
        self.assertEqual(existente.status_code, 200, existente.text)
        dados = existente.json()["dados"]
        self.assertEqual(dados["acao_efetiva"], "atualizar")
        self.assertEqual(dados["id"], self._parceiro("email", "gisele.nova@exemplo.example").id)
        self.assertEqual(
            self._parceiro("email", "gisele.nova@exemplo.example").name, "Gisele Existente"
        )

    def test_14_campo_fora_da_declaracao_recusa_422(self):
        """Campo de EMPRESA na operacao de contato: fronteira declarada, recusa nomeada."""
        resposta = self._upsert(
            {"name": "Helena", "email": EMAIL_1, "tf_cnpj": "11.222.333/0001-81"},
            chave_idempotencia="tre-e01-t03-teste-0016",
        )
        self.assertEqual(resposta.status_code, 422, resposta.text)
        self.assertEqual(self._codigo(resposta), "campo_nao_declarado")

    def test_15_campo_de_compliance_recusado_422(self):
        """AC9: opt-out e' do PostgreSQL (contrato §9) — enviar aqui e' recusa, nunca ignorado."""
        for campo, valor in (("do_not_contact", True), ("opt_out_email", True),
                             ("preferred_channel", "email"), ("legal_basis", "legitimo_interesse")):
            resposta = self._upsert(
                {"name": "Iara", "email": EMAIL_2, campo: valor},
                chave_idempotencia="tre-e01-t03-compliance-%s" % campo.replace("_", "-"),
            )
            self.assertEqual(resposta.status_code, 422, "%s: %s" % (campo, resposta.text))
            self.assertEqual(self._codigo(resposta), "campo_nao_declarado")

    def test_16_envelope_ecoa_correlation_id_e_idempotency_key(self):
        resposta = self._upsert(
            {"name": "Joana Prado", "email": "joana.prado@exemplo.example"},
            chave_idempotencia="tre-e01-t03-teste-0017",
            correlation_id="tre-e01-t03-correlacao",
        )
        self.assertEqual(resposta.status_code, 200, resposta.text)
        corpo = resposta.json()
        self.assertEqual(corpo["correlation_id"], "tre-e01-t03-correlacao")
        self.assertEqual(corpo["idempotency_key"], "tre-e01-t03-teste-0017")
        self.assertEqual(corpo["operacao"], OPERACAO)
        self.assertEqual(corpo["ambiente"], "dev")
        self.assertFalse(corpo["dry_run"])

    # ------------------------------------------------------------------ AC7 ambiente + rastro
    def test_17_ambiente_fora_da_politica_recusa_503_e_nao_escreve(self):
        self.icp.set_param("tf.api.ambiente", "homologacao")
        antes = self.env["res.partner"].search_count([])
        resposta = self._upsert(
            {"name": "Kleber", "email": EMAIL_1},
            chave_idempotencia="tre-e01-t03-teste-0018",
        )
        self.assertEqual(resposta.status_code, 503, resposta.text)
        self.assertEqual(self._codigo(resposta), "ambiente_nao_permitido")
        self.assertEqual(self.env["res.partner"].search_count([]), antes)

    def test_18_auditoria_da_escrita_registra_ids_sem_payload_e_sem_token(self):
        with self.assertLogs("transformativa_sales_ai.api", level="INFO") as captura:
            self._upsert(
                {"name": "Livia Marca da", "email": "livia.marcada@exemplo.example"},
                chave_idempotencia="tre-e01-t03-teste-0019",
                correlation_id="tre-e01-t03-auditoria",
            )
        linhas = [linha for linha in captura.output if "TF_API_AUDIT" in linha]
        self.assertEqual(len(linhas), 1, captura.output)
        linha = linhas[0]
        self.assertIn('"resultado": "ok"', linha)
        self.assertIn('"correlation_id": "tre-e01-t03-auditoria"', linha)
        self.assertIn('"operacao": "contato_upsert"', linha)
        self.assertIn('"acao": "upsert"', linha)
        self.assertIn('"modelo": "res.partner"', linha)
        self.assertIn('"idempotency_key": "tre-e01-t03-teste-0019"', linha)
        self.assertNotIn("Bearer", linha)
        # O e-mail e' dado pessoal: nao entra na trilha (o rastro do contato e' o id).
        self.assertNotIn("livia.marcada@exemplo.example", linha)
        self.assertNotIn("Livia Marca da", linha)
        ids_do_banco = self._parceiro("email", "livia.marcada@exemplo.example").ids
        self.assertIn('"ids": %s' % json.dumps(ids_do_banco), linha)

    # ------------------------------------------------------------------ AC9 escopo do compliance
    def test_19_campos_de_compliance_nao_existem_no_espelho(self):
        """O escopo declarado e' medido no MODELO: a lacuna do contrato §9 e' real, nao suposicao."""
        campos = self.env["res.partner"]._fields
        for campo in CAMPOS_DE_COMPLIANCE:
            self.assertNotIn(
                campo,
                campos,
                "campo de compliance %r existe no espelho: o escopo declarado (contrato §9) e' outro"
                % campo,
            )
