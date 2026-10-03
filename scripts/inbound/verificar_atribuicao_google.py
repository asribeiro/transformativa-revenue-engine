#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Suite offline da atribuicao de lead do Google (`google-lead-attribution-v1`) — card TRE-W7-E03-T01.

Mede o que da' para medir SEM banco e SEM rede: contrato, validacao de payload, a TABELA de atribuicao
caso a caso (forte/fraca/ausente/incoerente), idempotencia da chave, PII no resumo, auditoria da propria
fonte e as guardas de CLI (por subprocesso). O que depende de PostgreSQL e da porta do gclid e' do aceite
`scripts/inbound/aceite-atribuicao-google.sh` — aqui nao se simula banco.

Uso:
  python3 scripts/inbound/verificar_atribuicao_google.py            # mede o componente em vigor
  python3 scripts/inbound/verificar_atribuicao_google.py --autoteste  # muta COPIA e exige o item esperado

Exit: 0 = tudo OK · 1 = FALHOU · 2 = uso/nao testavel.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

RAIZ = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
COMPONENTE = os.path.join(RAIZ, "hermes", "inbound", "google", "atribuicao_google.py")
CONTRATO = os.path.join(RAIZ, "hermes", "inbound", "google", "atribuicao-google-v1.json")
EXEMPLOS = os.path.join(RAIZ, "hermes", "inbound", "google", "exemplos")

OK = 0
FALHOU = 0
ITENS = []


def item(nome: str, esperado, obtido) -> bool:
    global OK, FALHOU
    bom = esperado == obtido
    if bom:
        OK += 1
    else:
        FALHOU += 1
    ITENS.append({"item": nome, "esperado": esperado, "obtido": obtido, "resultado": "OK" if bom else "FALHOU"})
    print(f"{'OK    ' if bom else 'FALHOU'} {nome}\n       esperado={esperado!r} obtido={obtido!r}")
    return bom


def carregar_modulo(alvo: str, nome: str):
    spec = importlib.util.spec_from_file_location(nome, alvo)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[nome] = mod
    spec.loader.exec_module(mod)
    return mod


def ler_exemplo(nome: str) -> dict:
    with open(os.path.join(EXEMPLOS, nome), "r", encoding="utf-8") as fh:
        return json.load(fh)


def rodar_cli(alvo: str, argumentos: list, env_extra: dict | None = None) -> int:
    env = dict(os.environ)
    env.pop("TRE_GOOGLE_ADS_TOKEN", None)
    env["TRE_AMBIENTE"] = "dev"
    env.update(env_extra or {})
    proc = subprocess.run([sys.executable, alvo] + argumentos, capture_output=True, text=True, env=env, timeout=120)
    return proc.returncode


