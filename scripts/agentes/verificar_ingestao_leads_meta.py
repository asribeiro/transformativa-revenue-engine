#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verificador da ingestao de leads Meta v1 (`meta-lead-ingestion-v1`) — card TRE-W7-E02-T01.

Suite OFFLINE (sem banco, sem rede externa): mede o componente
`hermes/agentes/inbound/ingestao_leads_meta.py` pelo caminho real — contrato versionado, assinatura
HMAC do webhook, leitura da Graph API contra o SINK LOCAL (`scripts/agentes/stub-meta-graph-dev.py`,
127.0.0.1), normalizacao dos campos, PII fora do texto livre, auditoria da propria fonte e guardas de
ambiente/CLI (por subprocesso, medindo o exit code de verdade).

A suite NAO mocka o proprio alvo: nao existe `mock`/`patch` neste arquivo (item S0 mede isso); o que
existe e' sink local, como no aceite.

`--autoteste`: muta COPIA do componente e exige que o item ESPERADO reprove sem a mutacao — provar que
o teste cobre a regra, nao que ele passa.

Uso:
  python3 scripts/agentes/verificar_ingestao_leads_meta.py
  python3 scripts/agentes/verificar_ingestao_leads_meta.py --autoteste
Exit: 0 = PASS (0 falhas) · 1 = FALHOU · 3 = nao testavel.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent.parent
MODULO = RAIZ / "hermes" / "agentes" / "inbound" / "ingestao_leads_meta.py"
CONTRATO = RAIZ / "hermes" / "agentes" / "inbound" / "meta-lead-ingestion-v1.json"
STUB = RAIZ / "scripts" / "agentes" / "stub-meta-graph-dev.py"

ITENS_OK = 0
ITENS_FALHOU = 0
FALHAS = []


def item(nome: str, condicao: bool, detalhe: str = "") -> bool:
    global ITENS_OK, ITENS_FALHOU
    if condicao:
        ITENS_OK += 1
        print(f"OK     {nome}" + (f" ({detalhe})" if detalhe else ""))
    else:
        ITENS_FALHOU += 1
        FALHAS.append(nome)
        print(f"FALHOU {nome}" + (f" — {detalhe}" if detalhe else ""))
    return bool(condicao)


def carregar(caminho: Path):
    espec = importlib.util.spec_from_file_location(f"mod_{abs(hash(str(caminho))) % 10**8}", str(caminho))
    modulo = importlib.util.module_from_spec(espec)
    espec.loader.exec_module(modulo)
    return modulo


def webhook(leadgen_id: str, page_id: str = "page-1", form_id: str = "form-1",
            ad_id: str = "ad-1", created_time: int = 1790000000, campo: str = "leadgen") -> dict:
    return {"object": "page", "entry": [{"id": page_id, "time": created_time,
            "changes": [{"field": campo, "value": {"leadgen_id": leadgen_id, "page_id": page_id,
                                                   "form_id": form_id, "ad_id": ad_id,
                                                   "created_time": created_time}}]}]}


def payload_lead(email="Joao.Souza@Cliente-Demo.test", phone="+55 (11) 98888-7777",
                 nome="Joao Souza") -> dict:
    campos = []
    if email is not None:
        campos.append({"name": "email", "values": [email]})
    if phone is not None:
        campos.append({"name": "phone_number", "values": [phone]})
    campos += [{"name": "full_name", "values": [nome]}, {"name": "company_name", "values": ["Distribuidora Alfa"]},
               {"name": "job_title", "values": ["Diretor de Operacoes"]}, {"name": "city", "values": ["Campinas"]},
               {"name": "whatsapp_optin", "values": ["true"]}]
    return {"id": "lead-1", "created_time": 1790000000, "ad_id": "ad-1", "form_id": "form-1",
            "field_data": campos}


