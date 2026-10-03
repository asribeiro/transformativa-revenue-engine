#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verificador OFFLINE do envio outbound v1 (card TRE-W6-E04-T01) — `envio-outbound-v1`.

Mede o componente `hermes/agents/outreach/send_workflow.py` SEM PostgreSQL e SEM SMTP: a porta de banco
e o duble `scripts/agentes/duble_psql_envio.py` e o primitivo SMTP e um duble que anota a mensagem
recebida. O que se mede aqui: leitura do pedido aprovado, PORTAO do card irmao, guarda de escrita,
guarda de ambiente/destino, composicao da mensagem (texto aprovado + CTA), claim exatamente-uma-vez,
idempotencia (JA_ENVIADO / ENVIO_EM_VOO), falha do primitivo (nada gravado) e desfazer.

NAO se mede aqui: o banco de verdade, o TLS e o AUTH do Titan. Isso e o aceite E2E
(`scripts/agentes/teste_envio_outbound_aceite.sh`) em PostgreSQL descartavel na VPS.

Uso:
  python3 scripts/agentes/verificar_envio_outbound.py                 # suite
  python3 scripts/agentes/verificar_envio_outbound.py --autoteste     # suite + mutacoes (prova de dente)
  python3 scripts/agentes/verificar_envio_outbound.py --raiz <dir> [--codigo <modulo.py>]
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
import uuid
from pathlib import Path

ORG = "11111111-1111-4111-8111-111111111111"
CONTATO = "22222222-2222-4222-8222-222222222222"
PEDIDO = "33333333-3333-4333-8333-333333333333"
PEDIDO_INEXISTENTE = "99999999-9999-4999-8999-999999999999"
TEXTO = {"assunto": "Eficiencia operacional na Alfa",
         "corpo": "Bom dia, Maria. Vi que a Alfa revisou processos internos.",
         "cta": "Faz sentido conversarmos 20 minutos nesta semana?"}

ITENS: list[tuple[str, bool]] = []


def item(nome: str, esperado, obtido) -> None:
    ok = esperado == obtido
    ITENS.append((nome, ok))
    print(("OK     " if ok else "FALHOU ") + f"{nome} (esperado={esperado!r} obtido={obtido!r})")


def carregar_modulo(caminho: Path, nome: str):
    spec = importlib.util.spec_from_file_location(nome, caminho)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nome] = modulo
    spec.loader.exec_module(modulo)
    return modulo


def primitivo_falso(trabalho: Path) -> tuple[Path, Path]:
    captura = trabalho / "captura.jsonl"
    caminho = trabalho / "primitivo_falso.py"
    caminho.write_text(
        "import json, os, sys\n"
        "a = sys.argv\n"
        "with open(os.environ['TRE_CAPTURA'], 'a', encoding='utf-8') as fh:\n"
        "    fh.write(json.dumps({'destino': a[a.index('--para') + 1],\n"
        "                          'assunto': a[a.index('--assunto') + 1],\n"
        "                          'corpo': a[a.index('--corpo') + 1],\n"
        "                          'chave': a[a.index('--chave-idempotencia') + 1],\n"
        "                          'idempotencia': a[a.index('--chave-idempotencia') + 1]}) + '\\n')\n"
        "print('SINK_DEV_OK')\n"
        "sys.exit(int(os.environ.get('TRE_STUB_EXIT', '0')))\n", encoding="utf-8")
    return caminho, captura


def estado_da_medicao(trabalho: Path, modulo_aprovacao, *, nome: str = "estado.json",
                      status: str = "APPROVED", email: str = "maria.souza@dev.local",
                      texto: dict | None = None, pedido_id: str = PEDIDO) -> Path:
    texto = texto if texto is not None else TEXTO
    texto_hash = modulo_aprovacao.hash_do_texto(texto)
    pa = {"assunto": texto["assunto"], "corpo": texto["corpo"], "cta": texto["cta"],
          "organization_id": ORG, "contato_email": email, "canal": "EMAIL",
          "recommendation_id": None,
          "decisao": {"texto_hash": texto_hash, "por": "Anderson Ribeiro", "revisado": False}}
    pedido = {"proposed_action": pa, "status": status, "action_type": "SEND_EMAIL",
              "contato_id": CONTATO, "organizacao_id": ORG, "texto_hash": texto_hash,
              "chave": f"envio:{pedido_id}:{texto_hash}", "contato": "Maria Souza",
              "contato_email": email, "contato_canal": "EMAIL", "decidido_por": "Anderson Ribeiro",
              "decidido_em": "2026-10-02T23:00:00Z", "do_not_contact": False,
              "opt_out_email": False, "opt_out_whatsapp": False, "recomendacao_status": "OPEN"}
    caminho = trabalho / nome
    caminho.write_text(json.dumps({"pedidos": {pedido_id: pedido}, "sync": {}, "interacoes": []}),
                       encoding="utf-8")
    return caminho


