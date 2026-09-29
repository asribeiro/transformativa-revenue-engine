#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Suite do roteador do JEV (card TRE-W0-E04-T02).

Prova, item por item, cada criterio de aceitacao homologado pelo dono em
29/09/2026 para a integracao do JEV ao Hermes Dev Harness:

  1. o roteador le `hermes/jev/policy_v1.yaml` e recusa versao de politica
     desconhecida;
  2. a lane atribuida bate com a politica e fica registrada no recibo;
  3. confianca abaixo do limiar de abstencao gera abstencao e escalacao, nunca
     execucao;
  4. nenhuma das 8 acoes `nunca_decidido_por_maquina` passa;
  5. o recibo grava os 13 campos da politica, sem segredo;
  6. politica ausente ou ilegivel entra em modo degradado (lane high), nunca
     execucao silenciosa.

Alem disso a suite verifica que o roteador NAO tem limiar/lane/perfil literal
(prova comportamental: trocar o limiar no YAML muda a decisao) e que a matriz
Dev x Sales e respeitada (Sales AI nunca recebe credencial de deploy).

Uso:
    python3 scripts/verificar_jev_router.py                 # verifica
    python3 scripts/verificar_jev_router.py --autoteste      # verifica + mutacoes

O autoteste por mutacao copia o roteador e a politica para um diretorio
temporario, introduz a mutacao na COPIA e prova que a suite reprova a copia. Os
arquivos versionados nunca sao alterados.

Saida: OK/FALHOU por item + RESULTADO final, com codigo de saida 0 so quando tudo
passa (inclusive o autoteste, quando pedido).
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import re
import shutil
import site
import sqlite3
import subprocess
import sys
import tempfile

# ---------------------------------------------------------------------------
# PyYAML: mesma descoberta de ambiente dos outros verificadores do repo.
# ---------------------------------------------------------------------------
try:
    import yaml
except ModuleNotFoundError:
    for _candidato in ("/opt/hermes/.venv/lib/python3.13/site-packages",
                       "/opt/data/.venv/lib/python3.13/site-packages"):
        if pathlib.Path(_candidato).is_dir():
            site.addsitedir(_candidato)
    try:
        import yaml
    except ModuleNotFoundError:
        print("FALHOU PyYAML nao encontrado. Rode com o python do ambiente Hermes:\n"
              "  /opt/hermes/.venv/bin/python scripts/verificar_jev_router.py")
        sys.exit(2)

RAIZ = pathlib.Path(__file__).resolve().parents[1]
ROTEADOR = RAIZ / "hermes/jev/routing/router.py"
PACOTE = RAIZ / "hermes/jev/routing/__init__.py"
POLITICA = RAIZ / "hermes/jev/policy_v1.yaml"
PAPEIS = RAIZ / "hermes/policies"
RECIBO_EXEMPLO = RAIZ / "hermes/jev/receipts/exemplo-recibo.json"

PADRAO_LIMIAR = re.compile(r"0[.,](85|65)")
IMPORTES_PROIBIDOS = ("import requests", "import urllib", "import http.client",
                      "import openai", "import anthropic", "import socket",
                      "import subprocess")

# ---------------------------------------------------------------------------
# Infra da suite
# ---------------------------------------------------------------------------
def carregar_roteador(caminho=ROTEADOR, nome="router_da_suite"):
    """Carrega o modulo do roteador por caminho (o mesmo caminho em mutantes)."""
    especificacao = importlib.util.spec_from_file_location(nome, str(caminho))
    modulo = importlib.util.module_from_spec(especificacao)
    sys.modules[nome] = modulo
    especificacao.loader.exec_module(modulo)
    return modulo


def _texto(ok, detalhe=""):
    return (bool(ok), str(detalhe))


class Itens:
    """Coletor de itens: toda excecao vira FALHOU com a excecao registrada."""

    def __init__(self):
        self.lista = []

    def checar(self, nome, funcao):
        try:
            ok, detalhe = funcao()
        except Exception as erro:  # noqa: BLE001 - a suite precisa registrar tudo
            ok, detalhe = False, f"excecao: {type(erro).__name__}: {erro}"
        self.lista.append((nome, bool(ok), str(detalhe)))

    def add(self, nome, ok, detalhe=""):
        self.lista.append((nome, bool(ok), str(detalhe)))

    def falhas(self):
        return sum(1 for _, ok, _ in self.lista if not ok)