class SinkGraph:
    """Sobe o STUB real (nao um duble da suite) em 127.0.0.1 e conta as chamadas."""

    def __init__(self, leads: dict, token: str, falhar_primeira: str = "", falhar_sempre: str = ""):
        self.leads = Path(tempfile.mkdtemp(prefix="w7e02-sink-")) / "leads.json"
        self.registro = self.leads.parent / "graph.jsonl"
        self.leads.write_text(json.dumps(leads), encoding="utf-8")
        os.environ["TRE_META_STUB_TOKEN"] = token
        os.environ["TRE_META_STUB_LEADS"] = str(self.leads)
        os.environ["TRE_META_STUB_REGISTRO"] = str(self.registro)
        os.environ["TRE_META_STUB_FALHAR_PRIMEIRA"] = falhar_primeira
        os.environ["TRE_META_STUB_FALHAR_SEMPRE"] = falhar_sempre
        self.stub = carregar(STUB)
        self.porta = self._porta_livre()
        self.servidor = self.stub.ThreadingHTTPServer(("127.0.0.1", self.porta), self.stub.Manipulador)
        self.thread = threading.Thread(target=self.servidor.serve_forever, daemon=True)
        self.thread.start()
        for _ in range(50):
            if self.porta:
                break
            time.sleep(0.05)

    @staticmethod
    def _porta_livre() -> int:
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        porta = s.getsockname()[1]
        s.close()
        return porta

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.porta}"

    def linhas(self) -> list:
        if not self.registro.exists():
            return []
        return [json.loads(l) for l in self.registro.read_text(encoding="utf-8").splitlines() if l.strip()]

    def chamadas(self, leadgen_id: str) -> int:
        return sum(1 for l in self.linhas() if l["leadgen_id"] == leadgen_id)

    def parar(self) -> None:
        self.servidor.shutdown()
        self.servidor.server_close()
        shutil.rmtree(self.leads.parent, ignore_errors=True)


def config_de_teste(mod, base: str, token: str, segredo: str):
    return mod.Configuracao({"TRE_AMBIENTE": "dev", "TRE_META_GRAPH_BASE": base,
                             "TRE_META_GRAPH_VERSAO": "v21.0", "TRE_META_ACCESS_TOKEN": token,
                             "TRE_META_APP_SECRET": segredo,
                             "TRE_META_PORTA_BANCO": "docker exec -i pg-meta-acc psql -U sales_ai -d sales_intelligence"})


def assino(corpo: bytes, segredo: str) -> str:
    return "sha256=" + hmac.new(segredo.encode(), corpo, hashlib.sha256).hexdigest()


def ambiente_cli(mod, base: str, token: str, segredo: str, banco: str = None) -> dict:
    env = dict(os.environ)
    env.update({"TRE_AMBIENTE": "dev", "TRE_META_GRAPH_BASE": base, "TRE_META_GRAPH_VERSAO": "v21.0",
                "TRE_META_ACCESS_TOKEN": token, "TRE_META_APP_SECRET": segredo,
                "TRE_META_PORTA_BANCO": banco or "docker exec -i pg-meta-acc psql -U sales_ai -d sales_intelligence"})
    return env


def roda_cli(mod, args: list, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(MODULO)] + args, capture_output=True, text=True,
                          env=env, timeout=60)


