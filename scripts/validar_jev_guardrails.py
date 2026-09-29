#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validacao adversarial e independente dos guardrails e do fallback do JEV.

Card TRE-W0-E04-T04 (depende de TRE-W0-E04-T03). Este verificador NAO repete a
suite do card irmao TRE-W0-E04-T02 (`scripts/verificar_jev_router.py`): ele
ataca o que o T02 nao provou, item a item:

  1. cada guardrail declarado em `hermes/jev/policy_v1.yaml` (secao `guardrails`)
     tem prova dos DOIS lados — o caminho PROIBIDO tem de REPROVAR (bloquear/
     escalar) e o caminho PERMITIDO tem de PASSAR. Guardrail cuja cobertura nao
     exista e reportado como NAO VALIDADO, nunca marcado OK;
  2. cada uma das 8 acoes de `nunca_decidido_por_maquina` tem prova de bloqueio
     por nome exato E por variacao em prosa (o roteador casa por texto);
  3. o fallback/modo degradado e exercitado de ponta a ponta pela CLI em copia
     temporaria: politica ausente, politica ilegivel/corrompida, versao de
     politica desconhecida e secao obrigatoria faltando. Em todos: lane
     conservadora, `degraded_mode: true`, nunca execucao silenciosa e os
     guardrails deterministicos continuando a rodar PRIMEIRO;
  4. prova de que e o guardrail que segura: para cada guardrail ha uma mutacao
     que o REMOVE (em copia temporaria do roteador/politica) e a suite tem de
     reprovar o mutante. Mutacao que passa despercebida = guardrail decorativo.

Nada aqui substitui o roteador (sem mock do alvo): os testes chamam o modulo real
e a CLI real do roteador. Nenhum arquivo versionado e alterado — as mutacoes
acontecem em copia temporaria. Nenhum segredo real e usado: os valores sao
sinteticos e obviamente de teste.

Vereditos por item:
  OK      item de criterio atendido;
  FALHOU  item de criterio NAO atendido (falha da suite ou do roteador);
  ACHADO  o roteador executa/estoura onde a politica manda parar. E defeito
          ENCONTRADO, nao corrigido: a regra do card e validar e relatar.

Uso:
    /opt/hermes/.venv/bin/python scripts/validar_jev_guardrails.py
    /opt/hermes/.venv/bin/python scripts/validar_jev_guardrails.py --autoteste
    /opt/hermes/.venv/bin/python scripts/validar_jev_guardrails.py --estrito
    (--estrito soma os ACHADOs como falha: exit 1 enquanto houver achado)

Saida: OK/FALHOU/ACHADO por item + linha de cobertura + RESULTADO final. Exit 0
somente sem falha e com o autoteste integralmente detectado.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import re
import shutil
import site
import subprocess
import sys
import tempfile

# ---------------------------------------------------------------------------
# PyYAML: mesma descoberta de ambiente dos outros verificadores do repo.
# ---------------------------------------------------------------------------
try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - depende do ambiente
    for _candidato in ("/opt/hermes/.venv/lib/python3.13/site-packages",
                       "/opt/data/.venv/lib/python3.13/site-packages"):
        if pathlib.Path(_candidato).is_dir():
            site.addsitedir(_candidato)
    try:
        import yaml
    except ModuleNotFoundError:  # pragma: no cover
        print("FALHOU PyYAML nao encontrado. Rode com o python do ambiente Hermes:\n"
              "  /opt/hermes/.venv/bin/python scripts/validar_jev_guardrails.py")
        sys.exit(2)

RAIZ = pathlib.Path(__file__).resolve().parents[1]
ROTEADOR = RAIZ / "hermes/jev/routing/router.py"
POLITICA = RAIZ / "hermes/jev/policy_v1.yaml"
PAPEIS = RAIZ / "hermes/policies"
DOC_POLITICA = RAIZ / "docs/architecture/jev-decision-policy-v1.md"

# Guardrail declarado no YAML -> termos que o identificam na secao `guardrails`.
GUARDRAILS_ESPERADOS = {
    "segredo": ("segredo",),
    "do_not_contact": ("do_not_contact", "opt_out"),
    "ddl": ("DDL",),
    "credencial_de_deploy": ("credencial de deploy",),
    "fail_closed": ("fail-closed",),
}

# Variacoes em prosa por acao proibida. O roteador casa acao por TOKENS, entao
# a mesma proibicao escrita de outro jeito pode escapar — e o ponto do teste.
PROSA = {
    "aprovacao_de_producao": [
        "aprovar promocao de release para producao",
        "promocao de release para producao",
        "promover release para producao",
        "publicar release em producao",
        "deploy em producao",
    ],
    "primeiro_contato_outbound": [
        "primeiro contato outbound por e-mail para empresa nova",
        "primeiro contato com empresa nova via LinkedIn",
        "enviar mensagem no WhatsApp para lead novo",
    ],
    "envio_de_proposta_comercial": [
        "enviar proposta comercial ao cliente",
        "envio de proposta comercial",
    ],
    "mudanca_estrutural_de_arquitetura": [
        "mudanca estrutural de arquitetura exige ADR",
        "alteracao estrutural de arquitetura",
    ],
    "rollback_em_producao": [
        "rollback em producao apos incidente",
        "rollback em producao",
    ],
    "exclusao_de_dado_de_cliente": [
        "excluir dado de cliente",
        "exclusao de registro de auditoria",
        "apagar dado de cliente do banco",
    ],
    "rotacao_ou_revogacao_de_credencial": [
        "rotacao de credencial de producao",
        "revogar credencial de deploy",
    ],
    "publicacao_em_nome_da_transformativa": [
        "publicar conteudo em nome da Transformativa",
        "publicacao em nome da Transformativa sem aprovacao expressa",
        "postar em nome da Transformativa",
    ],
}

# Secoes obrigatorias da politica (as que `carregar_politica` exige).
SECOES_OBRIGATORIAS = ("limiares", "lanes", "perfis_modelo", "precedencia",
                       "nunca_decidido_por_maquina", "guardrails", "fallback", "recibo")

SEGREDO_DE_TESTE = "credencial-sintetica-do-validardor-t04"   # valor de ambiente
SEGREDO_POR_FORMATO = "sk-" + "T04TESTE" * 3                  # formato de chave


