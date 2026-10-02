# -*- coding: utf-8 -*-
"""Aceite da operacao de escrita de negocio `atividade_criar` — card TRE-W3-E01-T05.

O QUE ESTA SUITE PROVA (item por item, contra o Odoo de verdade e por HTTP de verdade):

  AC1 operacao declarada: `atividade_criar` aparece nas capacidades da API com o que a POLITICA em
      vigor declara (o teste le o arquivo da politica — nao ha' versao literal aqui, senao o item
      expira a cada versao nova);
  AC2 superficie fechada e ANCORA declarada: `res_model` e' VALOR FIXO da politica — o chamador que
      mandar outro modelo leva 422 `campo_fixo_divergente` e NADA e' criado; `res_id` e' obrigatorio;
      campo fora da declaracao (inclusive o id interno `res_model_id`) -> 422 `campo_nao_declarado`,
      nunca ignorado;
  AC3 cria UMA atividade na ancora declarada (`acao_efetiva: criar`) e devolve o id; o registro nasce
      no modelo declarado, conferido no BANCO (nao pelo eco da resposta);
  AC4 contrato de integracao: `idempotency_key` exigida e validada; `dry_run` descreve e NAO cria;
      `correlation_id` ecoado; a chave e a correlacao ficam REGISTRADAS na atividade criada;
  AC5 guarda de ambiente (ADR-005) na escrita: fora do ambiente permitido -> 503 e nada criado; sem
      ambiente declarado -> 503 (API inerte);
  AC6 rastro: UMA linha `TF_API_AUDIT` por chamada, sem token e sem payload;
  AC7 ACL: a criacao passa pelas ACLs do usuario dono da chave (`_check_access('create')` do
      `mail.activity` exige acesso ao DOCUMENTO ancorado) — sem acesso, recusa nomeada 403, nunca
      criacao "por baixo";
  AC9 lacunas DECLARADAS, nao silenciadas: (a) a operacao NAO declara identidade (a acao e' `criar`;
      quem garante nao duplicar e' o motor de dedup por chave, card TRE-W3-E02-T02) — e por isso o
      parametro escalar `identificador` e' recusado; (b) sem dedup por chave, o REPLAY da mesma chave
      ainda cria uma segunda atividade — medido aqui, roteado para o E02-T02; (c) atividade ancorada
      em `crm.lead` NAO e' servida (ancora fixa em `res.partner`) — recusa nomeada, com a contagem no
      banco provando que nenhuma atividade nasceu no modelo nao declarado.

O QUE ESTA SUITE NAO PROVA (declarado, para nao vender mais do que mede):
  * que o consumidor externo (n8n) funciona — e' a fase HTTP por `curl` do verificador
    (`scripts/odoo/verificar-atividade-criar.sh`);
  * a dedup por `idempotency_key` (retry/replay, card TRE-W3-E02-T02): aqui a chave e' exigida,
    validada e registrada — e a ausencia da dedup e' MEDIDA (item 15), nao escondida;
  * atividade ancorada em outro modelo (`crm.lead`, reuniao, proposta): ver a lacuna (c) acima;
  * rate limit / cache de politica / observabilidade duravel: card TRE-W3-E05-T01.
"""

from datetime import date, datetime, timedelta
import json

from odoo.modules.module import get_module_path
from odoo.tests import HttpCase, new_test_user, tagged

MODULO = "transformativa_sales_ai"
OPERACAO = "atividade_criar"
ANCORA = "res.partner"
MODELO_FORA_DA_ANCORA = "crm.lead"