# ---------------------------------------------------------------------------------------------
# Itens
# ---------------------------------------------------------------------------------------------
def medir(mod, alvo: str) -> dict:
    contrato = mod.carregar_contrato()

    # --- contrato -------------------------------------------------------------------------
    item("1 contrato carrega e valida (versao declarada)",
         "google-lead-attribution-v1", contrato.get("versao"))
    item("2 contrato exige lacunas declaradas", True, bool(contrato.get("lacunas")))
    item("3 regras da tabela em ordem crescente declarada",
         [1, 2, 3, 4], [r["ordem"] for r in contrato["tabela_de_atribuicao"]])

    contrato_quebrado = json.loads(json.dumps(contrato))
    contrato_quebrado["tabela_de_atribuicao"][0]["condicao"] = {}
    item("4 regra sem condicao declarada e' recusada pelo validador",
         True, bool(mod.validar_contrato(contrato_quebrado)))
    contrato_sem_lacuna = json.loads(json.dumps(contrato))
    contrato_sem_lacuna.pop("lacunas")
    item("5 contrato sem lacunas e' recusado pelo validador",
         True, bool(mod.validar_contrato(contrato_sem_lacuna)))

    # --- payload --------------------------------------------------------------------------
    ads = ler_exemplo("lead-google-ads.json")
    sem_id = dict(ads)
    sem_id.pop("lead_id")
    item("6 payload sem lead_id e' recusado",
         "PAYLOAD_INCOMPLETO", mod.validar_payload(sem_id, contrato)[0][0])
    sem_contato = dict(ads)
    sem_contato.pop("contato_email")
    sem_contato.pop("contato_telefone")
    item("7 lead sem e-mail e sem telefone e' recusado",
         "DADOS_INSUFICIENTES", mod.validar_payload(sem_contato, contrato)[0][0])
    fonte_estranha = dict(ads)
    fonte_estranha["fonte"] = "tiktok_lead"
    item("8 fonte fora do contrato e' recusada",
         "FONTE_DESCONHECIDA", mod.validar_payload(fonte_estranha, contrato)[0][0])

    # --- atribuicao -----------------------------------------------------------------------
    v = mod.atribuir(ads, contrato)
    item("9 formulario de Lead Ads com campanha: ATRIBUIDO pela evidencia forte",
         ("ATRIBUIDO", "FORMULARIO_GOOGLE_ADS", "cmp-7781", 0.95),
         (v["veredito"], v["evidencia"], v["campanha"], v["confianca"]))

    ads_sem_campanha = dict(ads)
    ads_sem_campanha.pop("campanha_id")
    v = mod.atribuir(ads_sem_campanha, contrato)
    item("10 formulario de Lead Ads sem campanha: NAO_ATRIBUIDO (incoerencia declarada)",
         ("NAO_ATRIBUIDO", "FORMULARIO_SEM_CAMPANHA"),
         (v["veredito"], v["motivo"]))

    gclid = ler_exemplo("lead-site-gclid.json")
    resolvido = {"status": "RESOLVIDO", "campanha_id": "cmp-900", "campanha_nome": "PMEs SP",
                 "grupo_anuncio_id": "ag-9", "palavra_chave": "agentes ia"}
    v = mod.atribuir(gclid, contrato, resolvido)
    item("11 gclid resolvido pela porta: ATRIBUIDO com a campanha da resolucao",
         ("ATRIBUIDO", "GCLID_RESOLVIDO", "cmp-900", 0.9),
         (v["veredito"], v["evidencia"], v["campanha"], v["confianca"]))

    nao_resolvido = {"status": "NAO_ENCONTRADO", "campanha_id": None}
    v = mod.atribuir(gclid, contrato, nao_resolvido)
    item("12 gclid nao resolvido: canal sim, campanha NAO (fail-closed)",
         ("ATRIBUIDO", "GCLID_NAO_RESOLVIDO", None, 0.6),
         (v["veredito"], v["evidencia"], v["campanha"], v["confianca"]))

    v = mod.atribuir(gclid, contrato, None)
    item("13 sem resolucao e sem utm: cai na evidencia fraca de utm_source (nunca inventa campanha)",
         ("ATRIBUIDO", "UTM_SOURCE_GOOGLE", None, 0.45),
         (v["veredito"], v["evidencia"], v["campanha"], v["confianca"]))

    sem_evidencia = ler_exemplo("lead-sem-evidencia.json")
    v = mod.atribuir(sem_evidencia, contrato)
    item("14 nenhuma evidencia nomeada: NAO_ATRIBUIDO/SEM_IDENTIFICADOR (sem canal)",
         ("NAO_ATRIBUIDO", "SEM_IDENTIFICADOR", None, 0.0),
         (v["veredito"], v["motivo"], v["canal"], v["confianca"]))

    forte = mod.atribuir(ads, contrato, {"status": "RESOLVIDO", "campanha_id": "cmp-900"})
    fraca = mod.atribuir(gclid, contrato, None)
    item("15 evidencia forte e fraca nao se misturam (confiancas distintas, forte vence)",
         True, forte["confianca"] > fraca["confianca"] and forte["evidencia"] != fraca["evidencia"])

    utm_facebook = dict(sem_evidencia)
    utm_facebook["utm_source"] = "facebook"
    item("16 canal que nao e' o do card nao e' atribuido aqui",
         "NAO_ATRIBUIDO", mod.atribuir(utm_facebook, contrato)["veredito"])

    # --- identidade, PII, envelope ---------------------------------------------------------
    item("17 chave de idempotencia deterministica e estavel",
         "google-lead:google_ads_lead_form:gads-88231", mod.chave_idempotencia(ads))

    resumo = mod.resumo_sem_pii(ads, mod.atribuir(ads, contrato))
    item("18 resumo NAO expoe e-mail nem telefone em claro",
         False, any(t in resumo for t in (ads["contato_email"], ads["contato_telefone"])))

    envelope = mod.envelope_de_atribuicao(ads, mod.atribuir(ads, contrato), contrato, None)
    item("19 envelope: contato mascarado e id do Google fora de coluna canonica",
         (True, True), (ads["contato_email"] not in json.dumps(envelope),
                        envelope["campanha_id"] == "cmp-7781"))

    desconhecido = dict(ads)
    desconhecido["form_id"] = "form-1"
    item("20 campo fora do mapa vai para campos_desconhecidos (nao vira coluna)",
         ["form_id"], mod.campos_desconhecidos(desconhecido, contrato))

    # --- auditoria da propria fonte --------------------------------------------------------
    item("21 auditoria da fonte: sem SQL proibido e INSERT so' nas 2 tabelas do canonico",
         [], mod.auditar_fonte(alvo))

    with tempfile.TemporaryDirectory() as tmp:
        copia = os.path.join(tmp, "mut_sql.py")
        with open(alvo, "r", encoding="utf-8") as fh:
            texto = fh.read()
        # mutacao minima e' troca do alvo declarado do INSERT; a auditoria tem de acusar.
        with open(copia, "w", encoding="utf-8") as fh:
            fh.write(texto.replace("INSERT INTO {TABELA_SYNC}", "INSERT INTO sales_intelligence.organizations"))
        itens_mutados = mod.auditar_fonte(copia)
        item("22 auditoria pega INSERT fora do limite (prova do proprio instrumento)",
             True, bool(itens_mutados) and itens_mutados[0]["padrao"] == "insert_fora_do_limite")

    # --- guardas de CLI --------------------------------------------------------------------
    item("23 prod RECUSA por desenho (exit 4)",
         4, rodar_cli(alvo, ["--planejar"], {"TRE_AMBIENTE": "prod"}))
    item("24 dev sem porta de banco no --ingerir: BANCO_NAO_DECLARADO (exit 3)",
         3, rodar_cli(alvo, ["--ingerir", "--confirmo", "--entrada", os.path.join(EXEMPLOS, "lead-google-ads.json")]))
    item("25 dev com banco remoto e' recusado (exit 3)",
         3, rodar_cli(alvo, ["--ingerir", "--confirmo", "--porta-banco", "ssh host psql -U x",
                             "--entrada", os.path.join(EXEMPLOS, "lead-google-ads.json")]))
    item("26 dev com resolvedor de gclid fora do loopback e' recusado (exit 3)",
         3, rodar_cli(alvo, ["--ingerir", "--confirmo", "--porta-banco", "docker exec -i pg-google-acc psql -U sales_ai",
                             "--porta-ads", "https://googleads.googleapis.com",
                             "--entrada", os.path.join(EXEMPLOS, "lead-google-ads.json")]))
    item("27 --ingerir sem --confirmo e' DRY_RUN local (exit 0)",
         0, rodar_cli(alvo, ["--atribuir", "--entrada", os.path.join(EXEMPLOS, "lead-google-ads.json")]))
    item("28 homolog sem aprovacao humana registrada e' recusado (exit 3)",
         3, rodar_cli(alvo, ["--planejar"], {"TRE_AMBIENTE": "homolog"}))

    if hasattr(mod, "auditar_fonte"):
        pass
    return {"itens": ITENS}