# ---------------------------------------------------------------------------
# A suite propriamente dita
# ---------------------------------------------------------------------------
def verificar(roteador, caminho_politica=POLITICA, diretorio_papeis=PAPEIS, area=None):
    """Devolve [(item, ok, detalhe)] para o modulo `roteador` informado."""
    itens = Itens()
    origem = pathlib.Path(getattr(roteador, "__file__", ROTEADOR))
    origem_txt = origem.read_text(encoding="utf-8")
    area = pathlib.Path(area or tempfile.mkdtemp(prefix="jev-suite-"))
    area.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- estrutura
    def _carrega():
        politica = roteador.carregar_politica(caminho_politica, diretorio_de_papeis=diretorio_papeis)
        return _texto(True, f"versao={politica['versao']} lanes={list(politica['lanes'])}")

    itens.checar("politica carregada com versao conhecida", _carrega)

    politica = roteador.carregar_politica(caminho_politica, diretorio_de_papeis=diretorio_papeis)
    papeis = roteador.carregar_politicas_de_papel(diretorio_papeis)
    lim = roteador.limiares(politica)
    aceitar, conservador, abster = lim["aceitar"], lim["conservador"], lim["abster"]
    lanes = roteador.ordem_lanes(politica)
    lane_conservadora = politica["_lane_conservadora"]
    campos = roteador.campos_do_recibo(politica)
    alta = min(0.99, aceitar + 0.05)
    baixa = max(0.0, abster - 0.05)
    faixa_conservadora = conservador + (aceitar - conservador) / 2

    itens.checar(
        "limiares coerentes (aceitar > conservador >= abster)",
        lambda: _texto(aceitar > conservador >= abster, f"{aceitar} > {conservador} >= {abster}"))

    itens.checar(
        "espelho do recibo do roteador == recibo.campos do YAML (13 campos)",
        lambda: _texto(tuple(campos) == tuple(roteador.CAMPOS_DO_RECIBO_PADRAO) and len(campos) == 13,
                       f"{len(campos)} campos"))

    itens.checar(
        "roteador sem limiar literal no codigo",
        lambda: _texto(not PADRAO_LIMIAR.search(origem_txt),
                       PADRAO_LIMIAR.search(origem_txt).group(0) if PADRAO_LIMIAR.search(origem_txt) else ""))

    def _sem_llm():
        achados = [m for m in IMPORTES_PROIBIDOS if m in origem_txt]
        return _texto(not achados, f"achados={achados}" if achados else "nenhuma chamada de rede/LLM")

    itens.checar("classificacao antes da LLM: roteador nao chama LLM nem rede", _sem_llm)

    # ------------------------------------------------- criterio 1: versao
    def _versao_desconhecida():
        texto = caminho_politica.read_text(encoding="utf-8")
        novo = texto.replace("versao: jev-policy-v1.0", "versao: jev-policy-v9.9")
        caminho = area / "politica-versao-desconhecida.yaml"
        caminho.write_text(novo, encoding="utf-8")
        try:
            roteador.carregar_politica(caminho)
        except roteador.PoliticaInvalida as erro:
            return _texto("desconhecida" in str(erro), str(erro)[:90])
        return _texto(False, "ACEITOU versao desconhecida")

    itens.checar("criterio 1: versao desconhecida e recusada na leitura", _versao_desconhecida)

    def _versao_desconhecida_nao_executa():
        resultado = roteador.decidir(
            {"card_id": "t_v9", "acao": "ajuste_de_texto", "lane_proposta": "small",
             "confianca": alta, "status": "ready"},
            politica=None, motivo_politica="versao de politica desconhecida")
        d = resultado["decisao"]
        return _texto((d["degraded_mode"] is True and d["pode_executar"] is False
                       and resultado["recibo"]["outcome"] != roteador.OUTCOME_EXECUTAR
                       and resultado["recibo"]["lane"] == lane_conservadora),
                      f"lane={resultado['recibo']['lane']} outcome={resultado['recibo']['outcome']} "
                      f"degraded_mode={d['degraded_mode']}")

    itens.checar("criterio 1: politica desconhecida leva a modo degradado, nunca a execucao",
                 _versao_desconhecida_nao_executa)

    # --------------------------------------------- criterio 2: lane no recibo
    def _lanes_batem():
        problemas = []
        for lane in lanes:
            resultado = roteador.decidir(
                {"card_id": f"t_{lane}", "acao": "tarefa de exemplo", "lane_proposta": lane,
                 "confianca": alta, "status": "ready"},
                politica=politica)
            recibo = resultado["recibo"]
            esperado_perfil = politica["lanes"][lane]["perfil"]
            if recibo["lane"] != lane:
                problemas.append(f"{lane}: recibo lane={recibo['lane']}")
            if recibo["model_profile"] != esperado_perfil:
                problemas.append(f"{lane}: perfil={recibo['model_profile']} esperado={esperado_perfil}")
            if recibo["policy_version"] != politica["versao"]:
                problemas.append(f"{lane}: policy_version={recibo['policy_version']}")
            if recibo["confidence"] != alta:
                problemas.append(f"{lane}: confidence={recibo['confidence']}")
            if not recibo.get("decision_id"):
                problemas.append(f"{lane}: decision_id vazio")
        return _texto(not problemas, "; ".join(problemas) if problemas else
                      f"{len(lanes)} lanes conferidas com o YAML e registradas no recibo")

    itens.checar("criterio 2: lane/perfil/confianca/policy_version batem com a politica", _lanes_batem)

    def _limiar_vem_do_yaml():
        texto = caminho_politica.read_text(encoding="utf-8")
        novo = texto.replace(f"aceitar: {aceitar}", "aceitar: 0.99")
        caminho = area / "politica-aceite-altissimo.yaml"
        caminho.write_text(novo, encoding="utf-8")
        outra = roteador.carregar_politica(caminho, diretorio_de_papeis=diretorio_papeis)
        resultado = roteador.decidir(
            {"card_id": "t_limiar", "acao": "tarefa de exemplo", "lane_proposta": "small",
             "confianca": alta, "status": "ready"},
            politica=outra)
        recibo = resultado["recibo"]
        esperado = roteador.lane_mais_conservadora(outra, "small", outra["_lane_conservadora"])
        return _texto(recibo["lane"] == esperado,
                      f"com aceitar=0.99 a lane virou {recibo['lane']} (esperado {esperado}); "
                      "prova que o limiar e lido do YAML, nao do codigo")

    itens.checar("criterio 2: o limiar usado vem do YAML (prova comportamental)", _limiar_vem_do_yaml)

    def _lane_desconhecida():
        resultado = roteador.decidir(
            {"card_id": "t_x", "acao": "tarefa de exemplo", "lane_proposta": "turbo",
             "confianca": alta, "status": "ready"}, politica=politica)
        return _texto(resultado["decisao"]["decidido"] == "abster_e_escalar"
                      and resultado["decisao"]["pode_executar"] is False,
                      f"decidido={resultado['decisao']['decidido']} lane={resultado['recibo']['lane']}")

    itens.checar("criterio 2: lane fora da politica nao executa (abstem e escala)", _lane_desconhecida)

    # ------------------------------------------ criterio 3: confianca baixa
    def _abstem_abaixo_do_limiar():
        resultado = roteador.decidir(
            {"card_id": "t_baixa", "acao": "tarefa de exemplo", "lane_proposta": "medium",
             "confianca": baixa, "status": "ready"}, politica=politica)
        d, recibo = resultado["decisao"], resultado["recibo"]
        return _texto(d["decidido"] == "abster_e_escalar" and d["outcome"] == roteador.OUTCOME_ESCALAR
                      and d["pode_executar"] is False and d["exige_escalacao"] is True
                      and recibo["confidence"] == baixa,
                      f"confianca={baixa} limiar_abster={abster} decidido={d['decidido']} "
                      f"lane={recibo['lane']} outcome={recibo['outcome']}")

    itens.checar(f"criterio 3: confianca {baixa} (< {abster}) abstem e escala", _abstem_abaixo_do_limiar)

    def _nao_infere_confianca():
        resultado = roteador.decidir(
            {"card_id": "t_sem_conf", "acao": "tarefa de exemplo", "lane_proposta": "small",
             "status": "ready"}, politica=politica)
        return _texto(resultado["decisao"]["pode_executar"] is False
                      and resultado["recibo"]["confidence"] is None,
                      f"decidido={resultado['decisao']['decidido']}")

    itens.checar("criterio 3: confianca ausente nao e inferida (abstem)", _nao_infere_confianca)

    def _borda_abster():
        resultado = roteador.decidir(
            {"card_id": "t_borda", "acao": "tarefa de exemplo", "lane_proposta": "medium",
             "confianca": abster, "status": "ready"}, politica=politica)
        return _texto(resultado["decisao"]["decidido"] == "executar"
                      and resultado["recibo"]["lane"] == lane_conservadora,
                      f"na borda {abster} a lane e {resultado['recibo']['lane']} "
                      f"(faixa fechada em baixo, conforme o YAML)")

    itens.checar(f"criterio 3: na borda exata {abster} a lane e a conservadora", _borda_abster)

    def _faixa_conservadora():
        problemas = []
        for lane in lanes:
            resultado = roteador.decidir(
                {"card_id": f"t_faixa_{lane}", "acao": "tarefa de exemplo", "lane_proposta": lane,
                 "confianca": faixa_conservadora, "status": "ready"}, politica=politica)
            final = resultado["recibo"]["lane"]
            if roteador.indice_da_lane(politica, final) < roteador.indice_da_lane(politica, lane):
                problemas.append(f"{lane} rebaixada para {final}")
            if final != roteador.lane_mais_conservadora(politica, lane, lane_conservadora):
                problemas.append(f"{lane}: lane {final} nao e a mais conservadora entre as duas")
        return _texto(not problemas, "; ".join(problemas) if problemas else
                      f"nenhum falso rebaixamento com confianca {faixa_conservadora}")

    itens.checar("criterio 3: faixa conservadora nunca rebaixa lane (anti falso-rebaixamento)",
                 _faixa_conservadora)

    # ------------------------------------------- criterio 4: nunca por maquina
    def _oito_acoes_bloqueadas():
        problemas = []
        for acao in roteador.acoes_nunca_decididas_por_maquina(politica):
            resultado = roteador.decidir(
                {"card_id": "t_nm", "acao": acao, "lane_proposta": "small",
                 "confianca": alta, "status": "ready"}, politica=politica)
            d, recibo = resultado["decisao"], resultado["recibo"]
            if d["pode_executar"] or d["decidido"] == "executar" or recibo["outcome"] != roteador.OUTCOME_BLOQUEAR:
                problemas.append(f"{acao}: decidido={d['decidido']} outcome={recibo['outcome']}")
        return _texto(not problemas, "; ".join(problemas) if problemas else
                      f"{len(roteador.acoes_nunca_decididas_por_maquina(politica))} acoes bloqueadas")

    itens.checar("criterio 4: as 8 acoes 'nunca decidido por maquina' terminam em bloqueio",
                 _oito_acoes_bloqueadas)

    def _primeiro_contato_precedencia():
        resultado = roteador.decidir(
            {"card_id": "t_pc", "acao": "primeiro_contato_outbound", "lane_proposta": "small",
             "confianca": alta, "status": "ready"}, politica=politica)
        d, recibo = resultado["decisao"], resultado["recibo"]
        motivos = " ".join(d["motivos"]).lower()
        return _texto(recibo["outcome"] == roteador.OUTCOME_BLOQUEAR
                      and "decisao humana" in motivos
                      and recibo["confidence"] is None
                      and d["exige_aprovacao_humana"] is True,
                      f"motivo={d['motivos'][0][:70]} confidence={recibo['confidence']}")

    itens.checar("criterio 4: primeiro contato para em Human Approval, antes do classificador",
                 _primeiro_contato_precedencia)

    def _frase_outbound():
        resultado = roteador.decidir(
            {"card_id": "t_pc2", "acao": "primeiro contato outbound por e-mail (LinkedIn/WhatsApp)",
             "lane_proposta": "small", "confianca": alta, "status": "ready"}, politica=politica)
        return _texto(resultado["recibo"]["outcome"] == roteador.OUTCOME_BLOQUEAR
                      and resultado["decisao"]["decidido"] == "bloquear",
                      f"decidido={resultado['decisao']['decidido']}")

    itens.checar("criterio 4: acao outbound escrita em prosa tambem e bloqueada", _frase_outbound)

    # ------------------------------------------------------ criterio 5: recibo
    def _treze_campos():
        resultado = roteador.decidir(
            {"card_id": "t_r", "acao": "tarefa de exemplo", "lane_proposta": "medium",
             "confianca": alta, "status": "ready"}, politica=politica)
        recibo = resultado["recibo"]
        return _texto(list(recibo.keys()) == list(campos) and len(recibo) == 13,
                      f"{len(recibo)} campos: {list(recibo.keys())}")

    itens.checar("criterio 5: recibo com os 13 campos do YAML, na ordem declarada", _treze_campos)

    def _sem_segredo_no_payload():
        # Segredo sintetico e obviamente de teste (nao e chave real): o objetivo e
        # provar que ele nao passa e nao aparece em nada que o roteador devolve.
        valor = "credencial-sintetica-de-teste-do-verificador"
        os.environ["TRE_TESTE_VERIFICADOR_PASSWORD"] = valor
        try:
            tarefa = {"card_id": "t_seg", "acao": "tarefa de exemplo", "lane_proposta": "small",
                      "confianca": alta, "status": "ready", "descricao": f"usar {valor} no contexto"}
            resultado = roteador.decidir(tarefa, politica=politica)
            bruto = json.dumps(resultado, ensure_ascii=False, default=str)
            return _texto(resultado["recibo"]["outcome"] == roteador.OUTCOME_BLOQUEAR
                          and valor not in bruto
                          and "segredo_sem_payload" in resultado["decisao"]["guardrails_acionados"],
                          f"outcome={resultado['recibo']['outcome']} "
                          f"guardrails={resultado['decisao']['guardrails_acionados']}")
        finally:
            os.environ.pop("TRE_TESTE_VERIFICADOR_PASSWORD", None)

    itens.checar("criterio 5: segredo no payload bloqueia e nao entra no recibo",
                 _sem_segredo_no_payload)

    def _segredo_por_formato():
        # Valor montado em pedacos: casa o detector de formato do roteador e o
        # proprio secret scan do repo, sem deixar literal de credencial no arquivo.
        valor = "sk-" + "TESTE" * 5
        resultado = roteador.decidir(
            {"card_id": "t_seg2", "acao": "tarefa de exemplo", "lane_proposta": "small",
             "confianca": alta, "descricao": valor, "status": "ready"}, politica=politica)
        bruto = json.dumps(resultado, ensure_ascii=False, default=str)
        return _texto(resultado["recibo"]["outcome"] == roteador.OUTCOME_BLOQUEAR and valor not in bruto,
                      f"outcome={resultado['recibo']['outcome']}")

    itens.checar("criterio 5: formato de credencial tambem bloqueia (scan de segredo)",
                 _segredo_por_formato)

    def _exemplo_versionado():
        if not RECIBO_EXEMPLO.is_file():
            return _texto(False, f"ausente: {RECIBO_EXEMPLO.relative_to(RAIZ)}")
        recibo = json.loads(RECIBO_EXEMPLO.read_text(encoding="utf-8"))
        problemas = []
        if list(recibo.keys()) != list(campos):
            problemas.append(f"campos={list(recibo.keys())}")
        if recibo.get("policy_version") != politica["versao"]:
            problemas.append(f"policy_version={recibo.get('policy_version')}")
        if recibo.get("router_version") != roteador.ROUTER_VERSION:
            problemas.append(f"router_version={recibo.get('router_version')}")
        if recibo.get("lane") not in lanes:
            problemas.append(f"lane={recibo.get('lane')}")
        if any(padrao.search(json.dumps(recibo, ensure_ascii=False))
               for _, padrao in roteador.PADROES_DE_SEGREDO):
            problemas.append("segredo no recibo de exemplo")
        return _texto(not problemas, "; ".join(problemas) if problemas else
                      "13 campos, sem segredo, versao da politica e do roteador corretas")

    itens.checar("criterio 5: recibo de exemplo versionado esta no contrato", _exemplo_versionado)

    def _hash_e_override():
        a = roteador.decidir({"card_id": "t_h", "acao": "tarefa de exemplo",
                              "lane_proposta": "small", "confianca": alta, "status": "ready"},
                             politica=politica)["recibo"]
        b = roteador.decidir({"card_id": "t_h", "acao": "outra tarefa",
                              "lane_proposta": "small", "confianca": alta, "status": "ready"},
                             politica=politica)["recibo"]
        c = roteador.decidir({"card_id": "t_h", "acao": "tarefa de exemplo",
                              "lane_proposta": "small", "confianca": alta, "status": "ready",
                              "override": {"por": "anderson", "motivo": "revisao manual"}},
                             politica=politica)["recibo"]
        return _texto(a["task_hash"] != b["task_hash"] and len(a["task_hash"]) == 64
                      and a["decision_id"] != b["decision_id"]
                      and c["override"] == {"por": "anderson", "motivo": "revisao manual"},
                      f"hash/task_id distintos e override registrado")

    itens.checar("criterio 5: task_hash/decision_id distinguem tarefas e override e registrado",
                 _hash_e_override)

    def _override_nao_rebaixa():
        resultado = roteador.decidir(
            {"card_id": "t_ov", "acao": "tarefa de exemplo", "lane_proposta": "high",
             "confianca": alta, "status": "ready",
             "override": {"por": "anderson", "lane": "small"}}, politica=politica)
        d = resultado["decisao"]
        elevado = roteador.decidir(
            {"card_id": "t_ov2", "acao": "tarefa de exemplo", "lane_proposta": "small",
             "confianca": alta, "status": "ready",
             "override": {"por": "anderson", "lane": "high"}}, politica=politica)
        return _texto(d["outcome"] == roteador.OUTCOME_BLOQUEAR
                      and elevado["decisao"]["lane"] == "high",
                      f"rebaixamento: {d['decidido']}; elevacao: {elevado['decisao']['lane']}")

    itens.checar("recibo/override: override humano so aumenta conservacao", _override_nao_rebaixa)

    # ---------------------------------------------------- criterio 6: fallback
    def _politica_ausente():
        caminho = area / "politica-que-nao-existe.yaml"
        if caminho.exists():
            caminho.unlink()
        try:
            roteador.carregar_politica(caminho)
            return _texto(False, "ACEITOU politica ausente")
        except roteador.PoliticaInvalida as erro:
            return _texto("ausente" in str(erro), str(erro)[:90])

    itens.checar("criterio 6: politica ausente e recusada na leitura", _politica_ausente)

    def _politica_ilegivel():
        caminho = area / "politica-ilegivel.yaml"
        caminho.write_text("versao: [isto nao fecha", encoding="utf-8")
        try:
            roteador.carregar_politica(caminho)
            return _texto(False, "ACEITOU politica ilegivel")
        except roteador.PoliticaInvalida as erro:
            return _texto("invalida" in str(erro) or "parseia" in str(erro), str(erro)[:80])

    itens.checar("criterio 6: politica ilegivel e recusada na leitura", _politica_ilegivel)

    def _degradado():
        resultado = roteador.decidir(
            {"card_id": "t_deg", "acao": "tarefa de exemplo", "lane_proposta": "small",
             "confianca": alta, "status": "ready"},
            politica=None, motivo_politica="politica ausente (arquivo renomeado)")
        d, recibo = resultado["decisao"], resultado["recibo"]
        return _texto(recibo["lane"] == lane_conservadora and d["degraded_mode"] is True
                      and d["pode_executar"] is False
                      and recibo["outcome"] == roteador.OUTCOME_ESCALAR
                      and recibo["confidence"] is None,
                      f"lane={recibo['lane']} degradado={d['degraded_mode']} "
                      f"outcome={recibo['outcome']} confidence={recibo['confidence']}")

    itens.checar(f"criterio 6: modo degradado usa lane {lane_conservadora} e nao executa", _degradado)

    def _degradado_com_guardrail():
        valor = "credencial-sintetica-de-teste-do-verificador"
        os.environ["TRE_TESTE_VERIFICADOR_PASSWORD"] = valor
        try:
            resultado = roteador.decidir(
                {"card_id": "t_deg2", "acao": "tarefa de exemplo", "descricao": valor,
                 "lane_proposta": "small", "confianca": alta},
                politica=None, motivo_politica="politica ausente")
            return _texto(resultado["recibo"]["outcome"] == roteador.OUTCOME_BLOQUEAR
                          and "segredo_sem_payload" in resultado["decisao"]["guardrails_acionados"],
                          f"outcome={resultado['recibo']['outcome']} "
                          "guardrails deterministas rodam antes do fallback")
        finally:
            os.environ.pop("TRE_TESTE_VERIFICADOR_PASSWORD", None)

    itens.checar("criterio 6: no modo degradado os guardrails continuam rodando primeiro",
                 _degradado_com_guardrail)

    def _degradado_por_renomeio():
        # Prova o caminho real do criterio 6: o arquivo de politica nao existe no
        # lugar onde o roteador procura, e o modo degradado e o que sobra.
        copia = area / "politica-renomeada"
        copia.mkdir(exist_ok=True)
        caminho = copia / "policy_v1.yaml"
        if caminho.exists():
            caminho.rename(copia / "policy_v1_renomeada.yaml")
        try:
            roteador.carregar_politica(caminho)
            return _texto(False, "ACEITOU politica renomeada")
        except roteador.PoliticaInvalida:
            pass
        resultado = roteador.decidir({"card_id": "t_ren", "acao": "tarefa de exemplo",
                                      "lane_proposta": "small", "confianca": alta},
                                     politica=None, motivo_politica="politica renomeada")
        return _texto(resultado["recibo"]["lane"] == roteador.LANE_DEGRADADA_PADRAO
                      and resultado["decisao"]["pode_executar"] is False,
                      f"lane={resultado['recibo']['lane']} "
                      f"(ultimo recurso={roteador.LANE_DEGRADADA_PADRAO}) "
                      f"outcome={resultado['recibo']['outcome']}")

    itens.checar("criterio 6: politica renomeada -> modo degradado, nunca execucao silenciosa",
                 _degradado_por_renomeio)

    # ------------------------------------------------ matriz Dev x Sales (T02)
    def _sales_ai_nao_faz_deploy():
        resultado = roteador.decidir(
            {"card_id": "t_sa", "acao": "executar deploy, promocao de release ou rollback",
             "papel_solicitado": "sales-ai", "lane_proposta": "critical", "confianca": alta},
            politica=politica)
        d = resultado["decisao"]
        return _texto(resultado["recibo"]["outcome"] == roteador.OUTCOME_BLOQUEAR
                      and d["papel_executor"] == "sales-ai"
                      and "papel_sem_credencial_de_deploy" in d["guardrails_acionados"],
                      f"decidido={d['decidido']} motivo={d['motivos'][0][:80]}")

    itens.checar("T02/harness: Sales AI pedindo deploy e bloqueado", _sales_ai_nao_faz_deploy)

    def _sales_ai_sem_credencial_de_deploy():
        proibidas = [str(x) for x in papeis["sales-ai"].get("credenciais_proibidas") or []]
        escolhida = next((x for x in proibidas if re.search(r"[A-Z]", x)), "")
        if not escolhida:
            return _texto(False, "sales-ai nao declara credencial proibida identificavel")
        resultado = roteador.decidir(
            {"card_id": "t_sa2", "acao": "gerar conteudo, resumo, score e recomendacao",
             "papel_solicitado": "sales-ai", "credencial_solicitada": escolhida,
             "lane_proposta": "medium", "confianca": alta}, politica=politica)
        return _texto(resultado["recibo"]["outcome"] == roteador.OUTCOME_BLOQUEAR,
                      f"credencial proibida pedida {escolhida!r}: "
                      f"outcome={resultado['recibo']['outcome']}")

    itens.checar("T02/harness: Sales AI nao recebe credencial de deploy", _sales_ai_sem_credencial_de_deploy)

    def _sales_ai_operacao_comercial():
        resultado = roteador.decidir(
            {"card_id": "t_sa3", "acao": "gerar conteudo, resumo, score e recomendacao",
             "papel_solicitado": "sales-ai", "lane_proposta": "medium", "confianca": alta},
            politica=politica)
        d = resultado["decisao"]
        return _texto(d["decidido"] == "executar" and d["papel_executor"] == "sales-ai"
                      and not any("credencial" in g for g in d["guardrails_acionados"]),
                      f"decidido={d['decidido']} papel={d['papel_executor']}")

    itens.checar("T02/harness: operacao comercial do Sales AI segue liberada", _sales_ai_operacao_comercial)

    def _papel_desconhecido():
        resultado = roteador.decidir(
            {"card_id": "t_sa4", "acao": "gerar conteudo", "papel_solicitado": "papel-inventado",
             "lane_proposta": "medium", "confianca": alta}, politica=politica)
        return _texto(resultado["recibo"]["outcome"] == roteador.OUTCOME_BLOQUEAR,
                      f"outcome={resultado['recibo']['outcome']} (fail-closed)")

    itens.checar("T02/harness: papel desconhecido bloqueia (fail-closed)", _papel_desconhecido)

    def _matriz_sem_conflito():
        problemas = []
        for papel, dado in papeis.items():
            permitidas = {str(x).split()[0].rstrip("*").upper()
                          for x in dado.get("credenciais_permitidas") or []}
            proibidas = {str(x).split()[0].rstrip("*").upper()
                         for x in dado.get("credenciais_proibidas") or []}
            conflito = permitidas & proibidas
            if conflito:
                problemas.append(f"{papel}: {sorted(conflito)} permitida e proibida")
        return _texto(not problemas, "; ".join(problemas) if problemas else
                      f"sem conflito de credencial em {len(papeis)} papeis")

    itens.checar("T02/harness: nenhuma credencial e permitida e proibida ao mesmo papel",
                 _matriz_sem_conflito)

    def _papeis_alterados_sao_recusados():
        copia = area / "papeis-mutados"
        copia.mkdir(exist_ok=True)
        for arquivo in diretorio_papeis.glob("*.yaml"):
            destino = copia / arquivo.name
            shutil.copy(arquivo, destino)
        alvo = copia / "sales-ai.yaml"
        alvo.write_text(alvo.read_text(encoding="utf-8").replace(
            "credenciais_permitidas:", "credenciais_permitidas:\n  - GITHUB_TOKEN"),
            encoding="utf-8")
        try:
            roteador.carregar_politica(caminho_politica, diretorio_de_papeis=copia)
            return _texto(False, "ACEITOU politica com Sales AI recebendo GITHUB_TOKEN")
        except roteador.PoliticaInvalida as erro:
            return _texto("sales-ai" in str(erro), str(erro)[:100])

    itens.checar("T02/harness: politica de papel que da deploy ao Sales AI e recusada",
                 _papeis_alterados_sao_recusados)

    # --------------------------------------------------- board (leitura real)
    def _board_temporario():
        banco = area / "kanban.db"
        if banco.exists():
            banco.unlink()
        conexao = sqlite3.connect(banco)
        conexao.executescript(
            "CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, body TEXT, status TEXT,"
            " assignee TEXT, priority INTEGER);"
            "CREATE TABLE task_links (parent_id TEXT, child_id TEXT, link_type TEXT);")
        conexao.executemany("INSERT INTO tasks VALUES (?,?,?,?,?,?)", [
            ("t_livre", "Ajuste de texto no runbook", "ajuste de texto, formatacao", "ready", None, 0),
            ("t_pai", "Card pai pendente", "trabalho do pai", "running", None, 0),
            ("t_filho", "Card filho dependente", "depende do pai",
             "todo", None, 0),
            ("t_ddl", "Aplicar DDL/migration em qualquer ambiente", "migration", "ready", None, 0),
        ])
        conexao.execute("INSERT INTO task_links VALUES ('t_pai','t_filho','dependency')")
        conexao.commit()
        conexao.close()

        problemas = []
        card = roteador.carregar_card_do_board("t_livre", caminho_do_banco=banco)
        if card["card_id"] != "t_livre" or card["dependencias_pendentes"]:
            problemas.append(f"card livre lido errado: {card}")

        filho = roteador.carregar_card_do_board("t_filho", caminho_do_banco=banco)
        if filho["dependencias_pendentes"] != ["t_pai"]:
            problemas.append(f"dependencia do filho nao lida: {filho['dependencias_pendentes']}")

        tarefa = roteador.tarefa_a_partir_do_card(filho, politica)
        resultado = roteador.decidir(tarefa, politica=politica)
        d = resultado["decisao"]
        if d["decidido"] != "aguardar_dependencia" or d["pode_executar"]:
            problemas.append(f"filho com pai pendente executou: {d['decidido']}")
        if resultado["recibo"]["card_id"] != "t_filho":
            problemas.append("recibo sem o card_id do board")
        return _texto(not problemas, "; ".join(problemas) if problemas else
                      "card, dependencia pendente e recibo do card conferidos")

    itens.checar("board: le o card e as dependencias; filho com pai pendente nao inicia",
                 _board_temporario)

    def _board_producao_no_card():
        card = roteador.carregar_card_do_board("t_ddl", caminho_do_banco=area / "kanban.db")
        tarefa = roteador.tarefa_a_partir_do_card(card, politica)
        resultado = roteador.decidir(tarefa, politica=politica)
        return _texto(resultado["recibo"]["outcome"] == roteador.OUTCOME_BLOQUEAR
                      and "ddl_fora_de_producao" in resultado["decisao"]["guardrails_acionados"],
                      f"outcome={resultado['recibo']['outcome']} "
                      f"guardrails={resultado['decisao']['guardrails_acionados']}")

    itens.checar("board: card de DDL sem ambiente declarado e bloqueado pelos guardrails",
                 _board_producao_no_card)

    def _classificador_de_card():
        problemas = []
        for lane in lanes:
            exemplos = politica["lanes"][lane].get("exemplos") or ""
            card = {"card_id": f"t_{lane}", "titulo": "tarefa de exemplo", "descricao": exemplos}
            classificacao = roteador.classificar_card(card, politica)
            if classificacao["lane_proposta"] != lane:
                problemas.append(f"{lane}: classificador disse {classificacao['lane_proposta']}")
        vazio = roteador.classificar_card({"card_id": "t_v", "titulo": "x", "descricao": "nada"},
                                          politica)
        if vazio["lane_proposta"] is not None:
            problemas.append(f"card sem evidencia classificou {vazio['lane_proposta']}")
        return _texto(not problemas, "; ".join(problemas) if problemas else
                      "exemplos do proprio YAML classificam na lane certa; sem evidencia, abstem")

    itens.checar("classificador de card usa os `exemplos` do YAML e abstem sem evidencia",
                 _classificador_de_card)

    # ------------------------------------------------------------------- CLI
    def _cli_sintetico():
        tarefa = {"card_id": "t_cli", "titulo": "ajuste de texto", "acao": "ajuste_de_texto",
                  "lane_proposta": "small", "confianca": alta, "status": "ready"}
        processo = subprocess.run(
            [sys.executable, str(origem), "--json", json.dumps(tarefa)],
            capture_output=True, text=True, cwd=str(RAIZ))
        if processo.returncode != 0:
            return _texto(False, f"exit={processo.returncode} stderr={processo.stderr[:120]}")
        saida = json.loads(processo.stdout)
        recibo = saida["recibo"]
        return _texto(set(recibo) == set(campos) and recibo["lane"] == "small"
                      and recibo["policy_version"] == politica["versao"]
                      and bool(recibo["decision_id"]),
                      f"exit=0 lane={recibo['lane']} decision_id={recibo['decision_id']} "
                      f"policy_version={recibo['policy_version']}")

    itens.checar("CLI: entrada sintetica imprime o recibo (13 campos) e sai 0", _cli_sintetico)

    def _cli_bloqueio():
        tarefa = {"card_id": "t_cli2", "acao": "primeiro_contato_outbound",
                  "lane_proposta": "small", "confianca": alta}
        processo = subprocess.run(
            [sys.executable, str(origem), "--json", json.dumps(tarefa)],
            capture_output=True, text=True, cwd=str(RAIZ))
        if processo.returncode != 3:
            return _texto(False, f"exit={processo.returncode} stderr={processo.stderr[:120]}")
        saida = json.loads(processo.stdout)
        return _texto(saida["recibo"]["outcome"] == roteador.OUTCOME_BLOQUEAR,
                      f"exit=3 outcome={saida['recibo']['outcome']}")

    itens.checar("CLI: bloqueio sai com codigo 3 e recibo em BLOCK", _cli_bloqueio)

    def _cli_card_do_board():
        banco = area / "kanban.db"
        if not banco.is_file():
            return _texto(False, "banco temporario do board nao existe (item anterior)")
        processo = subprocess.run(
            [sys.executable, str(origem), "--card", "t_livre", "--board-db", str(banco)],
            capture_output=True, text=True, cwd=str(RAIZ))
        if processo.returncode not in (0, 2, 3):
            return _texto(False, f"exit={processo.returncode} stderr={processo.stderr[:120]}")
        saida = json.loads(processo.stdout)
        recibo = saida["recibo"]
        return _texto(recibo["card_id"] == "t_livre" and recibo["lane"] in lanes
                      and recibo["policy_version"] == politica["versao"],
                      f"exit={processo.returncode} lane={recibo['lane']} card={recibo['card_id']}")

    itens.checar("CLI: le o card do board e imprime o recibo", _cli_card_do_board)

    def _cli_degradado():
        copia = area / "cli-degradado"
        copia.mkdir(exist_ok=True)
        roteador_copia = copia / "router.py"
        shutil.copy(origem, roteador_copia)
        tarefa = {"card_id": "t_deg_cli", "acao": "tarefa de exemplo",
                  "lane_proposta": "small", "confianca": alta}
        processo = subprocess.run(
            [sys.executable, str(roteador_copia), "--json", json.dumps(tarefa),
             "--politica", str(copia / "policy_v1.yaml")],
            capture_output=True, text=True, cwd=str(copia))
        if processo.returncode != 2:
            return _texto(False, f"exit={processo.returncode} stderr={processo.stderr[:150]}")
        saida = json.loads(processo.stdout)
        recibo, decisao = saida["recibo"], saida["decisao"]
        return _texto(decisao["degraded_mode"] is True
                      and decisao["pode_executar"] is False
                      and recibo["lane"] == roteador.LANE_DEGRADADA_PADRAO
                      and recibo["policy_version"] is None
                      and len(recibo) == 13,
                      f"exit=2 lane={recibo['lane']} degraded_mode={decisao['degraded_mode']} "
                      f"policy_version={recibo['policy_version']} campos={len(recibo)}")

    itens.checar("CLI (T02): politica renomeada em copia temporaria -> modo degradado, exit 2",
                 _cli_degradado)

    # ------------------- regressao dos defeitos D03, D04 e D05 (validacao T04)
    # Itens que REPROVAM se o defeito voltar. Estao na suite do roteador porque o
    # defeito era dele: a validacao independente (scripts/validar_jev_guardrails.py)
    # continua sendo a prova adversarial, com itens proprios.

    def _d03_fonte_human_approval():
        arquivo = pathlib.Path(diretorio_papeis) / "human-approval.yaml"
        if not arquivo.is_file():
            return _texto(False, f"ausente: {arquivo}")
        bruto = arquivo.read_text(encoding="utf-8")
        dado = yaml.safe_load(bruto) or {}
        declaradas = [str(x) for secao in ("exige_aprovacao", "nunca_automatico")
                      for x in dado.get(secao) or []]
        problemas = []
        if not declaradas:
            return _texto(False, "a fonte nao declara acao em exige_aprovacao/nunca_automatico")
        if "role:" in bruto:
            problemas.append("human-approval.yaml passou a declarar role:")
        entradas = roteador.acoes_de_decisao_humana(politica, papeis)
        faltando = [a for a in declaradas if a not in entradas]
        if faltando:
            problemas.append(f"a fonte nao entra na camada Human Approval: {faltando}")
        for acao in declaradas:
            resultado = roteador.decidir(
                {"card_id": "t_d03", "acao": acao, "lane_proposta": "small",
                 "confianca": alta, "status": "ready"}, politica=politica)
            d, recibo = resultado["decisao"], resultado["recibo"]
            if d["pode_executar"] or recibo["outcome"] != roteador.OUTCOME_BLOQUEAR:
                problemas.append(f"'{acao}': decidido={d['decidido']} outcome={recibo['outcome']}")
        return _texto(not problemas, "; ".join(problemas) if problemas else
                      f"{len(declaradas)} declaracoes da fonte bloqueiam com BLOCK, sem chave role:")
    itens.checar("D03: a fonte human-approval.yaml entra na camada Human Approval sem depender "
                 "de role: (9 declaracoes nao executam)", _d03_fonte_human_approval)

    def _d04_frases_em_prosa():
        frases = ("promocao de release para producao",
                  "promover release para producao",
                  "publicar release em producao",
                  "exclusao de registro de auditoria")
        problemas = []
        for frase in frases:
            resultado = roteador.decidir(
                {"card_id": "t_d04", "acao": frase, "lane_proposta": "small",
                 "confianca": alta, "status": "ready"}, politica=politica)
            d, recibo = resultado["decisao"], resultado["recibo"]
            if d["pode_executar"] or recibo["outcome"] != roteador.OUTCOME_BLOQUEAR:
                problemas.append(f"'{frase}': decidido={d['decidido']} outcome={recibo['outcome']}")
        return _texto(not problemas, "; ".join(problemas) if problemas else
                      "as 4 frases em prosa que escapavam do bloqueio terminam em BLOCK")
    itens.checar("D04: as 4 frases em prosa que escapavam do bloqueio terminam em BLOCK",
                 _d04_frases_em_prosa)

    def _d04_alinhamento_do_vocabulario():
        declaradas = set(roteador.acoes_nunca_decididas_por_maquina(politica))
        codigos = [codigo for codigo, _grupos in roteador.REGRAS_DE_ACAO_HUMANA]
        fora = [c for c in codigos if c not in declaradas]
        sem_regra = [a for a in sorted(declaradas) if a not in codigos]
        problemas = []
        if fora:
            problemas.append(f"codigo do roteador fora da politica: {fora}")
        if sem_regra:
            problemas.append(f"acao da politica sem regra canonica no roteador: {sem_regra}")
        return _texto(not problemas, "; ".join(problemas) if problemas else
                      f"{len(codigos)} regras canonicas alinhadas com "
                      f"{len(declaradas)} acoes de nunca_decidido_por_maquina")
    itens.checar("D04: o vocabulario canonico do roteador nao inventa nem omite acao da politica",
                 _d04_alinhamento_do_vocabulario)

    def _d05_borda_de_entrada():
        problemas = []
        casos = (("texto solto, nao mapa", "payload nao-mapa (texto)"),
                 (["lista"], "payload nao-mapa (lista)"),
                 ({"card_id": "t_d05", "acao": "ajuste de texto", "sinais": ["lista"]},
                  "sinais nao-mapa"))
        for entrada, rotulo in casos:
            try:
                resultado = roteador.decidir(entrada, politica=politica)
            except Exception as erro:  # noqa: BLE001 - a excecao e o defeito
                problemas.append(f"{rotulo}: excecao {type(erro).__name__}: {erro}")
                continue
            recibo, d = resultado["recibo"], resultado["decisao"]
            if recibo["outcome"] != roteador.OUTCOME_BLOQUEAR or d["pode_executar"] is not False:
                problemas.append(f"{rotulo}: decidido={d['decidido']} outcome={recibo['outcome']}")
            elif "payload_valido" not in d["guardrails_acionados"]:
                problemas.append(f"{rotulo}: guardrail payload_valido nao registrado")
            elif list(recibo.keys()) != list(campos):
                problemas.append(f"{rotulo}: recibo fora do contrato")
        return _texto(not problemas, "; ".join(problemas) if problemas else
                      "3 entradas de tipo invalido: BLOCK com recibo de 13 campos, sem excecao")
    itens.checar("D05: entrada de tipo invalido termina em BLOCK com recibo, nunca em excecao",
                 _d05_borda_de_entrada)

    # ------------- correcao de raiz do D06 (card TRE-W0-E04-T07) --------------
    # Itens que provam a CLASSE do problema, nao as frases do dia: a acao passa a ser
    # resolvida para um CODIGO antes de decidir, o roteador infere dominio sensivel do
    # proprio texto e, sem codigo conhecido, acao em tarefa sensivel NAO executa.
    # Nenhum destes itens depende de prosa nova entrar na tabela de conceitos.

    # Frases inventadas pelo executor deste card (T07): nao estao no card, no D04, no
    # D06 nem em nenhum teste anterior. Cobrem primeiro contato, credencial, publicacao
    # em nome da Transformativa e dado de cliente.
    FRASES_INVENTADAS_T07 = (
        "enviar mensagem de apresentacao para prospect novo",
        "enviar mensagem para lead recem-chegado",
        "trocar a senha de servico do banco",
        "divulgar comunicado assinado pela Transformativa",
        "apagar linha do cadastro de clientes",
    )
    FRASES_QUE_ESCAPARAM = ("promocao de release para producao",
                            "promover release para producao",
                            "publicar release em producao",
                            "exclusao de registro de auditoria",
                            "enviar mensagem ao primeiro cliente interessado")

    def _nao_executa(frase, **extra):
        """Problemas ao decidir a frase: tem de parar em BLOCK/ESCALATE, recibo de 13."""
        tarefa = {"card_id": "t_t07", "acao": frase, "lane_proposta": "small",
                  "confianca": alta, "status": "ready"}
        tarefa.update(extra)
        resultado = roteador.decidir(tarefa, politica=politica)
        d, recibo = resultado["decisao"], resultado["recibo"]
        problemas = []
        if d["pode_executar"] or d["decidido"] == "executar":
            problemas.append(f"'{frase}': EXECUTOU (decidido={d['decidido']} "
                             f"outcome={recibo['outcome']} lane={recibo['lane']})")
        if recibo["outcome"] not in (roteador.OUTCOME_BLOQUEAR, roteador.OUTCOME_ESCALAR):
            problemas.append(f"'{frase}': outcome={recibo['outcome']}")
        if list(recibo.keys()) != list(campos):
            problemas.append(f"'{frase}': recibo fora do contrato ({len(recibo)} campos)")
        if recibo["lane"] != lane_conservadora:
            problemas.append(f"'{frase}': lane={recibo['lane']} (esperado {lane_conservadora})")
        return problemas

    def _t07_codigo_canonico_e_a_via_principal():
        problemas = []
        codigos = roteador.acoes_nunca_decididas_por_maquina(politica)
        for codigo in codigos:
            # O texto da acao e INOCENTE de proposito: quem bloqueia e o codigo.
            resultado = roteador.decidir(
                {"card_id": "t_t07a", "acao": "ajuste de texto no runbook",
                 "acao_codigo": codigo, "lane_proposta": "small", "confianca": alta,
                 "status": "ready"}, politica=politica)
            d, recibo = resultado["decisao"], resultado["recibo"]
            if d["pode_executar"] or recibo["outcome"] != roteador.OUTCOME_BLOQUEAR:
                problemas.append(f"codigo {codigo}: decidido={d['decidido']} "
                                 f"outcome={recibo['outcome']}")
            elif d.get("codigo_de_acao") != codigo \
                    or d.get("origem_do_codigo_de_acao") != "codigo_canonico":
                problemas.append(f"codigo {codigo}: nao registrado na decisao "
                                 f"(codigo={d.get('codigo_de_acao')!r} "
                                 f"origem={d.get('origem_do_codigo_de_acao')!r})")
        return _texto(not problemas, "; ".join(problemas) if problemas else
                      f"{len(codigos)} codigos canonicos bloqueiam pelo campo "
                      f"{roteador.CAMPO_DO_CODIGO_DE_ACAO!r} com o codigo registrado na decisao")

    itens.checar("T07: o codigo canonico e a via principal (acao_codigo bloqueia sem prosa)",
                 _t07_codigo_canonico_e_a_via_principal)

    def _t07_codigo_comum_executa():
        resultado = roteador.decidir(
            {"card_id": "t_t07b", "acao": "ajuste de texto no runbook",
             "acao_codigo": "ajuste_de_texto", "lane_proposta": "small",
             "confianca": alta, "status": "ready"}, politica=politica)
        d, recibo = resultado["decisao"], resultado["recibo"]
        return _texto(d["decidido"] == "executar" and recibo["outcome"] == roteador.OUTCOME_EXECUTAR
                      and d.get("codigo_de_acao") == "ajuste_de_texto"
                      and d.get("origem_do_codigo_de_acao") == "codigo_canonico"
                      and d.get("dominios_sensiveis") == [] and list(recibo.keys()) == list(campos),
                      f"decidido={d['decidido']} outcome={recibo['outcome']} "
                      f"codigo={d.get('codigo_de_acao')!r} dominios={d.get('dominios_sensiveis')}")

    itens.checar("T07: acao resolvida por codigo canonico comum executa (a falha fechada "
                 "nao virou bloqueio geral)", _t07_codigo_comum_executa)

    def _t07_benignas_seguem_executando():
        problemas = []
        for acao in ("tarefa de exemplo", "ajuste de texto simples",
                     "consulta de status do card"):
            resultado = roteador.decidir(
                {"card_id": "t_t07b2", "acao": acao, "lane_proposta": "small",
                 "confianca": alta, "status": "ready"}, politica=politica)
            if resultado["decisao"]["decidido"] != "executar":
                problemas.append(f"'{acao}': {resultado['decisao']['decidido']} "
                                 f"({resultado['decisao']['motivos'][0][:60]})")
        return _texto(not problemas, "; ".join(problemas) if problemas else
                      "3 acoes comuns, sem dominio sensivel, seguem executando")

    itens.checar("T07: acao sem dominio sensivel segue executando (nao ha bloqueio geral)",
                 _t07_benignas_seguem_executando)

    def _t07_frases_que_escaparam():
        problemas = [p for frase in FRASES_QUE_ESCAPARAM for p in _nao_executa(frase)]
        return _texto(not problemas, "; ".join(problemas) if problemas else
                      f"as {len(FRASES_QUE_ESCAPARAM)} frases que escapavam (4 do D04 + 1 "
                      "do D06) terminam em BLOCK/ESCALATE com recibo de 13 campos")

    itens.checar("T07: as 5 frases que ja escaparam nao executam (4 do D04 + 1 do D06)",
                 _t07_frases_que_escaparam)

    def _t07_frases_inventadas():
        problemas = [p for frase in FRASES_INVENTADAS_T07 for p in _nao_executa(frase)]
        return _texto(not problemas, "; ".join(problemas) if problemas else
                      f"{len(FRASES_INVENTADAS_T07)} frases novas inventadas pelo executor "
                      "do card tambem terminam em BLOCK/ESCALATE")

    itens.checar("T07: frases novas inventadas pelo executor (primeiro contato, credencial, "
                 "publicacao, dado de cliente) nao executam", _t07_frases_inventadas)

    def _t07_dominio_inferido_sem_sinal():
        frase = "enviar mensagem ao primeiro cliente interessado"
        resultado = roteador.decidir(
            {"card_id": "t_t07c", "acao": frase, "lane_proposta": "small",
             "confianca": alta, "status": "ready"}, politica=politica)
        d, recibo = resultado["decisao"], resultado["recibo"]
        motivos = " ".join(d["motivos"]).lower()
        problemas = []
        if not d.get("dominios_sensiveis_inferidos_do_texto"):
            problemas.append("nenhum dominio inferido do texto")
        if d["pode_executar"] or recibo["outcome"] != roteador.OUTCOME_ESCALAR:
            problemas.append(f"decidido={d['decidido']} outcome={recibo['outcome']}")
        if roteador.MOTIVO_ACAO_NAO_CLASSIFICADA not in motivos:
            problemas.append(f"motivo sem '{roteador.MOTIVO_ACAO_NAO_CLASSIFICADA}': {d['motivos']}")
        if list(recibo.keys()) != list(campos):
            problemas.append("recibo fora do contrato")
        return _texto(not problemas, "; ".join(problemas) if problemas else
                      f"chamador nao declarou sinal nenhum: inferiu "
                      f"{d.get('dominios_sensiveis_inferidos_do_texto')} do texto e escalou "
                      f"({d['decidido']})")

    itens.checar("T07: dominio sensivel inferido do texto barra mesmo sem sinal declarado",
                 _t07_dominio_inferido_sem_sinal)

    def _t07_dominio_declarado_barra():
        resultado = roteador.decidir(
            {"card_id": "t_t07d", "acao": "ajuste de texto simples", "lane_proposta": "small",
             "confianca": alta, "status": "ready", "sinais": {"dado_de_cliente": True}},
            politica=politica)
        d, recibo = resultado["decisao"], resultado["recibo"]
        problemas = []
        if d.get("dominios_sensiveis_declarados") != ["dado_de_cliente"]:
            problemas.append(f"dominios declarados={d.get('dominios_sensiveis_declarados')}")
        if d["pode_executar"] or recibo["outcome"] != roteador.OUTCOME_ESCALAR:
            problemas.append(f"decidido={d['decidido']} outcome={recibo['outcome']}")
        if roteador.MOTIVO_ACAO_NAO_CLASSIFICADA not in " ".join(d["motivos"]).lower():
            problemas.append("motivo obrigatorio ausente")
        return _texto(not problemas, "; ".join(problemas) if problemas else
                      "uniao dos sinais: dominio declarado no payload tambem barra a acao "
                      "nao classificada")

    itens.checar("T07: dominio sensivel declarado pelo chamador tambem barra a acao nao "
                 "classificada", _t07_dominio_declarado_barra)

    def _t07_codigo_desconhecido_e_texto_em_desacordo():
        problemas = []
        desconhecido = roteador.decidir(
            {"card_id": "t_t07e", "acao": "ajuste de texto no runbook",
             "acao_codigo": "codigo-que-nao-existe", "sinais": {"credencial": True},
             "lane_proposta": "small", "confianca": alta, "status": "ready"}, politica=politica)
        d = desconhecido["decisao"]
        if d["pode_executar"] or desconhecido["recibo"]["outcome"] != roteador.OUTCOME_ESCALAR:
            problemas.append(f"codigo desconhecido: {d['decidido']}/{desconhecido['recibo']['outcome']}")
        if d.get("codigo_declarado_e_desconhecido") != "codigo-que-nao-existe":
            problemas.append(f"codigo desconhecido nao registrado: "
                             f"{d.get('codigo_declarado_e_desconhecido')!r}")
        em_desacordo = roteador.decidir(
            {"card_id": "t_t07f", "acao": "enviar mensagem ao primeiro cliente interessado",
             "acao_codigo": "ajuste_de_texto", "lane_proposta": "small",
             "confianca": alta, "status": "ready"}, politica=politica)
        if em_desacordo["decisao"]["pode_executar"]:
            problemas.append("codigo comum liberou um texto com dominio sensivel")
        return _texto(not problemas, "; ".join(problemas) if problemas else
                      "codigo desconhecido e codigo comum em desacordo com o texto escalam, "
                      "com o codigo declarado registrado na decisao")

    itens.checar("T07: codigo desconhecido e codigo em desacordo com o texto nao abrem buraco",
                 _t07_codigo_desconhecido_e_texto_em_desacordo)

    def _t07_contrato_de_codigo():
        proibidos = set(roteador.acoes_nunca_decididas_por_maquina(politica))
        comuns = set(roteador.CODIGOS_DE_ACAO_COMUNS)
        problemas = []
        conflito = sorted(proibidos & comuns)
        if conflito:
            problemas.append(f"codigo comum tambem proibido: {conflito}")
        if not comuns:
            problemas.append("o lado comum do contrato esta vazio")
        if not proibidos:
            problemas.append("a politica nao declara acao proibida")
        for codigo in sorted(proibidos):
            resolucao = roteador.resolver_codigo_de_acao(
                {"card_id": "t", "acao": "ajuste de texto", "acao_codigo": codigo}, politica)
            if resolucao["classe"] != "proibida" or resolucao["origem"] != "codigo_canonico":
                problemas.append(f"{codigo}: resolucao={resolucao}")
        for dominio in roteador.DOMINIOS_SENSIVEIS:
            if dominio not in set(roteador.SINAIS_DE_DOMINIO_SENSIVEL.values()):
                problemas.append(f"dominio sensivel sem sinal declaravel correspondente: {dominio}")
        return _texto(not problemas, "; ".join(problemas) if problemas else
                      f"contrato fechado: {len(proibidos)} codigos proibidos (da politica) e "
                      f"{len(comuns)} codigos comuns (do roteador), disjuntos; "
                      f"{len(roteador.DOMINIOS_SENSIVEIS)} dominios sensiveis declaraveis")

    itens.checar("T07: contrato de codigo e dominios — lados disjuntos e completos",
                 _t07_contrato_de_codigo)

    # --------------------------------------------------------- arquivos-chave
    itens.add("arquivos do roteador existem (router.py e __init__.py)",
              ROTEADOR.is_file() and PACOTE.is_file(),
              f"{ROTEADOR.relative_to(RAIZ)} / {PACOTE.relative_to(RAIZ)}")

    return itens.lista


