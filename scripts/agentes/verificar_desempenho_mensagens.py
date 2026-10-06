#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verificador OFFLINE do desempenho de mensagens — `desempenho-mensagens-v1` (card TRE-W8-E04-T01).

Mede o componente `hermes/analytics/desempenho_mensagens.py` SEM PostgreSQL: a porta de banco e o duble
`scripts/agentes/duble_psql_desempenho.py`, que responde a UNICA consulta de leitura aplicando o mesmo
WHERE. O que se mede aqui: normalizacao de canal, criterio de credito (mais proximo anterior), classes de
resposta contra o vocabulario do card irmao, bordas da janela, taxas, ranking por amostra, guardas
(prod recusado, somente leitura, fail-closed do contrato) e nao-exposicao de PII.

NAO se mede aqui: o PostgreSQL de verdade. Isso e o aceite E2E
(`scripts/agentes/teste_desempenho_mensagens_aceite.sh`, container descartavel na VPS).

Uso:
  python3 scripts/agentes/verificar_desempenho_mensagens.py                 # suite
  python3 scripts/agentes/verificar_desempenho_mensagens.py --autoteste     # suite + mutacoes (dentes)
  python3 scripts/agentes/verificar_desempenho_mensagens.py --raiz <dir> [--codigo <modulo.py>]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CONTRATO_REL = "hermes/analytics/desempenho-mensagens-v1.json"
MODULO_REL = "hermes/analytics/desempenho_mensagens.py"
DUBLE_REL = "scripts/agentes/duble_psql_desempenho.py"
IRMAO_REL = "hermes/agentes/respostas/ingestao-respostas-v1.json"

ORG_A, ORG_B, ORG_C, ORG_D, ORG_E, ORG_F, ORG_G, ORG_H, ORG_Y, ORG_I = (
    "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222",
    "33333333-3333-4333-8333-333333333333", "44444444-4444-4444-8444-444444444444",
    "55555555-5555-4555-8555-555555555555", "66666666-6666-4666-8666-666666666666",
    "77777777-7777-4777-8777-777777777777", "88888888-8888-4888-8888-888888888888",
    "99999999-9999-4999-8999-999999999999", "aaaa1111-1111-4111-8111-111111111111")
CONTA_KA, CONTA_KB, CONTA_KZ = ("aaaaaaaa-0000-4000-8000-000000000001",
                                "bbbbbbbb-0000-4000-8000-000000000002",
                                "cccccccc-0000-4000-8000-000000000003")

DESDE = "2026-09-01T00:00:00Z"
ATE = "2026-09-10T00:00:00Z"
ARGS_RECORTE = ["--desde", DESDE, "--ate", ATE]

ITENS: list[tuple[str, bool]] = []


def item(nome: str, esperado, obtido) -> None:
    ok = esperado == obtido
    ITENS.append((nome, ok))
    print(("OK     " if ok else "FALHOU ") + f"{nome} (esperado={esperado!r} obtido={obtido!r})")


def envio(id_, org, contato, canal, quando, ref, tipo="OUTBOUND_EMAIL"):
    return {"id": id_, "organization_id": org, "contact_id": contato, "channel": canal,
            "direction": "OUTBOUND", "interaction_type": tipo, "occurred_at": quando,
            "content_reference": ref, "response_category": ""}


def resposta(id_, org, contato, canal, quando, categoria):
    return {"id": id_, "organization_id": org, "contact_id": contato, "channel": canal,
            "direction": "INBOUND", "interaction_type": "EMAIL_RESPOSTA", "occurred_at": quando,
            "content_reference": "UIDVALIDITY:99", "response_category": categoria}