def ler_estado(caminho: Path) -> dict:
    return json.loads(caminho.read_text(encoding="utf-8"))


def capturadas(caminho: Path) -> list[dict]:
    if not caminho.exists():
        return []
    return [json.loads(l) for l in caminho.read_text(encoding="utf-8").splitlines() if l.strip()]


def medir(raiz: Path, modulo: Path, trabalho: Path) -> None:
    """Roda a bateria. O veredito vai para ITENS (globais) — o autoteste le a lista."""
    modulo_aprovacao = carregar_modulo(raiz / "hermes/agents/outreach/approval_workflow.py",
                                       "tre_aprov_medida")
    componente = carregar_modulo(modulo, "tre_envio_medido")
    duble = raiz / "scripts/agentes/duble_psql_envio.py"
    fake_smtp, captura = primitivo_falso(trabalho)

    def cli(*args, captura_smtp=True):
        ambiente = dict(os.environ)
        if captura_smtp:
            ambiente["TRE_CAPTURA"] = str(captura)
        return subprocess.run([sys.executable, str(modulo), *args], capture_output=True, text=True,
                              cwd=str(raiz), env=ambiente)

    def porta(estado: Path) -> str:
        return f"{sys.executable} {duble} {estado}"

    # ---------------------------------------------------------------- A. CLI sem banco
    p = cli("--planejar")
    item("A1 --planejar exit 0", 0, p.returncode)
    item("A2 --planejar nao abre conexao", True, "PLANO_OK" in p.stdout and "nenhuma conexao" in p.stdout)
    item("A3 --regras exit 0", 0, cli("--regras").returncode)
    item("A4 prod RECUSA (exit 4)", 4, cli("--ambiente", "prod", "--prefixo", "x", "--fila").returncode)
    item("A5 uso incompleto (sem --prefixo) exit 2", 2, cli("--ambiente", "dev", "--fila").returncode)
    item("A6 uso incompleto (sem acao) exit 2",
         2, cli("--ambiente", "dev", "--prefixo", "x").returncode)

    # ---------------------------------------------------------------- B. guarda de escrita
    def recusa(sql, permitir_update=False):
        try:
            componente.validar_sql(sql, permitir_update=permitir_update)
            return "ACEITOU"
        except componente.RecusaDeEscrita:
            return "RECUSOU"

    item("B1 DDL RECUSA", "RECUSOU", recusa("CREATE TABLE x (id int);"))
    item("B2 ALTER RECUSA", "RECUSOU", recusa("ALTER TABLE sales_intelligence.interactions ADD COLUMN x int;"))
    item("B3 DELETE RECUSA", "RECUSOU", recusa("DELETE FROM sales_intelligence.sync_events;", True))
    item("B4 escrita em OUTRA tabela RECUSA",
         "RECUSOU", recusa("INSERT INTO sales_intelligence.human_approvals (id) VALUES ('x');"))
    item("B5 UPDATE fora das rodadas RECUSA",
         "RECUSOU", recusa("UPDATE sales_intelligence.sync_events SET status = 'X';"))
    item("B6 escrita nas duas tabelas ACEITA", "ACEITOU",
         recusa("INSERT INTO sales_intelligence.interactions (id) VALUES ('x');"))

    # ---------------------------------------------------------------- C. politica e contrato
    trabalho_pol = trabalho / "politica"
    trabalho_pol.mkdir(exist_ok=True)
    caminho_politica = raiz / "hermes/agents/outreach/politica-envio-v1.json"
    politica_boa = json.loads(caminho_politica.read_text(encoding="utf-8"))
    contrato_boa = json.loads((raiz / "docs/data/data_contract_v1.json").read_text(encoding="utf-8"))

    def validar(politica: dict, contrato: dict, nome: str) -> str:
        (trabalho_pol / f"politica-{nome}.json").write_text(json.dumps(politica), encoding="utf-8")
        (trabalho_pol / f"contrato-{nome}.json").write_text(json.dumps(contrato), encoding="utf-8")
        try:
            componente.carregar_politicas(raiz, trabalho_pol / f"politica-{nome}.json",
                                          trabalho_pol / f"contrato-{nome}.json")
            return "CARREGOU"
        except componente.RecusaDePolitica:
            return "RECUSOU"

    item("C1 politica coerente CARREGA", "CARREGOU", validar(politica_boa, contrato_boa, "ok"))
    mut = json.loads(json.dumps(politica_boa))
    mut["guarda_de_escrita"]["tabelas"] = ["sales_intelligence.interactions"]
    item("C2 tabelas divergentes RECUSA", "RECUSOU", validar(mut, contrato_boa, "tabelas"))
    mut = json.loads(json.dumps(politica_boa))
    mut["vereditos"] = [v for v in mut["vereditos"] if v != "ENVIADO"]
    item("C3 veredito faltando RECUSA", "RECUSOU", validar(mut, contrato_boa, "veredito"))
    mut = json.loads(json.dumps(contrato_boa))
    mut["vocabularies"].pop("human_approvals.status", None)
    item("C4 contrato sem vocabulario de status RECUSA", "RECUSOU", validar(politica_boa, mut, "vocabulario"))
    mut = json.loads(json.dumps(politica_boa))
    mut["ambiente"]["recusado"] = "homolog"
    item("C5 ambiente.recusado fora de prod RECUSA", "RECUSOU", validar(mut, contrato_boa, "ambiente"))
    item("C6 politica ausente RECUSA exit 3",
         3, cli("--politica", str(trabalho_pol / "nao-existe.json"), "--planejar").returncode)

    # ---------------------------------------------------------------- D. fila
    estado = estado_da_medicao(trabalho, modulo_aprovacao)
    p = cli("--ambiente", "dev", "--prefixo", porta(estado), "--fila")
    item("D1 --fila exit 0", 0, p.returncode)
    item("D2 fila ve 1 elegivel", 1, json.loads(p.stdout)["fila"]["elegiveis"])
    item("D3 fila marca o ainda-nao-enviado como ELEGIVEL", "ELEGIVEL",
         ((json.loads(p.stdout)["fila"]["pedidos"] or [{}])[0]).get("veredito"))

    # ---------------------------------------------------------------- E. dry-run
    p = cli("--ambiente", "dev", "--prefixo", porta(estado), "--enviar", PEDIDO)
    rel = json.loads(p.stdout)
    item("E1 dry-run exit 0", 0, p.returncode)
    item("E2 dry-run e PLANO", "PLANO", rel["envios"][0]["veredito"])
    item("E3 dry-run nao fala com o SMTP", 0, len(capturadas(captura)))
    item("E4 dry-run nao reclama chave (nada em sync_events)", 0, len(ler_estado(estado)["sync"]))

    # ---------------------------------------------------------------- F. envio confirmado
    envio = ["--ambiente", "dev", "--prefixo", porta(estado), "--enviar", PEDIDO, "--confirmo",
             "--primitivo", str(fake_smtp)]
    p = cli(*envio)
    rel = json.loads(p.stdout)
    item("F1 envio exit 0", 0, p.returncode)
    item("F2 veredito ENVIADO", "ENVIADO", rel["envios"][0].get("veredito"))
    item("F3 UMA mensagem chegou ao primitivo", 1, len(capturadas(captura)))
    item("F4 corpo enviado = texto aprovado + CTA", f"{TEXTO['corpo']}\n\n{TEXTO['cta']}",
         capturadas(captura)[0]["corpo"] if capturadas(captura) else None)
    item("F5 destino = contato do pedido (nunca da linha de comando)",
         "maria.souza@dev.local", capturadas(captura)[0]["destino"] if capturadas(captura) else None)
    item("F6 chave = envio:<pedido>:<texto_hash>",
         f"envio:{PEDIDO}:{modulo_aprovacao.hash_do_texto(TEXTO)}",
         capturadas(captura)[0]["chave"] if capturadas(captura) else None)
    dados = ler_estado(estado)
    sync = (list(dados["sync"].values()) or [{}])[0]
    item("F7 sync_events ENVIADO", "ENVIADO", sync.get("status"))
    item("F8 UMA interaction gravada", 1, len(dados["interacoes"]))
    item("F9 interaction referencia o que foi autorizado",
         f"envio:{PEDIDO}:{modulo_aprovacao.hash_do_texto(TEXTO)}",
         (dados["interacoes"] or [{}])[0].get("content_reference"))
    item("F10 sync_events aponta a interaction", (dados["interacoes"] or [{}])[0].get("interaction_id"),
         sync.get("interaction_id"))
    item("F11 fila passa a marcar JA_ENVIADO", "JA_ENVIADO",
         ((json.loads(cli("--ambiente", "dev", "--prefixo", porta(estado), "--fila").stdout)
           ["fila"]["pedidos"] or [{}])[0]).get("veredito"))

    # ---------------------------------------------------------------- G. idempotencia
    p = cli(*envio)
    rel = json.loads(p.stdout)
    item("G1 replay = JA_ENVIADO", "JA_ENVIADO", rel["envios"][0].get("motivo"))
    item("G2 replay nao reenvia", 1, len(capturadas(captura)))

    estado_falha = estado_da_medicao(trabalho, modulo_aprovacao, nome="estado_falha.json")
    os.environ["TRE_STUB_EXIT"] = "1"
    p = cli("--ambiente", "dev", "--prefixo", porta(estado_falha), "--enviar", PEDIDO, "--confirmo",
            "--primitivo", str(fake_smtp))
    rel = json.loads(p.stdout)
    dados_falha = ler_estado(estado_falha)
    os.environ.pop("TRE_STUB_EXIT")
    item("G3 falha do primitivo RECUSA (ENVIO_FALHOU)", "ENVIO_FALHOU", rel["envios"][0].get("motivo"))
    item("G4 claim foi feito ANTES do SMTP (a chave ficou FALHOU)",
         "FALHOU", (list(dados_falha["sync"].values()) or [{}])[0].get("status"))
    item("G5 falha NAO grava fato em interactions", 0, len(dados_falha["interacoes"]))

    estado_em_voo = estado_da_medicao(trabalho, modulo_aprovacao, nome="estado_voo.json")
    chave = f"envio:{PEDIDO}:{modulo_aprovacao.hash_do_texto(TEXTO)}"
    dados_voo = ler_estado(estado_em_voo)
    dados_voo["sync"][chave] = {"status": "ENVIANDO", "tentativas": 1, "interaction_id": None}
    estado_em_voo.write_text(json.dumps(dados_voo), encoding="utf-8")
    captura.unlink(missing_ok=True)
    p = cli("--ambiente", "dev", "--prefixo", porta(estado_em_voo), "--enviar", PEDIDO, "--confirmo",
            "--primitivo", str(fake_smtp))
    rel = json.loads(p.stdout)
    item("G6 chave em voo (ENVIANDO) RECUSA", "ENVIO_EM_VOO", rel["envios"][0].get("motivo"))
    item("G7 chave em voo nao reenvia (nenhuma mensagem nova)", 0, len(capturadas(captura)))

    # ---------------------------------------------------------------- H. envio 2 (destino/ambiente)
    estado_real = estado_da_medicao(trabalho, modulo_aprovacao, nome="estado_real.json",
                                    email="contato@empresa.com.br")
    captura.unlink(missing_ok=True)
    p = cli("--ambiente", "dev", "--prefixo", porta(estado_real), "--enviar", PEDIDO, "--confirmo",
            "--primitivo", str(fake_smtp))
    rel = json.loads(p.stdout)
    item("H1 dev com destino real RECUSA (DESTINO_NAO_DEV)", "DESTINO_NAO_DEV", rel["envios"][0].get("motivo"))
    item("H2 destino real nao gera mensagem", 0, len(capturadas(captura)))
    item("H3 destino real nao reclama chave", 0, len(ler_estado(estado_real)["sync"]))
    item("H4 homolog sem aprovacao registrada RECUSA", "HOMOLOG_SEM_APROVACAO",
         json.loads(cli("--ambiente", "homolog", "--prefixo", porta(estado_real), "--enviar", PEDIDO,
                        "--confirmo", "--primitivo", str(fake_smtp)).stdout)["envios"][0].get("motivo"))
    item("H5 homolog no lugar da aprovacao: dev continua exigindo dominio de dev", "DESTINO_NAO_DEV",
         json.loads(cli("--ambiente", "dev", "--prefixo", porta(estado_real), "--enviar", PEDIDO,
                        "--confirmo", "--primitivo", str(fake_smtp)).stdout)["envios"][0].get("motivo"))

    # ---------------------------------------------------------------- I. portao do card irmao
    estado_pendente = estado_da_medicao(trabalho, modulo_aprovacao, nome="estado_pendente.json",
                                        status="PENDING")
    p = cli("--ambiente", "dev", "--prefixo", porta(estado_pendente), "--enviar", PEDIDO, "--confirmo",
            "--primitivo", str(fake_smtp))
    item("I1 pedido fora do estado aprovado RECUSA no portao", "PORTAO_NAO_LIBEROU",
         json.loads(p.stdout)["envios"][0].get("motivo"))
    item("I2 pedido fora do estado aprovado nao aparece na fila", 0,
         json.loads(cli("--ambiente", "dev", "--prefixo", porta(estado_pendente), "--fila").stdout)
         ["fila"].get("elegiveis"))
    p = cli("--ambiente", "dev", "--prefixo", porta(estado), "--enviar", PEDIDO_INEXISTENTE, "--confirmo",
            "--primitivo", str(fake_smtp))
    item("I3 pedido inexistente RECUSA", "PORTAO_NAO_LIBEROU", json.loads(p.stdout)["envios"][0].get("motivo"))
    estado_expirado = estado_da_medicao(trabalho, modulo_aprovacao, nome="estado_expirado.json",
                                        status="EXPIRED")
    dados_exp = ler_estado(estado_expirado)
    dados_exp["pedidos"][PEDIDO]["forcar_status"] = True
    estado_expirado.write_text(json.dumps(dados_exp), encoding="utf-8")
    captura.unlink(missing_ok=True)
    p = cli("--ambiente", "dev", "--prefixo", porta(estado_expirado), "--enviar", PEDIDO, "--confirmo",
            "--primitivo", str(fake_smtp))
    item("I4 portao devolve pode_enviar=False (EXPIRED) RECUSA", "PORTAO_NAO_LIBEROU",
         json.loads(p.stdout)["envios"][0].get("motivo"))
    item("I5 pedido recusado pelo portao nao gera mensagem", 0, len(capturadas(captura)))

    # ---------------------------------------------------------------- J. desfazer
    estado_env = estado_da_medicao(trabalho, modulo_aprovacao, nome="estado_env.json")
    captura.unlink(missing_ok=True)
    cli("--ambiente", "dev", "--prefixo", porta(estado_env), "--enviar", PEDIDO, "--confirmo",
        "--primitivo", str(fake_smtp), "--correlation-id", "corr-e04")
    dados_env = ler_estado(estado_env)
    item("J0 (pre-condicao) envio registrado ENVIADO", "ENVIADO",
         (list(dados_env["sync"].values()) or [{}])[0].get("status"))
    p = cli("--ambiente", "dev", "--prefixo", porta(estado_env), "--desfazer", "corr-e04")
    item("J1 --desfazer dry-run conta 1", 1, json.loads(p.stdout)["desfazer"].get("seriam_desfeitos"))
    item("J2 dry-run nao marca nada", "ENVIADO",
         (list(ler_estado(estado_env)["sync"].values()) or [{}])[0].get("status"))
    p = cli("--ambiente", "dev", "--prefixo", porta(estado_env), "--desfazer", "corr-e04", "--confirmo")
    item("J3 --confirmo sem --por RECUSA", "OPERADOR_AUSENTE",
         json.loads(p.stdout)["desfazer"].get("motivo"))
    p = cli("--ambiente", "dev", "--prefixo", porta(estado_env), "--desfazer", "corr-e04", "--por",
            "Anderson Ribeiro", "--motivo", "pedido errado", "--confirmo")
    item("J4 --desfazer --confirmo marca DESFEITO", "DESFEITO", json.loads(p.stdout)["desfazer"].get("veredito"))
    dados_pos = ler_estado(estado_env)
    item("J5 o fato em interactions NAO e apagado", 1, len(dados_pos["interacoes"]))
    item("J6 a marca carrega operador e motivo", "DESFEITO_POR:Anderson Ribeiro:pedido errado",
         (list(dados_pos["sync"].values()) or [{}])[0].get("error_message"))