def suite(mod, contrato: dict, sink: SinkGraph, token: str, segredo: str) -> None:
    print("== S. contrato, assinatura e normalizacao (sem banco)")
    proibidos = ["unittest." + "mock", "monkey" + "patch", "Magic" + "Mock"]
    item("S0 a suite nao mocka o proprio alvo (0 mock/patch no arquivo)",
         not any(p in Path(__file__).read_text(encoding="utf-8") for p in proibidos))
    item("S1 contrato carrega e valida (versao, campos, tentativas)",
         mod.carregar_contrato(str(CONTRATO)).get("versao") == mod.VERSAO)
    falhas_vocab = mod.validar_contrato({**contrato, "vocabulario": {**contrato["vocabulario"],
                                                                    "response_category": []}})
    item("S2 contrato com vocabulario vazio e' recusado", any("response_category" in f for f in falhas_vocab))
    falhas_retry = mod.validar_contrato({**contrato, "graph": {**contrato["graph"], "tentativas": 9}})
    item("S3 contrato com retry acima de 3 e' recusado (enxurrada de chamada paga)",
         any("tentativas" in f for f in falhas_retry))
    falhas_mapa = mod.validar_contrato({**contrato, "campos": {**contrato["campos"], "mapa": {}}})
    item("S4 contrato sem mapa de campos e' recusado", any("mapa" in f for f in falhas_mapa))
    falhas_chave = mod.validar_contrato({**contrato, "persistencia": {**contrato["persistencia"],
                                          "idempotencia": {"chave": "outra"}}})
    item("S5 contrato com chave de idempotencia diferente e' recusado",
         any("idempotencia" in f for f in falhas_chave))

    corpo = json.dumps(webhook("lead-1"), sort_keys=True).encode()
    ok, _ = mod.assinatura_valida(corpo, assino(corpo, segredo), segredo)
    item("S6 assinatura valida e' aceita", ok is True)
    ok, motivo = mod.assinatura_valida(corpo, assino(corpo, "outro-segredo"), segredo)
    item("S7 HMAC de outro segredo e' recusado", ok is False and "nao casa" in motivo)
    ok, motivo = mod.assinatura_valida(corpo, None, segredo)
    item("S8 assinatura ausente e' recusada", ok is False and "ausente" in motivo)
    ok, motivo = mod.assinatura_valida(corpo, "sha1=deadbeef", segredo)
    item("S9 assinatura com prefixo fora do declarado e' recusada", ok is False and "prefixo" in motivo)
    ok, motivo = mod.assinatura_valida(corpo, assino(corpo, segredo), "")
    item("S10 sem app secret a conferencia e' fail-closed", ok is False and "app secret" in motivo)
    ok, _ = mod.assinatura_valida(corpo, assino(corpo, segredo).upper(), segredo)
    item("S11 HMAC em caixa alta e' aceito (comparacao estavel)", ok is True)

    notificacoes = mod.extrair_notificacoes(webhook("lead-9"))
    item("S12 webhook do objeto page vira notificacao (page/leadgen/form/ad)",
         len(notificacoes) == 1 and notificacoes[0]["leadgen_id"] == "lead-9"
         and notificacoes[0]["form_id"] == "form-1" and notificacoes[0]["ad_id"] == "ad-1")
    item("S13 entrega com outro 'field' nao vira notificacao",
         mod.extrair_notificacoes(webhook("lead-9", campo="feed")) == [])
    item("S14 chave de idempotencia distingue page e leadgen",
         mod.chave_de({"page_id": "p1", "leadgen_id": "l1"}) != mod.chave_de({"page_id": "p1", "leadgen_id": "l2"})
         and mod.chave_de({"page_id": "p1", "leadgen_id": "l1"}) == "meta-lead:p1:l1")

    normalizado = mod.normalizar_lead(payload_lead(), contrato)
    item("S15 campo do mapa vira campo canonico (phone_number -> phone)",
         normalizado["valores"].get("phone") == "+55 (11) 98888-7777"
         and normalizado["valores"].get("company_name") == "Distribuidora Alfa")
    item("S16 campo fora do mapa e' REGISTRADO pelo nome, sem valor",
         [d["campo"] for d in normalizado["desconhecidos"]] == ["whatsapp_optin"]
         and all("valores" in d and "value" not in d for d in normalizado["desconhecidos"]))

    contato = mod.contato_do_lead(normalizado, contrato)
    item("S17 contato normaliza e-mail (minusculo) e telefone (digitos)",
         contato["email"] == "joao.souza@cliente-demo.test" and contato["phone"] == "5511988887777")
    so_email = mod.contato_do_lead(mod.normalizar_lead(payload_lead(phone=None), contrato), contrato)
    item("S18 so e-mail basta (telefone nao e obrigatorio)", so_email["email"] and so_email["phone"] is None)
    so_fone = mod.contato_do_lead(mod.normalizar_lead(payload_lead(email=None), contrato), contrato)
    item("S19 so telefone basta (e-mail nao e obrigatorio)", so_fone["phone"] == "5511988887777")
    try:
        mod.contato_do_lead(mod.normalizar_lead(payload_lead(email=None, phone=None), contrato), contrato)
        item("S20 sem e-mail e sem telefone RECUSA (DADOS_INSUFICIENTES)", False, "nao recusou")
    except mod.Recusa as e:
        item("S20 sem e-mail e sem telefone RECUSA (DADOS_INSUFICIENTES)", e.motivo == "DADOS_INSUFICIENTES",
             e.motivo)
    try:
        mod.contato_do_lead(mod.normalizar_lead(payload_lead(email="sem-arroba", phone=None), contrato), contrato)
        item("S21 e-mail fora de formato + sem telefone RECUSA", False, "nao recusou")
    except mod.Recusa as e:
        item("S21 e-mail fora de formato + sem telefone RECUSA", e.motivo == "DADOS_INSUFICIENTES")
    misto = mod.contato_do_lead(mod.normalizar_lead(payload_lead(email="sem-arroba"), contrato), contrato)
    item("S22 e-mail invalido mas telefone valido: o telefone sustenta o vinculo",
         misto["email"] is None and misto["phone"] == "5511988887777")

    resumo = mod.resumo_conteudo(normalizado)
    item("S23 resumo NAO expoe e-mail em claro", "joao.souza@cliente-demo.test" not in resumo)
    item("S24 resumo NAO expoe telefone em claro", "98888" not in resumo and "7777" not in resumo)
    item("S25 resumo declara presenca mascarada e campos desconhecidos",
         "email: presente (mascarado)" in resumo and "campos_desconhecidos: whatsapp_optin" in resumo)
    item("S26 resumo mantem o contexto nao-PII (empresa)",
         "empresa: Distribuidora Alfa" in resumo)

    print("== A. auditoria da propria fonte")
    item("A1 fonte real nao tem escrita proibida nem INSERT fora das 2 tabelas", mod.auditar_fonte(str(MODULO)) == [])
    mutante = Path(tempfile.mkdtemp(prefix="w7e02-fonte-")) / "mutante.py"
    mutante.write_text(MODULO.read_text(encoding="utf-8")
                       + '\nSQL = "INSERT INTO sales_intelligence.contacts (id) VALUES (1)"\n', encoding="utf-8")
    violacoes = mod.auditar_fonte(str(mutante))
    item("A2 auditoria ACUSA INSERT fora de interactions/sync_events",
         any(v["padrao"] == "insert_fora_das_tabelas" for v in violacoes), str(violacoes)[:120])

    print("== G. primitivo da Graph API contra o sink local (127.0.0.1)")
    config = config_de_teste(mod, sink.base, token, segredo)
    payload, chamadas = mod.puxar_lead(config, contrato, "lead-1")
    item("G1 lead valido e' lido em 1 chamada", payload.get("id") == "lead-1" and chamadas == 1)
    linhas = sink.linhas()
    item("G2 o token vai no cabecalho Authorization e NAO na URL",
         linhas and linhas[-1]["authorization_presente"] is True and token not in linhas[-1]["caminho"])
    item("G3 o caminho da leitura segue <versao>/<leadgen_id>",
         linhas[-1]["caminho"].endswith("/v21.0/lead-1"))
    try:
        mod.puxar_lead(config, contrato, "lead-desconhecido")
        item("G4 lead inexistente vira LEAD_INDISPONIVEL (404, sem retry)", False, "nao recusou")
    except mod.Recusa as e:
        item("G4 lead inexistente vira LEAD_INDISPONIVEL (404, sem retry)",
             e.motivo == "LEAD_INDISPONIVEL" and sink.chamadas("lead-desconhecido") == 1)
    token_ruim = mod.Configuracao({**{k: v for k, v in os.environ.items()},
                                   "TRE_META_ACCESS_TOKEN": "token-errado"})
    token_ruim.graph_base = sink.base
    token_ruim.graph_versao = "v21.0"
    token_ruim.timeout = ""
    antes = sink.chamadas("lead-1")
    try:
        mod.puxar_lead(token_ruim, contrato, "lead-1")
        item("G5 credencial recusada (401) e' erro definitivo, sem retry", False, "nao recusou")
    except mod.Recusa as e:
        item("G5 credencial recusada (401) e' erro definitivo, sem retry",
             e.motivo == "ERRO_GRAPH" and sink.chamadas("lead-1") - antes == 1)
    item("G6 nenhuma chamada do componente carrega o token na URL registrada",
         all(token not in l["caminho"] for l in sink.linhas()))

    print("== C. guardas de ambiente e CLI (subprocesso, exit code medido)")
    env = ambiente_cli(mod, sink.base, token, segredo)
    r = roda_cli(mod, ["--planejar"], env)
    item("C1 --planejar com dev completo (exit 0)", r.returncode == 0, f"rc={r.returncode}")
    item("C2 --planejar mascara os segredos (nem token nem app secret na saida)",
         token not in r.stdout and segredo not in r.stdout)
    r = roda_cli(mod, ["--conferir"], env)
    item("C3 --conferir limpo (exit 0)", r.returncode == 0, f"rc={r.returncode} {r.stdout[-200:]}")
    r = roda_cli(mod, ["--ingerir", "--webhook", "/tmp/nao-existe.jsonl", "--chave-idempotencia", "x"], env)
    item("C4 --ingerir sem --confirmo e' DRY_RUN (exit 0, sem tocar banco/Graph)",
         r.returncode == 0 and "DRY_RUN_OK" in r.stdout)
    r = roda_cli(mod, ["--conferir"], {**env, "TRE_AMBIENTE": "prod"})
    item("C5 prod RECUSA por desenho (exit 4)", r.returncode == 4, f"rc={r.returncode}")
    r = roda_cli(mod, ["--conferir"], {**env, "TRE_META_PORTA_BANCO": "ssh host psql -U sales_ai -d sales_intelligence"})
    item("C6 porta de banco remota RECUSA em dev (BANCO_NAO_E_DEV, exit 3)",
         r.returncode == 3 and "BANCO_NAO_E_DEV" in r.stdout, f"rc={r.returncode}")
    r = roda_cli(mod, ["--conferir"], {**env, "TRE_META_GRAPH_BASE": "https://graph.facebook.com"})
    item("C7 Graph API fora de loopback RECUSA em dev (GRAPH_NAO_E_DEV, exit 3)",
         r.returncode == 3 and "GRAPH_NAO_E_DEV" in r.stdout, f"rc={r.returncode}")
    r = roda_cli(mod, ["--conferir"], {**env, "TRE_AMBIENTE": "homolog", "TRE_META_APROVACAO_HUMANA": ""})
    item("C8 homolog sem aprovacao registrada RECUSA (exit 3)",
         r.returncode == 3 and "HOMOLOG_SEM_APROVACAO" in r.stdout, f"rc={r.returncode}")