def fixture() -> list[dict]:
    """Corpus declarado. Cada linha existe para medir UMA coisa nomeada na bateria."""
    linhas: list[dict] = []
    # H1 (ORG A): 5 envios; 2 respostas INTERESSE — uma do contato do envio, outra de um envio SEM contato.
    for n, hora in enumerate(("10:00", "10:30", "11:00", "11:30", "12:00"), start=1):
        contato = CONTA_KA if n == 1 else ""
        linhas.append(envio(f"e{n}", ORG_A, contato, "EMAIL", f"2026-09-01T{hora}:00Z",
                            f"envio:ped{n}:H1"))
    linhas.append(resposta("r1", ORG_A, CONTA_KA, "email", "2026-09-01T13:00:00Z", "INTERESSE"))
    linhas.append(resposta("r2", ORG_A, "", "email", "2026-09-01T13:30:00Z", "INTERESSE"))
    # H2 (ORG B): 5 envios; 1 INTERESSE + 1 AUTO_RESPOSTA (descartada, NAO conta como resposta).
    for n, hora in enumerate(("10:00", "10:30", "11:00", "11:30", "12:00"), start=6):
        linhas.append(envio(f"e{n}", ORG_B, CONTA_KB, "EMAIL", f"2026-09-02T{hora}:00Z",
                            f"envio:ped{n}:H2"))
    linhas.append(resposta("r3", ORG_B, CONTA_KB, "email", "2026-09-02T13:00:00Z", "INTERESSE"))
    linhas.append(resposta("r4", ORG_B, CONTA_KB, "email", "2026-09-02T14:00:00Z", "AUTO_RESPOSTA"))
    # H3 (ORG D): 2 envios antes da MESMA resposta -> credito a UM so (o mais proximo anterior).
    linhas.append(envio("e11", ORG_D, "", "EMAIL", "2026-09-03T10:00:00Z", "envio:ped11:H3"))
    linhas.append(envio("e12", ORG_D, "", "EMAIL", "2026-09-03T11:00:00Z", "envio:ped12:H3"))
    linhas.append(resposta("r5", ORG_D, "", "email", "2026-09-03T12:00:00Z", "INTERESSE"))
    # H4/H5 (ORG E/F, WHATSAPP): borda da janela — exatamente no limite CONTA, 1s depois NAO.
    linhas.append(envio("e13", ORG_E, "", "WHATSAPP", "2026-09-04T00:00:00Z", "envio:ped13:H4",
                        "OUTBOUND_WHATSAPP"))
    linhas.append(resposta("r6", ORG_E, "", "whatsapp", "2026-09-04T12:00:00Z", "INTERESSE"))
    linhas.append(envio("e14", ORG_F, "", "WHATSAPP", "2026-09-05T00:00:00Z", "envio:ped14:H5",
                        "OUTBOUND_WHATSAPP"))
    linhas.append(resposta("r7", ORG_F, "", "whatsapp", "2026-09-05T12:00:01Z", "INTERESSE"))
    # H6 (ORG G): envio com contato divergente NAO credita a resposta de outro contato.
    linhas.append(envio("e17", ORG_H, CONTA_KB, "EMAIL", "2026-09-07T10:00:00Z", "envio:ped17:H7"))
    linhas.append(resposta("r9", ORG_H, CONTA_KZ, "email", "2026-09-07T11:00:00Z", "INTERESSE"))
    linhas.append(envio("e15", ORG_G, "", "EMAIL", "2026-09-06T10:00:00Z", "envio:ped15:H6"))
    linhas.append(envio("e16", ORG_G, CONTA_KB, "EMAIL", "2026-09-06T11:00:00Z", "envio:ped16:H6"))
    linhas.append(resposta("r8", ORG_G, CONTA_KB, "email", "2026-09-06T12:00:00Z", "SEM_INTERESSE"))
    # Excluidas (cada uma cai num balde proprio, nunca em silencio).
    linhas.append(envio("x1", ORG_A, "", "EMAIL", "2026-09-01T09:00:00Z", ""))            # sem `envio:`
    linhas.append(envio("x6", ORG_A, "", "EMAIL", "2026-09-01T09:10:00Z", "envio:quebrada"))  # malformada
    linhas.append(resposta("x2", ORG_Y, "", "email", "2026-09-08T10:00:00Z", ""))          # sem categoria
    linhas.append(resposta("x3", ORG_Y, "", "email", "2026-09-08T11:00:00Z", "NAO_RESPOSTA"))
    linhas.append(envio("e19", ORG_Y, "", "EMAIL", "2026-09-20T10:00:00Z", "envio:ped19:H9"))  # depois de --ate
    # H8 (ORG C): 5 envios; 1 OPT_OUT — opt-out tem taxa PROPRIA e nunca conta como interesse.
    for n, hora in enumerate(("10:00", "11:00", "12:00", "13:00", "14:00"), start=20):
        linhas.append(envio(f"e{n}", ORG_C, "", "EMAIL", f"2026-09-09T{hora}:00Z",
                            f"envio:ped{n}:H8"))
    linhas.append(resposta("r10", ORG_C, "", "email", "2026-09-09T15:00:00Z", "OPT_OUT"))
    # H10 (ORG I): 5 envios; a UNICA resposta e uma AUTO_RESPOSTA -> nenhum envio foi respondido por gente.
    for n, hora in enumerate(("10:00", "11:00", "12:00", "13:00", "14:00"), start=30):
        linhas.append(envio(f"e{n}", ORG_I, "", "EMAIL", f"2026-09-09T{hora}:00Z",
                            f"envio:ped{n}:H10"))
    linhas.append(resposta("r11", ORG_I, "", "email", "2026-09-09T15:30:00Z", "AUTO_RESPOSTA"))
    return linhas