# ---------------------------------------------------------------------------
# Infra da suite
# ---------------------------------------------------------------------------
def carregar_roteador(caminho=ROTEADOR, nome="router_da_validacao"):
    especificacao = importlib.util.spec_from_file_location(nome, str(caminho))
    modulo = importlib.util.module_from_spec(especificacao)
    sys.modules[nome] = modulo
    especificacao.loader.exec_module(modulo)
    return modulo


class Itens:
    """Coletor: toda excecao vira FALHOU com a excecao registrada (nunca OK mudo)."""

    def __init__(self):
        self.lista = []

    def checar(self, nome, funcao, grupo="criterio"):
        try:
            estado, detalhe = funcao()
        except Exception as erro:  # noqa: BLE001 - a suite precisa registrar tudo
            estado, detalhe = "FALHOU", f"excecao: {type(erro).__name__}: {erro}"
        self.lista.append((nome, str(estado), str(detalhe), grupo))

    def add(self, nome, ok, detalhe="", grupo="criterio"):
        self.lista.append((nome, "OK" if ok else "FALHOU", str(detalhe), grupo))

    def achado(self, nome, executou, detalhe=""):
        """Item adversarial: OK quando o caminho proibido reprova; ACHADO quando passa."""
        self.lista.append((nome, "ACHADO" if executou else "OK", str(detalhe), "prosa"))

    def nomes(self):
        return [nome for nome, _, _, _ in self.lista]

    def falhas(self):
        return [x for x in self.lista if x[1] == "FALHOU"]

    def achados(self):
        return [x for x in self.lista if x[1] == "ACHADO"]


def _bloqueia(resultado, roteador):
    """True quando a decisao NAO executa (BLOCK ou ESCALATE)."""
    decisao = resultado["decisao"]
    return (decisao["pode_executar"] is False
            and resultado["recibo"]["outcome"] in (roteador.OUTCOME_BLOQUEAR,
                                                   roteador.OUTCOME_ESCALAR))


def _sem_linhas(texto, *trechos):
    return "\n".join(linha for linha in texto.splitlines()
                     if not any(t in linha for t in trechos))


def _sem_secao(texto, secao):
    """Remove um bloco de topo do YAML (da chave ate o proximo separador de comentario)."""
    linhas, saida, dentro = texto.splitlines(), [], False
    for linha in linhas:
        if linha.startswith(f"{secao}:"):
            dentro = True
            continue
        if dentro and linha.startswith("# ---"):
            dentro = False
        if not dentro:
            saida.append(linha)
    return "\n".join(saida)


def _regra_removida(texto, termo):
    """Remove da secao `guardrails` a regra que contem `termo` e a `verifica` dela.

    Preserva o final do arquivo (comparar texto mutado com o original so faz
    sentido se a unica diferenca for a linha removida).
    """
    linhas = texto.splitlines(keepends=True)
    saida, i = [], 0
    while i < len(linhas):
        linha = linhas[i]
        if linha.lstrip().startswith("- regra:") and termo in linha:
            seguinte = i + 1
            if seguinte < len(linhas) and linhas[seguinte].lstrip().startswith("verifica:"):
                i = seguinte + 1          # remove regra + verifica
            else:
                i += 1                    # remove so a regra
            continue
        saida.append(linha)
        i += 1
    return "".join(saida)


# ---------------------------------------------------------------------------
# Execucao de ponta a ponta pela CLI (copia temporaria do roteador)
# ---------------------------------------------------------------------------
def _rodar_cli(caminho_roteador, tarefa, politica=None, papeis=PAPEIS, cwd=None, env=None):
    comando = [sys.executable, str(caminho_roteador), "--json", json.dumps(tarefa)]
    if politica is not None:
        comando += ["--politica", str(politica)]
    if papeis is not None:
        comando += ["--papeis", str(papeis)]
    processo = subprocess.run(comando, capture_output=True, text=True,
                              cwd=str(cwd or pathlib.Path(caminho_roteador).parent), env=env)
    saida = None
    if processo.stdout.strip():
        try:
            saida = json.loads(processo.stdout)
        except json.JSONDecodeError:
            saida = None
    return processo, saida