def autoteste(mod, contrato: dict) -> None:
    """Muta copia do componente e EXIGE que o item esperado reprove sem a mutacao (prova de dente)."""
    print("== D. autoteste por mutacao (o item tem de reprovar sem a regra)")
    fonte = MODULO.read_text(encoding="utf-8")
    base_dir = Path(tempfile.mkdtemp(prefix="w7e02-mut-"))
    token, segredo = "tok-autoteste", "seg-autoteste"

    def assinatura_ok(m) -> bool:
        corpo = json.dumps(webhook("lead-1"), sort_keys=True).encode()
        return m.assinatura_valida(corpo, "sha256=" + "0" * 64, segredo)[0] is False

    def insuficiente_ok(m) -> bool:
        try:
            m.contato_do_lead(m.normalizar_lead(payload_lead(email=None, phone=None), contrato), contrato)
            return False
        except m.Recusa as e:
            return e.motivo == "DADOS_INSUFICIENTES"

    def pii_ok(m) -> bool:
        resumo = m.resumo_conteudo(m.normalizar_lead(payload_lead(), contrato))
        return "joao.souza@cliente-demo.test" not in resumo.lower() and "98888" not in resumo

    def chave_ok(m) -> bool:
        return m.chave_de({"page_id": "p1", "leadgen_id": "l1"}) != m.chave_de({"page_id": "p1", "leadgen_id": "l2"})

    def desconhecido_ok(m) -> bool:
        n = m.normalizar_lead(payload_lead(), contrato)
        return [d["campo"] for d in n["desconhecidos"]] == ["whatsapp_optin"]

    def auditoria_ok(m, contexto) -> bool:
        return any(v["padrao"] == "insert_fora_das_tabelas" for v in m.auditar_fonte(str(contexto)))

    mutacoes = [
        ("assinatura", "if not hmac.compare_digest(esperado.lower(), texto.lower()):", "if False:",
         "S7 HMAC de outro segredo e' recusado", assinatura_ok),
        ("dados_insuficientes", 'if not contato["email"] and not contato["phone"]:', "if False:",
         "S20 sem e-mail e sem telefone RECUSA", insuficiente_ok),
        ("pii_no_resumo", 'partes.append("email: presente (mascarado)" if valores.get("email") else "email: ausente")',
         'partes.append("email: " + (valores.get("email") or "ausente"))',
         "S23 resumo NAO expoe e-mail em claro", pii_ok),
        ("chave_fixa", "return f\"{PADRAO_CHAVE}{notificacao['page_id']}:{notificacao['leadgen_id']}\"",
         'return PADRAO_CHAVE + "fixa"', "S14 chave distingue page e leadgen", chave_ok),
        ("desconhecido_ignorado", 'desconhecidos.append({"campo": nome, "valores": len(valores_enviados)})', "pass",
         "S16 campo fora do mapa e' registrado pelo nome", desconhecido_ok),
        ("auditoria_insert", 'for alvo_insert in re.findall(r"INSERT INTO\\s+([A-Za-z0-9_.{}]+)", corpo):',
         "for alvo_insert in []:", "A2 auditoria ACUSA INSERT fora das 2 tabelas", auditoria_ok),
    ]
    for indice, (nome, antigo, novo, item_esperado, verificador) in enumerate(mutacoes):
        if antigo not in fonte:
            item(f"D{indice} mutacao '{nome}' aplicavel", False, "ancora de texto nao encontrada na fonte")
            continue
        destino = base_dir / f"{indice}-{nome}.py"
        texto = fonte.replace(antigo, novo, 1)
        if nome == "chave_fixa":
            texto = texto.replace("PADRAO_CHAVE = \"meta-lead:\"", "PADRAO_CHAVE = \"meta-lead:\"", 1)
        destino.write_text(texto, encoding="utf-8")
        try:
            m = carregar(destino)
        except Exception as e:  # noqa: BLE001
            item(f"D{indice} mutacao '{nome}' carrega", False, f"{type(e).__name__}: {e}"[:160])
            continue
        contexto = None
        if nome == "auditoria_insert":
            contexto = destino
            contexto.write_text(texto + '\nSQL = "INSERT INTO sales_intelligence.contacts (id) VALUES (1)"\n',
                                encoding="utf-8")
        try:
            passou = verificador(m, contexto) if contexto else verificador(m)
        except Exception as e:  # noqa: BLE001
            passou = False
            print(f"       (mutante '{nome}' levantou {type(e).__name__}: {str(e)[:100]})")
        item(f"D{indice} mutacao '{nome}' e' DETECTADA (item '{item_esperado}' reprova)", not passou,
             "o item continuou passando com a regra removida" if passou else "")
    shutil.rmtree(base_dir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verificador da ingestao de leads Meta v1 (TRE-W7-E02-T01)")
    parser.add_argument("--autoteste", action="store_true", help="muta copia do componente e exige o dente")
    args = parser.parse_args()
    for caminho in (MODULO, CONTRATO, STUB):
        if not caminho.exists():
            print(f"NAO_TESTAVEL artefato ausente: {caminho}")
            return 3
    mod = carregar(MODULO)
    contrato = mod.carregar_contrato(str(CONTRATO))
    token, segredo = "tok-sink-local", "segredo-app-local"
    sink = SinkGraph({"lead-1": payload_lead(), "lead-em-500": payload_lead()}, token, falhar_primeira="lead-em-500")
    try:
        suite(mod, contrato, sink, token, segredo)
        print("== R. retry limitado")
        config = config_de_teste(mod, sink.base, token, segredo)
        payload, chamadas = mod.puxar_lead(config, contrato, "lead-em-500")
        item("R1 500 na primeira tentativa: retenta UMA vez e sucede (2 chamadas)",
             payload.get("form_id") == "form-1" and chamadas == 2, f"chamadas={chamadas}")
        if args.autoteste:
            # o teto e' medido com 500 SEMPRE: o original para no teto do contrato; o mutante (7) nao.
            fonte = MODULO.read_text(encoding="utf-8")
            destino = Path(tempfile.mkdtemp(prefix="w7e02-retry-")) / "sem-teto.py"
            destino.write_text(fonte.replace('tentativas = int(graph.get("tentativas") or 2)', "tentativas = 7", 1),
                               encoding="utf-8")
            m = carregar(destino)
            sink2 = SinkGraph({"lead-1": payload_lead()}, token, falhar_sempre="lead-1")
            try:
                m.puxar_lead(config_de_teste(m, sink2.base, token, segredo), contrato, "lead-1")
                teto_do_mutante = sink2.chamadas("lead-1")
            except Exception:  # noqa: BLE001
                teto_do_mutante = sink2.chamadas("lead-1")
            sink2.parar()
            sink3 = SinkGraph({"lead-1": payload_lead()}, token, falhar_sempre="lead-1")
            try:
                mod.puxar_lead(config_de_teste(mod, sink3.base, token, segredo), contrato, "lead-1")
                teto_do_original = sink3.chamadas("lead-1")
            except Exception:  # noqa: BLE001
                teto_do_original = sink3.chamadas("lead-1")
            sink3.parar()
            item("R2 o teto do contrato vale no original e a mutacao 'retry_sem_teto' e' DETECTADA",
                 teto_do_original == contrato["graph"]["tentativas"] and teto_do_mutante != teto_do_original,
                 f"original={teto_do_original} mutante={teto_do_mutante} "
                 f"(contrato={contrato['graph']['tentativas']})")
            shutil.rmtree(destino.parent, ignore_errors=True)
            autoteste(mod, contrato)
    finally:
        sink.parar()
    print(f"\nRESULTADO: {'PASS' if ITENS_FALHOU == 0 else 'FALHOU'} ({ITENS_OK} OK / {ITENS_FALHOU} FALHOU)")
    if FALHAS:
        print("itens reprovados: " + "; ".join(FALHAS))
    return 0 if ITENS_FALHOU == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