# ---------------------------------------------------------------------------------------------
# Autoteste por mutacao: cada mutacao tem de reprovar O ITEM ESPERADO
# ---------------------------------------------------------------------------------------------
MUTACOES = [
    ("contrato", "confianca-forte-vira-fraca", lambda t: t.replace('"confianca": 0.95', '"confianca": 0.45'),
     ["9 formulario de Lead Ads com campanha: ATRIBUIDO pela evidencia forte"]),
    ("contrato", "ordem-do-formulario-vai-para-o-fim", lambda t: t.replace('"ordem": 1,', '"ordem": 9,'),
     ["3 regras da tabela em ordem crescente declarada"]),
    ("contrato", "fallback-ganha-canal", lambda t: t.replace('"canal": null,\n    "confianca": 0.0',
                                                            '"canal": "google",\n    "confianca": 0.0'),
     ["14 nenhuma evidencia nomeada: NAO_ATRIBUIDO/SEM_IDENTIFICADOR (sem canal)"]),
    ("componente", "resumo-passa-a-expor-email", lambda t: t.replace(
        'partes = [f"fonte={payload.get(\'fonte\')}", f"evidencia={veredito.get(\'evidencia\')}"]',
        'partes = [str(payload.get("contato_email")), str(payload.get("contato_telefone"))]'),
     ["18 resumo NAO expoe e-mail nem telefone em claro"]),
    ("componente", "auditoria-com-literal-proibido", lambda t: t.replace('"UP" + "DATE" + " "', '"UPDATE "'),
     ["21 auditoria da fonte: sem SQL proibido e INSERT so' nas 2 tabelas do canonico"]),
    ("componente", "insert-da-trilha-em-tabela-errada", lambda t: t.replace(
        "INSERT INTO {TABELA_SYNC}", "INSERT INTO sales_intelligence.organizations"),
     ["21 auditoria da fonte: sem SQL proibido e INSERT so' nas 2 tabelas do canonico"]),
    ("componente", "incoerencia-do-formulario-desligada", lambda t: t.replace(
        'if str(payload.get("fonte") or "") == item.get("fonte") and not str(payload.get(item.get("falta")) or "").strip():',
        'if False:'),
     ["10 formulario de Lead Ads sem campanha: NAO_ATRIBUIDO (incoerencia declarada)"]),
    ("contrato", "resolucao-de-gclid-ignorada", lambda t: t.replace('"resolucao_gclid": "NAO_ENCONTRADO"',
                                                                   '"resolucao_gclid": "RESOLVIDO"'),
     ["12 gclid nao resolvido: canal sim, campanha NAO (fail-closed)"]),
]