MUTACOES = [
    ("sem-guarda-de-destino", 'if not destino.lower().endswith("@" + dev):', "if False:",
     "H1 dev com destino real RECUSA (DESTINO_NAO_DEV)"),
    ("sem-guarda-de-escrita", 'if tabela.lower() not in TABELAS_ESCRITA:',
     "if False:", "B4 escrita em OUTRA tabela RECUSA"),
    ("sem-try-do-portao", "raise RecusaDeEnvio(MOTIVO_PORTAO_NAO_LIBEROU, str(recusa)) from recusa",
     "raise", "I1 pedido fora do estado aprovado RECUSA no portao"),
    ("ignora-o-portao", 'if not consulta.get("pode_enviar"):', "if False:",
     "I4 portao devolve pode_enviar=False (EXPIRED) RECUSA"),
    ("sem-claim-antes-do-smtp", "    reclamar_chave(politica, prefixo, chave, pedido_id, tentativas, correlation_id, triggered_by,\n                   str(primitivo))",
     "    pass  # mutacao: sem claim", "G4 claim foi feito ANTES do SMTP (a chave ficou FALHOU)"),
    ("ignora-falha-do-primitivo", "    if proc.returncode != 0:", "    if False:",
     "G3 falha do primitivo RECUSA (ENVIO_FALHOU)"),
    ("sem-cta-na-mensagem", "    if cta:", "    if False:",
     "F4 corpo enviado = texto aprovado + CTA"),
]