def escrever_estado(caminho: Path, linhas: list[dict]) -> Path:
    caminho.write_text(json.dumps({"interacoes": linhas}, ensure_ascii=False), encoding="utf-8")
    return caminho


def carregar_modulo(caminho: Path, nome: str):
    spec = importlib.util.spec_from_file_location(nome, caminho)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nome] = modulo
    spec.loader.exec_module(modulo)
    return modulo


def executar(raiz: Path, modulo: Path, estado: Path, *args: str):
    porta = f"{sys.executable} {raiz / DUBLE_REL} {estado}"
    return subprocess.run([sys.executable, str(modulo), "--raiz", str(raiz), "--ambiente", "dev",
                           "--prefixo", porta, *args], capture_output=True, text=True, timeout=120)


def relatorio(raiz: Path, modulo: Path, estado: Path, *args: str):
    proc = executar(raiz, modulo, estado, *args)
    try:
        return json.loads(proc.stdout), proc
    except json.JSONDecodeError:
        return {"_erro": "rc=%s out=%s err=%s" % (proc.returncode, proc.stdout[:300], proc.stderr[-1200:])}, proc


def por_variante(rel: dict) -> dict:
    return {v["texto_hash"]: v for v in rel.get("por_variante", [])}


def json_ou_vazio(texto: str) -> dict:
    try:
        return json.loads(texto)
    except (json.JSONDecodeError, TypeError):
        return {}


def balde(rel: dict, nome: str):
    return rel.get("excluidas_do_recorte", {}).get(nome)