# ---------------------------------------------------------------------------
# Autoteste por mutacao
# ---------------------------------------------------------------------------
def _sem_linhas(texto, *trechos):
    return "\n".join(linha for linha in texto.splitlines()
                     if not any(t in linha for t in trechos))


def mutacoes():
    """Cada mutacao devolve (nome, mexer_no_codigo, mexer_na_politica, mexer_nos_papeis)."""
    identidade = lambda t: t  # noqa: E731
    dar_deploy_ao_sales_ai = lambda t: t.replace(  # noqa: E731
        "credenciais_permitidas:", "credenciais_permitidas:\n  - GITHUB_TOKEN")

    def versao_desconhecida_aceita(codigo):
        return codigo.replace("    if not versao or str(versao) not in VERSOES_DE_POLITICA_SUPORTADAS:",
                              "    if False:")

    def guardrail_segredo_ignorado(codigo):
        return codigo.replace("    motivo_segredo = _segredo_no_payload(tarefa)",
                              '    motivo_segredo = ""')

    def confianca_baixa_executa(codigo):
        return codigo.replace("    if confianca < abster:",
                              "    if False:  # mutacao: confianca baixa passa")

    def recibo_sem_campo(codigo):
        return codigo.replace('        "router_version": ROUTER_VERSION,\n', "")

    def degradado_executa(codigo):
        return codigo.replace('        plano["outcome"] = OUTCOME_ESCALAR\n'
                              '        plano["motivos"] = [motivo_politica',
                              '        plano["outcome"] = OUTCOME_EXECUTAR\n'
                              '        plano["decidido"] = "executar"\n'
                              '        plano["motivos"] = [motivo_politica')

    def limiar_hard_coded(codigo):
        return codigo.replace('    aceitar = lim.get("aceitar")', "    aceitar = 0.85")

    def precedencia_humana_ignorada(codigo):
        return codigo.replace("    if humano:", "    if False and humano:")

    def dependencias_ignoradas(codigo):
        return codigo.replace("    if pendencia:", "    if False and pendencia:")

    def papel_proibido_liberado(codigo):
        return codigo.replace('        return papel, f"papel {papel} nao pode: {proibidas[0]}"',
                              '        return papel, ""')

    def segredo_vaza_no_recibo(codigo):
        return codigo.replace('        "card_id": card_id,',
                              '        "card_id": tarefa.get("descricao") or card_id,')

    def resolucao_de_codigo_neutralizada(codigo):
        # A acao volta a depender so de prosa: nenhum codigo resolve.
        return codigo.replace(
            "    resolucao = resolver_codigo_de_acao(tarefa, politica) if politica is not None else _resolucao_vazia()",
            "    resolucao = _resolucao_vazia()")

    def inferencia_de_dominio_desligada(codigo):
        # O roteador deixa de inferir dominio sensivel do texto (so o declarado conta).
        return codigo.replace(
            "    inferidos = _dominios_sensiveis_do_texto(_texto_da_acao(tarefa))",
            "    inferidos = set()")

    def sinais_declarados_ignorados(codigo):
        # O roteador deixa de ler os sinais de dominio declarados pelo chamador.
        return codigo.replace(
            "    declarados = _dominios_sensiveis_declarados(tarefa)", "    declarados = set()")

    def falha_fechada_removida(codigo):
        # Sem codigo conhecido, a acao sensivel volta a executar.
        return codigo.replace(
            "    incerteza = _falha_fechada_por_acao_nao_classificada(resolucao, dominios)",
            "    incerteza = None")

    return [
        ("aceitar versao de politica desconhecida", versao_desconhecida_aceita, identidade, identidade),
        ("ignorar o guardrail de segredo", guardrail_segredo_ignorado, identidade, identidade),
        ("permitir confianca baixa executar", confianca_baixa_executa, identidade, identidade),
        ("deixar um dos 13 campos de fora do recibo", recibo_sem_campo, identidade, identidade),
        ("politica ausente seguir executando (modo degradado falso)", degradado_executa, identidade, identidade),
        ("limiar hard-coded no roteador (ignora o YAML)", limiar_hard_coded, identidade, identidade),
        ("inverter a precedencia: sem Human Approval", precedencia_humana_ignorada, identidade, identidade),
        ("ignorar dependencia/prioridade do board", dependencias_ignoradas, identidade, identidade),
        ("deixar o papel proibido passar (Sales AI com deploy)", papel_proibido_liberado, identidade, identidade),
        ("segredo vazando no recibo", segredo_vaza_no_recibo, identidade, identidade),
        ("T07: acao deixa de resolver para codigo canonico (volta a depender de prosa)",
         resolucao_de_codigo_neutralizada, identidade, identidade),
        ("T07: inferencia de dominio sensivel do texto desligada",
         inferencia_de_dominio_desligada, identidade, identidade),
        ("T07: sinais de dominio declarados pelo chamador ignorados",
         sinais_declarados_ignorados, identidade, identidade),
        ("T07: falha fechada da acao nao classificada removida",
         falha_fechada_removida, identidade, identidade),
        ("politica com fallback para lane barata", identidade,
         lambda t: t.replace("lane conservadora configurada: high", "lane conservadora configurada: small"),
         identidade),
        ("politica sem o guardrail fail-closed", identidade,
         lambda t: _sem_linhas(t, "falha de guardrail bloqueia", "fail-closed"), identidade),
        ("politica com versao desconhecida", identidade,
         lambda t: t.replace("versao: jev-policy-v1.0", "versao: jev-policy-v9.9"), identidade),
        ("politica de papel dando deploy ao Sales AI", identidade, identidade, dar_deploy_ao_sales_ai),
    ]