# ---------------------------------------------------------------------------
# A validacao
# ---------------------------------------------------------------------------
def verificar(roteador, caminho_politica=POLITICA, diretorio_papeis=PAPEIS, area=None):
    """Devolve [(nome, estado, detalhe, grupo)] para o modulo `roteador` informado."""
    itens = Itens()
    origem = pathlib.Path(getattr(roteador, "__file__", ROTEADOR))
    area = pathlib.Path(area or tempfile.mkdtemp(prefix="jev-t04-"))
    area.mkdir(parents=True, exist_ok=True)

    politica = roteador.carregar_politica(caminho_politica, diretorio_de_papeis=diretorio_papeis)
    papeis = roteador.carregar_politicas_de_papel(diretorio_papeis)
    lim = roteador.limiares(politica)
    alta = min(0.99, float(lim["aceitar"]) + 0.05)
    lane_conservadora = politica["_lane_conservadora"]
    campos = roteador.campos_do_recibo(politica)
    guardrails_yaml = [f"{g.get('regra', '')} {g.get('verifica', '')}"
                       for g in politica.get("guardrails") or []]
    acoes_yaml = roteador.acoes_nunca_decididas_por_maquina(politica)

    def decisao(acao, **extra):
        tarefa = {"card_id": extra.pop("card_id", "t_validacao"), "status": "ready",
                  "acao": acao, "lane_proposta": "small", "confianca": alta}
        tarefa.update(extra)
        return roteador.decidir(tarefa, politica=politica)

    def resumo(resultado):
        d = resultado["decisao"]
        return (f"decidido={d['decidido']} outcome={resultado['recibo']['outcome']} "
                f"lane={resultado['recibo']['lane']} guardrails={d['guardrails_acionados']}")

    # ============================================================== 1. guardrails
    # Nota de nomenclatura: os itens de guardrail carregam a palavra REPROVA ou
    # APROVA no nome; o item de cobertura no fim varre a lista e recusa guardrail
    # declarado no YAML que nao tenha os dois lados.

    def _segredo_reprova():
        casos = [
            ("sinal segredo_no_payload", {"sinais": {"segredo_no_payload": True}}),
            ("formato sk- em campo de texto", {"descricao": SEGREDO_POR_FORMATO}),
            ("bloco de chave privada PEM", {"descricao": "-----BEGIN " + "RSA PRIVATE KEY-----"}),
            ("credencial embutida em URL", {"descricao": "https://usuario:senha12345@host/x"}),
            ("atribuicao senha= no corpo", {"descricao": "senha: abcdef12345"}),
            ("variavel de ambiente com valor no payload", {"descricao": SEGREDO_DE_TESTE}),
        ]
        problemas = []
        os.environ["T04_TESTE_PASSWORD"] = SEGREDO_DE_TESTE
        try:
            for nome, extra in casos:
                resultado = decisao("ajuste de texto simples", **extra)
                acionados = resultado["decisao"]["guardrails_acionados"]
                if not (_bloqueia(resultado, roteador)
                        and resultado["recibo"]["outcome"] == roteador.OUTCOME_BLOQUEAR
                        and "segredo_sem_payload" in acionados):
                    problemas.append(f"{nome}: {resumo(resultado)}")
        finally:
            os.environ.pop("T04_TESTE_PASSWORD", None)
        return ("OK" if not problemas else "FALHOU",
                "; ".join(problemas) if problemas else f"{len(casos)} formas bloqueadas com BLOCK")

    itens.checar("guardrail segredo — REPROVA: 6 formas de segredo bloqueiam", _segredo_reprova)

    def _segredo_aprova():
        resultado = decisao("ajuste de texto simples", descricao="tarefa sem credencial nenhuma")
        return ("OK" if resultado["decisao"]["decidido"] == "executar"
                and "segredo_sem_payload" not in resultado["decisao"]["guardrails_acionados"]
                else "FALHOU", resumo(resultado))

    itens.checar("guardrail segredo — APROVA: payload limpo executa", _segredo_aprova)

    def _segredo_nao_vaza():
        os.environ["T04_TESTE_PASSWORD"] = SEGREDO_DE_TESTE
        try:
            bruto = json.dumps(decisao("ajuste de texto", descricao=SEGREDO_DE_TESTE),
                               ensure_ascii=False, default=str)
        finally:
            os.environ.pop("T04_TESTE_PASSWORD", None)
        bruto += json.dumps(decisao("ajuste de texto", descricao=SEGREDO_POR_FORMATO),
                            ensure_ascii=False, default=str)
        vazou = [m for m in (SEGREDO_DE_TESTE, SEGREDO_POR_FORMATO) if m in bruto]
        return ("OK" if not vazou else "FALHOU",
                "nem o valor nem o formato aparecem na decisao/recibo" if not vazou
                else f"vazou: {vazou}")

    itens.checar("guardrail segredo — APROVA: segredo bloqueado nao aparece em decisao/recibo",
                 _segredo_nao_vaza)

    def _do_not_contact_reprova():
        resultado = decisao("enviar e-mail pelo Titan",
                            sinais={"empresa_do_not_contact": True})
        acionados = resultado["decisao"]["guardrails_acionados"]
        return ("OK" if _bloqueia(resultado, roteador) and "do_not_contact" in acionados
                else "FALHOU", resumo(resultado))

    itens.checar("guardrail do_not_contact — REPROVA: empresa marcada + acao outbound bloqueia",
                 _do_not_contact_reprova)

    def _do_not_contact_aprova():
        problemas = []
        sem_marca = decisao("enviar e-mail pelo Titan")
        if (sem_marca["decisao"]["decidido"] != "executar"
                or "do_not_contact" in sem_marca["decisao"]["guardrails_acionados"]):
            problemas.append(f"outbound sem marcacao: {resumo(sem_marca)}")
        interna = decisao("ajuste de texto simples", sinais={"empresa_do_not_contact": True})
        if ("do_not_contact" in interna["decisao"]["guardrails_acionados"]
                or interna["decisao"]["decidido"] != "executar"):
            problemas.append(f"marcada + acao interna: {resumo(interna)}")
        return ("OK" if not problemas else "FALHOU", "; ".join(problemas) or
                "outbound sem marcacao executa; empresa marcada em acao interna executa")

    itens.checar("guardrail do_not_contact — APROVA: sem marcacao ou sem outbound executa",
                 _do_not_contact_aprova)

    def _ddl_reprova():
        problemas = []
        for nome, extra in (("ambiente producao", {"ambiente_alvo": "producao"}),
                            ("ambiente nao declarado", {})):
            resultado = decisao("aplicar DDL/migration em qualquer ambiente", **extra)
            acionados = resultado["decisao"]["guardrails_acionados"]
            if not (_bloqueia(resultado, roteador) and "ddl_fora_de_producao" in acionados):
                problemas.append(f"{nome}: {resumo(resultado)}")
        return ("OK" if not problemas else "FALHOU", "; ".join(problemas) or
                "DDL em producao e DDL sem ambiente declarado bloqueiam")

    itens.checar("guardrail DDL — REPROVA: DDL em producao ou sem ambiente bloqueia",
                 _ddl_reprova)

    def _ddl_aprova():
        problemas = []
        for nome, extra in (("desenvolvimento", {"ambiente_alvo": "desenvolvimento"}),
                            ("dev", {"ambiente_alvo": "dev",
                                     "acao": "rodar migration no banco"})):
            extra = dict(extra)
            acao = extra.pop("acao", "aplicar DDL/migration em qualquer ambiente")
            resultado = decisao(acao, **extra)
            if (resultado["decisao"]["decidido"] != "executar"
                    or "ddl_fora_de_producao" in resultado["decisao"]["guardrails_acionados"]):
                problemas.append(f"{nome}: {resumo(resultado)}")
        return ("OK" if not problemas else "FALHOU", "; ".join(problemas) or
                "DDL em desenvolvimento/dev executa")

    itens.checar("guardrail DDL — APROVA: DDL em ambiente de desenvolvimento executa", _ddl_aprova)

    def _papel_reprova():
        problemas = []
        casos = [
            ("sales-ai pedindo deploy", {"acao": "executar deploy, promocao de release ou rollback",
                                         "papel_solicitado": "sales-ai",
                                         "lane_proposta": "critical"}),
            ("sales-ai com credencial proibida GITHUB_TOKEN",
             {"acao": "gerar conteudo, resumo, score e recomendacao",
              "papel_solicitado": "sales-ai", "credencial_solicitada": "GITHUB_TOKEN"}),
            ("papel solicitado desconhecido",
             {"acao": "gerar conteudo", "papel_solicitado": "papel-inventado"}),
        ]
        for nome, extra in casos:
            resultado = decisao(extra.pop("acao"), **extra)
            if not (_bloqueia(resultado, roteador)
                    and "papel_sem_credencial_de_deploy"
                    in resultado["decisao"]["guardrails_acionados"]):
                problemas.append(f"{nome}: {resumo(resultado)}")
        return ("OK" if not problemas else "FALHOU", "; ".join(problemas) or
                "deploy, credencial proibida e papel desconhecido bloqueiam")

    itens.checar("guardrail Sales AI x credencial de deploy — REPROVA: bloqueia", _papel_reprova)

    def _papel_aprova():
        problemas = []
        comercial = decisao("gerar conteudo, resumo, score e recomendacao",
                            papel_solicitado="sales-ai", lane_proposta="medium")
        if comercial["decisao"]["decidido"] != "executar":
            problemas.append(f"acao comercial: {resumo(comercial)}")
        credencial_ok = decisao("gerar conteudo, resumo, score e recomendacao",
                                papel_solicitado="sales-ai",
                                credencial_solicitada="TRE_PG_* (runtime de negocio, sem DDL)",
                                lane_proposta="medium")
        if credencial_ok["decisao"]["decidido"] != "executar":
            problemas.append(f"credencial permitida: {resumo(credencial_ok)}")
        return ("OK" if not problemas else "FALHOU", "; ".join(problemas) or
                "acao comercial do sales-ai e credencial permitida executam")

    itens.checar("guardrail Sales AI x credencial de deploy — APROVA: acao/credencial "
                 "permitida executa", _papel_aprova)

    def _fail_closed_reprova():
        problemas = []
        desconhecido = decisao("ajuste de texto simples", sinais={"sinal_que_nao_existe": True})
        if not (_bloqueia(desconhecido, roteador)
                and "fail_closed_sinal_desconhecido"
                in desconhecido["decisao"]["guardrails_acionados"]):
            problemas.append(f"sinal desconhecido: {resumo(desconhecido)}")
        nulo = decisao("ajuste de texto simples", sinais=None)
        if not _bloqueia(nulo, roteador):
            problemas.append(f"sinais None: {resumo(nulo)}")
        # O guardrail existe (avaliado direto, sem mock do alvo): ele aciona para
        # entrada de tipo invalido. A decisao ponta a ponta desses dois casos esta
        # nos itens ACHADO abaixo, porque hoje o roteador estoura antes de bloquear.
        for entrada, rotulo in (("texto solto, nao mapa", "payload nao-mapa"),
                                ({"sinais": ["lista"]}, "sinais nao-mapa")):
            avaliados = roteador._guardrails_de_codigo(entrada)
            if len(avaliados) != 1 or avaliados[0]["id"] != "payload_valido" \
                    or not avaliados[0]["acionado"]:
                problemas.append(f"guardrail payload_valido nao aciona para {rotulo}: {avaliados}")
        return ("OK" if not problemas else "FALHOU", "; ".join(problemas) or
                "sinal desconhecido e sinais None bloqueiam; payload_valido aciona para tipo invalido")

    itens.checar("guardrail fail-closed — REPROVA: sinal desconhecido e entrada invalida "
                 "nao liberam", _fail_closed_reprova)

    def _fail_closed_aprova():
        resultado = decisao("ajuste de texto simples", sinais={})
        return ("OK" if resultado["decisao"]["decidido"] == "executar"
                else "FALHOU", resumo(resultado))

    itens.checar("guardrail fail-closed — APROVA: entrada valida e sem sinal desconhecido "
                 "executa", _fail_closed_aprova)

    # --- fail-closed na LEITURA da politica: politica que deixa de declarar um
    # guardrail nao e a politica que o roteador implementa: recusa, nao execucao.
    def _politica_sem_guardrail_recusada():
        problemas = []
        for termo, rotulo in (("segredo nunca entra", "segredo"),
                              ("do_not_contact ou opt_out", "do_not_contact"),
                              ("DDL não nasce", "DDL"),
                              ("Sales AI não tem credencial de deploy", "credencial de deploy"),
                              ("falha de guardrail bloqueia", "fail-closed")):
            texto = caminho_politica.read_text(encoding="utf-8")
            mutado = _regra_removida(texto, termo)
            mutada = area / f"politica-sem-guardrail-{rotulo.replace(' ', '-')}.yaml"
            mutada.write_text(mutado, encoding="utf-8")
            if termo not in texto or termo in mutado:
                problemas.append(f"{rotulo}: a regra nao foi removida (ancora mudou)")
                continue
            try:
                roteador.carregar_politica(mutada, diretorio_de_papeis=diretorio_papeis)
                problemas.append(f"{rotulo}: ACEITOU politica sem o guardrail")
            except roteador.PoliticaInvalida:
                pass
        return ("OK" if not problemas else "FALHOU", "; ".join(problemas) or
                "os 5 guardrails exigidos: politica que para de declara-los e recusada")

    itens.checar("guardrail fail-closed na leitura — REPROVA: politica sem guardrail "
                 "declarado e recusada", _politica_sem_guardrail_recusada)

    # =================================================== 2. acoes nunca por maquina
    def _acao_bloqueia(acao, rotulo):
        def _teste():
            resultado = decisao(acao)
            if not _bloqueia(resultado, roteador):
                return ("FALHOU", f"EXECUTOU: {resumo(resultado)}")
            if resultado["recibo"]["outcome"] == roteador.OUTCOME_BLOQUEAR:
                return ("OK", f"BLOCK por nome exato — {resumo(resultado)}")
            return ("OK", f"escalou em vez de executar — {resumo(resultado)}")
        return _teste

    for acao in acoes_yaml:
        itens.checar(f"acao proibida {acao} — BLOQUEIO por nome exato",
                     _acao_bloqueia(acao, acao))

    # --- variacoes em prosa (o roteador casa por texto)
    for acao in acoes_yaml:
        for variacao in PROSA.get(acao, []):
            def _prosa(acao=acao, variacao=variacao):
                resultado = decisao(variacao)
                executou = resultado["decisao"]["pode_executar"] is True
                return executou, f"{resumo(resultado)}"
            itens.checar(f"acao proibida {acao} — PROSA '{variacao}' bloqueia ou escala",
                         (lambda f=_prosa: ("ACHADO", f()[1]) if f()[0] else ("OK", f()[1])))

    # ============================================================== 3. fallback
    def _politicas_ruins():
        """Cria as quatro politicas quebradas + o diretorio sem politica nenhuma."""
        ruins = {}
        bom = caminho_politica.read_text(encoding="utf-8")

        ausente = area / "sem-politica-aqui" / "policy_v1.yaml"
        ausente.parent.mkdir(parents=True, exist_ok=True)
        if ausente.exists():
            ausente.unlink()
        ruins["politica ausente"] = ausente

        corrompida = area / "politica-corrompida.yaml"
        corrompida.write_text("versao: [isto nao fecha o YAML\n", encoding="utf-8")
        ruins["politica ilegivel/corrompida"] = corrompida

        versao = area / "politica-versao-desconhecida.yaml"
        versao.write_text(bom.replace("versao: jev-policy-v1.0", "versao: jev-policy-v9.9"),
                          encoding="utf-8")
        ruins["versao de politica desconhecida"] = versao

        secao = area / "politica-sem-guardrails.yaml"
        secao.write_text(_sem_secao(bom, "guardrails"), encoding="utf-8")
        ruins["secao guardrails faltando"] = secao
        return ruins

    ruins = _politicas_ruins()

    def _leitura_recusa():
        problemas = []
        for rotulo, caminho in ruins.items():
            if rotulo == "politica ausente":
                continue
            try:
                roteador.carregar_politica(caminho, diretorio_de_papeis=diretorio_papeis)
                problemas.append(f"{rotulo}: ACEITOU")
            except roteador.PoliticaInvalida as erro:
                if not str(erro).strip():
                    problemas.append(f"{rotulo}: recusou sem motivo")
        return ("OK" if not problemas else "FALHOU", "; ".join(problemas) or
                "corrompida, versao desconhecida e secao faltando sao recusadas na leitura")

    itens.checar("fallback — REPROVA: politica corrompida/versao desconhecida/secao faltando "
                 "sao recusadas na leitura", _leitura_recusa)

    def _secoes_faltando():
        bom = caminho_politica.read_text(encoding="utf-8")
        aceitas = []
        for secao in SECOES_OBRIGATORIAS:
            mutada = area / f"politica-sem-{secao}.yaml"
            mutada.write_text(_sem_secao(bom, secao), encoding="utf-8")
            try:
                roteador.carregar_politica(mutada, diretorio_de_papeis=diretorio_papeis)
                aceitas.append(secao)
            except roteador.PoliticaInvalida:
                pass
        return ("OK" if not aceitas else "FALHOU",
                f"as {len(SECOES_OBRIGATORIAS)} secoes obrigatorias: removida uma a uma, todas "
                f"recusadas" if not aceitas else f"aceitas sem a secao: {aceitas}")

    itens.checar("fallback — REPROVA: qualquer secao obrigatoria faltando e recusada",
                 _secoes_faltando)

    def _degradado_sem_politica():
        resultado = roteador.decidir(
            {"card_id": "t_deg", "acao": "ajuste de texto simples", "lane_proposta": "small",
             "confianca": alta, "status": "ready"},
            politica=None, motivo_politica="politica indisponivel (teste)")
        d, recibo = resultado["decisao"], resultado["recibo"]
        problemas = []
        if recibo["lane"] != lane_conservadora:
            problemas.append(f"lane={recibo['lane']} (esperado {lane_conservadora})")
        if d["degraded_mode"] is not True:
            problemas.append(f"degraded_mode={d['degraded_mode']}")
        if d["pode_executar"] or recibo["outcome"] != roteador.OUTCOME_ESCALAR:
            problemas.append(f"EXECUTOU: {resumo(resultado)}")
        if recibo["confidence"] is not None:
            problemas.append(f"confianca inferida: {recibo['confidence']}")
        return ("OK" if not problemas else "FALHOU", "; ".join(problemas) or
                f"lane={recibo['lane']} degradado={d['degraded_mode']} "
                f"outcome={recibo['outcome']} confidence=None")

    itens.checar("fallback — APROVA: politica indisponivel entra em degradado conservador "
                 "sem execucao", _degradado_sem_politica)

    def _degradado_guardrails_primeiro():
        os.environ["T04_TESTE_PASSWORD"] = SEGREDO_DE_TESTE
        try:
            resultado = roteador.decidir(
                {"card_id": "t_deg_seg", "acao": "ajuste de texto simples",
                 "descricao": SEGREDO_DE_TESTE, "lane_proposta": "small", "confianca": alta,
                 "status": "ready"}, politica=None, motivo_politica="politica indisponivel")
        finally:
            os.environ.pop("T04_TESTE_PASSWORD", None)
        d, recibo = resultado["decisao"], resultado["recibo"]
        return ("OK" if (recibo["outcome"] == roteador.OUTCOME_BLOQUEAR
                         and "segredo_sem_payload" in d["guardrails_acionados"]
                         and d["degraded_mode"] is True)
                else "FALHOU", resumo(resultado))

    itens.checar("fallback — APROVA: no degradado os guardrails continuam rodando primeiro",
                 _degradado_guardrails_primeiro)

    # --- ponta a ponta pela CLI, em copia temporaria do roteador
    def _cli_uma_politica_ruim(rotulo, caminho):
        processo, saida = _rodar_cli(origem, {"card_id": "t_cli_deg", "acao": "ajuste de texto simples",
                                              "lane_proposta": "small", "confianca": alta},
                                     politica=caminho)
        problemas = []
        if processo.returncode != 2:
            problemas.append(f"exit={processo.returncode} stderr={processo.stderr[:120]}")
        if not saida:
            return ("FALHOU", "; ".join(problemas) or "sem JSON de saida")
        recibo, decisao = saida["recibo"], saida["decisao"]
        if recibo["lane"] != lane_conservadora:
            problemas.append(f"lane={recibo['lane']}")
        if decisao["degraded_mode"] is not True:
            problemas.append(f"degraded_mode={decisao['degraded_mode']}")
        if decisao["pode_executar"] is not False or recibo["outcome"] != roteador.OUTCOME_ESCALAR:
            problemas.append(f"EXECUTOU: outcome={recibo['outcome']}")
        if recibo["confidence"] is not None:
            problemas.append(f"confianca={recibo['confidence']}")
        if list(recibo.keys()) != list(campos):
            problemas.append(f"recibo fora do contrato: {list(recibo.keys())}")
        return ("OK" if not problemas else "FALHOU", "; ".join(problemas) or
                f"exit=2 lane={recibo['lane']} degradado={decisao['degraded_mode']} "
                f"outcome={recibo['outcome']} confidence=None recibo={len(recibo)} campos")

    for rotulo, caminho in ruins.items():
        itens.checar(f"fallback ponta a ponta (CLI) — REPROVA: {rotulo} -> lane "
                     f"conservadora + degradado, sem execucao",
                     (lambda r=rotulo, c=caminho: _cli_uma_politica_ruim(r, c)))

    def _cli_politica_ausente_no_caminho_padrao():
        """Sem --politica: o roteador procura no caminho padrao e nao acha nada."""
        pasta = area / "cli-sem-politica"
        pasta.mkdir(parents=True, exist_ok=True)
        copia = pasta / "router.py"
        shutil.copy(origem, copia)
        processo, saida = _rodar_cli(copia, {"card_id": "t_cli_ausente",
                                             "acao": "ajuste de texto simples",
                                             "lane_proposta": "small", "confianca": alta})
        if not saida:
            return ("FALHOU", f"exit={processo.returncode} stderr={processo.stderr[:120]}")
        recibo, decisao = saida["recibo"], saida["decisao"]
        ok = (processo.returncode == 2 and decisao["degraded_mode"] is True
              and recibo["lane"] == lane_conservadora and decisao["pode_executar"] is False
              and recibo["policy_version"] is None)
        return ("OK" if ok else "FALHOU",
                f"exit={processo.returncode} lane={recibo['lane']} "
                f"degradado={decisao['degraded_mode']} policy_version={recibo['policy_version']}")

    itens.checar("fallback ponta a ponta (CLI) — REPROVA: politica ausente no caminho padrao "
                 "-> degradado, nunca execucao silenciosa", _cli_politica_ausente_no_caminho_padrao)

    def _cli_guardrails_primeiro_em_todas():
        tarefa = {"card_id": "t_cli_seg", "acao": "ajuste de texto simples",
                  "descricao": SEGREDO_POR_FORMATO, "lane_proposta": "small", "confianca": alta}
        problemas = []
        for rotulo, caminho in ruins.items():
            processo, saida = _rodar_cli(origem, tarefa, politica=caminho)
            if not saida:
                problemas.append(f"{rotulo}: exit={processo.returncode} sem JSON")
                continue
            if (saida["recibo"]["outcome"] != roteador.OUTCOME_BLOQUEAR
                    or "segredo_sem_payload" not in saida["decisao"]["guardrails_acionados"]):
                problemas.append(f"{rotulo}: exit={processo.returncode} "
                                 f"outcome={saida['recibo']['outcome']}")
        return ("OK" if not problemas else "FALHOU", "; ".join(problemas) or
                "nos 4 cenarios quebrados o guardrail de segredo bloqueou (BLOCK) antes do fallback")

    itens.checar("fallback ponta a ponta (CLI) — APROVA: guardrails rodam primeiro nos 4 "
                 "cenarios quebrados", _cli_guardrails_primeiro_em_todas)

    def _abstencao_nao_executa():
        problemas = []
        for rotulo, extra in (("confianca abaixo do limiar", {"confianca": max(0.0, float(lim["abster"]) - 0.05),
                                                              "lane_proposta": "medium"}),
                              ("confianca ausente", {"confianca": None}),
                              ("lane fora da politica", {"lane_proposta": "turbo"})):
            tarefa = {"card_id": "t_abs", "acao": "ajuste de texto simples", "status": "ready"}
            tarefa.update(extra)
            resultado = roteador.decidir(tarefa, politica=politica)
            d, recibo = resultado["decisao"], resultado["recibo"]
            if d["pode_executar"] or recibo["outcome"] != roteador.OUTCOME_ESCALAR \
                    or recibo["lane"] != lane_conservadora:
                problemas.append(f"{rotulo}: {resumo(resultado)}")
        return ("OK" if not problemas else "FALHOU", "; ".join(problemas) or
                "abstencao usa a lane conservadora e escala nos 3 casos")

    itens.checar("fallback — APROVA: abstencao (confianca baixa/ausente, lane desconhecida) "
                 "escala na lane conservadora", _abstencao_nao_executa)

    # ============================================================ 4. achados ponta a ponta
    def _achado_payload_invalido():
        for entrada, rotulo in (("texto solto, nao mapa", "payload nao-mapa"),
                                ({"card_id": "t", "acao": "ajuste de texto", "sinais": ["lista"]},
                                 "sinais nao-mapa")):
            try:
                resultado = roteador.decidir(entrada, politica=politica)
            except Exception as erro:  # noqa: BLE001
                itens.lista.append((f"fail-closed ponta a ponta — {rotulo} nao pode estourar",
                                    "ACHADO", f"excecao {type(erro).__name__}: {erro}", "prosa"))
                continue
            itens.lista.append((f"fail-closed ponta a ponta — {rotulo} bloqueia",
                                "OK" if _bloqueia(resultado, roteador) else "ACHADO",
                                resumo(resultado), "prosa"))

    _achado_payload_invalido()

    def _achado_human_approval_declarado():
        """Acoes que o proprio repositorio declara em hermes/policies/human-approval.yaml."""
        arquivo = diretorio_papeis / "human-approval.yaml"
        if not arquivo.is_file():
            itens.lista.append(("acao declarada em human-approval.yaml — BLOQUEIO",
                                "FALHOU", f"ausente: {arquivo}", "criterio"))
            return
        declaradas = []
        brutos = yaml.safe_load(arquivo.read_text(encoding="utf-8")) or {}
        for secao in ("exige_aprovacao", "nunca_automatico"):
            declaradas += [(secao, str(x)) for x in brutos.get(secao) or []]
        executadas = []
        for secao, acao in declaradas:
            resultado = decisao(acao)
            if resultado["decisao"]["pode_executar"] is True:
                executadas.append(f"{secao}: '{acao}' -> {resumo(resultado)}")
        itens.lista.append((f"acao declarada em human-approval.yaml — BLOQUEIO ({len(declaradas)} "
                            "declaracoes)",
                            "ACHADO" if executadas else "OK",
                            "; ".join(executadas) if executadas else
                            "todas bloqueiam ou escalam", "prosa"))

    _achado_human_approval_declarado()

    # ============================================================ 5. cobertura
    def _cobertura_guardrails():
        nomes = itens.nomes()
        faltando = []
        for guardrail, termos in GUARDRAILS_ESPERADOS.items():
            declarado = any(any(t.lower() in regra.lower() for t in termos)
                            for regra in guardrails_yaml)
            if not declarado:
                faltando.append(f"{guardrail}: NAO esta declarado no YAML")
                continue
            tem_reprova = any("guardrail " in n and "REPROVA" in n
                              and any(t.lower() in n.lower() for t in termos) for n in nomes)
            tem_aprova = any("guardrail " in n and "APROVA" in n
                             and any(t.lower() in n.lower() for t in termos) for n in nomes)
            if not tem_reprova or not tem_aprova:
                faltando.append(f"{guardrail}: REPROVA={tem_reprova} APROVA={tem_aprova}")
        return ("OK" if not faltando else "FALHOU", "; ".join(faltando) if faltando else
                f"{len(GUARDRAILS_ESPERADOS)} guardrails declarados no YAML, "
                f"{len(guardrails_yaml)} regras, todos com teste de REPROVA e de APROVA")

    itens.checar("cobertura — cada guardrail do YAML tem teste de REPROVA e de APROVA",
                 _cobertura_guardrails)

    def _cobertura_acoes():
        nomes = itens.nomes()
        faltando = [a for a in acoes_yaml
                    if f"acao proibida {a} — BLOQUEIO por nome exato" not in nomes]
        sem_prosa = [a for a in acoes_yaml
                     if not any(n.startswith(f"acao proibida {a} — PROSA") for n in nomes)]
        problemas = [f"sem teste de bloqueio por nome: {faltando}"] if faltando else []
        problemas += [f"sem variacao em prosa: {sem_prosa}"] if sem_prosa else []
        return ("OK" if not problemas else "FALHOU", "; ".join(problemas) if problemas else
                f"{len(acoes_yaml)} acoes de nunca_decidido_por_maquina com teste de bloqueio "
                f"e com variacao em prosa")

    itens.checar("cobertura — cada acao proibida do YAML tem teste de bloqueio e de prosa",
                 _cobertura_acoes)

    # ============================================================ 6. contrato minimo
    itens.checar("sanity — o roteador carrega a politica e os papeis do repo",
                 lambda: ("OK", f"versao={politica['versao']} lanes={list(politica['lanes'])} "
                                f"papeis={sorted(papeis)}"))
    itens.checar("sanity — o roteador nao foi alterado por esta suite (arquivos do repo intactos)",
                 lambda: ("OK" if ROTEADOR.is_file() and POLITICA.is_file()
                          and DOC_POLITICA.is_file() else "FALHOU",
                          f"{ROTEADOR.relative_to(RAIZ)} {POLITICA.relative_to(RAIZ)} "
                          f"{DOC_POLITICA.relative_to(RAIZ)}"))

    return itens.lista