def medir(raiz: Path, modulo: Path, trabalho: Path) -> None:
    """Bateria offline: cada item mede UMA afirmacao nomeada."""
    linhas = fixture()
    estado = escrever_estado(trabalho / "estado.json", linhas)

    # ---- RUN-A: recorte declarado, janela 14 dias, limite de amostra 5
    rel, proc = relatorio(raiz, modulo, estado, *ARGS_RECORTE, "--janela-dias", "14", "--limite-amostra", "5")
    v = por_variante(rel)
    item("A0a o componente respondeu JSON (diagnostico se falhou)", "", rel.get("_erro", ""))
    item("A0 (pre-condicao) veredito ANALISADO", "ANALISADO", rel.get("veredito"))
    item("A1 exit 0 na leitura", 0, proc.returncode)
    pergunta = lambda variante, campo: v.get(variante, {}).get(campo)  # noqa: E731
    item("V1 canal `EMAIL` (envio) casa com `email` (resposta) — normalizacao",
         2, pergunta("H1", "respondidas"))
    item("V4 dois envios antes da mesma resposta: credito a UM so (mais proximo anterior)",
         1, pergunta("H3", "respondidas"))
    item("V4b resposta creditada uma unica vez (contagem de resposta, nao de envio)",
         1, pergunta("H3", "respostas_comerciais"))
    item("V4c o credito foi do envio mais proximo (segundo envio), nao do primeiro",
         1, pergunta("H3", "positivas"))
    item("V3 envio cuja UNICA resposta e AUTO_RESPOSTA nao conta como respondido",
         0, pergunta("H10", "respondidas"))
    item("V3b AUTO_RESPOSTA e medida e reportada como descartada (nao somada como resposta)",
         1, pergunta("H2", "respostas_descartadas"))
    item("V3c AUTO_RESPOSTA nunca vira interesse",
         0.0, pergunta("H10", "taxa_de_interesse"))
    item("V5 resposta de contato DIVERGENTE nao credita envio com contato declarado",
         0, pergunta("H7", "respondidas"))
    item("V5b resposta do mesmo contato credita (ORG G)",
         1, pergunta("H6", "respondidas"))
    item("V15 outbound sem `envio:` nao e contado como enviada",
         5, pergunta("H1", "enviadas"))
    item("V16 referencia `envio:` malformada e excluida (nao vira variante)",
         1, balde(rel, "referencia_malformada"))
    item("V16b balde: inbound sem categoria (lead de formulario, nao resposta)",
         1, balde(rel, "inbound_sem_categoria"))
    item("V16c balde: categoria de resposta fora do vocabulario validado",
         1, balde(rel, "inbound_categoria_fora_do_vocabulario"))
    item("V17 envio DEPOIS do fim do recorte nao entra no recorte",
         1, balde(rel, "fora_do_recorte_de_envio"))
    item("V14 ranking: melhor variante e a de maior taxa de interesse com amostra",
         "H1", (rel.get("melhor_variante") or {}).get("texto_hash"))
    item("V14b motivo nulo quando ha vencedor", None, rel.get("melhor_variante_motivo"))
    item("V14c taxa de interesse da melhor variante (2 de 5)",
         0.4, pergunta("H1", "taxa_de_interesse"))
    item("V14d taxa de resposta da H1 (2 de 5)", 0.4, pergunta("H1", "taxa_de_resposta"))
    item("V14e tempo medio de resposta da H1 em horas (3h e 1.5h -> 2.25)",
         2.25, pergunta("H1", "tempo_medio_de_resposta_horas"))
    item("V14f mediana do tempo de resposta da H1 (3h e 1.5h -> 2.25)",
         2.25, pergunta("H1", "tempo_mediano_de_resposta_horas"))
    item("V19 amostra suficiente e declarada por variante",
         [False, True, True], [pergunta("H3", "amostra_suficiente"), pergunta("H1", "amostra_suficiente"),
                               pergunta("H2", "amostra_suficiente")])
    item("V20 opt-out entra em taxa propria",
         0.2, pergunta("H8", "taxa_de_opt_out"))
    item("V20b opt-out NAO vira interesse (a variante do descadastro nao tem nenhum)",
         0.0, pergunta("H8", "taxa_de_interesse"))
    item("V20c a variante com opt-out conta a resposta como negativa",
         1, pergunta("H8", "respondidas"))

    # ---- RUN-B: janela de 12h exatas -> limite INCLUSIVO
    rel_b, _ = relatorio(raiz, modulo, estado, *ARGS_RECORTE, "--janela-dias", "0.5", "--limite-amostra", "1")
    vb = por_variante(rel_b)
    item("V2 resposta EXATAMENTE no limite da janela CONTA",
         1, vb.get("H4", {}).get("respondidas"))
    item("V2b resposta 1s depois do limite NAO conta",
         0, vb.get("H5", {}).get("respondidas"))

    # ---- RUN-C: limite de amostra acima de todo mundo
    rel_c, _ = relatorio(raiz, modulo, estado, *ARGS_RECORTE, "--limite-amostra", "100")
    item("V13 sem amostra suficiente nao se elege vencedor",
         None, rel_c.get("melhor_variante"))
    item("V13b motivo declarado quando nao se elege vencedor",
         "AMOSTRA_INSUFICIENTE", rel_c.get("melhor_variante_motivo"))

    # ---- V6 PII: a saida e agregada (nenhum id de organizacao/contato/approval na saida)
    texto = json.dumps(rel, ensure_ascii=False)
    vazados = [ident for ident in (ORG_A, ORG_B, ORG_D, ORG_H, CONTA_KA, CONTA_KB, CONTA_KZ,
                                   "ped1", "ped12") if ident in texto]
    item("V6 a saida nao carrega organization_id/contact_id/approval_id", [], vazados)
    item("V6b a saida carrega o hash do TEXTO (identidade da variante)", True, "H1" in texto)

    # ---- V10 determinismo
    rel_d, _ = relatorio(raiz, modulo, estado, *ARGS_RECORTE, "--janela-dias", "14", "--limite-amostra", "5")
    item("V10 duas leituras iguais com o mesmo recorte (sem carimbo)", True, rel == rel_d)
    proc_carimbo = executar(raiz, modulo, estado, *ARGS_RECORTE, "--com-carimbo")
    item("V10b --com-carimbo inclui gerado_em", True, "gerado_em" in proc_carimbo.stdout)

    # ---- V9 somente leitura: o duble anotou as chamadas e nenhuma foi recusada
    chamadas = estado.with_suffix(".json.chamadas.jsonl")
    anotacoes = ([json.loads(l) for l in chamadas.read_text().splitlines() if l.strip()]
                 if chamadas.exists() else [])
    item("V9 todas as chamadas ao banco foram SELECT", 0, sum(1 for a in anotacoes if "recusado" in a))
    item("V9b o SQL nunca trouxe verbo de escrita", True,
         bool(anotacoes) and all("recusado" not in a for a in anotacoes))

    # ---- V8 guarda de escrita no proprio componente
    modulo_obj = carregar_modulo(modulo, "desempenho_medido")
    recusou = False
    try:
        modulo_obj.afirmar_somente_leitura("UPDATE sales_intelligence.interactions SET subject='x'")
    except Exception as exc:  # noqa: BLE001
        recusou = getattr(exc, "motivo", "") == "ESCRITA_RECUSADA"
    item("V8 SQL de escrita RECUSA (ESCRITA_RECUSADA)", True, recusou)

    # ---- V7 prod recusado
    proc_prod = subprocess.run([sys.executable, str(modulo), "--raiz", str(raiz), "--ambiente", "prod",
                                "--prefixo", "x"], capture_output=True, text=True)
    item("V7 ambiente prod RECUSADO (ADR-005)", 4, proc_prod.returncode)
    item("V7b motivo declarado no prod", "PROD_RECUSADO", json_ou_vazio(proc_prod.stdout).get("motivo"))

    # ---- V18 porta ausente
    proc_porta = subprocess.run([sys.executable, str(modulo), "--raiz", str(raiz), "--ambiente", "dev",
                                 "--prefixo", "/nao/existe/psql", *ARGS_RECORTE],
                                capture_output=True, text=True)
    item("V18 porta de banco ausente RECUSA exit 2", 2, proc_porta.returncode)
    item("V18b motivo declarado na porta ausente", "PORTA_DE_BANCO_AUSENTE",
         json_ou_vazio(proc_porta.stdout).get("motivo"))

    # ---- V21/V15b baldes de defesa (porta que ignora o WHERE): exercitados na unidade, nao pelo duble
    contrato = modulo_obj.carregar_contrato(raiz, raiz / CONTRATO_REL, raiz / IRMAO_REL)
    from datetime import datetime, timedelta, timezone  # noqa: PLC0415

    desde_dt = datetime(2026, 9, 1, tzinfo=timezone.utc)
    ate_dt = datetime(2026, 9, 10, tzinfo=timezone.utc)
    janela = timedelta(days=14)
    fora_do_recorte = [resposta("r99", ORG_A, "", "email", "2020-01-01T00:00:00Z", "INTERESSE"),
                       envio("x99", ORG_A, "", "EMAIL", "2026-09-02T00:00:00Z", "")]
    dados = modulo_obj.separar(fora_do_recorte, desde_dt, ate_dt, janela,
                               contrato["_classe_de"], set(contrato["_vocabulario"]))
    item("V21 resposta fora do recorte e contada, nao casada em silencio",
         1, dados["excluidas"]["resposta_fora_do_recorte"])
    item("V15b outbound sem referencia de envio e contado como excluida (defesa contra porta que nao filtra)",
         1, dados["excluidas"]["outbound_sem_referencia_de_envio"])

    # ---- V11 contrato incoerente (vocabulario do irmao sem uma categoria) -> fail-closed
    irmao = json.loads((raiz / IRMAO_REL).read_text(encoding="utf-8"))
    irmao["vocabulario"]["response_category"] = [c for c in irmao["vocabulario"]["response_category"]
                                                 if c != "OPT_OUT"]
    contrato_incoerente = trabalho / "irmao-incoerente.json"
    contrato_incoerente.write_text(json.dumps(irmao, ensure_ascii=False), encoding="utf-8")
    proc_inc = executar(raiz, modulo, estado, *ARGS_RECORTE, "--contrato-irmao", str(contrato_incoerente))
    item("V11 vocabulario do irmao sem uma categoria -> CONTRATO_INCOERENTE exit 3",
         3, proc_inc.returncode)
    item("V11b motivo declarado na incoerencia", "CONTRATO_INCOERENTE",
         json_ou_vazio(proc_inc.stdout).get("motivo"))

    # ---- V12 --regras
    proc_regras = subprocess.run([sys.executable, str(modulo), "--raiz", str(raiz), "--ambiente", "dev",
                                  "--regras"], capture_output=True, text=True)
    regras = json_ou_vazio(proc_regras.stdout)
    item("V12 --regras traz o criterio de atribuicao",
         True, "mais PROXIMO ANTERIOR" in regras.get("criterio_de_atribuicao", {}).get("regra", ""))
    item("V12b --regras traz as quatro classes de resposta",
         ["NAO_E_RESPOSTA_DE_LEAD", "RESPOSTA_INDEFINIDA", "RESPOSTA_NEGATIVA", "RESPOSTA_POSITIVA"],
         sorted(regras.get("classes_de_resposta", {})))