def autoteste():
    """Prova que a suite reprova cada mutacao. Roda em copia temporaria."""
    print("\n=== AUTOTESTE: mutacoes que a suite precisa reprovar ===")
    detectadas = 0
    entradas = mutacoes()
    for indice, (nome, mexer_no_codigo, mexer_na_politica, mexer_nos_papeis) in enumerate(entradas, start=1):
        with tempfile.TemporaryDirectory(prefix=f"jev-mutacao-{indice}-") as temporario:
            area = pathlib.Path(temporario)
            base = ROTEADOR.read_text(encoding="utf-8")
            texto_politica = POLITICA.read_text(encoding="utf-8")
            mutado = mexer_no_codigo(base)
            politica_alterada = mexer_na_politica(texto_politica) != texto_politica
            papeis_alterados = False
            papeis = area / "policies"
            papeis.mkdir()
            for arquivo in PAPEIS.glob("*.yaml"):
                destino = papeis / arquivo.name
                bruto = arquivo.read_text(encoding="utf-8")
                alterado = mexer_nos_papeis(bruto)
                papeis_alterados = papeis_alterados or alterado != bruto
                destino.write_text(alterado, encoding="utf-8")
            if not (mutado != base or politica_alterada or papeis_alterados):
                print(f"FALHOU NAO detectada: {nome}  <-- a mutacao nem foi aplicada (ancora mudou)")
                continue
            alvo_codigo = area / "router.py"
            alvo_codigo.write_text(mutado, encoding="utf-8")
            politica = area / "policy_v1.yaml"
            politica.write_text(mexer_na_politica(texto_politica), encoding="utf-8")

            try:
                modulo = carregar_roteador(alvo_codigo, nome=f"router_mutado_{indice}")
                itens = verificar(modulo, caminho_politica=politica,
                                  diretorio_papeis=papeis, area=area / "trabalho")
                falhas = sum(1 for _, ok, _ in itens if not ok)
                reprovados = [n for n, ok, _ in itens if not ok]
            except Exception as erro:  # noqa: BLE001 - mutacao invalida tambem conta como detectada
                falhas, reprovados = 1, [f"o mutante nem carrega: {type(erro).__name__}: {erro}"]

        if falhas:
            detectadas += 1
            print(f"OK    detectada: {nome}  ({falhas} item(ns) reprovado(s): "
                  f"{', '.join(reprovados[:3])}{'...' if len(reprovados) > 3 else ''})")
        else:
            print(f"FALHOU NAO detectada: {nome}  <-- buraco na suite")
    print(f"\nautoteste: {detectadas}/{len(entradas)} mutacoes detectadas")
    return detectadas == len(entradas)