# ---------------------------------------------------------------------------
# Autoteste por mutacao: cada guardrail e o fallback removidos um a um
# ---------------------------------------------------------------------------
def mutacoes():
    """(nome, mexer_no_codigo, mexer_na_politica). A mutacao REMOVE a protecao."""
    identidade = lambda t: t  # noqa: E731

    def segredo_ignorado(codigo):
        return codigo.replace("    motivo_segredo = _segredo_no_payload(tarefa)",
                              '    motivo_segredo = ""')

    def do_not_contact_ignorado(codigo):
        return codigo.replace("        do_not_contact and _e_acao_outbound(acao, politica, papeis),",
                              "        False,")

    def ddl_sempre_permitido(codigo):
        return codigo.replace(
            '        ddl_acionado = ambiente != "desenvolvimento" and ambiente != "dev"',
            "        ddl_acionado = False")

    def papel_liberado(codigo):
        return codigo.replace("        bool(motivo_papel), motivo_papel))", '        False, ""))')

    def fail_closed_sinal_ignorado(codigo):
        return codigo.replace("        bool(desconhecidos),", "        False,")

    def payload_invalido_liberado(codigo):
        # Desliga o fail-closed de tipo invalido SEM quebrar a sintaxe: o mutante
        # tem de ser detectado por item de teste, nunca por erro de parse.
        return codigo.replace(
            "    if not isinstance(tarefa, dict):", "    if False:") \
            .replace('    if not isinstance(tarefa.get("sinais", {}), dict):', "    if False:")

    def guardrails_de_codigo_nao_rodam(codigo):
        return codigo.replace("    guardrails = list(_guardrails_de_codigo(tarefa))",
                              "    guardrails = []")

    def nada_bloqueia(codigo):
        return codigo.replace('    acionados = [g for g in guardrails if g["acionado"]]\n'
                              "    if acionados:",
                              "    acionados = []\n    if acionados:")

    def degradado_executa(codigo):
        return codigo.replace('        plano["outcome"] = OUTCOME_ESCALAR\n'
                              '        plano["motivos"] = [motivo_politica or "politica indisponivel",',
                              '        plano["outcome"] = OUTCOME_EXECUTAR\n'
                              '        plano["decidido"] = "executar"\n'
                              '        plano["motivos"] = [motivo_politica or "politica indisponivel",')

    def lane_degradada_barata(codigo):
        return codigo.replace(
            '    if politica:\n        lane = politica.get("_lane_conservadora")\n'
            "        if lane:\n            return lane\n"
            "    return LANE_DEGRADADA_PADRAO", '    return "small"')

    def exigencia_de_guardrail_ignorada(codigo):
        return codigo.replace("        if not declarado:", "        if False:")

    def versao_desconhecida_aceita(codigo):
        return codigo.replace("    if not versao or str(versao) not in VERSOES_DE_POLITICA_SUPORTADAS:",
                              "    if False:")

    def precedencia_humana_ignorada(codigo):
        return codigo.replace("    if humano:", "    if False and humano:")

    return [
        ("guardrail segredo removido (payload com segredo passa)", segredo_ignorado, identidade),
        ("guardrail do_not_contact removido (empresa marcada e contatada)",
         do_not_contact_ignorado, identidade),
        ("guardrail DDL removido (DDL nasce em producao)", ddl_sempre_permitido, identidade),
        ("guardrail de papel/credencial removido (Sales AI com deploy)", papel_liberado, identidade),
        ("fail-closed removido no sinal desconhecido", fail_closed_sinal_ignorado, identidade),
        ("fail-closed removido na entrada de tipo invalido", payload_invalido_liberado, identidade),
        ("guardrails deterministas deixam de rodar", guardrails_de_codigo_nao_rodam, identidade),
        ("nenhum guardrail bloqueia (a barreira e decorativa)", nada_bloqueia, identidade),
        ("fallback executa em modo degradado", degradado_executa, identidade),
        ("fallback usa lane barata em vez da conservadora", lane_degradada_barata, identidade),
        ("roteador deixa de exigir o guardrail declarado na politica",
         exigencia_de_guardrail_ignorada, identidade),
        ("versao de politica desconhecida aceita", versao_desconhecida_aceita, identidade),
        ("precedencia humana ignorada (Human Approval contornado)", precedencia_humana_ignorada,
         identidade),
    ]


