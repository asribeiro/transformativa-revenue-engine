#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Suite offline do componente `whatsapp_lead` (card TRE-W7-E05-T01) — sem banco, sem rede.

Mede o que o componente DECIDE (dados minimos, identidade pelo telefone, classificacao por regra,
precedencia do descadastro, bloqueio de contato, janela de atendimento, idempotencia, limite de
escrita, privacidade, guardas de ambiente) contra uma porta de banco FALSA que registra o SQL pedido.
A porta falsa nao testa o PostgreSQL (isso e' o aceite em container descartavel); ela testa a DECISAO.

Uso:
  python3 scripts/agentes/verificar_whatsapp_lead.py              # suite
  python3 scripts/agentes/verificar_whatsapp_lead.py --autoteste  # + prova de dente por mutacao
Saida: um item por linha (OK / FALHOU) e resumo; exit 0 = PASS, 1 = falhou, 2 = uso errado.
"""

import argparse
import copy
import importlib.util
import json
import os
import re
import sys
import tempfile

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.abspath(os.path.join(AQUI, "..", ".."))
MODULO_PADRAO = os.path.join(RAIZ, "hermes", "agentes", "inbound", "whatsapp_lead.py")
CONTRATO_PADRAO = os.path.join(RAIZ, "hermes", "agentes", "inbound", "whatsapp-lead-v1.json")

TELEFONE_CRU = "+55 11 98888-7777"
TELEFONE_DIGITOS = "988887777"
CONTATO_OK = {"id": "c0000000-0000-4000-8000-000000000001",
              "organization_id": "o0000000-0000-4000-8000-000000000001",
              "do_not_contact": False, "opt_out_whatsapp": False,
              "phone": "+55 11 98888-7777", "whatsapp": "+55 11 98888-7777"}
CONTATO_FIXO = {"id": "c0000000-0000-4000-8000-000000000002",
                "organization_id": "o0000000-0000-4000-8000-000000000002",
                "do_not_contact": False, "opt_out_whatsapp": False,
                "phone": "1133334444", "whatsapp": ""}

STATUS_CONHECIDOS = ["RECEBIDO", "JA_RECEBIDO", "SEM_VINCULO", "REVIEW_REQUIRED",
                     "BLOQUEADO_POR_BLOQUEIO", "EVENTO_INVALIDO", "SEM_DADOS_MINIMOS"]


def evento(tid="msg-0001", texto_mensagem="podemos conversar amanha?", **kw):
    corpo = {
        "message_id": tid,
        "recebido_em": "2026-10-03T18:12:00Z",
        "remetente": {"telefone": TELEFONE_CRU, "nome_perfil": "Marina"},
        "mensagem": {"tipo": "TEXTO", "texto": texto_mensagem},
        "canal": {"origem": "provedor_whatsapp", "numero_destino": "+55 11 3333-1000"},
    }
    for chave, valor in kw.items():
        corpo[chave] = valor
    return corpo


def digitos(valor):
    return re.sub(r"[^0-9]", "", valor or "")


def direita11(valor):
    d = digitos(valor)
    return d[-11:] if d else ""


def literais(sql):
    """Literais de string do SQL (para auditar o que seria gravado)."""
    return [l.replace("''", "'") for l in re.findall(r"'((?:[^']|'')*)'", sql)]


class PortaFalsa:
    """Porta de banco falsa: registra o SQL e responde por cenario. Nenhuma conexao real."""

    def __init__(self, contatos=None, ultima=None, trilha=None):
        self.contatos = list(contatos if contatos is not None else [CONTATO_OK])
        self.ultima = ultima
        self.trilha = dict(trilha or {})
        self.leituras = []
        self.escritas = []
        self.ambiente = "dev"
        self.prefixo = "docker exec -i pg-whatsapp-acc psql -U sales_ai -d sales_intelligence"

    def consultar(self, sql: str) -> list:
        self.leituras.append(sql)
        if "FROM sales_intelligence.sync_events" in sql:
            m = re.search(r"idempotency_key = '((?:[^']|'')*)'", sql)
            chave = (m.group(1).replace("''", "'") if m else "")
            return [self.trilha[chave]] if chave in self.trilha else []
        if "FROM sales_intelligence.contacts c" in sql:
            colunas = re.findall(r"coalesce\(c\.(\w+), ''\)", sql)
            nucleos = re.findall(r"= '([0-9]+)'", sql)
            achados = []
            for contato in self.contatos:
                if any(direita11(contato.get(coluna) or "") in nucleos for coluna in colunas):
                    achados.append(contato)
            return achados[:3]
        if "FROM sales_intelligence.interactions i" in sql:
            return [{"ultima": self.ultima}] if self.ultima else []
        return []

    def executar(self, sql: str) -> list:
        self.escritas.append(sql)
        if "INSERT INTO sales_intelligence.interactions" in sql:
            return [{"id": "interacao-%04d" % len(self.escritas)}]
        if "INSERT INTO sales_intelligence.sync_events" in sql:
            m = re.search(r"'whatsapp:[^']*'", sql)
            if m:
                chave = m.group(0)[1:-1]
                status = next((s for s in STATUS_CONHECIDOS if f"'{s}'" in sql), "")
                self.trilha.setdefault(chave, {"entity_id": None, "status": status})
            return []
        return []

    def tabelas_escritas(self) -> set:
        tabelas = set()
        for sql in self.escritas:
            for tabela in re.findall(r"INSERT INTO\s+(sales_intelligence\.[a-z_]+)", sql, re.IGNORECASE):
                tabelas.add(tabela)
        return tabelas

    def interacoes(self) -> list:
        return [sql for sql in self.escritas if "INSERT INTO sales_intelligence.interactions" in sql]

    def trilhas(self) -> list:
        return [sql for sql in self.escritas if "INSERT INTO sales_intelligence.sync_events" in sql]


def carregar_modulo(caminho: str = MODULO_PADRAO, nome: str = "whatsapp_lead"):
    spec = importlib.util.spec_from_file_location(nome, caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def rodar(modulo, contrato, porta, evento_caso, escrever=True):
    saida = modulo.Saida(None, None, None)
    resultado = modulo.processar(evento_caso, contrato, porta, saida, escrever)
    return resultado, saida


# --------------------------------------------------------------------------------------------
# Itens da suite (cada um devolve (nome, ok, detalhe))
# --------------------------------------------------------------------------------------------
def item_contrato(modulo, contrato):
    itens = [("contrato versionado carrega e valida (0 falha do validador)",
              modulo.validar_contrato(contrato) == [], "; ".join(modulo.validar_contrato(contrato)))]
    sem_opt_out = copy.deepcopy(contrato)
    sem_opt_out["regras"] = [r for r in sem_opt_out["regras"]
                             if str(r.get("categoria")).upper() != "OPT_OUT"]
    falhas = modulo.validar_contrato(sem_opt_out)
    itens.append(("contrato sem a barreira de descadastro RECUSA (CONTRATO_INVALIDO)",
                  any("OPT_OUT" in f for f in falhas), "; ".join(falhas)))
    opt_out_fora = copy.deepcopy(contrato)
    for regra in opt_out_fora["regras"]:
        if str(regra.get("categoria")).upper() == "OPT_OUT":
            regra["ordem"] = 9
    falhas = modulo.validar_contrato(opt_out_fora)
    itens.append(("OPT_OUT fora da ordem 1 RECUSA (descadastro tem de vencer o interesse)",
                  any("ordem 1" in f for f in falhas), "; ".join(falhas)))
    janela_zero = copy.deepcopy(contrato)
    janela_zero["janela_de_atendimento"]["minutos"] = 0
    itens.append(("janela de atendimento nao positiva RECUSA",
                  any("janela_de_atendimento.minutos" in f for f in modulo.validar_contrato(janela_zero)),
                  ""))
    escopo_aberto = copy.deepcopy(contrato)
    escopo_aberto["escrita"]["tabelas"] = ["interactions", "sync_events", "outbox_events"]
    itens.append(("escrita fora do escopo declarado RECUSA",
                  any("escrita.tabelas" in f for f in modulo.validar_contrato(escopo_aberto)), ""))
    regra_vazia = copy.deepcopy(contrato)
    for regra in regra_vazia["regras"]:
        if str(regra.get("categoria")).upper() == "INTERESSE":
            regra["deteccao"] = {"minimo": 1, "campos": ["texto"], "padroes": []}
    itens.append(("regra sem padroes declarados RECUSA",
                  any("sem padroes" in f for f in modulo.validar_contrato(regra_vazia)), ""))
    return itens


def item_fonte(modulo):
    violacoes = modulo.auditar_fonte()
    return [("auditoria da propria fonte: 0 violacao (sem DDL/UPDATE/DELETE)",
             violacoes == [], str(violacoes)),
            ("fonte declara a gravacao por INSERT", "INSERT INTO" in
             open(MODULO_PADRAO, encoding="utf-8").read(), "")]


def item_guardas(modulo, contrato):
    itens = []
    prod = modulo.validar(modulo.Configuracao({"TRE_AMBIENTE": "prod",
                                               "TRE_WHATSAPP_PORTA_BANCO": "docker exec -i pg-whatsapp-acc psql"}))
    itens.append(("prod RECUSA por desenho (PRODUCAO_RECUSADA)",
                  [m for m, _ in prod] == ["PRODUCAO_RECUSADA"], str(prod)))
    remoto = modulo.validar(modulo.Configuracao(
        {"TRE_AMBIENTE": "dev",
         "TRE_WHATSAPP_PORTA_BANCO": "ssh root@10.0.0.9 psql -U sales_ai -d sales_intelligence"}))
    itens.append(("dev RECUSA prefixo de banco remoto (BANCO_NAO_E_DEV)",
                  [m for m, _ in remoto] == ["BANCO_NAO_E_DEV"], str(remoto)))
    dev_ok = modulo.validar(modulo.Configuracao(
        {"TRE_AMBIENTE": "dev",
         "TRE_WHATSAPP_PORTA_BANCO": "docker exec -i pg-whatsapp-acc psql -U sales_ai -d sales_intelligence"}))
    itens.append(("dev aceita porta local de container de dev/aceite", dev_ok == [], str(dev_ok)))
    sem_porta = modulo.validar(modulo.Configuracao({"TRE_AMBIENTE": "dev"}))
    itens.append(("dev sem porta de banco RECUSA (BANCO_NAO_DECLARADO)",
                  [m for m, _ in sem_porta] == ["BANCO_NAO_DECLARADO"], str(sem_porta)))
    homolog = modulo.validar(modulo.Configuracao(
        {"TRE_AMBIENTE": "homolog",
         "TRE_WHATSAPP_PORTA_BANCO": "docker exec -i pg-whatsapp-acc psql"}))
    itens.append(("homolog sem aprovacao registrada RECUSA (HOMOLOG_SEM_APROVACAO)",
                  [m for m, _ in homolog] == ["HOMOLOG_SEM_APROVACAO"], str(homolog)))
    homolog_ok = modulo.validar(modulo.Configuracao(
        {"TRE_AMBIENTE": "homolog", "TRE_WHATSAPP_PORTA_BANCO": "docker exec -i pg-whatsapp-acc psql",
         "TRE_WHATSAPP_APROVACAO_HUMANA": "aprovacao-2026-10-03"}))
    itens.append(("homolog com aprovacao registrada segue", homolog_ok == [], str(homolog_ok)))
    invalido = modulo.validar(modulo.Configuracao({"TRE_AMBIENTE": "staging"}))
    itens.append(("ambiente fora de dev/homolog/prod RECUSA (AMBIENTE_INVALIDO)",
                  [m for m, _ in invalido] == ["AMBIENTE_INVALIDO"], str(invalido)))
    return itens


def item_dados_minimos(modulo, contrato):
    itens = []
    casos = [
        ("sem message_id: SEM_DADOS_MINIMOS e nenhuma interacao",
         evento(message_id=""), "SEM_DADOS_MINIMOS"),
        ("telefone curto (sem DDD): SEM_DADOS_MINIMOS e nenhuma interacao",
         evento(remetente={"telefone": "988887777"}), "SEM_DADOS_MINIMOS"),
        ("recebido_em ausente: EVENTO_INVALIDO (a hora nao se inventa)",
         evento(recebido_em=""), "EVENTO_INVALIDO"),
        ("mensagem.tipo fora do vocabulario: EVENTO_INVALIDO e nenhuma interacao",
         evento(mensagem={"tipo": "ENQUETE", "texto": "oi"}), "EVENTO_INVALIDO"),
    ]
    for nome, caso, esperado in casos:
        porta = PortaFalsa()
        resultado, _ = rodar(modulo, contrato, porta, caso)
        itens.append((nome, resultado["status"] == esperado and not porta.interacoes(),
                      f"status={resultado['status']} interacoes={len(porta.interacoes())}"))
    porta = PortaFalsa()
    rodar(modulo, contrato, porta, evento(message_id=""))
    itens.append(("evento invalido grava apenas trilha (sync_events)",
                  porta.tabelas_escritas() == {"sales_intelligence.sync_events"},
                  str(porta.tabelas_escritas())))
    return itens


def item_classificacao(modulo, contrato):
    itens = []
    casos = [
        ("PARAR vira OPT_OUT/DESCADASTRO", "PARAR", "OPT_OUT", "DESCADASTRO"),
        ("pedido de descadastro vira OPT_OUT", "nao quero mais receber, obrigado", "OPT_OUT", "DESCADASTRO"),
        ("mensagem mista: OPT_OUT vence o sinal de interesse",
         "podemos conversar depois, mas nao quero mais receber", "OPT_OUT", "DESCADASTRO"),
        ("negacao explicita vence o padrao de interesse",
         "nao quero saber disso", "SEM_INTERESSE", "RECUSA"),
        ("recusa simples vira SEM_INTERESSE", "obrigado, mas sem interesse", "SEM_INTERESSE", "RECUSA"),
        ("pedido de conversa vira INTERESSE",
         "podemos agendar uma conversa?", "INTERESSE", "PEDIDO_DE_CONVERSA"),
        ("resposta automatica do numero vira AUTO_RESPOSTA",
         "mensagem automatica: retornaremos o seu contato", "AUTO_RESPOSTA", "RESPOSTA_AUTOMATICA"),
        ("oferta nao solicitada vira RUIDO",
         "promocao imperdivel, aumente suas vendas", "RUIDO", "RUIDO_COMERCIAL"),
        ("texto sem sinal vira INDEFINIDO com confianca 0",
         "bom dia", "INDEFINIDO", "INDEFINIDO"),
    ]
    for nome, corpo, categoria, intencao in casos:
        resultado = modulo.classificar(evento(mensagem={"tipo": "TEXTO", "texto": corpo}), contrato)
        ok = resultado["categoria"] == categoria and resultado["intent"] == intencao
        if categoria == "INDEFINIDO":
            ok = ok and resultado["confianca"] == 0
        itens.append((nome, ok, f"{resultado['categoria']}/{resultado['intent']} conf={resultado['confianca']}"))
    midia = modulo.classificar(evento(mensagem={"tipo": "AUDIO", "midia_id": "mid-1"}), contrato)
    itens.append(("midia sem texto vira INDEFINIDO (midia nao e' baixada nem transcrita)",
                  midia["categoria"] == "INDEFINIDO" and midia["confianca"] == 0
                  and "nao e' baixada" in midia["motivo"], midia["motivo"]))
    return itens


def item_identidade(modulo, contrato):
    itens = []
    porta = PortaFalsa()
    resultado, _ = rodar(modulo, contrato, porta, evento())
    itens.append(("celular com codigo do pais casa o contato pelo nucleo nacional",
                  resultado["status"] == "RECEBIDO", resultado["status"]))
    porta = PortaFalsa()
    resultado, _ = rodar(modulo, contrato, porta,
                         evento(remetente={"telefone": "11 98888-7777"}))
    itens.append(("celular sem codigo do pais casa o mesmo contato",
                  resultado["status"] == "RECEBIDO", resultado["status"]))
    porta = PortaFalsa()
    resultado, _ = rodar(modulo, contrato, porta,
                         evento(remetente={"telefone": "+55 11 98888-7777".replace("98888", "90000")}))
    itens.append(("telefone desconhecido: SEM_VINCULO e nenhuma interacao",
                  resultado["status"] == "SEM_VINCULO" and not porta.interacoes(),
                  f"status={resultado['status']} interacoes={len(porta.interacoes())}"))
    porta = PortaFalsa(contatos=[CONTATO_FIXO])
    resultado, _ = rodar(modulo, contrato, porta,
                         evento(remetente={"telefone": "+55 11 3333-4444"}))
    itens.append(("telefone fixo com codigo do pais casa o nucleo nacional (10 digitos)",
                  resultado["status"] == "RECEBIDO", resultado["status"]))
    porta = PortaFalsa(contatos=[CONTATO_OK, dict(CONTATO_OK,
                                                  id="c0000000-0000-4000-8000-000000000003")])
    resultado, _ = rodar(modulo, contrato, porta, evento())
    itens.append(("telefone ambiguo (2 contatos): REVIEW_REQUIRED e nenhuma interacao",
                  resultado["status"] == "REVIEW_REQUIRED" and not porta.interacoes(),
                  f"status={resultado['status']} interacoes={len(porta.interacoes())}"))
    porta = PortaFalsa(contatos=[])
    resultado, _ = rodar(modulo, contrato, porta, evento())
    itens.append(("base sem o contato: nada e' criado (organizacao nao se inventa)",
                  resultado["status"] == "SEM_VINCULO" and porta.tabelas_escritas()
                  == {"sales_intelligence.sync_events"}, str(porta.tabelas_escritas())))
    itens.append(("nucleo nacional normaliza +55 / 55 / sem pais",
                  modulo.nucleo_telefone("+55 11 98888-7777") == "11988887777"
                  and modulo.nucleo_telefone("5511988887777") == "11988887777"
                  and modulo.nucleo_telefone("11988887777") == "11988887777"
                  and modulo.nucleo_telefone("988887777") == "", "nucleo fora do esperado"))
    return itens


def item_bloqueio(modulo, contrato):
    itens = []
    porta = PortaFalsa(contatos=[dict(CONTATO_OK, do_not_contact=True)])
    resultado, _ = rodar(modulo, contrato, porta, evento())
    itens.append(("contato com do_not_contact: BLOQUEADO_POR_BLOQUEIO e nenhum proximo passo",
                  resultado["status"] == "BLOQUEADO_POR_BLOQUEIO"
                  and resultado["proximo_passo"] == "NENHUM_FILA_HUMANA"
                  and len(porta.interacoes()) == 1,
                  f"status={resultado['status']} proximo={resultado.get('proximo_passo')}"))
    porta = PortaFalsa(contatos=[dict(CONTATO_OK, opt_out_whatsapp=True)])
    resultado, _ = rodar(modulo, contrato, porta, evento())
    itens.append(("contato com opt_out_whatsapp: BLOQUEADO_POR_BLOQUEIO (base manda)",
                  resultado["status"] == "BLOQUEADO_POR_BLOQUEIO"
                  and resultado["proximo_passo"] == "NENHUM_FILA_HUMANA", resultado["status"]))
    porta = PortaFalsa()
    resultado, _ = rodar(modulo, contrato, porta, evento(texto_mensagem="PARAR"))
    itens.append(("mensagem OPT_OUT de contato sem bloqueio: BLOQUEADO_POR_BLOQUEIO",
                  resultado["status"] == "BLOQUEADO_POR_BLOQUEIO"
                  and resultado["proximo_passo"] == "NENHUM_FILA_HUMANA", resultado["status"]))
    itens.append(("bloqueio nao emite comando de escrita em contacts (dono e' o Odoo)",
                  all("sales_intelligence.contacts" not in sql for sql in porta.escritas), ""))
    return itens


def item_janela(modulo, contrato):
    itens = []
    porta = PortaFalsa(ultima=None)
    resultado, _ = rodar(modulo, contrato, porta, evento())
    itens.append(("primeira entrada do contato: janela abre e a resposta e' livre",
                  resultado["status"] == "RECEBIDO"
                  and resultado["proximo_passo"] == "RESPOSTA_LIVRE_SUGERIDA", str(resultado.get("proximo_passo"))))
    porta = PortaFalsa(ultima="2026-10-03T18:02:00Z")
    resultado, _ = rodar(modulo, contrato, porta, evento())
    itens.append(("entrada anterior 10 min atras: janela aberta (resposta livre)",
                  resultado["proximo_passo"] == "RESPOSTA_LIVRE_SUGERIDA", str(resultado.get("proximo_passo"))))
    porta = PortaFalsa(ultima="2026-09-30T18:00:00Z")
    resultado, _ = rodar(modulo, contrato, porta, evento())
    itens.append(("janela fechada (3 dias): reengajamento exige template + aprovacao humana",
                  resultado["proximo_passo"] == "REENGAJAMENTO_COM_TEMPLATE_APROVACAO_HUMANA",
                  str(resultado.get("proximo_passo"))))
    porta = PortaFalsa(ultima="2026-09-30T18:00:00Z")
    resultado, saida = rodar(modulo, contrato, porta, evento())
    evidencia = json.dumps(saida.relatorio, ensure_ascii=False)
    itens.append(("proposta fora da janela nao contem envio: so' proposta, nunca mensagem",
                  "ENVIO" not in evidencia.upper() and "RESPOSTA_LIVRE_SUGERIDA" not in evidencia, ""))
    return itens


def item_idempotencia(modulo, contrato):
    itens = []
    porta = PortaFalsa(trilha={"whatsapp:msg-0001": {"entity_id": "c0000000-0000-4000-8000-000000000001",
                                                     "status": "RECEBIDO"}})
    resultado, _ = rodar(modulo, contrato, porta, evento())
    itens.append(("reentrega da mesma mensagem: JA_RECEBIDO e zero escrita",
                  resultado["status"] == "JA_RECEBIDO" and porta.escritas == [],
                  f"status={resultado['status']} escritas={len(porta.escritas)}"))
    porta = PortaFalsa()
    rodar(modulo, contrato, porta, evento())
    antes = len(porta.escritas)
    resultado, _ = rodar(modulo, contrato, porta, evento())
    itens.append(("replay depois da gravacao: JA_RECEBIDO sem novas linhas",
                  resultado["status"] == "JA_RECEBIDO" and len(porta.escritas) == antes,
                  f"status={resultado['status']} escritas={len(porta.escritas) - antes}"))
    return itens


def item_escrita(modulo, contrato):
    itens = []
    porta = PortaFalsa()
    resultado, _ = rodar(modulo, contrato, porta, evento())
    itens.append(("recebido: interacao e trilha gravadas",
                  resultado["status"] == "RECEBIDO"
                  and porta.tabelas_escritas() == {"sales_intelligence.interactions",
                                                   "sales_intelligence.sync_events"},
                  str(porta.tabelas_escritas())))
    valores = literais(porta.interacoes()[0])
    itens.append(("interacao com canal/direcao/tipo do contrato",
                  "WHATSAPP" in valores and "INBOUND" in valores and "WHATSAPP_MENSAGEM" in valores,
                  str(valores[:8])))
    itens.append(("interacao ligada ao contato e a organizacao resolvidos",
                  CONTATO_OK["id"] in valores and CONTATO_OK["organization_id"] in valores, ""))
    itens.append(("content_reference e' o message_id e occurred_at e' o recebido_em",
                  "msg-0001" in valores and "2026-10-03T18:12:00+00:00" in valores, str(valores[-6:])))
    itens.append(("chave de idempotencia whatsapp:<message_id> na trilha",
                  "whatsapp:msg-0001" in literais(porta.trilhas()[0]), ""))
    itens.append(("classificacao entra na interacao (categoria/intencao/sentimento/confianca)",
                  all(v in valores for v in ("INTERESSE", "PEDIDO_DE_CONVERSA", "POSITIVO"))
                  and "0.75" in porta.interacoes()[0], str(valores[-4:])))
    proibidos = re.compile(r"\b(UPDATE|DELETE|TRUNCATE|ALTER|DROP)\b", re.IGNORECASE)
    itens.append(("nenhum comando de escrita proibido no SQL emitido",
                  not any(proibidos.search(sql) for sql in porta.escritas), ""))
    itens.append(("uma mensagem gera UMA trilha (chave whatsapp:<id> e' UNIQUE)",
                  len(porta.trilhas()) == 1 and len(porta.interacoes()) == 1,
                  f"trilhas={len(porta.trilhas())} interacoes={len(porta.interacoes())}"))
    porta_banco = modulo.PortaBanco("docker exec -i pg-whatsapp-acc psql -U sales_ai -d x", "dev")
    try:
        lido = porta_banco._analisar('[{"a":1,"b":"x"}, \n {"a":2,"b":"y"}]')
    except Exception as e:  # defeito medido na rodada 1: ler so' a ultima linha levanta Recusa
        lido = f"{type(e).__name__}: {e}"
    itens.append(("porta de banco le o DOCUMENTO JSON (psql quebra o agregado em linhas)",
                  lido == [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}], str(lido)[:120]))
    return itens


def item_dry_run(modulo, contrato):
    porta = PortaFalsa()
    resultado, _ = rodar(modulo, contrato, porta, evento(), escrever=False)
    return [("sem --confirmo: DRY_RUN e zero escrita",
             resultado["status"] == "DRY_RUN" and porta.escritas == [],
             f"status={resultado['status']} escritas={len(porta.escritas)}")]


def item_privacidade(modulo, contrato):
    porta = PortaFalsa()
    _, saida = rodar(modulo, contrato, porta, evento())
    evidencia = json.dumps(saida.relatorio, ensure_ascii=False)
    itens = [("telefone do lead nao aparece cru na evidencia",
              TELEFONE_CRU not in evidencia and TELEFONE_DIGITOS not in evidencia, ""),
             ("numero de destino nao aparece cru na evidencia",
              "3333-1000" not in evidencia and "1133331000" not in evidencia, ""),
             ("mascaramento do telefone preserva apenas os 2 ultimos digitos",
              modulo.mascarar_telefone(TELEFONE_CRU).endswith("77")
              and TELEFONE_DIGITOS not in modulo.mascarar_telefone(TELEFONE_CRU),
              modulo.mascarar_telefone(TELEFONE_CRU))]
    return itens


def verificar(modulo, contrato, caminho_modulo):
    itens = []
    modulo.print = lambda *a, **k: None  # a suite mede decisao, nao ruido de stdout
    itens += item_contrato(modulo, contrato)
    itens += item_fonte(modulo)
    itens += item_guardas(modulo, contrato)
    itens += item_dados_minimos(modulo, contrato)
    itens += item_classificacao(modulo, contrato)
    itens += item_identidade(modulo, contrato)
    itens += item_bloqueio(modulo, contrato)
    itens += item_janela(modulo, contrato)
    itens += item_idempotencia(modulo, contrato)
    itens += item_escrita(modulo, contrato)
    itens += item_dry_run(modulo, contrato)
    itens += item_privacidade(modulo, contrato)
    return itens


# --------------------------------------------------------------------------------------------
# Prova de dente: cada mutacao tem de reprovar o ITEM DECLARADO (fail-closed)
# --------------------------------------------------------------------------------------------
MUTACOES = [
    {
        "nome": "D1 sem message_id passando (dados minimos removidos)",
        "alvo": "sem message_id: SEM_DADOS_MINIMOS e nenhuma interacao",
        "tipo": "modulo",
        "de": "    if not message_id:\n        return (\"SEM_DADOS_MINIMOS\"",
        "para": "    if False and not message_id:\n        return (\"SEM_DADOS_MINIMOS\"",
    },
    {
        "nome": "D2 precedencia invertida (OPT_OUT deixa de vencer)",
        "alvo": "mensagem mista: OPT_OUT vence o sinal de interesse",
        "tipo": "modulo",
        "de": "key=lambda r: r.get(\"ordem\", 99))",
        "para": "key=lambda r: -r.get(\"ordem\", 99))",
    },
    {
        "nome": "D3 negacao explicita removida do contrato",
        "alvo": "negacao explicita vence o padrao de interesse",
        "tipo": "contrato",
        "aplicar": lambda c: [r["deteccao"]["padroes"].remove("n[aã]o quero (saber|entender|conhecer|nada|isso)")
                              for r in c["regras"] if str(r.get("categoria")).upper() == "SEM_INTERESSE"],
    },
    {
        "nome": "D4 normalizacao do codigo do pais removida",
        "alvo": "telefone fixo com codigo do pais casa o nucleo nacional (10 digitos)",
        "tipo": "modulo",
        "de": "    if len(digitos) >= 12 and digitos.startswith(\"55\"):",
        "para": "    if False and len(digitos) >= 12 and digitos.startswith(\"55\"):",
    },
    {
        "nome": "D5 bloqueio de contato desligado",
        "alvo": "contato com do_not_contact: BLOQUEADO_POR_BLOQUEIO e nenhum proximo passo",
        "tipo": "modulo",
        "de": "    bloqueio = identidade.get(\"bloqueado\") or classificacao[\"categoria\"] == \"OPT_OUT\"",
        "para": "    bloqueio = False",
    },
    {
        "nome": "D6 janela de atendimento sempre aberta",
        "alvo": "janela fechada (3 dias): reengajamento exige template + aprovacao humana",
        "tipo": "modulo",
        "de": "    return {\"aberta\": restantes > 0, ",
        "para": "    return {\"aberta\": True, ",
    },
    {
        "nome": "D7 identidade desconhecida virando interacao",
        "alvo": "telefone desconhecido: SEM_VINCULO e nenhuma interacao",
        "tipo": "modulo",
        "de": "    if identidade[\"status\"] == \"SEM_VINCULO\":",
        "para": "    if False and identidade[\"status\"] == \"SEM_VINCULO\":",
    },
    {
        "nome": "D8 mascaramento do telefone removido",
        "alvo": "telefone do lead nao aparece cru na evidencia",
        "tipo": "modulo",
        "de": "    return (\"*\" * max(0, len(digitos) - 2)) + digitos[-2:] if digitos else \"<oculta>\"",
        "para": "    return numero",
    },
    {
        "nome": "D9 trilha da mensagem duplicada (chave UNIQUE estoura)",
        "alvo": "uma mensagem gera UMA trilha (chave whatsapp:<id> e' UNIQUE)",
        "tipo": "modulo",
        "de": "    gravar_trilha(porta, chave, \"RECEBIDO\", payload, entity_id=identidade.get(\"contact_id\"))",
        "para": ("    gravar_trilha(porta, chave, \"RECEBIDO\", payload, entity_id=identidade.get(\"contact_id\"))\n"
                 "    gravar_trilha(porta, chave, \"RECEBIDO\", payload, entity_id=identidade.get(\"contact_id\"))"),
    },
    {
        "nome": "D10 porta de banco lendo so' a ultima linha do JSON",
        "alvo": "porta de banco le o DOCUMENTO JSON (psql quebra o agregado em linhas)",
        "tipo": "modulo",
        "de": "        bruto = (saida or \"\").strip()",
        "para": "        bruto = ((saida or \"\").splitlines() or [\"\"])[-1].strip()",
    },
]


def aplicar_mutacao(mutacao, caminho_modulo, contrato):
    """Devolve (caminho_modulo_mutado, contrato_mutado) ou levanta RuntimeError quando nao aplica."""
    if mutacao["tipo"] == "contrato":
        mutado = copy.deepcopy(contrato)
        mutacao["aplicar"](mutado)
        return caminho_modulo, mutado
    with open(caminho_modulo, "r", encoding="utf-8") as fh:
        fonte = fh.read()
    if mutacao["de"] not in fonte:
        raise RuntimeError("MUTACAO_NAO_APLICADA")
    diretorio = tempfile.mkdtemp(prefix="mut-whatsapp-lead-")
    alvo = os.path.join(diretorio, "whatsapp_lead_mutado.py")
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
                modulo = carregar_modulo(caminho_modulo, "whatsapp_lead_dente")
            else:
                modulo = carregar_modulo(modulo_mutado_caminho, "whatsapp_lead_dente")
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
    p = argparse.ArgumentParser(prog="verificar_whatsapp_lead")
    p.add_argument("--autoteste", action="store_true")
    p.add_argument("--modulo", default=os.environ.get("WHATSAPP_LEAD_MODULO") or MODULO_PADRAO)
    p.add_argument("--contrato", default=os.environ.get("WHATSAPP_LEAD_CONTRATO") or CONTRATO_PADRAO)
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
        print(f"VERIFICADOR_WHATSAPP_LEAD_PASS ({len(itens)} itens, 0 falhas"
              + (f", {len(MUTACOES)} dentes)" if args.autoteste else ")"))
        return 0
    print(f"FALHOU ({len(itens)} itens, {falhas} falhas)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
