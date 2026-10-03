# -*- coding: utf-8 -*-
"""Controlador da API controlada — card TRE-W3-E01-T01 (`t_e0489efc`).

UMA rota, UM verbo, UMA decisao:

    POST /tf/api/v1/<operacao>     Authorization: Bearer <chave de API do Odoo>

O que este arquivo faz, em ordem, a cada chamada:

  1. le o corpo JSON (recusa corpo que nao e' objeto JSON);
  2. le o ambiente declarado (`ir.config_parameter` `tf.api.ambiente`) e a aprovacao registrada
     (`tf.api.aprovacao`) — a guarda do ADR-005;
  3. carrega a politica versionada (`api/politica_api.json`, ou o caminho do parametro
     `tf.api.politica`) e manda o motor montar o PLANO (`api/motor.py`);
  4. executa o plano pelo ORM — nunca por SQL (doc 02 §3) e nunca com `sudo()` no dado: as ACLs do
     usuario dono da chave valem (`TRE-W2-E07-T01`);
  5. responde no envelope proprio e grava UMA linha de auditoria (`TF_API_AUDIT {json}`), inclusive
     nas recusas, sem payload e sem token.

O que este arquivo NAO faz (de proposito):
  * nao decide nada: toda a decisao esta' no motor, para poder ser medida sem subir Odoo;
  * nao tem rota generica de modelo/metodo/campo: fora da politica, a resposta e' recusa;
  * nao autentica: quem autentica e' o `auth='bearer'` do Odoo 19, contra `res.users.apikeys`
    (a chave pertence a um usuario de integracao; a ACL desse usuario e' parte do controle);
  * nao guarda o token em lugar nenhum: a chave chega no header e morre na requisicao;
  * nao faz `sudo()` para ler/escrever dado de negocio — so' para ler o parametro de sistema.
"""

import json
import logging
import time
import uuid

from odoo import http
from odoo.exceptions import AccessError, UserError
from odoo.http import request

from ..api import motor

_logger = logging.getLogger("transformativa_sales_ai.api")

ROTA = "/tf/api/v1/<string:operacao>"
PARAMETRO_AMBIENTE = "tf.api.ambiente"
PARAMETRO_APROVACAO = "tf.api.aprovacao"
PARAMETRO_POLITICA = "tf.api.politica"


def _parametro(chave):
    return request.env["ir.config_parameter"].sudo().get_param(chave) or ""


def _politica():
    """Carrega a politica do caminho declarado (padrao: a do modulo). Ilegivel = recusa nomeada."""
    caminho = _parametro(PARAMETRO_POLITICA) or motor.CAMINHO_PADRAO_DA_POLITICA
    return motor.carregar_politica(caminho)


def _corpo_da_requisicao():
    bruto = request.httprequest.get_data(as_text=True) or ""
    if not bruto.strip():
        return {}
    try:
        return json.loads(bruto)
    except ValueError as exc:
        raise motor.ErroApi("payload_invalido", "corpo nao e' JSON valido (%s)" % exc)