def preparar_arvore(destino: Path, raiz: Path) -> None:
    (destino / "docs").mkdir(parents=True, exist_ok=True)
    shutil.copytree(raiz / "docs/data", destino / "docs/data")
    shutil.copytree(raiz / "hermes/agents/outreach", destino / "hermes/agents/outreach")
    shutil.copytree(raiz / "hermes/integracoes", destino / "hermes/integracoes")
    (destino / "scripts/agentes").mkdir(parents=True, exist_ok=True)
    shutil.copy2(raiz / "scripts/agentes/duble_psql_envio.py",
                 destino / "scripts/agentes/duble_psql_envio.py")


def autoteste(raiz: Path, modulo: Path, trabalho_base: Path) -> int:
    print("== autoteste por mutacao (cada mutacao tem de reprovar O ITEM QUE NOMEIA) ==")
    falhas = 0
    for nome, de, para, item_esperado in MUTACOES:
        destino = trabalho_base / f"mut-{nome}"
        if destino.exists():
            shutil.rmtree(destino)
        preparar_arvore(destino, raiz)
        copia = destino / "hermes/agents/outreach/send_workflow.py"
        shutil.copy2(modulo, copia)
        texto = copia.read_text(encoding="utf-8")
        if de not in texto:
            print(f"ABORTA mutacao {nome}: ancora nao encontrada ({de[:60]!r}) — mutacao que nao aplica e buraco")
            return 1
        copia.write_text(texto.replace(de, para, 1), encoding="utf-8")
        trabalho_mut = destino / "trabalho"
        trabalho_mut.mkdir(parents=True, exist_ok=True)
        ITENS.clear()
        medir(destino, copia, trabalho_mut)
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
    modulo = Path(args.codigo).resolve() if args.codigo else raiz / "hermes/agents/outreach/send_workflow.py"
    trabalho = Path(tempfile.mkdtemp(prefix="envio-outbound-"))
    ITENS.clear()
    medir(raiz, modulo, trabalho)
    falhas = sum(1 for _, ok in ITENS if not ok)
    if args.autoteste:
        trabalho_base = Path(tempfile.mkdtemp(prefix="envio-dentes-"))
        falhas += autoteste(raiz, modulo, trabalho_base)
    if falhas:
        print(f"\nFALHOU (envio outbound v1: {len(ITENS)} itens, {falhas} falhas)")
        return 1
    print(f"\nPASS (envio outbound v1: {len(ITENS)} itens, 0 falhas)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