# Cada dente tem de REPROVAR o item que nomeia (mutacao aplicada em COPIA temporaria).
MUTACOES = [
    ("sem-normalizacao-de-canal", MODULO_REL,
     [("return (canal or \"\").strip().upper()", "return (canal or \"\").strip()")],
     "V1 canal `EMAIL` (envio) casa com `email` (resposta) — normalizacao"),
    ("janela-exclusiva-no-limite", MODULO_REL,
     [("if resposta[\"momento\"] - envio[\"momento\"] > janela:", "if resposta[\"momento\"] - envio[\"momento\"] >= janela:")],
     "V2 resposta EXATAMENTE no limite da janela CONTA"),
    ("credito-a-todos-os-envios", MODULO_REL,
     [("        if resposta[\"contact_id\"]:\n"
       "            especificos = [e for e in candidatos if e[\"contact_id\"] == resposta[\"contact_id\"]]\n"
       "            escolhido = _escolher(especificos, resposta, janela)\n"
       "        if escolhido is None:\n"
       "            escopo_da_organizacao = [e for e in candidatos if not e[\"contact_id\"]]\n"
       "            escolhido = _escolher(escopo_da_organizacao, resposta, janela)",
       "        for envio in candidatos:\n"
       "            if (envio[\"momento\"] < resposta[\"momento\"]\n"
       "                    and resposta[\"momento\"] - envio[\"momento\"] <= janela\n"
       "                    and (not envio[\"contact_id\"] or envio[\"contact_id\"] == resposta[\"contact_id\"])):\n"
       "                creditos.setdefault(envio[\"id\"], []).append(resposta)")],
     "V4 dois envios antes da mesma resposta: credito a UM so (mais proximo anterior)"),
    ("auto-resposta-conta-como-resposta", CONTRATO_REL,
     [("\"RESPOSTA_INDEFINIDA\": [\"INDEFINIDO\"],", "\"RESPOSTA_INDEFINIDA\": [\"INDEFINIDO\", \"AUTO_RESPOSTA\"],"),
      ("\"NAO_E_RESPOSTA_DE_LEAD\": [\"AUTO_RESPOSTA\", \"BOUNCE\", \"RUIDO\"]",
       "\"NAO_E_RESPOSTA_DE_LEAD\": [\"BOUNCE\", \"RUIDO\"]")],
     "V3 envio cuja UNICA resposta e AUTO_RESPOSTA nao conta como respondido"),
    ("amostra-desligada", MODULO_REL,
     [("\"amostra_suficiente\": enviadas >= limite_amostra", "\"amostra_suficiente\": enviadas >= 0")],
     "V13 sem amostra suficiente nao se elege vencedor"),
    ("sem-guarda-de-prod", MODULO_REL,
     [("if args.ambiente == \"prod\":", "if False:")],
     "V7 ambiente prod RECUSADO (ADR-005)"),
    ("referencia-malformada-aceita", MODULO_REL,
     [("if len(partes) != 3 or partes[0] != \"envio\" or not all(partes):\n        return None\n    return partes[1], partes[2]",
       "if len(partes) < 2 or partes[0] != \"envio\" or not all(partes):\n        return None\n    return partes[1], (partes[2] if len(partes) > 2 else \"\")")],
     "V16 referencia `envio:` malformada e excluida (nao vira variante)"),
]