class ApiControlada(http.Controller):
    """A porta unica: uma rota por VERBO, e a operacao declarada na politica."""

    @http.route(
        ROTA,
        type="http",
        auth="bearer",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def executar(self, operacao, **kwargs):
        inicio = time.monotonic()
        correlacao = None
        plano = None
        ambiente = _parametro(PARAMETRO_AMBIENTE)
        politica_versao = None
        try:
            corpo = _corpo_da_requisicao()
            correlacao = corpo.get("correlation_id") or str(uuid.uuid4())
            politica = _politica()
            politica_versao = politica.get("versao")
            plano = motor.montar_plano(
                politica,
                operacao,
                corpo,
                ambiente=ambiente,
                aprovacao=_parametro(PARAMETRO_APROVACAO),
            )
            # Escrita que falha NAO deixa rastro no banco. O ORM levanta no meio do `create`/`write`
            # e, capturando a excecao DEPOIS, a linha problematica ja' esta' na transacao: o
            # envelope diria "recusado" com o registro gravado. O savepoint e' o rollback
            # cirurgico — descarta so' a operacao que falhou e mantem a transacao viva para
            # responder o envelope. Defeito medido no aceite de TRE-W3-E01-T02
            # (`test_15_uuid_canonico_fora_do_formato_recusa_422`: 4 registros depois da recusa,
            # 3 antes), fechado neste card junto do teste que o cobre.
            with request.env.cr.savepoint():
                dados = self._executar(plano, politica)
            resposta = self._resposta_ok(plano, politica_versao, correlacao, dados)
            self._auditar(plano, correlacao, ambiente, politica_versao, "ok", None,
                          resposta["http"], dados, inicio)
            return request.make_json_response(resposta["corpo"], status=resposta["http"])
        except motor.ErroApi as erro:
            return self._recusar(erro, operacao, correlacao, ambiente, politica_versao, plano,
                                 inicio)
        except AccessError as erro:
            return self._recusar(
                motor.ErroApi("acesso_negado", str(erro)),
                operacao, correlacao, ambiente, politica_versao, plano, inicio,
            )
        except UserError as erro:
            return self._recusar(
                motor.ErroApi("valor_invalido", str(erro)),
                operacao, correlacao, ambiente, politica_versao, plano, inicio,
            )
        except Exception as erro:  # pragma: no cover - rede de seguranca com trilha
            _logger.exception("TF_API_FALHA_INTERNA operacao=%s", operacao)
            return self._recusar(
                motor.ErroApi("erro_interno", "falha interna: %s" % erro),
                operacao, correlacao, ambiente, politica_versao, plano, inicio,
            )

    # ------------------------------------------------------------------ execucao
    def _executar(self, plano, politica):
        if plano["acao"] == "capacidades":
            return {"capacidades": motor.capacidades(politica, plano["ambiente"])}
        modelo = request.env[plano["modelo"]]
        if plano["acao"] == "ler":
            if plano["dry_run"]:
                return {"dry_run": True, "consultaria": self._descricao_da_leitura(plano)}
            # Leitura de ARQUIVADOS: so' quando o plano declara (a operacao declara, o chamador
            # pede). Sem isso, `active = false` no CRM e' invisivel e "arquivado" viraria
            # "ausente" na reconciliacao (card TRE-W3-E04-T01).
            consulta = modelo.with_context(active_test=False) if plano.get("incluir_arquivados") else modelo
            registros = consulta.search_read(
                domain=plano["filtro"],
                fields=plano["campos"],
                limit=plano["limite"],
                order=plano["ordem"],
            )
            return {"registros": registros, "total": len(registros)}
        return self._executar_escrita(modelo, plano)

    def _executar_escrita(self, modelo, plano):
        acao = plano["acao"]
        valores = plano["valores"]
        if acao == "upsert":
            return self._executar_upsert(modelo, plano, valores)
        identidade = plano["campo_de_identidade"]
        valor = plano["valor_de_identidade"]
        if acao == "atualizar":
            existentes = modelo.search([(identidade, "=", valor)], limit=2)
            if len(existentes) != 1:
                raise motor.ErroApi(
                    "valor_invalido",
                    "acao atualizar exige exatamente um registro com %s = %r (encontrados: %d)"
                    % (identidade, valor, len(existentes)),
                )
            if plano["dry_run"]:
                return {
                    "dry_run": True,
                    "acao_efetiva": "atualizar",
                    "id": existentes.id,
                    "atualizaria": sorted(valores),
                }
            existentes.write(valores)
            return {"acao_efetiva": "atualizar", "ids": existentes.ids}
        if plano["dry_run"]:
            return {"dry_run": True, "acao_efetiva": "criar", "criaria": sorted(valores)}
        criado = modelo.create(valores)
        return {"acao_efetiva": "criar", "ids": criado.ids}

    # ------------------------------------------------------------------ upsert por identidade
    @staticmethod
    def _casar_por_identidade(modelo, plano):
        """Casa o registro pelos identificadores DECLARADOS na politica, na ordem da lista.

        A ordem e' a prioridade (`campos_de_identidade`), mas quem casa e' o CONJUNTO: o valor
        de cada identificador presente no pedido e' usado para buscar, e a uniao dos achados e' o
        que o portao de ambiguidade avalia. Devolve (registros, casados) — `casados` existe para a
        mensagem de recusa dizer POR QUE casou mais de um (auditoria do consumidor), sem payload.

        `limit=2` em cada busca e' de proposito: mais de um achado ja' e' ambiguidade, e o numero
        exato nao interessa — o que interessa e' nunca escolher sozinho.
        """
        conjunto = modelo.browse()
        casados = []
        for campo in plano.get("campos_de_identidade") or ():
            valor = (plano.get("valores_de_identidade") or {}).get(campo)
            if valor in (None, ""):
                continue
            achados = modelo.search([(campo, "=", valor)], limit=2)
            if achados:
                conjunto |= achados
                casados.append("%s=%s" % (campo, valor))
        return conjunto, casados

    def _executar_upsert(self, modelo, plano, valores):
        """Upsert: cria uma vez, atualiza depois — e, se a identidade for ambigua, nao escreve.

        A ordem das identidades e' declarada na politica (nao literal aqui). Quando dois
        identificadores do MESMO pedido apontam para registros diferentes, o resultado e'
        `valor_ambiguo` (409) e nada e' tocado: o contrato (§5) manda reportar ambiguidade, nunca
        resolve-la por heuristica.
        """
        registros, casados = self._casar_por_identidade(modelo, plano)
        if len(registros) > 1:
            raise motor.ErroApi(
                "valor_ambiguo",
                "os identificadores do pedido casaram mais de um registro (%s) — a API nao "
                "escolhe por conta propria (contrato §5: ambiguidade e' reportada)"
                % "; ".join(casados),
            )
        if registros:
            if plano["dry_run"]:
                return {
                    "dry_run": True,
                    "acao_efetiva": "atualizar",
                    "id": registros.id,
                    "atualizaria": sorted(valores),
                }
            registros.write(valores)
            return {"acao_efetiva": "atualizar", "ids": registros.ids}
        if plano["dry_run"]:
            return {"dry_run": True, "acao_efetiva": "criar", "criaria": sorted(valores)}
        criado = modelo.create(valores)
        return {"acao_efetiva": "criar", "ids": criado.ids}

    @staticmethod
    def _descricao_da_leitura(plano):
        return {
            "modelo": plano["modelo"],
            "campos": plano["campos"],
            "filtro": plano["filtro"],
            "limite": plano["limite"],
            "ordem": plano["ordem"],
        }

    # ------------------------------------------------------------------ respostas
    @staticmethod
    def _resposta_ok(plano, politica_versao, correlacao, dados):
        corpo = {
            "ok": True,
            "operacao": plano["operacao"],
            "acao": plano["acao"],
            "ambiente": plano["ambiente"],
            "politica_versao": politica_versao,
            "correlation_id": correlacao,
            "idempotency_key": plano["idempotency_key"],
            "dry_run": plano["dry_run"],
            "dados": dados,
        }
        return {"http": 200, "corpo": corpo}

    def _recusar(self, erro, operacao, correlacao, ambiente, politica_versao, plano, inicio):
        correlacao = correlacao or str(uuid.uuid4())
        self._auditar(plano, correlacao, ambiente, politica_versao, "recusado", erro.codigo,
                      erro.http, None, inicio,
                      operacao=operacao)
        corpo = {
            "ok": False,
            "codigo": erro.codigo,
            "mensagem": erro.mensagem,
            "operacao": operacao,
            "ambiente": ambiente or None,
            "correlation_id": correlacao,
        }
        return request.make_json_response(corpo, status=erro.http)

    # ------------------------------------------------------------------ auditoria
    def _auditar(self, plano, correlacao, ambiente, politica_versao, resultado, codigo, http_status,
                 dados, inicio, operacao=None):
        """UMA linha por chamada, inclusive nas recusas. Sem payload e sem token (AC7)."""
        ids = []
        if isinstance(dados, dict):
            if isinstance(dados.get("ids"), list):
                ids = dados["ids"]
            elif isinstance(dados.get("id"), int):
                ids = [dados["id"]]
        linha = {
            "correlation_id": correlacao,
            "operacao": (plano or {}).get("operacao", operacao),
            "acao": (plano or {}).get("acao"),
            "modelo": (plano or {}).get("modelo"),
            "ambiente": ambiente or None,
            "politica_versao": politica_versao,
            "resultado": resultado,
            "codigo": codigo,
            "http": http_status,
            "ids": ids,
            "idempotency_key": (plano or {}).get("idempotency_key"),
            "dry_run": (plano or {}).get("dry_run", False),
            "usuario_api_id": request.env.uid,
            "duracao_ms": int((time.monotonic() - inicio) * 1000),
        }
        _logger.info("TF_API_AUDIT %s", json.dumps(linha, sort_keys=True, ensure_ascii=False))