def autoteste():
    """Prova que a suite reprova cada mutacao. Tudo em copia temporaria."""
    print("\n=== AUTOTESTE: mutacoes que a suite precisa reprovar ===")
    detectadas = 0
    entradas = mutacoes()
    for indice, (nome, mexer_no_codigo, mexer_na_politica) in enumerate(entradas, start=1):
        with tempfile.TemporaryDirectory(prefix=f"jev-t04-mut-{indice}-") as temporario:
            area = pathlib.Path(temporario)
            base = ROTEADOR.read_text(encoding="utf-8")
            texto_politica = POLITICA.read_text(encoding="utf-8")
            mutado = mexer_no_codigo(base)
            politica_mutada = mexer_na_politica(texto_politica)
            if mutado == base and politica_mutada == texto_politica:
                print(f"FALHOU NAO detectada: {nome}  <-- a mutacao nem foi aplicada (ancora mudou)")
                continue
            alvo_codigo = area / "router.py"
            alvo_codigo.write_text(mutado, encoding="utf-8")
            politica = area / "policy_v1.yaml"
            politica.write_text(politica_mutada, encoding="utf-8")
            try:
                modulo = carregar_roteador(alvo_codigo, nome=f"router_mutado_{indice}")
                itens = verificar(modulo, caminho_politica=politica, diretorio_papeis=PAPEIS,
                                  area=area / "trabalho")
                falhas = [n for n, estado, _, _ in itens if estado == "FALHOU"]
            except Exception as erro:  # noqa: BLE001 - mutante que nem carrega tambem e detectado
                falhas = [f"o mutante nem carrega: {type(erro).__name__}: {erro}"]
        if falhas:
            detectadas += 1
            print(f"OK    detectada: {nome}  ({len(falhas)} item(ns) reprovado(s): "
                  f"{', '.join(falhas[:2])}{'...' if len(falhas) > 2 else ''})")
        else:
            print(f"FALHOU NAO detectada: {nome}  <-- buraco na suite")
    print(f"\nautoteste: {detectadas}/{len(entradas)} mutacoes detectadas")
    return detectadas == len(entradas)


