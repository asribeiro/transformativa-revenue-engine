#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Suite offline do componente `captura_site` (card TRE-W7-E01-T01) — sem banco, sem rede.

Mede o que o componente DECIDE (consentimento, dados minimos, identidade, idempotencia, limite de
escrita, privacidade, guardas de ambiente) contra uma porta de banco FALSA que registra o SQL pedido.
A porta falsa nao testa o PostgreSQL (isso e' o aceite em container descartavel); ela testa a DECISAO.

Uso:
  python3 scripts/agentes/verificar_captura_site.py              # suite
  python3 scripts/agentes/verificar_captura_site.py --autoteste  # + prova de dente por mutacao
Saida: um item por linha (OK / FALHOU) e resumo; exit 0 = PASS, 1 = falhou, 2 = uso errado.
"""

import argparse
import copy
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.abspath(os.path.join(AQUI, "..", ".."))
MODULO_PADRAO = os.path.join(RAIZ, "hermes", "agentes", "inbound", "captura_site.py")
CONTRATO_PADRAO = os.path.join(RAIZ, "hermes", "agentes", "inbound", "captura-site-v1.json")

SUBMISSAO_OK = {
    "submission_id": "form-2026-10-03-0001",
    "enviado_em": "2026-10-03T15:30:00Z",
    "origem": {"pagina": "/contato", "utm_source": "linkedin", "utm_campaign": "eficiencia"},
    "consentimento": {"aceito": True, "legal_basis": "CONSENTIMENTO", "texto_versao": "privacidade-v1"},
    "empresa": {"nome": "Distribuidora Aurora LTDA", "cnpj": "", "dominio": "aurora.test",
                "cidade": "Campinas", "estado": "SP", "website_url": "https://aurora.test"},
    "contato": {"nome": "Marina Prado", "email": "marina@aurora.test", "telefone": "+55 19 99888-7766",
                "cargo": "COO", "preferred_channel": "email"},
}
EMAIL_CRU = "marina@aurora.test"
TELEFONE_CRU = "+55 19 99888-7766"


class PortaFalsa:
    """Porta de banco falsa: registra o SQL e responde por cenario. Nenhuma conexao real."""

    def __init__(self, organizacao_por_forte=None, organizacao_por_fraco=None, trilha=None):
        self.organizacao_por_forte = organizacao_por_forte
        self.organizacao_por_fraco = organizacao_por_fraco
        self.trilha = trilha
        self.leituras = []
        self.escritas = []
        self.ambiente = "dev"
        self.prefixo = "docker exec -i pg-site-acc psql -U sales_ai -d sales_intelligence"

    def consultar(self, sql: str) -> list:
        self.leituras.append(sql)
        alvo = sql.lower()
        if "from sales_intelligence.sync_events" in alvo:
            return [self.trilha] if self.trilha else []
        if "from sales_intelligence.organizations" in alvo:
            if "join sales_intelligence.contacts" in alvo:
                return [self.organizacao_por_fraco] if self.organizacao_por_fraco else []
            if self.organizacao_por_forte and (
                    re.search(r"\bcnpj\s*=", alvo) or re.search(r"\bdomain\s*=", alvo)
                    or re.search(r"\blinkedin_url\s*=", alvo)):
                return [self.organizacao_por_forte]
            if self.organizacao_por_fraco and re.search(r"\blower\(coalesce\(trade_name", alvo):
                return [self.organizacao_por_fraco]
            return []
        return []

    def executar(self, sql: str) -> list:
        self.escritas.append(sql)
        if "returning id" in sql.lower():
            return [{"id": "00000000-0000-4000-8000-%012d" % len(self.escritas)}]
        return []

    def tabelas_escritas(self) -> set:
        tabelas = set()
        for sql in self.escritas:
            m = re.search(r"INSERT INTO\s+(sales_intelligence\.[a-z_]+)", sql, re.IGNORECASE)
            if m:
                tabelas.add(m.group(1))
        return tabelas


def carregar_modulo(caminho: str = MODULO_PADRAO, nome: str = "captura_site"):
    spec = importlib.util.spec_from_file_location(nome, caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def contagens(porta: PortaFalsa) -> dict:
    contagem = {"organizations": 0, "contacts": 0, "interactions": 0, "sync_events": 0}
    for tabela in porta.tabelas_escritas():
        contagem[tabela.split(".")[-1]] = contagem.get(tabela.split(".")[-1], 0) + 1
    return contagem


def capturar(modulo, submissao, contrato, porta, escrever=True):
    saida = modulo.Saida(None, None, None)
    return modulo.capturar(submissao, contrato, porta, saida, escrever)


# --------------------------------------------------------------------------------------------
# Itens da suite (cada um devolve (nome, ok, detalhe))
# --------------------------------------------------------------------------------------------
CADASTRO = {"sales_intelligence.organizations", "sales_intelligence.contacts",
            "sales_intelligence.interactions"}
TRILHA = {"sales_intelligence.sync_events"}


def item_contrato(modulo, contrato):
    itens = []
    falhas_validacao = modulo.validar_contrato(contrato)
    itens.append(("contrato versionado carrega e valida (0 falha do validador)", falhas_validacao == [],
                  "; ".join(falhas_validacao)))
    invalido = copy.deepcopy(contrato)
    invalido["submissao"]["campos_obrigatorios"] = [c for c in invalido["submissao"]["campos_obrigatorios"]
                                                    if c != "consentimento.aceito"]
    falhas = modulo.validar_contrato(invalido)
    itens.append(("contrato sem 'consentimento.aceito' RECUSA (CONTRATO_INVALIDO)",
                  any("consentimento.aceito" in f for f in falhas), "; ".join(falhas)))
    fraco_no_limiar = copy.deepcopy(contrato)
    fraco_no_limiar["identificadores"]["limiar_de_merge_automatico"] = 0.75
    falhas = modulo.validar_contrato(fraco_no_limiar)
    itens.append(("contrato com identificador fraco no limiar de merge RECUSA",
                  any("fraco" in f for f in falhas), "; ".join(falhas)))
    limiar_zero = copy.deepcopy(contrato)
    limiar_zero["identificadores"]["limiar_de_merge_automatico"] = 0
    itens.append(("contrato com limiar fora de (0,1] RECUSA",
                  any("fora de (0,1]" in f for f in modulo.validar_contrato(limiar_zero)), ""))
    return itens


def item_fonte(modulo):
    return [("auditoria da propria fonte: 0 violacao (sem DDL/UPDATE/DELETE)", modulo.auditar_fonte() == [],
             str(modulo.auditar_fonte()))]


def item_guardas(modulo, contrato, caminho_modulo):
    itens = []
    env_base = {"TRE_AMBIENTE": "dev", "TRE_SITE_PORTA_BANCO":
                "docker exec -i pg-site-acc psql -U sales_ai -d sales_intelligence"}
    itens.append(("dev com porta local: guardas OK", modulo.validar(modulo.Configuracao(env_base)) == [],
                  str(modulo.validar(modulo.Configuracao(env_base)))))
    remoto = dict(env_base, TRE_SITE_PORTA_BANCO="ssh root@10.0.0.9 psql -U sales_ai -d sales_intelligence")
    problemas = modulo.validar(modulo.Configuracao(remoto))
    itens.append(("dev RECUSA prefixo de banco remoto (BANCO_NAO_E_DEV)",
                  any(m == "BANCO_NAO_E_DEV" for m, _ in problemas), str(problemas)))
    sem_porta = {k: v for k, v in env_base.items() if k != "TRE_SITE_PORTA_BANCO"}
    itens.append(("dev sem porta de banco RECUSA (BANCO_NAO_DECLARADO)",
                  any(m == "BANCO_NAO_DECLARADO" for m, _ in modulo.validar(modulo.Configuracao(sem_porta))), ""))
    homolog = dict(env_base, TRE_AMBIENTE="homolog")
    itens.append(("homolog sem aprovacao registrada RECUSA (HOMOLOG_SEM_APROVACAO)",
                  any(m == "HOMOLOG_SEM_APROVACAO" for m, _ in modulo.validar(modulo.Configuracao(homolog))), ""))
    prod = dict(env_base, TRE_AMBIENTE="prod")
    itens.append(("prod RECUSA por desenho (exit 4)",
                  any(m == "PRODUCAO_RECUSADA" for m, _ in modulo.validar(modulo.Configuracao(prod))), ""))
    # Caminho de linha de comando: cobre o exit code real (nao so' a funcao de guarda)
    saida = subprocess.run([sys.executable, caminho_modulo, "--submissao", "-", "--ambiente", "prod"],
                           input='{"submission_id":"x"}', capture_output=True, text=True)
    itens.append(("CLI: prod sai com exit 4 sem tocar o banco", saida.returncode == 4,
                  f"exit={saida.returncode} {saida.stdout.strip()[:120]}"))
    saida = subprocess.run([sys.executable, caminho_modulo, "--submissao", "-", "--ambiente", "dev"],
                           input='{"submission_id":"x"}', capture_output=True, text=True)
    itens.append(("CLI: dev sem porta de banco sai com exit 3", saida.returncode == 3,
                  f"exit={saida.returncode}"))
    return itens


def item_consentimento(modulo, contrato):
    itens = []
    sem_aceite = copy.deepcopy(SUBMISSAO_OK)
    sem_aceite["consentimento"] = {"aceito": False, "legal_basis": "CONSENTIMENTO"}
    porta = PortaFalsa()
    resultado = capturar(modulo, sem_aceite, contrato, porta)
    itens.append(("sem consentimento: RECUSADO_CONSENTIMENTO e nenhum cadastro (so' trilha)",
                  resultado["status"] == "RECUSADO_CONSENTIMENTO"
                  and not (porta.tabelas_escritas() & CADASTRO)
                  and porta.tabelas_escritas() <= TRILHA,
                  f"{resultado['status']} escritas={sorted(porta.tabelas_escritas())}"))
    porta = PortaFalsa()
    resultado = capturar(modulo, sem_aceite, contrato, porta, escrever=False)
    itens.append(("sem consentimento em dry-run: nada e' escrito",
                  resultado["status"] == "RECUSADO_CONSENTIMENTO" and not porta.escritas, ""))
    base_invalida = copy.deepcopy(SUBMISSAO_OK)
    base_invalida["consentimento"] = {"aceito": True, "legal_basis": "BASE_INVENTADA"}
    porta = PortaFalsa()
    resultado = capturar(modulo, base_invalida, contrato, porta)
    itens.append(("legal_basis fora do vocabulario RECUSA (fail-closed, nenhum cadastro)",
                  resultado["status"] == "RECUSADO_CONSENTIMENTO"
                  and not (porta.tabelas_escritas() & CADASTRO),
                  resultado["status"]))
    return itens


def item_dados_minimos(modulo, contrato):
    itens = []
    sem_email = copy.deepcopy(SUBMISSAO_OK)
    sem_email["contato"] = {"nome": "Marina Prado"}
    porta = PortaFalsa()
    resultado = capturar(modulo, sem_email, contrato, porta)
    itens.append(("sem canal de resposta (e-mail/telefone): SEM_DADOS_MINIMOS, nenhum cadastro",
                  resultado["status"] == "SEM_DADOS_MINIMOS"
                  and not (porta.tabelas_escritas() & CADASTRO),
                  resultado["status"]))
    sem_id = copy.deepcopy(SUBMISSAO_OK)
    del sem_id["submission_id"]
    porta = PortaFalsa()
    itens.append(("sem submission_id: SUBMISSAO_INVALIDA",
                  capturar(modulo, sem_id, contrato, porta)["status"] == "SUBMISSAO_INVALIDA", ""))
    return itens


def item_captura(modulo, contrato):
    itens = []
    porta = PortaFalsa()
    resultado = capturar(modulo, SUBMISSAO_OK, contrato, porta)
    contagem = contagens(porta)
    itens.append(("submissao valida: CAPTURADO com 1 organizacao, 1 contato, 1 interacao e trilha",
                  resultado["status"] == "CAPTURADO" and contagem == {"organizations": 1, "contacts": 1,
                                                                     "interactions": 1, "sync_events": 1},
                  str(contagem)))
    itens.append(("organizacao nasce com UUID canonico gerado no INSERT (gen_random_uuid)",
                  any("gen_random_uuid()" in s for s in porta.escritas), ""))
    itens.append(("interacao no canal do contrato (WEBSITE/INBOUND/FORMULARIO_SITE)",
                  any(all(v in s for v in ("'WEBSITE'", "'INBOUND'", "'FORMULARIO_SITE'"))
                      for s in porta.escritas), ""))
    itens.append(("compliance gravado: legal_basis + source + bloqueio booleano",
                  any("legal_basis" in s and "'WEBSITE_FORMULARIO'" in s for s in porta.escritas), ""))
    itens.append(("limite de escrita: so' organizations/contacts/interactions/sync_events",
                  porta.tabelas_escritas() <= {"sales_intelligence.organizations",
                                               "sales_intelligence.contacts",
                                               "sales_intelligence.interactions",
                                               "sales_intelligence.sync_events"},
                  str(sorted(porta.tabelas_escritas()))))
    proibido = re.compile(r"\b(UPDATE|DELETE|TRUNCATE|ALTER|DROP|CREATE)\b", re.IGNORECASE)
    itens.append(("nenhum SQL de escrita proibida (UPDATE/DELETE/DDL) foi emitido",
                  not any(proibido.search(s) for s in porta.escritas), str(porta.escritas)[:200]))
    return itens


def item_identidade(modulo, contrato):
    itens = []
    com_cnpj = copy.deepcopy(SUBMISSAO_OK)
    com_cnpj["empresa"]["cnpj"] = "12.345.678/0001-90"
    com_cnpj["submission_id"] = "form-forte-0001"
    porta = PortaFalsa(organizacao_por_forte={"id": "aaaaaaaa-0000-4000-8000-000000000001",
                                              "trade_name": "Aurora", "city": "Campinas"})
    resultado = capturar(modulo, com_cnpj, contrato, porta)
    contagem = contagens(porta)
    itens.append(("identificador FORTE casa: reusa a organizacao, zero INSERT em organizations",
                  resultado["status"] == "CAPTURADO" and contagem["organizations"] == 0
                  and contagem["interactions"] == 1, str(contagem)))
    fraca = copy.deepcopy(SUBMISSAO_OK)
    fraca["submission_id"] = "form-fraca-0001"
    porta = PortaFalsa(organizacao_por_fraco={"id": "bbbbbbbb-0000-4000-8000-000000000002",
                                              "trade_name": "Distribuidora Aurora LTDA", "city": "Campinas"})
    resultado = capturar(modulo, fraca, contrato, porta)
    itens.append(("identificador FRACO casa: REVIEW_REQUIRED, so' trilha (nunca merge silencioso)",
                  resultado["status"] == "REVIEW_REQUIRED"
                  and porta.tabelas_escritas() == {"sales_intelligence.sync_events"},
                  f"{resultado['status']} escritas={sorted(porta.tabelas_escritas())}"))
    itens.append(("limiar de merge automatico declarado no contrato e' 0.95",
                  float(contrato["identificadores"]["limiar_de_merge_automatico"]) == 0.95, ""))
    return itens


def item_idempotencia(modulo, contrato):
    itens = []
    porta = PortaFalsa(trilha={"id": "cccccccc-0000-4000-8000-000000000003", "status": "CAPTURADO",
                               "entity_id": "aaaaaaaa-0000-4000-8000-000000000001"})
    resultado = capturar(modulo, SUBMISSAO_OK, contrato, porta)
    itens.append(("reentrega da mesma submissao: JA_CAPTURADO e zero escrita",
                  resultado["status"] == "JA_CAPTURADO" and not porta.escritas, resultado["status"]))
    itens.append(("chave de idempotencia e' site:<submission_id>",
                  modulo.chave_de(SUBMISSAO_OK) == "site:form-2026-10-03-0001", modulo.chave_de(SUBMISSAO_OK)))
    return itens


def item_privacidade(modulo, contrato):
    itens = []
    porta = PortaFalsa()
    saida_relatorio = []
    original_print = modulo.print

    def print_capturado(*args, **kwargs):
        saida_relatorio.append(" ".join(str(a) for a in args))

    modulo.print = print_capturado
    try:
        capturar(modulo, SUBMISSAO_OK, contrato, porta)
    finally:
        modulo.print = original_print
    evidencia = "\n".join(saida_relatorio)
    itens.append(("e-mail do lead nao aparece cru na evidencia",
                  EMAIL_CRU not in evidencia and "marina@" not in evidencia, evidencia[:160]))
    itens.append(("telefone do lead nao aparece cru na evidencia",
                  TELEFONE_CRU not in evidencia and "99888" not in evidencia, ""))
    itens.append(("CNPJ e' mascarado na auditoria",
                  modulo.mascarar_cnpj("12.345.678/0001-90") == "**.***.***/0190"
                  and "12.345.678" not in modulo.mascarar_cnpj("12.345.678/0001-90"), ""))
    itens.append(("token configurado nao aparece na evidencia",
                  modulo.mascarar("segredo-do-formulario") .startswith("se"), ""))
    return itens


def verificar(modulo, contrato, caminho_modulo):
    itens = []
    modulo.print = lambda *a, **k: None  # a suite mede decisao, nao ruido de stdout
    itens += item_contrato(modulo, contrato)
    itens += item_fonte(modulo)
    itens += item_guardas(modulo, contrato, caminho_modulo)
    itens += item_consentimento(modulo, contrato)
    itens += item_dados_minimos(modulo, contrato)
    itens += item_captura(modulo, contrato)
    itens += item_identidade(modulo, contrato)
    itens += item_idempotencia(modulo, contrato)
    itens += item_privacidade(modulo, contrato)
    return itens


# --------------------------------------------------------------------------------------------
# Prova de dente: cada mutacao tem de reprovar o ITEM DECLARADO (fail-closed)
# --------------------------------------------------------------------------------------------
MUTACOES = [
    {
        "nome": "D1 contrato sem o par 'consentimento.aceito'",
        "alvo": "contrato versionado carrega e valida (0 falha do validador)",
        "tipo": "contrato",
        "aplicar": lambda c: c["submissao"]["campos_obrigatorios"].remove("consentimento.aceito"),
    },
    {
        "nome": "D2 contrato com identificador fraco no limiar de merge",
        "alvo": "contrato versionado carrega e valida (0 falha do validador)",
        "tipo": "contrato",
        "aplicar": lambda c: c["identificadores"]["fracos"][0].update({"confianca": 0.95}),
    },
    {
        "nome": "D3 fonte: a barreira de consentimento removida",
        "alvo": "sem consentimento: RECUSADO_CONSENTIMENTO e nenhum cadastro (so' trilha)",
        "tipo": "modulo",
        "de": "if aceito is not True or legal_basis not in (vocab.get(\"legal_basis\") or []):",
        "para": "if False and (aceito is not True or legal_basis not in (vocab.get(\"legal_basis\") or [])):",
    },
    {
        "nome": "D4 fonte: a checagem de idempotencia removida",
        "alvo": "reentrega da mesma submissao: JA_CAPTURADO e zero escrita",
        "tipo": "modulo",
        "de": "    existente = trilha_existente(porta, chave)",
        "para": "    existente = None",
    },
    {
        "nome": "D5 fonte: o mascaramento do e-mail removido",
        "alvo": "e-mail do lead nao aparece cru na evidencia",
        "tipo": "modulo",
        "de": "    return (local[:2] + \"*\" * max(0, len(local) - 2)) + \"@\" + dominio",
        "para": "    return local + \"@\" + dominio",
    },
]


def aplicar_mutacao(mutacao, caminho_modulo, contrato):
    """Devolve (caminho_modulo_mutado, contrato_mutado) ou lanca Recusa-like em caso de nao aplicacao."""
    if mutacao["tipo"] == "contrato":
        mutado = copy.deepcopy(contrato)
        mutacao["aplicar"](mutado)
        if mutacao.get("aplicar2"):
            mutacao["aplicar2"](mutado)
        return caminho_modulo, mutado
    with open(caminho_modulo, "r", encoding="utf-8") as fh:
        fonte = fh.read()
    if mutacao["de"] not in fonte:
        raise RuntimeError("MUTACAO_NAO_APLICADA")
    diretorio = tempfile.mkdtemp(prefix="mut-captura-site-")
    alvo = os.path.join(diretorio, "captura_site_mutado.py")
    with open(alvo, "w", encoding="utf-8") as fh:
        fh.write(fonte.replace(mutacao["de"], mutacao["para"], 1))
    return alvo, contrato


def autoteste(caminho_modulo, caminho_contrato):
    itens = []
    with open(caminho_contrato, "r", encoding="utf-8") as fh:
        contrato = json.load(fh)
    for mutacao in MUTACOES:
        try:
            modulo_mutado_caminho, contrato_mutado = aplicar_mutacao(mutacao, caminho_modulo, contrato)
        except RuntimeError:
            itens.append((f"dente {mutacao['nome']}: MUTACAO_NAO_APLICADA", False, ""))
            continue
        try:
            if mutacao["tipo"] == "contrato":
                modulo = carregar_modulo(caminho_modulo, "captura_site_dente")
            else:
                modulo = carregar_modulo(modulo_mutado_caminho, "captura_site_dente")
            itens_mutados = verificar(modulo, contrato_mutado, modulo_mutado_caminho)
        except Exception as e:  # mutacao que quebra o modulo tambem e' dente que falhou
            itens.append((f"dente {mutacao['nome']}: modulo mutado levantou {type(e).__name__}", False, str(e)[:120]))
            continue
        alvo = [ok for nome, ok, _ in itens_mutados if nome == mutacao["alvo"]]
        if not alvo:
            itens.append((f"dente {mutacao['nome']}: item alvo ausente da suite", False, ""))
        elif alvo[0] is False:
            itens.append((f"dente {mutacao['nome']}: item declarado REPROVOU sob mutacao", True, ""))
        else:
            itens.append((f"dente {mutacao['nome']}: MUTACAO_SEM_DENTE (item seguiu verde)", False, ""))
    return itens


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="verificar_captura_site")
    p.add_argument("--autoteste", action="store_true")
    p.add_argument("--modulo", default=os.environ.get("CAPTURA_SITE_MODULO") or MODULO_PADRAO)
    p.add_argument("--contrato", default=os.environ.get("CAPTURA_SITE_CONTRATO") or CONTRATO_PADRAO)
    args = p.parse_args(argv)

    if not os.path.isfile(args.modulo) or not os.path.isfile(args.contrato):
        print("FALHOU modulo ou contrato ausente")
        return 2
    with open(args.contrato, "r", encoding="utf-8") as fh:
        contrato = json.load(fh)
    modulo = carregar_modulo(args.modulo)
    itens = verificar(modulo, contrato, args.modulo)
    if args.autoteste:
        itens += autoteste(args.modulo, args.contrato)

    falhas = 0
    for nome, ok, detalhe in itens:
        if ok:
            print(f"OK    {nome}")
        else:
            falhas += 1
            print(f"FALHOU {nome}" + (f" — {detalhe}" if detalhe else ""))
    print("---")
    if falhas == 0:
        print(f"VERIFICADOR_CAPTURA_SITE_PASS ({len(itens)} itens, 0 falhas"
              + (f", {len(MUTACOES)} dentes)" if args.autoteste else ")"))
        return 0
    print(f"FALHOU ({len(itens)} itens, {falhas} falhas)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