def preparar_arvore(destino: Path, raiz: Path) -> None:
    (destino / "hermes/analytics").mkdir(parents=True, exist_ok=True)
    (destino / "hermes/agentes/respostas").mkdir(parents=True, exist_ok=True)
    (destino / "scripts/agentes").mkdir(parents=True, exist_ok=True)
    shutil.copy2(raiz / CONTRATO_REL, destino / CONTRATO_REL)
    shutil.copy2(raiz / MODULO_REL, destino / MODULO_REL)
    shutil.copy2(raiz / IRMAO_REL, destino / IRMAO_REL)
    shutil.copy2(raiz / DUBLE_REL, destino / DUBLE_REL)


def autoteste(raiz: Path, modulo: Path, trabalho_base: Path) -> int:
    print("== autoteste por mutacao (cada mutacao tem de reprovar O ITEM QUE NOMEIA) ==")
    falhas = 0
    for nome, arquivo_rel, trocas, item_esperado in MUTACOES:
        destino = trabalho_base / f"mut-{nome}"
        if destino.exists():
            shutil.rmtree(destino)
        preparar_arvore(destino, raiz)
        copia = destino / arquivo_rel
        if arquivo_rel == MODULO_REL:
            shutil.copy2(modulo, copia)
        texto = copia.read_text(encoding="utf-8")
        for de, para in trocas:
            if de not in texto:
                print(f"ABORTA mutacao {nome}: ancora nao encontrada ({de[:60]!r}) — mutacao que nao aplica e buraco")
                return 1
            texto = texto.replace(de, para, 1)
        copia.write_text(texto, encoding="utf-8")
        trabalho = destino / "trabalho"
        trabalho.mkdir(parents=True, exist_ok=True)
        ITENS.clear()
        medir(destino, destino / MODULO_REL, trabalho)
        estado_do_item = {n: ok for n, ok in ITENS}
        if item_esperado not in estado_do_item:
            print(f"ABORTA mutacao {nome}: o item {item_esperado!r} nem existe na bateria")
            return 1
        if estado_do_item[item_esperado]:
            print(f"FALHOU dente {nome}: mutou e o item {item_esperado!r} continuou VERDE")
            falhas += 1
        else:
            print(f"OK     dente {nome} -> reprovou {item_esperado!r}")
    return falhas


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raiz", default=None)
    parser.add_argument("--codigo", default=None)
    parser.add_argument("--autoteste", action="store_true")
    args = parser.parse_args(argv)
    raiz = Path(args.raiz).resolve() if args.raiz else Path(__file__).resolve().parents[2]
    modulo = Path(args.codigo).resolve() if args.codigo else raiz / MODULO_REL
    trabalho = Path(tempfile.mkdtemp(prefix="desempenho-"))
    ITENS.clear()
    medir(raiz, modulo, trabalho)
    falhas = sum(1 for _, ok in ITENS if not ok)
    if args.autoteste:
        trabalho_base = Path(tempfile.mkdtemp(prefix="desempenho-dentes-"))
        falhas += autoteste(raiz, modulo, trabalho_base)
    if falhas:
        print(f"\nFALHOU (desempenho de mensagens v1: {len(ITENS)} itens, {falhas} falhas)")
        return 1
    print(f"\nPASS (desempenho de mensagens v1: {len(ITENS)} itens, 0 falhas)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