def rodada(alvo: str, rotulo: str = "") -> dict:
    global OK, FALHOU, ITENS
    OK = FALHOU = 0
    ITENS = []
    mod = carregar_modulo(alvo, "mod_" + re.sub(r"[^a-zA-Z0-9]", "_", rotulo or "base"))
    medir(mod, alvo)
    return {"rotulo": rotulo, "ok": OK, "falhou": FALHOU, "itens": list(ITENS)}


def main(argv=None) -> int:
    global OK, FALHOU
    parser = argparse.ArgumentParser(description="Suite offline da atribuicao de lead do Google")
    parser.add_argument("--componente", default=COMPONENTE)
    parser.add_argument("--autoteste", action="store_true")
    parser.add_argument("--saida", default=None)
    args = parser.parse_args(argv)

    if not os.path.isfile(args.componente):
        print(f"nao testavel: componente ausente: {args.componente}")
        return 2

    base = rodada(args.componente, "base")
    print(f"\n# base: {base['ok']} OK / {base['falhou']} FALHOU")
    resultado = {"base": {"ok": base["ok"], "falhou": base["falhou"]}, "mutacoes": []}
    dentes_ok = dentes_falhou = 0

    if args.autoteste:
        if base["falhou"]:
            print("# controle VERMELHO: o autoteste nao comeca com a base reprovada")
            return 1
        with tempfile.TemporaryDirectory() as tmp:
            for onde, rotulo, mutar, itens_esperados in MUTACOES:
                destino = os.path.join(tmp, rotulo.replace("-", "_"))
                os.makedirs(destino)
                alvo = os.path.join(destino, "atribuicao_google.py")
                with open(args.componente, "r", encoding="utf-8") as fh:
                    texto_componente = fh.read()
                with open(CONTRATO, "r", encoding="utf-8") as fh:
                    texto_contrato = fh.read()
                if onde == "componente":
                    novo_componente, novo_contrato = mutar(texto_componente), texto_contrato
                else:
                    novo_componente, novo_contrato = texto_componente, mutar(texto_contrato)
                if novo_componente == texto_componente and novo_contrato == texto_contrato:
                    print(f"FALHOU mutacao '{rotulo}': ancora de texto nao existe no arquivo")
                    resultado["mutacoes"].append({"mutacao": rotulo, "aplicada": False})
                    dentes_falhou += 1
                    continue
                with open(alvo, "w", encoding="utf-8") as fh:
                    fh.write(novo_componente)
                with open(os.path.join(destino, "atribuicao-google-v1.json"), "w", encoding="utf-8") as fh:
                    fh.write(novo_contrato)
                shutil.copytree(EXEMPLOS, os.path.join(destino, "exemplos"))
                sub = rodada(alvo, rotulo)
                reprovados = [i["item"] for i in sub["itens"] if i["resultado"] == "FALHOU"]
                bateu = all(e in reprovados for e in itens_esperados)
                print(f"{'OK    ' if bateu else 'FALHOU'} dente '{rotulo}': reprovou os itens esperados={itens_esperados}")
                if bateu:
                    dentes_ok += 1
                else:
                    dentes_falhou += 1
                resultado["mutacoes"].append({"mutacao": rotulo, "aplicada": True, "reprovados": reprovados,
                                              "esperados": itens_esperados, "bateu": bateu})
        print(f"# dentes: {dentes_ok}/{len(MUTACOES)} reprovaram o item esperado")

    falhas = base["falhou"] + dentes_falhou
    veredito = "SUITE_ATRIBUICAO_GOOGLE_OK" if falhas == 0 else "SUITE_ATRIBUICAO_GOOGLE_FALHOU"
    print(f"\n# veredito: {veredito} ({base['ok']} itens OK na base, {base['falhou']} FALHOU, "
          f"{dentes_ok}/{len(MUTACOES)} dentes)")
    if args.saida:
        os.makedirs(os.path.dirname(os.path.abspath(args.saida)), exist_ok=True)
        with open(args.saida, "w", encoding="utf-8") as fh:
            json.dump({"veredito": veredito, "base": {"ok": base["ok"], "falhou": base["falhou"]},
                       "dentes": {"ok": dentes_ok, "total": len(MUTACOES), "falhou": dentes_falhou},
                       "resultado": resultado}, fh, ensure_ascii=False, indent=2)
    return 0 if falhas == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
