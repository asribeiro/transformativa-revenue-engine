#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Suite offline do nurture automatizado (`nutricao-automatica-v1`) — card TRE-W9-E05-T01.

Mede o componente SEM banco (ele nao tem porta de banco: as entradas sao os relatorios dos pais) e
SEM rede, sobre fixtures construidas NA FORMA declarada pelos contratos dos pais — o vocabulario de
dias/faixas e o fuso sao LIDOS do contrato `melhor-horario-v1.json`, nunca digitados aqui.

Itens (cada um imprime OK/FALHOU):
   1. contrato e guardas de ambiente: `--conferir` exit 0; `prod` exit 4 ANTES de ler entrada;
      `homolog` sem `--confirmo` exit 2; relatorio ausente exit 2 (uso); sem porta de banco na CLI;
   2. dependencias: relatorio ausente/ilegivel/versao errada RECUSA (exit 3); fuso ilegivel RECUSA;
      janela fora da grade RECUSA;
   3. contrato incoerente RECUSA (aprovacao humana desligada, max_toques != passos, intervalo que
      diminui, passo fora de ordem, versao de entrada divergente);
   4. PRE-CONDICAO das duas camadas: sem `previsao_emitida` OU sem `melhor_janela`, PLANO_ABSTIDO
      com fila vazia e `faltando` nomeando a camada — fail-closed;
   5. PLANO_EMITIDO: N organizacoes x 4 passos, canal = o previsto pelo pai (nao remede), janela = a
      do pai (nao remede), `due_at` conferido A MAO (ancora + intervalo deslocado para o dia-alvo),
      fila ordenada por (due_at, canal, organizacao), tudo com aprovacao humana obrigatoria;
   6. determinismo (mesma entrada + referencia => mesmo hash; `gerado_em` fora do hash);
   7. guarda de escrita: statement de escrita plantado numa COPIA do componente e' detectado
      (ESCRITA_NO_CODIGO) — prova de que a guarda reprova, nao so' documenta;
   8. saida sem PII e dashboard HTML auto-contido.