# ---------------------------------------------------------------------------
def imprimir(itens):
    for nome, ok, detalhe in itens:
        print(f"{'OK    ' if ok else 'FALHOU '}{nome}" + (f"  [{detalhe}]" if detalhe else ""))
    return sum(1 for _, ok, _ in itens if not ok)


def main():
    print("=" * 78)
    print("SUITE DO ROTEADOR DO JEV (card TRE-W0-E04-T02)")
    print(f"  roteador: {ROTEADOR.relative_to(RAIZ)}")
    print(f"  politica: {POLITICA.relative_to(RAIZ)}")
    print(f"  recibo:   {RECIBO_EXEMPLO.relative_to(RAIZ)}")
    print("=" * 78)

    temporario = tempfile.mkdtemp(prefix="jev-suite-")
    modulo = carregar_roteador()
    itens = verificar(modulo, area=pathlib.Path(temporario))
    falhas = imprimir(itens)

    teste_ok = True
    if "--autoteste" in sys.argv:
        teste_ok = autoteste()

    print()
    if falhas == 0 and teste_ok:
        print(f"RESULTADO: PASS ({len(itens)} itens, 0 falhas)"
              + (" + autoteste OK" if "--autoteste" in sys.argv else ""))
        return 0
    print(f"RESULTADO: FALHOU ({len(itens)} itens, {falhas} falha(s))"
          + ("" if teste_ok else " + autoteste com buraco"))
    return 1


if __name__ == "__main__":
    sys.exit(main())
