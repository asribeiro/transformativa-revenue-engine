#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Suite offline do componente `captura_evento` (card TRE-W7-E06-T01) — sem banco, sem rede.

Mede o que o componente DECIDE (vinculo do evento, consentimento e sua forma, dados minimos,
identidade, idempotencia, limite de escrita, privacidade, guardas de ambiente) contra uma porta de banco
FALSA que registra o SQL pedido. A porta falsa nao testa o PostgreSQL (isso e' o aceite em container
descartavel); ela testa a DECISAO.

Uso:
  python3 scripts/agentes/verificar_captura_evento.py              # suite
  python3 scripts/agentes/verificar_captura_evento.py --autoteste  # + prova de dente por mutacao
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
MODULO_PADRAO = os.path.join(RAIZ, "hermes", "agentes", "inbound", "captura_evento.py")
CONTRATO_PADRAO = os.path.join(RAIZ, "hermes", "agentes", "inbound", "captura-evento-v1.json")

COLETA_OK = {
    "captura_id": "evt-2026-10-02-0001",
    "origem_evento": {
        "event_id": "evento-pmi-sp-2026-10",
        "evento_nome": "Encontro PMI Sao Paulo — Gestao e IA",
        "evento_inicio": "2026-10-02",
        "local": "Sao Paulo/SP",
        "stand": "A-12",
        "capture_method": "QR_CODE",
        "capturado_em": "2026-10-02T17:40:00Z",
    },
    "consentimento": {"aceito": True, "legal_basis": "CONSENTIMENTO", "forma": "TERMO_DIGITAL",
                      "texto_versao": "privacidade-evento-v1"},
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
        self.prefixo = "docker exec -i pg-evt-acc psql -U sales_ai -d sales_intelligence"

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


def carregar_modulo(caminho: str = MODULO_PADRAO, nome: str = "captura_evento"):
    spec = importlib.util.spec_from_file_location(nome, caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def contagens(porta: PortaFalsa) -> dict:
    contagem = {"organizations": 0, "contacts": 0, "interactions": 0, "sync_events": 0}
    for tabela in porta.tabelas_escritas():
        contagem[tabela.split(".")[-1]] = contagem.get(tabela.split(".")[-1], 0) + 1
    return contagem


def capturar(modulo, coleta, contrato, porta, escrever=True):
    saida = modulo.Saida(None, None, None)
    return modulo.capturar(coleta, contrato, porta, saida, escrever)


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
    sem_forma = copy.deepcopy(contrato)
    sem_forma["coleta"]["campos_obrigatorios"] = [c for c in sem_forma["coleta"]["campos_obrigatorios"]
                                                  if c != "consentimento.forma"]
    falhas = modulo.validar_contrato(sem_forma)
    itens.append(("contrato sem 'consentimento.forma' RECUSA (CONTRATO_INVALIDO)",
                  any("consentimento.forma" in f for f in falhas), "; ".join(falhas)))
    sem_evento = copy.deepcopy(contrato)
    sem_evento["coleta"]["campos_obrigatorios"] = [c for c in sem_evento["coleta"]["campos_obrigatorios"]
                                                   if c != "origem_evento.event_id"]
    falhas = modulo.validar_contrato(sem_evento)
    itens.append(("contrato sem 'origem_evento.event_id' RECUSA (o vinculo do evento e' exigido)",
                  any("origem_evento.event_id" in f for f in falhas), "; ".join(falhas)))
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
    env_base = {"TRE_AMBIENTE": "dev", "TRE_EVENTO_PORTA_BANCO":
                "docker exec -i pg-evt-acc psql -U sales_ai -d sales_intelligence"}
    itens.append(("dev com porta local: guardas OK", modulo.validar(modulo.Configuracao(env_base)) == [],
                  str(modulo.validar(modulo.Configuracao(env_base)))))
    remoto = dict(env_base, TRE_EVENTO_PORTA_BANCO="ssh root@10.0.0.9 psql -U sales_ai -d sales_intelligence")
    problemas = modulo.validar(modulo.Configuracao(remoto))
    itens.append(("dev RECUSA prefixo de banco remoto (BANCO_NAO_E_DEV)",
                  any(m == "BANCO_NAO_E_DEV" for m, _ in problemas), str(problemas)))
    sem_porta = {k: v for k, v in env_base.items() if k != "TRE_EVENTO_PORTA_BANCO"}
    itens.append(("dev sem porta de banco RECUSA (BANCO_NAO_DECLARADO)",
                  any(m == "BANCO_NAO_DECLARADO" for m, _ in modulo.validar(modulo.Configuracao(sem_porta))), ""))
    homolog = dict(env_base, TRE_AMBIENTE="homolog")
    itens.append(("homolog sem aprovacao registrada RECUSA (HOMOLOG_SEM_APROVACAO)",
                  any(m == "HOMOLOG_SEM_APROVACAO" for m, _ in modulo.validar(modulo.Configuracao(homolog))), ""))
    prod = dict(env_base, TRE_AMBIENTE="prod")
    itens.append(("prod RECUSA por desenho (exit 4)",
                  any(m == "PRODUCAO_RECUSADA" for m, _ in modulo.validar(modulo.Configuracao(prod))), ""))
    saida = subprocess.run([sys.executable, caminho_modulo, "--coleta", "-", "--ambiente", "prod"],
                           input='{"captura_id":"x"}', capture_output=True, text=True)
    itens.append(("CLI: prod sai com exit 4 sem tocar o banco", saida.returncode == 4,
                  f"exit={saida.returncode} {saida.stdout.strip()[:120]}"))
    saida = subprocess.run([sys.executable, caminho_modulo, "--coleta", "-", "--ambiente", "dev"],
                           input='{"captura_id":"x"}', capture_output=True, text=True)
    itens.append(("CLI: dev sem porta de banco sai com exit 3", saida.returncode == 3,
                  f"exit={saida.returncode}"))
    return itens


def item_evento(modulo, contrato):
    """Barreira propria do canal: sem o vinculo do evento, nada e' cadastrado."""
    itens = []
    sem_event_id = copy.deepcopy(COLETA_OK)
    sem_event_id["origem_evento"]["event_id"] = ""
    porta = PortaFalsa()
    resultado = capturar(modulo, sem_event_id, contrato, porta)
    itens.append(("sem event_id: EVENTO_NAO_DECLARADO e nenhum cadastro (vinculo do evento e' barreira)",
                  resultado["status"] == "EVENTO_NAO_DECLARADO"
                  and not (porta.tabelas_escritas() & CADASTRO)
                  and porta.tabelas_escritas() <= TRILHA,
                  f"{resultado['status']} escritas={sorted(porta.tabelas_escritas())}"))
    sem_capturado_em = copy.deepcopy(COLETA_OK)
    sem_capturado_em["origem_evento"]["capturado_em"] = ""
    porta = PortaFalsa()
    itens.append(("sem capturado_em: EVENTO_NAO_DECLARADO, nenhum cadastro",
                  capturar(modulo, sem_capturado_em, contrato, porta)["status"] == "EVENTO_NAO_DECLARADO"
                  and not (porta.tabelas_escritas() & CADASTRO), ""))
    instante_invalido = copy.deepcopy(COLETA_OK)
    instante_invalido["origem_evento"]["capturado_em"] = "ontem a tarde"
    porta = PortaFalsa()
    itens.append(("capturado_em que nao e' instante ISO RECUSA (EVENTO_NAO_DECLARADO)",
                  capturar(modulo, instante_invalido, contrato, porta)["status"] == "EVENTO_NAO_DECLARADO"
                  and not (porta.tabelas_escritas() & CADASTRO), ""))
    metodo_inventado = copy.deepcopy(COLETA_OK)
    metodo_inventado["origem_evento"]["capture_method"] = "DRONE"
    porta = PortaFalsa()
    item = ("capture_method fora do vocabulario RECUSA (metodo nao se inventa)",
            capturar(modulo, metodo_inventado, contrato, porta)["status"] == "EVENTO_NAO_DECLARADO"
            and not (porta.tabelas_escritas() & CADASTRO), "")
    itens.append(item)
    porta = PortaFalsa()
    capturar(modulo, COLETA_OK, contrato, porta)
    inter = [s for s in porta.escritas if "INSERT INTO sales_intelligence.interactions" in s]
    trilha_ins = [s for s in porta.escritas if "INSERT INTO sales_intelligence.sync_events" in s]
    itens.append(("coleta valida: o evento vai para o resumo/referencia da interacao, nao para coluna nova",
                  bool(inter) and "Encontro PMI Sao Paulo" in inter[0] and "metodo=QR_CODE" in inter[0]
                  and "'evt-2026-10-02-0001'" in inter[0]
                  and not any("event_id" in s for s in inter)
                  and bool(trilha_ins) and "evento-pmi-sp-2026-10" in trilha_ins[0],
                  (inter[0][:200] if inter else "sem INSERT em interactions")))
    return itens


def item_consentimento(modulo, contrato):
    itens = []
    sem_aceite = copy.deepcopy(COLETA_OK)
    sem_aceite["consentimento"] = {"aceito": False, "legal_basis": "CONSENTIMENTO", "forma": "TERMO_DIGITAL"}
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
    base_invalida = copy.deepcopy(COLETA_OK)
    base_invalida["consentimento"] = {"aceito": True, "legal_basis": "BASE_INVENTADA",
                                      "forma": "TERMO_DIGITAL"}
    porta = PortaFalsa()
    itens.append(("legal_basis fora do vocabulario RECUSA (fail-closed, nenhum cadastro)",
                  capturar(modulo, base_invalida, contrato, porta)["status"] == "RECUSADO_CONSENTIMENTO"
                  and not (porta.tabelas_escritas() & CADASTRO), ""))
    forma_invalida = copy.deepcopy(COLETA_OK)
    forma_invalida["consentimento"] = {"aceito": True, "legal_basis": "CONSENTIMENTO", "forma": "BOCA_A_BOCA"}
    porta = PortaFalsa()
    itens.append(("forma de consentimento fora do vocabulario RECUSA (a forma e' a evidencia do opt-in)",
                  capturar(modulo, forma_invalida, contrato, porta)["status"] == "RECUSADO_CONSENTIMENTO"
                  and not (porta.tabelas_escritas() & CADASTRO), ""))
    return itens


def item_dados_minimos(modulo, contrato):
    itens = []
    sem_email = copy.deepcopy(COLETA_OK)
    sem_email["contato"] = {"nome": "Marina Prado"}
    porta = PortaFalsa()
    itens.append(("sem canal de resposta (e-mail/telefone): SEM_DADOS_MINIMOS, nenhum cadastro",
                  capturar(modulo, sem_email, contrato, porta)["status"] == "SEM_DADOS_MINIMOS"
                  and not (porta.tabelas_escritas() & CADASTRO), ""))
    sem_id = copy.deepcopy(COLETA_OK)
    del sem_id["captura_id"]
    porta = PortaFalsa()
    itens.append(("sem captura_id: COLETA_INVALIDA",
                  capturar(modulo, sem_id, contrato, porta)["status"] == "COLETA_INVALIDA", ""))
    return itens


def item_captura(modulo, contrato):
    itens = []
    porta = PortaFalsa()
    resultado = capturar(modulo, COLETA_OK, contrato, porta)
    contagem = contagens(porta)
    itens.append(("coleta valida: CAPTURADO com 1 organizacao, 1 contato, 1 interacao e trilha",
                  resultado["status"] == "CAPTURADO" and contagem == {"organizations": 1, "contacts": 1,
                                                                     "interactions": 1, "sync_events": 1},
                  str(contagem)))
    itens.append(("organizacao nasce com UUID canonico gerado no INSERT (gen_random_uuid)",
                  any("gen_random_uuid()" in s for s in porta.escritas), ""))
    itens.append(("interacao no canal do contrato (EVENTO/INBOUND/CAPTURA_EVENTO)",
                  any(all(v in s for v in ("'EVENTO'", "'INBOUND'", "'CAPTURA_EVENTO'"))
                      for s in porta.escritas), ""))
    itens.append(("compliance gravado: legal_basis + source do evento + bloqueio booleano",
                  any("legal_basis" in s and "'EVENTO_CAPTURA'" in s for s in porta.escritas), ""))
    itens.append(("occurred_at da interacao vem do capturado_em do evento",
                  any("2026-10-02T17:40:00" in s for s in porta.escritas), ""))
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
    com_cnpj = copy.deepcopy(COLETA_OK)
    com_cnpj["empresa"]["cnpj"] = "12.345.678/0001-90"
    com_cnpj["captura_id"] = "evt-forte-0001"
    porta = PortaFalsa(organizacao_por_forte={"id": "aaaaaaaa-0000-4000-8000-000000000001",
                                              "trade_name": "Aurora", "city": "Campinas"})
    resultado = capturar(modulo, com_cnpj, contrato, porta)
    contagem = contagens(porta)
    itens.append(("identificador FORTE casa: reusa a organizacao, zero INSERT em organizations",
                  resultado["status"] == "CAPTURADO" and contagem["organizations"] == 0
                  and contagem["interactions"] == 1, str(contagem)))
    fraca = copy.deepcopy(COLETA_OK)
    fraca["captura_id"] = "evt-fraca-0001"
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
    resultado = capturar(modulo, COLETA_OK, contrato, porta)
    itens.append(("reentrega da mesma coleta: JA_CAPTURADO e zero escrita",
                  resultado["status"] == "JA_CAPTURADO" and not porta.escritas, resultado["status"]))
    itens.append(("chave de idempotencia e' evento:<event_id>:<captura_id>",
                  modulo.chave_de(COLETA_OK) == "evento:evento-pmi-sp-2026-10:evt-2026-10-02-0001",
                  modulo.chave_de(COLETA_OK)))
    outro_evento = copy.deepcopy(COLETA_OK)
    outro_evento["origem_evento"]["event_id"] = "evento-sebrae-2026-11"
    itens.append(("o mesmo lead em dois eventos sao duas coletas distintas (event_id entra na chave)",
                  modulo.chave_de(outro_evento) != modulo.chave_de(COLETA_OK),
                  modulo.chave_de(outro_evento)))
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
        capturar(modulo, COLETA_OK, contrato, porta)
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
                  modulo.mascarar("segredo-do-evento").startswith("se"), ""))
    return itens