Autoteste por mutacao: cada mutacao e' aplicada numa COPIA do componente, a suite roda contra a copia
e o item NOMEADO tem de reprovar (mutacao que nao derruba o item que a nomeia e' buraco da suite).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

VERSAO = "nutricao-automatica-v1"
CARD = "TRE-W9-E05-T01"
COMPONENTE = "hermes/agentes/analytics/nutricao_automatica.py"
CONTRATO = "hermes/agentes/analytics/nutricao-automatica-v1.json"
CONTRATO_CANAL = "hermes/agentes/analytics/previsao-canal-v1.json"
CONTRATO_HORARIO = "hermes/analytics/melhor-horario-v1.json"
REFERENCIA = "2026-10-03T00:00:00Z"          # sabado 02:00 local (-03:00) -> ancora quinta 08/10 20:00
DUE_ESPERADO = ["2026-10-08T23:00:00Z", "2026-10-15T23:00:00Z", "2026-10-22T23:00:00Z", "2026-11-05T23:00:00Z"]
ORG = {1: "00000001-0000-0000-0000-000000000000", 2: "00000002-0000-0000-0000-000000000000",
       3: "00000003-0000-0000-0000-000000000000"}


def carregar_modulo(raiz: str):
    caminho = os.path.join(raiz, COMPONENTE)
    spec = importlib.util.spec_from_file_location("nutricao_automatica_sob_teste", caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def grade_do_pai(raiz: str) -> dict:
    """Grade e fuso LIDOS do contrato do pai (o fixture nao digita vocabulario)."""
    with open(os.path.join(raiz, CONTRATO_HORARIO), encoding="utf-8") as fh:
        horario = json.load(fh)
    return {"dias": horario["dias"],
            "faixas": [{"nome": f["nome"], "de": f["de"], "ate": f["ate"]} for f in horario["faixas"]],
            "offset_utc": horario["fuso"]["offset_utc"], "fuso": horario["fuso"]["nome"]}


def relatorios_de_fixture(raiz: str) -> tuple[dict, dict]:
    grade = grade_do_pai(raiz)
    horario = {"versao": "melhor-horario-v1", "veredito": "ANALISADO",
               "fuso": {"nome": grade["fuso"], "offset_utc": grade["offset_utc"]},
               "grade": {"dias": grade["dias"], "faixas": grade["faixas"]},
               "melhor_janela": {"dia": "quinta", "faixa": "noite"}, "melhor_janela_motivo": None}
    canal = {"versao": "previsao-canal-v1", "hash_do_relatorio": "b" * 64,
             "pre_condicao_dados_multicanal": {"atendida": True, "canais_com_base": 2, "canais_suficientes": 2,
                                               "organizacoes_com_interacao": 4, "faltando": []},
             "resumo": {"endpoint_principal": "Reunião", "previsao_emitida": True,
                        "organizacoes_com_previsao": 3, "distribuicao_das_previsoes": {"EMAIL": 1, "WHATSAPP": 2}},
             "previsoes": [
                 {"organization_id": ORG[1], "canal_previsto": "WHATSAPP", "amostra_do_canal": 3,
                  "canais_bloqueados": []},
                 {"organization_id": ORG[2], "canal_previsto": "EMAIL", "amostra_do_canal": 4,
                  "canais_bloqueados": [{"canal": "WHATSAPP", "motivo": "opt_out_whatsapp"}]},
                 {"organization_id": ORG[3], "canal_previsto": "WHATSAPP", "amostra_do_canal": 3,
                  "canais_bloqueados": []}]}
    return canal, horario


def recusa_de(funcao, *args, **kwargs):
    """Executa e devolve (motivo, None) em caso de recusa, ou (None, resultado)."""
    try:
        return None, funcao(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - a Recusa do componente tem motivo/codigo
        return str(getattr(exc, "motivo", exc)), None


def rodar_suite(raiz: str) -> list:
    modulo = carregar_modulo(raiz)
    contrato = modulo.carregar_contrato(os.path.join(raiz, CONTRATO))
    rel_canal, rel_horario = relatorios_de_fixture(raiz)
    itens = []

    def item(nome, ok, detalhe=""):
        itens.append((nome, bool(ok), "" if detalhe is None else str(detalhe)))

    agora = datetime.fromisoformat(REFERENCIA.replace("Z", "+00:00"))
    componente = os.path.join(raiz, COMPONENTE)

    def cli(*args):
        return subprocess.run([sys.executable, componente] + list(args), capture_output=True, text=True)

    # 1. guardas de ambiente e CLI
    proc = cli("--ambiente", "dev", "--conferir")
    item("conferir: --conferir valida contrato e guarda de escrita (exit 0)", proc.returncode == 0,
         proc.stdout.strip()[-160:] or proc.stderr[-160:])
    item("conferir: declara os quatro passos e a aprovacao humana obrigatoria",
         "passos=4" in proc.stdout and "aprovacao_humana=True" in proc.stdout, proc.stdout.strip()[:160])
    proc = cli("--ambiente", "prod")
    item("guardas: prod RECUSA por desenho (exit 4)", proc.returncode == 4, proc.stdout.strip()[:120])
    proc = cli("--ambiente", "prod", "--relatorio-canal", "/tmp/nao-existe.json")
    item("guardas: prod RECUSA ANTES de ler relatorio (exit 4)", proc.returncode == 4, proc.stdout.strip()[:120])
    proc = cli("--ambiente", "homolog")
    item("guardas: homolog sem --confirmo RECUSA (exit 2)", proc.returncode == 2, proc.stdout.strip()[:120])
    proc = cli("--ambiente", "dev")
    item("uso: sem --relatorio-* RECUSA (exit 2)", proc.returncode == 2, proc.stdout.strip()[:120])
    proc = cli("--ambiente", "dev", "--porta-banco", "docker exec -i pg-x psql -U a -d b")
    item("uso: NAO existe --porta-banco na CLI (o componente nao abre banco)", proc.returncode == 2,
         proc.stderr.strip()[:120])

    # 2. dependencias
    motivo, _ = recusa_de(modulo.carregar_relatorio, os.path.join(raiz, "nao-existe.json"),
                          "previsao-canal-v1", "canal")
    item("dependencia: relatorio do pai ausente RECUSA", motivo == "RELATORIO_DO_PAI_AUSENTE", motivo)
    temporario = tempfile.mkdtemp(prefix="nutri-dep-")
    errado = os.path.join(temporario, "canal.json")
    with open(errado, "w", encoding="utf-8") as fh:
        json.dump({"versao": "outra-coisa-v9"}, fh)
    motivo, _ = recusa_de(modulo.carregar_relatorio, errado, "previsao-canal-v1", "canal")
    item("dependencia: versao do relatorio diferente da exigida RECUSA", motivo == "DEPENDENCIA_INCOERENTE", motivo)
    motivo, _ = recusa_de(modulo.carregar_contrato_do_pai, os.path.join(raiz, CONTRATO_CANAL), "nome",
                          "previsao-canal-v1", "canal")
    item("dependencia: contrato do pai conferido tambem na rodada normal (campo errado RECUSA)",
         motivo == "DEPENDENCIA_INCOERENTE", motivo)
    horario_ruim = dict(rel_horario)
    horario_ruim["fuso"] = {"nome": "America/Sao_Paulo", "offset_utc": "UTC-3"}
    motivo, _ = recusa_de(modulo.offset_do_pai, horario_ruim)
    item("dependencia: fuso ilegivel no relatorio do horario RECUSA", motivo == "FUSO_INVALIDO", motivo)
    janela_ruim = dict(rel_horario)
    janela_ruim["melhor_janela"] = {"dia": "feriado", "faixa": "noite"}
    motivo, _ = recusa_de(modulo.grade_da_janela, janela_ruim, "feriado", "noite")
    item("dependencia: dia fora da grade do pai RECUSA", motivo == "JANELA_FORA_DA_GRADE", motivo)
    motivo, _ = recusa_de(modulo.grade_da_janela, rel_horario, "quinta", "madrugada_fake")
    item("dependencia: faixa fora da grade do pai RECUSA", motivo == "JANELA_FORA_DA_GRADE", motivo)

    # 3. contrato incoerente
    def contrato_mutado(**alteracoes):
        copia = json.loads(json.dumps(contrato))
        for chave, valor in alteracoes.items():
            if chave == "passos":
                copia["politica"]["passos"] = valor
            elif chave == "max_toques":
                copia["politica"]["max_toques"] = valor
            elif chave == "entradas":
                copia["entradas"]["canal"]["versao_exigida"] = valor
            elif chave == "lacunas":
                copia["lacunas_declaradas"] = valor
            else:
                copia["politica"][chave] = valor
        return copia

    motivo, _ = recusa_de(modulo.validar_contrato, contrato_mutado(exige_aprovacao_humana=False))
    item("contrato: aprovacao humana desligada RECUSA (nurture nao se executa sozinho)",
         motivo == "CONTRATO_INCOERENTE", motivo)
    motivo, _ = recusa_de(modulo.validar_contrato, contrato_mutado(max_toques=9))
    item("contrato: max_toques != numero de passos RECUSA", motivo == "CONTRATO_INCOERENTE", motivo)
    motivo, _ = recusa_de(modulo.validar_contrato, contrato_mutado(passos=[
        {"passo": 1, "intervalo_dias": 0, "rotulo": "a"}, {"passo": 2, "intervalo_dias": 30, "rotulo": "b"},
        {"passo": 3, "intervalo_dias": 10, "rotulo": "c"}, {"passo": 4, "intervalo_dias": 40, "rotulo": "d"}]))
    item("contrato: intervalo que diminui RECUSA", motivo == "CONTRATO_INCOERENTE", motivo)
    motivo, _ = recusa_de(modulo.validar_contrato, contrato_mutado(passos=[
        {"passo": 2, "intervalo_dias": 0, "rotulo": "a"}, {"passo": 2, "intervalo_dias": 4, "rotulo": "b"},
        {"passo": 3, "intervalo_dias": 11, "rotulo": "c"}, {"passo": 4, "intervalo_dias": 25, "rotulo": "d"}]))
    item("contrato: passo fora de ordem RECUSA", motivo == "CONTRATO_INCOERENTE", motivo)
    motivo, _ = recusa_de(modulo.validar_contrato, contrato_mutado(entradas="previsao-canal-v9"))
    item("contrato: versao de entrada diferente do codigo RECUSA", motivo == "CONTRATO_INCOERENTE", motivo)
    motivo, _ = recusa_de(modulo.validar_contrato, contrato_mutado(lacunas=[]))
    item("contrato: sem lacunas declaradas RECUSA (lacuna escondida nao passa)", motivo == "CONTRATO_INCOERENTE", motivo)

    # 4. pre-condicao das duas camadas
    canal_sem_base = dict(rel_canal)
    canal_sem_base["pre_condicao_dados_multicanal"] = {"atendida": False, "faltando": ["minimo_canais_com_base"]}
    plano = modulo.planejar(contrato, canal_sem_base, rel_horario, agora, "dev")
    item("pre-condicao: canal sem base -> PLANO_ABSTIDO com fila vazia e motivo nomeado",
         plano["veredito"] == "PLANO_ABSTIDO" and plano["plano_emitido"] is False and plano["fila"] == []
         and any("pre_condicao_dados_multicanal" in f for f in plano["pre_condicoes"]["faltando"]),
         plano["pre_condicoes"]["faltando"])
    canal_sem_previsao = dict(rel_canal, previsoes=[])
    plano = modulo.planejar(contrato, canal_sem_previsao, rel_horario, agora, "dev")
    item("pre-condicao: sem previsoes -> PLANO_ABSTIDO",
         plano["veredito"] == "PLANO_ABSTIDO" and any("previsoes" in f for f in plano["pre_condicoes"]["faltando"]),
         plano["pre_condicoes"]["faltando"])
    horario_sem_amostra = dict(rel_horario, melhor_janela=None, melhor_janela_motivo="AMOSTRA_INSUFICIENTE")
    plano = modulo.planejar(contrato, rel_canal, horario_sem_amostra, agora, "dev")
    item("pre-condicao: janela sem amostra -> PLANO_ABSTIDO nomeando AMOSTRA_INSUFICIENTE",
         plano["veredito"] == "PLANO_ABSTIDO" and plano["fila"] == []
         and any("AMOSTRA_INSUFICIENTE" in f for f in plano["pre_condicoes"]["faltando"]),
         plano["pre_condicoes"]["faltando"])

    # 5. plano emitido
    plano = modulo.planejar(contrato, rel_canal, rel_horario, agora, "dev")
    item("plano: 3 organizacoes x 4 passos = 12 toques em PLANO_EMITIDO",
         plano["veredito"] == "PLANO_EMITIDO" and plano["plano_emitido"] is True and len(plano["fila"]) == 12
         and len(plano["por_organizacao"]) == 3, (plano["veredito"], len(plano["fila"])))
    por_org = {o["organization_id"]: o for o in plano["por_organizacao"]}
    item("plano: canal de cada toque e' o canal_previsto do pai (nao remede)",
         por_org[ORG[1]]["canal"] == "WHATSAPP" and por_org[ORG[2]]["canal"] == "EMAIL"
         and por_org[ORG[3]]["canal"] == "WHATSAPP"
         and {t["canal"] for t in plano["fila"] if t["organization_id"] == ORG[2]} == {"EMAIL"},
         {k[:8]: v["canal"] for k, v in por_org.items()})
    item("plano: janela de cada toque e' a melhor_janela do pai (nao remede)",
         {t["dia"] for t in plano["fila"]} == {"quinta"} and {t["faixa"] for t in plano["fila"]} == {"noite"}
         and plano["janela"]["fonte"] == "melhor-horario-v1:melhor_janela")
    item("plano: janela declarada com o fuso do pai (offset -03:00 e faixa de inicio 20h)",
         plano["janela"]["offset_utc"] == "-03:00" and plano["janela"]["faixa_de"] == 20, plano["janela"])
    toques_org1 = sorted((t for t in plano["fila"] if t["organization_id"] == ORG[1]), key=lambda t: t["passo"])
    item("cadencia: due_at conferido A MAO (ancora 08/10 + 4/11/25 dias, deslocado para quinta 20:00 local)",
         [t["due_at_utc"] for t in toques_org1] == DUE_ESPERADO,
         [t["due_at_utc"] for t in toques_org1])
    item("cadencia: toque NUNCA cai no passado (due_at_utc > referencia)",
         all(t["due_at_utc"] > REFERENCIA.replace("Z", "Z") for t in plano["fila"])
         and min(t["due_at_utc"] for t in plano["fila"]) >= DUE_ESPERADO[0],
         min(t["due_at_utc"] for t in plano["fila"]))
    item("cadencia: due_at_local e' a hora de inicio da faixa no offset do pai",
         all(t["due_at_local"].endswith("-03:00") and t["due_at_local"][11:19] == "20:00:00" for t in plano["fila"]),
         plano["fila"][0]["due_at_local"])
    item("fila: ordenada por (due_at_utc, canal, organizacao) — determinismo declarado",
         [(t["due_at_utc"], t["canal"], t["organization_id"]) for t in plano["fila"]]
         == sorted((t["due_at_utc"], t["canal"], t["organization_id"]) for t in plano["fila"])
         and [t["canal"] for t in plano["fila"][:3]] == ["EMAIL", "WHATSAPP", "WHATSAPP"],
         [t["canal"] for t in plano["fila"][:3]])
    item("fila: TODO toque exige aprovacao humana e carrega as condicoes de parada do contrato",
         all(t["exige_aprovacao_humana"] is True for t in plano["fila"])
         and all(t["condicoes_de_parada"] == contrato["politica"]["condicoes_de_parada"] for t in plano["fila"])
         and plano["politica"]["nao_envia"] is True,
         plano["politica"]["exige_aprovacao_humana"])
    item("fila: contagem por canal e intervalo entre toques fecham com a politica",
         plano["resumo"]["por_canal"] == {"EMAIL": 4, "WHATSAPP": 8} and plano["resumo"]["toques"] == 12
         and [t["intervalo_dias"] for t in toques_org1] == [0, 4, 11, 25], plano["resumo"]["por_canal"])
    item("lacuna medida: organizacao com canal bloqueado entra na fila pelo canal elegivel e a lacuna e' contada",
         plano["lacunas"]["organizacoes_com_canal_bloqueado"] == 1
         and por_org[ORG[2]]["canais_bloqueados"] == [{"canal": "WHATSAPP", "motivo": "opt_out_whatsapp"}],
         plano["lacunas"])

    # 6. determinismo
    plano2 = modulo.planejar(contrato, rel_canal, rel_horario, agora, "dev")
    item("determinismo: mesma entrada + mesma referencia => mesmo hash_do_plano",
         plano["hash_do_plano"] == plano2["hash_do_plano"] and len(plano["hash_do_plano"]) == 64,
         plano["hash_do_plano"][:16])
    plano3 = modulo.planejar(contrato, rel_canal, rel_horario,
                             datetime.fromisoformat("2026-10-04T00:00:00+00:00"), "dev")
    item("determinismo: referencia diferente muda o hash (o due_at e' entrada do hash)",
         plano3["hash_do_plano"] != plano["hash_do_plano"],
         (plano["hash_do_plano"][:12], plano3["hash_do_plano"][:12]))
    plano4 = dict(plano)
    plano4["gerado_em"] = "1999-01-01T00:00:00Z"
    item("determinismo: gerado_em NAO entra no hash",
         modulo.hash_do_plano(plano4) == plano["hash_do_plano"], None)

    # 7. guarda de escrita (dente do guard: statement plantado numa copia tem de ser detectado)
    copia = os.path.join(tempfile.mkdtemp(prefix="nutri-guarda-"), "componente.py")
    with open(componente, encoding="utf-8") as origem:
        texto = origem.read()
    with open(copia, "w", encoding="utf-8") as destino:
        destino.write(texto + "\nSQL_PLANTADO = 'INSERT INTO sales_intelligence.interactions VALUES (1)'\n")
    motivo, _ = recusa_de(modulo.auditar_proprio_codigo, copia)
    item("guarda de escrita: statement de escrita plantado e' detectado (ESCRITA_NO_CODIGO)",
         motivo == "ESCRITA_NO_CODIGO", motivo)
    motivo, _ = recusa_de(modulo.auditar_proprio_codigo, componente)
    item("guarda de escrita: o componente REAL nao carrega statement de escrita", motivo is None, motivo)

    # 8. saida
    saida = tempfile.mkdtemp(prefix="nutri-saida-")
    caminho_canal = os.path.join(saida, "canal.json")
    caminho_horario = os.path.join(saida, "horario.json")
    with open(caminho_canal, "w", encoding="utf-8") as fh:
        json.dump(rel_canal, fh)
    with open(caminho_horario, "w", encoding="utf-8") as fh:
        json.dump(rel_horario, fh)
    proc = cli("--ambiente", "dev", "--relatorio-canal", caminho_canal, "--relatorio-horario", caminho_horario,
               "--agora", REFERENCIA, "--saida", saida)
    ok_json = os.path.isfile(os.path.join(saida, "nutricao-automatica.json"))
    ok_html = os.path.isfile(os.path.join(saida, "nutricao-automatica.html"))
    item("saida: CLI gera JSON + HTML (exit 0)", proc.returncode == 0 and ok_json and ok_html,
         proc.stdout.strip()[:160])
    if ok_json and ok_html:
        with open(os.path.join(saida, "nutricao-automatica.json"), encoding="utf-8") as fh:
            do_cli = json.load(fh)
        with open(os.path.join(saida, "nutricao-automatica.html"), encoding="utf-8") as fh:
            html = fh.read()
        plano_cli = modulo.planejar(contrato, rel_canal, rel_horario, agora, "dev",
                                    sha_contrato=modulo.sha256_de_arquivo(os.path.join(raiz, CONTRATO)),
                                    sha_canal=modulo.sha256_de_arquivo(caminho_canal),
                                    sha_horario=modulo.sha256_de_arquivo(caminho_horario))
        item("saida: o JSON da CLI bate com o plano derivado em memoria (mesmo hash, mesmo caminho logico)",
             do_cli["hash_do_plano"] == plano_cli["hash_do_plano"] and do_cli["fila"] == plano_cli["fila"]
             and do_cli["resumo"] == plano_cli["resumo"],
             (do_cli["hash_do_plano"][:16], plano_cli["hash_do_plano"][:16]))
        item("saida: sem PII (nenhum e-mail, dominio ou nome; so' UUID, canal, janela e datas)",
             "@" not in json.dumps(do_cli, ensure_ascii=False) and ".test" not in json.dumps(do_cli, ensure_ascii=False))
        item("saida: dashboard HTML auto-contido (sem http/https/script/link)",
             "http://" not in html and "https://" not in html and "<script" not in html and "<link" not in html
             and len(html) > 800 and "Fila de toques" in html and "aprovacao humana" in html)
    else:
        item("saida: o JSON da CLI bate com o plano derivado (mesmo hash)", False, "sem saida")
        item("saida: sem PII (nenhum e-mail, dominio ou nome; so' UUID, canal, janela e datas)", False, "sem saida")
        item("saida: dashboard HTML auto-contido (sem http/https/script/link)", False, "sem saida")

    return itens


def imprimir(itens: list, raiz: str, caminho_json: str | None = None) -> int:
    falhas = [nome for nome, ok, _ in itens if not ok]
    for nome, ok, detalhe in itens:
        print(("OK    " if ok else "FALHOU ") + nome + ("" if ok else " " + detalhe))
    if caminho_json:
        with open(caminho_json, "w", encoding="utf-8") as fh:
            json.dump([{"item": n, "ok": o, "detalhe": d} for n, o, d in itens], fh, ensure_ascii=False, indent=2)
    print("---")
    if falhas:
        print("VERIFICADOR_NUTRICAO_AUTOMATICA_FALHOU (%d itens, %d falhas)" % (len(itens), len(falhas)))
        print("falhas: %s" % "; ".join(falhas))
        return 1
    print("VERIFICADOR_NUTRICAO_AUTOMATICA_PASS (%d itens, 0 falhas)" % len(itens))
    return 0


MUTACOES = [
    ("sem-pre-condicao", '    if not pre.get("atendida"):', "    if False:",
     "pre-condicao: canal sem base -> PLANO_ABSTIDO com fila vazia e motivo nomeado"),
    ("sem-abstencao-de-janela", '    if not rel_horario.get("melhor_janela"):', "    if False:",
     "pre-condicao: janela sem amostra -> PLANO_ABSTIDO nomeando AMOSTRA_INSUFICIENTE"),
    ("sem-deslocamento-para-o-dia-alvo", '    avanco = (dias.index(dia_alvo) - alvo.weekday()) % 7', "    avanco = 0",
     "cadencia: due_at conferido A MAO (ancora 08/10 + 4/11/25 dias, deslocado para quinta 20:00 local)"),
    ("sem-aprovacao-humana", '                "exige_aprovacao_humana": True,',
     '                "exige_aprovacao_humana": False,',
     "fila: TODO toque exige aprovacao humana e carrega as condicoes de parada do contrato"),
    ("ordem-da-fila-trocada", '    fila.sort(key=lambda t: (t["due_at_utc"], t["canal"], t["organization_id"]))',
     '    fila.sort(key=lambda t: (t["due_at_utc"], t["organization_id"], t["canal"]))',
     "fila: ordenada por (due_at_utc, canal, organizacao) — determinismo declarado"),
]


def copiar_raiz(raiz: str, destino: str) -> None:
    for relativo in (COMPONENTE, CONTRATO, CONTRATO_CANAL, CONTRATO_HORARIO):
        alvo = os.path.join(destino, relativo)
        os.makedirs(os.path.dirname(alvo), exist_ok=True)
        shutil.copyfile(os.path.join(raiz, relativo), alvo)


def autoteste(raiz: str) -> int:
    detectadas = 0
    for nome, de, para, item_alvo in MUTACOES:
        destino = tempfile.mkdtemp(prefix="nutri-mut-%s-" % nome)
        copiar_raiz(raiz, destino)
        caminho_componente = os.path.join(destino, COMPONENTE)
        with open(caminho_componente, encoding="utf-8") as fh:
            texto = fh.read()
        if de not in texto:
            print("FALHOU mutacao %s: ancora nao encontrada no componente" % nome)
            continue
        with open(caminho_componente, "w", encoding="utf-8") as fh:
            fh.write(texto.replace(de, para, 1))
        try:
            itens = rodar_suite(destino)
        except Exception as exc:  # noqa: BLE001 - copia mutada pode quebrar de proposito
            print("OK    mutacao %s: a copia mutada falhou ao rodar (%s) — reprova" % (nome, exc))
            detectadas += 1
            continue
        ok_item = dict((nome_item, ok) for nome_item, ok, _ in itens).get(item_alvo)
        if ok_item is False:
            print("OK    mutacao %s: item reprovado '%s'" % (nome, item_alvo))
            detectadas += 1
        else:
            print("FALHOU mutacao %s: o item '%s' NAO reprovou (buraco da suite)" % (nome, item_alvo))
    print("---")
    if detectadas == len(MUTACOES):
        print("AUTOTESTE OK (%d/%d mutacoes detectadas)" % (detectadas, len(MUTACOES)))
        return 0
    print("AUTOTESTE FALHOU (%d/%d mutacoes detectadas)" % (detectadas, len(MUTACOES)))
    return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Suite offline do %s (%s)" % (VERSAO, CARD))
    parser.add_argument("--raiz", default=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
    parser.add_argument("--autoteste", action="store_true", help="muta copias do componente e exige o item nomeado reprovando")
    parser.add_argument("--json", dest="caminho_json", default=None, help="grava o resultado dos itens neste arquivo")
    args = parser.parse_args(argv)
    raiz = os.path.abspath(args.raiz)
    if args.autoteste:
        codigo_suite = imprimir(rodar_suite(raiz), raiz, args.caminho_json)
        return autoteste(raiz) or codigo_suite
    return imprimir(rodar_suite(raiz), raiz, args.caminho_json)


if __name__ == "__main__":
    sys.exit(main())