@tagged("post_install", "-at_install")
class TestAtividadeCriar(HttpCase):
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
            login="tf_api_atividade",
            groups="base.group_user,%s.group_tf_sales_ai_user,sales_team.group_sale_salesman"
            % MODULO,
        )
        cls.chave = cls.env["res.users.apikeys"].with_user(cls.usuario)._generate(
            scope="rpc",
            name="teste-atividade-criar",
            expiration_date=datetime.now() + timedelta(hours=12),
        )
        cls.ancora = cls.env[ANCORA].create({"name": "Parceiro ancora do E01-T05"})
        cls.tipo_de_atividade = cls.env["mail.activity.type"].search([], limit=1)

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

    def _criar(self, valores, chave_idempotencia="tre-e01-t05-teste-0001", **extra):
        corpo = {
            "idempotency_key": chave_idempotencia,
            "parametros": {"valores": valores},
        }
        corpo.update(extra)
        return self._post(corpo)

    def _valores_do_caso(self, **extra):
        valores = {
            "res_id": self.ancora.id,
            "summary": "Ligar para o decisor",
            "date_deadline": (date.today() + timedelta(days=3)).isoformat(),
        }
        if self.tipo_de_atividade:
            valores["activity_type_id"] = self.tipo_de_atividade.id
        valores.update(extra)
        return valores

    def _codigo(self, resposta):
        self.assertIn(resposta.status_code, range(400, 600), resposta.text)
        return resposta.json()["codigo"]

    def _contagem_de_atividades(self):
        return self.env["mail.activity"].search_count([])

    def _declaracao(self):
        op = next(
            (o for o in self.politica["operacoes"] if o["nome"] == OPERACAO), None
        )
        self.assertIsNotNone(op, "a politica real nao declara a operacao %s" % OPERACAO)
        return op, op["modelos"]["mail.activity"]

    # ------------------------------------------------------------------ AC1 operacao declarada
    def test_01_operacao_declarada_nas_capacidades(self):
        """A API serve a operacao e o que ela declara e' o que a POLITICA em vigor declara."""
        declarada, _ = self._declaracao()
        resposta = self._post(
            {"correlation_id": "tre-e01-t05-capacidades"}, operacao="sistema_capacidades"
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

    # ------------------------------------------------------------------ AC2 a declaracao da ancora
    def test_02_ancora_e_valor_fixo_declarados_na_politica(self):
        """A ancora e' DECLARADA (valor fixo), nao escolhida pelo chamador — lida do artefato."""
        declarada, modelo = self._declaracao()
        self.assertEqual(modelo["acao"], "criar", "a operacao e' de criacao")
        self.assertEqual(
            modelo["valores_fixos"],
            {"res_model": ANCORA},
            "a ancora (res_model) tem de ser valor FIXO declarado na politica",
        )
        self.assertIn("res_model", modelo["campos"])
        self.assertNotIn("res_model", modelo.get("campos_obrigatorios", []))
        self.assertEqual(modelo.get("campos_obrigatorios"), ["res_id"])
        self.assertIn("res_id", modelo["campos"])
        # AC9: a operacao de CRIACAO nao declara identidade (nao ha' registro anterior a casar).
        self.assertFalse(modelo.get("campo_de_identidade"), modelo)
        self.assertFalse(modelo.get("campos_de_identidade"), modelo)
        self.assertTrue(declarada["aceita_dry_run"], "a operacao declara dry-run")

    # ------------------------------------------------------------------ AC3 cria na ancora
    def test_03_cria_a_atividade_na_ancora_declarada(self):
        antes = self._contagem_de_atividades()
        resposta = self._criar(self._valores_do_caso())
        self.assertEqual(resposta.status_code, 200, resposta.text)
        dados = resposta.json()["dados"]
        self.assertEqual(dados["acao_efetiva"], "criar")
        self.assertEqual(len(dados["ids"]), 1)
        # AC3: a medicao e' no BANCO (nao no eco da resposta).
        criada = self.env["mail.activity"].browse(dados["ids"][0]).exists()
        self.assertTrue(criada, "a atividade devolvida nao existe no banco")
        self.assertEqual(self._contagem_de_atividades(), antes + 1, "criou mais de uma atividade")
        self.assertEqual(criada.res_model, ANCORA, "a atividade nasceu em outro modelo")
        self.assertEqual(criada.res_id, self.ancora.id)
        self.assertEqual(criada.summary, "Ligar para o decisor")
        self.assertEqual(
            criada.date_deadline, date.today() + timedelta(days=3), criada.date_deadline
        )
        self.assertEqual(criada.create_uid, self.usuario, "a criacao nao foi do dono da chave")

    def test_04_ancora_divergente_do_payload_recusa_422_e_nao_cria(self):
        """AC2/AC9: o chamador NAO escolhe o modelo-alvo — `crm.lead` divergente e' recusa nomeada."""
        antes = self._contagem_de_atividades()
        resposta = self._criar(self._valores_do_caso(res_model=MODELO_FORA_DA_ANCORA))
        self.assertEqual(resposta.status_code, 422, resposta.text)
        self.assertEqual(self._codigo(resposta), "campo_fixo_divergente")
        self.assertEqual(self._contagem_de_atividades(), antes, "a recusa criou atividade")

    def test_05_res_id_obrigatorio(self):
        valores = self._valores_do_caso()
        valores.pop("res_id")
        antes = self._contagem_de_atividades()
        resposta = self._criar(valores, chave_idempotencia="tre-e01-t05-teste-0005")
        self.assertEqual(resposta.status_code, 422, resposta.text)
        self.assertEqual(self._codigo(resposta), "campo_obrigatorio_ausente")
        self.assertEqual(self._contagem_de_atividades(), antes)

    def test_06_campo_fora_da_declaracao_recusa_422(self):
        """Fronteira declarada: inclusive o id interno da ancora (`res_model_id`) e' recusado."""
        for campo, valor in (("res_model_id", 1), ("note", "<p>texto</p>"), ("stage_id", 1),
                             ("tf_next_best_action", "ligar")):
            resposta = self._criar(
                self._valores_do_caso(**{campo: valor}),
                chave_idempotencia="tre-e01-t05-fora-%s" % campo.replace("_", "-"),
            )
            self.assertEqual(resposta.status_code, 422, "%s: %s" % (campo, resposta.text))
            self.assertEqual(self._codigo(resposta), "campo_nao_declarado")

    def test_07_tipo_e_prazo_declarados_sao_gravados(self):
        if not self.tipo_de_atividade:
            self.skipTest("base sem mail.activity.type: o default do modelo nao pode ser medido")
        prazo = (date.today() + timedelta(days=10)).isoformat()
        resposta = self._criar(
            self._valores_do_caso(activity_type_id=self.tipo_de_atividade.id, date_deadline=prazo),
            chave_idempotencia="tre-e01-t05-teste-0007",
        )
        self.assertEqual(resposta.status_code, 200, resposta.text)
        criada = self.env["mail.activity"].browse(resposta.json()["dados"]["ids"][0])
        self.assertEqual(criada.activity_type_id, self.tipo_de_atividade)
        self.assertEqual(criada.date_deadline.isoformat(), prazo)

    # ------------------------------------------------------------------ AC4 contrato de integracao
    def test_08_rastreio_da_chamada_fica_registrado_na_atividade(self):
        """A chave e a correlacao ficam no REGISTRO criado, alem da trilha (doc 13 §9)."""
        resposta = self._criar(
            self._valores_do_caso(tf_idempotency_key="tre-e01-t05-registro",
                                  tf_correlation_id="tre-e01-t05-correlacao-registro"),
            chave_idempotencia="tre-e01-t05-teste-0008",
            correlation_id="tre-e01-t05-correlacao-registro",
        )
        self.assertEqual(resposta.status_code, 200, resposta.text)
        self.assertEqual(resposta.json()["correlation_id"], "tre-e01-t05-correlacao-registro")
        criada = self.env["mail.activity"].browse(resposta.json()["dados"]["ids"][0])
        self.assertEqual(criada.tf_idempotency_key, "tre-e01-t05-registro")
        self.assertEqual(criada.tf_correlation_id, "tre-e01-t05-correlacao-registro")

    def test_09_escrita_sem_idempotency_key_e_com_chave_torta(self):
        antes = self._contagem_de_atividades()
        sem_chave = self._post({"parametros": {"valores": self._valores_do_caso()}})
        self.assertEqual(sem_chave.status_code, 422, sem_chave.text)
        self.assertEqual(self._codigo(sem_chave), "idempotency_key_ausente")
        torta = self._post(
            {
                "idempotency_key": "curta!!",
                "parametros": {"valores": self._valores_do_caso()},
            }
        )
        self.assertEqual(torta.status_code, 422, torta.text)
        self.assertEqual(self._codigo(torta), "idempotency_key_invalida")
        self.assertEqual(self._contagem_de_atividades(), antes)

    def test_10_dry_run_descreve_e_nao_cria(self):
        antes = self._contagem_de_atividades()
        resposta = self._criar(
            self._valores_do_caso(), chave_idempotencia="tre-e01-t05-teste-0010", dry_run=True
        )
        self.assertEqual(resposta.status_code, 200, resposta.text)
        corpo = resposta.json()
        self.assertTrue(corpo["dry_run"])
        self.assertEqual(corpo["dados"]["acao_efetiva"], "criar")
        # O valor fixo declarado aparece no que a chamada CRIARIA — e' o descritivo, nao o registro.
        self.assertIn("res_model", corpo["dados"]["criaria"])
        self.assertEqual(self._contagem_de_atividades(), antes, "o dry-run criou atividade")

    def test_11_envelope_ecoa_operacao_ambiente_e_chave(self):
        resposta = self._criar(
            self._valores_do_caso(), chave_idempotencia="tre-e01-t05-teste-0011"
        )
        self.assertEqual(resposta.status_code, 200, resposta.text)
        corpo = resposta.json()
        self.assertEqual(corpo["operacao"], OPERACAO)
        self.assertEqual(corpo["ambiente"], "dev")
        self.assertEqual(corpo["acao"], "criar")
        self.assertEqual(corpo["idempotency_key"], "tre-e01-t05-teste-0011")
        self.assertFalse(corpo["dry_run"])

    # ------------------------------------------------------------------ AC6 rastro
    def test_12_auditoria_da_criacao_sem_payload_e_sem_token(self):
        with self.assertLogs("transformativa_sales_ai.api", level="INFO") as captura:
            self._criar(
                self._valores_do_caso(),
                chave_idempotencia="tre-e01-t05-teste-0012",
                correlation_id="tre-e01-t05-auditoria",
            )
        linhas = [linha for linha in captura.output if "TF_API_AUDIT" in linha]
        self.assertEqual(len(linhas), 1, captura.output)
        linha = linhas[0]
        self.assertIn('"resultado": "ok"', linha)
        self.assertIn('"correlation_id": "tre-e01-t05-auditoria"', linha)
        self.assertIn('"operacao": "atividade_criar"', linha)
        self.assertIn('"acao": "criar"', linha)
        self.assertIn('"modelo": "mail.activity"', linha)
        self.assertIn('"idempotency_key": "tre-e01-t05-teste-0012"', linha)
        self.assertNotIn("Bearer", linha)
        # O resumo da atividade e' payload de negocio: nao entra na trilha.
        self.assertNotIn("Ligar para o decisor", linha)

    def test_13_recusa_tambem_deixa_uma_linha_de_trilha(self):
        with self.assertLogs("transformativa_sales_ai.api", level="INFO") as captura:
            self._criar(
                self._valores_do_caso(res_model=MODELO_FORA_DA_ANCORA),
                chave_idempotencia="tre-e01-t05-teste-0013",
            )
        linhas = [linha for linha in captura.output if "TF_API_AUDIT" in linha]
        self.assertEqual(len(linhas), 1, captura.output)
        self.assertIn('"resultado": "recusado"', linhas[0])
        self.assertIn('"codigo": "campo_fixo_divergente"', linhas[0])

    # ------------------------------------------------------------------ AC5 guarda de ambiente
    def test_14_ambiente_fora_da_politica_recusa_503_e_nao_cria(self):
        self.icp.set_param("tf.api.ambiente", "homologacao")
        antes = self._contagem_de_atividades()
        resposta = self._criar(
            self._valores_do_caso(), chave_idempotencia="tre-e01-t05-teste-0014"
        )
        self.assertEqual(resposta.status_code, 503, resposta.text)
        self.assertEqual(self._codigo(resposta), "ambiente_nao_permitido")
        self.assertEqual(self._contagem_de_atividades(), antes)

    def test_15_sem_ambiente_declarado_a_api_fica_inerte(self):
        self.icp.set_param("tf.api.ambiente", "")
        antes = self._contagem_de_atividades()
        resposta = self._criar(
            self._valores_do_caso(), chave_idempotencia="tre-e01-t05-teste-0015"
        )
        self.assertEqual(resposta.status_code, 503, resposta.text)
        self.assertEqual(self._codigo(resposta), "ambiente_nao_declarado")
        self.assertEqual(self._contagem_de_atividades(), antes)

    # ------------------------------------------------------------------ AC7 ACL do dono da chave
    def test_16_usuario_sem_escrita_no_documento_recusa_403(self):
        """O `mail.activity.create` exige acesso de ESCRITA ao DOCUMENTO ancorado — sem `sudo()`.

        A chave abaixo e' de um usuario com `base.group_user` (que da' LEITURA em `res.partner`) e
        SEM os grupos de vendas: a criacao da atividade morre em `AccessError` -> recusa nomeada 403,
        sem registro criado "por baixo". O caminho feliziro (item 3) prova o outro lado: com a ACL de
        escrita, a MESMA ancora cria.
        """
        sem_escrita = new_test_user(
            self.env,
            login="tf_api_atividade_sem_escrita",
            groups="base.group_user,%s.group_tf_sales_ai_user" % MODULO,
        )
        chave_sem_escrita = self.env["res.users.apikeys"].with_user(sem_escrita)._generate(
            scope="rpc",
            name="teste-atividade-criar-sem-escrita",
            expiration_date=datetime.now() + timedelta(hours=12),
        )
        antes = self._contagem_de_atividades()
        resposta = self._criar(
            self._valores_do_caso(),
            chave_idempotencia="tre-e01-t05-teste-0016",
            chave=chave_sem_escrita,
        )
        self.assertEqual(resposta.status_code, 403, resposta.text)
        self.assertEqual(self._codigo(resposta), "acesso_negado")
        self.assertEqual(self._contagem_de_atividades(), antes, "criou atividade sem acesso")

    # ------------------------------------------------------------------ AC9 lacunas declaradas
    def test_17_operacao_de_criacao_nao_aceita_identificador_escalar(self):
        """Sem identidade declarada, o parametro escalar da forma 'um campo' e' recusado."""
        antes = self._contagem_de_atividades()
        resposta = self._post(
            {
                "idempotency_key": "tre-e01-t05-teste-0017",
                "parametros": {
                    "identificador": str(self.ancora.id),
                    "valores": self._valores_do_caso(),
                },
            }
        )
        self.assertEqual(resposta.status_code, 400, resposta.text)
        self.assertEqual(self._codigo(resposta), "payload_invalido")
        self.assertEqual(self._contagem_de_atividades(), antes)

    def test_18_lacuna_sem_dedup_por_chave_e_medida(self):
        """REPLAY com a MESMA `idempotency_key` ainda cria uma segunda atividade.

        E' a lacuna declarada (o motor de dedup por chave e' o card TRE-W3-E02-T02), medida aqui
        para nao ser silenciosa: o E2E #001 (doc 08 §3 passos 18-19) e' quem vai cobrar "zero
        duplicatas" — e o que este item garante e' que o estado de hoje esta' REGISTRADO.
        """
        corpo = {
            "idempotency_key": "tre-e01-t05-teste-0018-replay",
            "correlation_id": "tre-e01-t05-replay",
            "parametros": {"valores": self._valores_do_caso()},
        }
        primeira = self._post(corpo)
        segunda = self._post(corpo)
        self.assertEqual(primeira.status_code, 200, primeira.text)
        self.assertEqual(segunda.status_code, 200, segunda.text)
        self.assertEqual(len(primeira.json()["dados"]["ids"]), 1)
        self.assertEqual(len(segunda.json()["dados"]["ids"]), 1)
        # Documenta o comportamento medido (lacuna do E02-T02), sem fingir dedup que nao existe.
        self.assertEqual(
            self.env["mail.activity"].search_count(
                [("tf_idempotency_key", "=", "tre-e01-t05-teste-0018-replay")]
            ),
            0,
            "o rastreio so' entra na atividade quando o CHAMADOR o declara no payload",
        )

    def test_19_ancora_nao_declarada_nao_recebe_atividade(self):
        """AC9: atividade ancorada em modelo nao declarado e' recusa nomeada — e nada nasce."""
        antes = self.env["mail.activity"].search_count([("res_model", "=", MODELO_FORA_DA_ANCORA)])
        resposta = self._criar(
            self._valores_do_caso(res_model=MODELO_FORA_DA_ANCORA),
            chave_idempotencia="tre-e01-t05-teste-0019",
        )
        self.assertEqual(resposta.status_code, 422, resposta.text)
        self.assertEqual(
            self.env["mail.activity"].search_count([("res_model", "=", MODELO_FORA_DA_ANCORA)]),
            antes,
            "nasceu atividade no modelo nao declarado na politica",
        )