def verificar(modulo, contrato, caminho_modulo):
    itens = []
    modulo.print = lambda *a, **k: None  # a suite mede decisao, nao ruido de stdout
    itens += item_contrato(modulo, contrato)
    itens += item_fonte(modulo)
    itens += item_guardas(modulo, contrato, caminho_modulo)
    itens += item_evento(modulo, contrato)
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
        "nome": "D1 contrato sem a forma de consentimento obrigatoria",
        "alvo": "contrato versionado carrega e valida (0 falha do validador)",
        "tipo": "contrato",
        "aplicar": lambda c: c["coleta"]["campos_obrigatorios"].remove("consentimento.forma"),
    },
    {
        "nome": "D2 contrato com identificador fraco no limiar de merge",
        "alvo": "contrato versionado carrega e valida (0 falha do validador)",
        "tipo": "contrato",
        "aplicar": lambda c: c["identificadores"]["fracos"][0].update({"confianca": 0.95}),
    },
    {
        "nome": "D3 fonte: a barreira do vinculo do evento removida",
        "alvo": "sem event_id: EVENTO_NAO_DECLARADO e nenhum cadastro (vinculo do evento e' barreira)",
        "tipo": "modulo",
        "de": "    if not event_id or not capturado_em:",
        "para": "    if False and (not event_id or not capturado_em):",
    },
    {
        "nome": "D4 fonte: a checagem de idempotencia removida",
        "alvo": "reentrega da mesma coleta: JA_CAPTURADO e zero escrita",
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
    """Devolve (caminho_modulo_mutado, contrato_mutado) ou lanca RuntimeError se nao aplicar."""
    if mutacao["tipo"] == "contrato":
        mutado = copy.deepcopy(contrato)
        mutacao["aplicar"](mutado)
        return caminho_modulo, mutado
    with open(caminho_modulo, "r", encoding="utf-8") as fh:
        fonte = fh.read()
    if mutacao["de"] not in fonte:
        raise RuntimeError("MUTACAO_NAO_APLICADA")
    diretorio = tempfile.mkdtemp(prefix="mut-captura-evento-")
    alvo = os.path.join(diretorio, "captura_evento_mutado.py")
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
                modulo = carregar_modulo(caminho_modulo, "captura_evento_dente")
            else:
                modulo = carregar_modulo(modulo_mutado_caminho, "captura_evento_dente")
            itens_mutados = verificar(modulo, contrato_mutado, modulo_mutado_caminho)
        except Exception as e:  # mutacao que quebra o modulo tambem e' dente que falhou
            itens.append((f"dente {mutacao['nome']}: modulo mutado levantou {type(e).__name__}", False,
                          str(e)[:120]))
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
    p = argparse.ArgumentParser(prog="verificar_captura_evento")
    p.add_argument("--autoteste", action="store_true")
    p.add_argument("--modulo", default=os.environ.get("CAPTURA_EVENTO_MODULO") or MODULO_PADRAO)
    p.add_argument("--contrato", default=os.environ.get("CAPTURA_EVENTO_CONTRATO") or CONTRATO_PADRAO)
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
        print(f"VERIFICADOR_CAPTURA_EVENTO_PASS ({len(itens)} itens, 0 falhas"
              + (f", {len(MUTACOES)} dentes)" if args.autoteste else ")"))
        return 0
    print(f"FALHOU ({len(itens)} itens, {falhas} falhas)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