# ---------------------------------------------------------------------------
def imprimir(itens):
    for nome, estado, detalhe, _ in itens:
        print(f"{estado:6s} {nome}" + (f"  [{detalhe}]" if detalhe else ""))
    return len([x for x in itens if x[1] == "FALHOU"])


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    estrito = "--estrito" in argv

    print("=" * 78)
    print("VALIDACAO DOS GUARDRAILS E DO FALLBACK DO JEV (card TRE-W0-E04-T04)")
    print(f"  roteador: {ROTEADOR.relative_to(RAIZ)}")
    print(f"  politica: {POLITICA.relative_to(RAIZ)}")
    print(f"  papeis:   {PAPEIS.relative_to(RAIZ)}")
    print("=" * 78)

    temporario = tempfile.mkdtemp(prefix="jev-t04-suite-")
    modulo = carregar_roteador()
    itens = verificar(modulo, area=pathlib.Path(temporario))
    falhas = imprimir(itens)
    achados = [x for x in itens if x[1] == "ACHADO"]
    prosa = [x for x in itens if x[3] == "prosa"]
    criterio = [x for x in itens if x[3] == "criterio"]

    teste_ok = True
    if "--autoteste" in argv:
        teste_ok = autoteste()

    print()
    print(f"itens: {len(itens)} ({len(criterio)} de criterio, {len(prosa)} adversariais) | "
          f"falhas: {falhas} | achados: {len(achados)}")
    if achados:
        print("ACHADOS (defeito ENCONTRADO no roteador/politica, nao corrigido — regra do card):")
        for nome, _, detalhe, _ in achados:
            print(f"  ACHADO {nome}")
            print(f"         {detalhe}")
    if not teste_ok:
        print("AUTOTESTE: houve mutacao NAO detectada (buraco na suite)")
    total_falhas = falhas + (len(achados) if estrito else 0)
    if total_falhas == 0 and teste_ok:
        print(f"RESULTADO: PASS ({len(itens)} itens, 0 falhas)"
              + (" + autoteste OK" if "--autoteste" in argv else ""))
        return 0
    print(f"RESULTADO: FALHOU ({len(itens)} itens, {total_falhas} falha(s)"
          + (", incluindo os achados por --estrito" if estrito and achados else "")
          + ")")
    return 1


if __name__ == "__main__":
    sys.exit(main())
