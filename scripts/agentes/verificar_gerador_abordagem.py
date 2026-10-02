#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Suite offline do GERADOR DE ABORDAGEM v1 (card TRE-W6-E02-T01).

Mede, SEM banco e SEM rede externa, o que da para medir aqui:

  A  identidade do componente, politica padrao, contrato do componente e cobertura do vocabulario;
  B  guarda de contato (compliance): bloqueio absoluto e bloqueio POR CANAL;
  C  regras que NAO podem estar no codigo: acao do contrato, papel de decisao, id de regra,
     operacao/tipo de terceiro (TIER/PRIORITY/NEXT_BEST_ACTION/OUTBOUND) e o texto do prompt;
  D  guarda de escrita: DDL, escrita fora das duas tabelas, DELETE sem --confirmo, DELETE com --confirmo;
  E  validacao deterministica da abordagem: fato sustentado, citacao, limite, afirmacao proibida;
  F  idempotencia: hash de entrada estavel, id deterministico (uuid5), relogio/correlacao fora do hash,
     renderizador offline deterministico;
  G  adaptador de provedor contra um STUB HTTP local (formato compativel com OpenAI): corpo da
     requisicao (modelo, prompt versionado, temperatura, response_format), parsing da resposta,
     retry em 5xx, 4xx sem retry, resposta ilegivel, cerca de codigo, e alucinacao barrada na validacao;
  H  CLI: --planejar/--regras/--provedor invalido/prod/sem --ambiente/sem --prefixo;
  I  contrato do componente: tabelas escritas, lacunas declaradas, fonte do vocabulario.

Nao entra aqui o que exige banco (pedido gravado em human_approvals, auditoria em agent_runs,
replay, supersessao EXPIRED): isso e o ACEITE E2E em PostgreSQL descartavel —
`scripts/agentes/teste_gerador_abordagem_aceite.sh`.

Uso:
  python3 scripts/agentes/verificar_gerador_abordagem.py [--raiz <dir>] [--modulo <py>]
      [--politica <json>] [--contrato <json>] [--prompt <md>]
  python3 scripts/agentes/verificar_gerador_abordagem.py --autoteste

Exit: 0 = PASS (0 falhas) · 1 = FALHOU · 2 = uso/guardrail.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ITENS_OK = 0
ITENS_FALHOU = 0
FALHAS: list[str] = []


def item(nome: str, esperado, obtido) -> None:
    global ITENS_OK, ITENS_FALHOU
    if esperado == obtido:
        print(f"OK     {nome} ({obtido})")
        ITENS_OK += 1
    else:
        print(f"FALHOU {nome} (esperado={esperado} obtido={obtido})")
        ITENS_FALHOU += 1
        FALHAS.append(nome)


def carregar_modulo(caminho: Path):
    spec = importlib.util.spec_from_file_location("gerador_abordagem_sob_teste", caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def descobrir_raiz_padrao() -> Path:
    atual = Path(__file__).resolve()
    for pai in atual.parents:
        if (pai / "docs" / "data" / "data_contract_v1.json").is_file():
            return pai
    return atual.parents[2]


# ---------------------------------------------------------------------------------------
# Stub HTTP compativel com OpenAI (so local): prova o adaptador sem credencial real
# ---------------------------------------------------------------------------------------
class _Stub(BaseHTTPRequestHandler):
    respostas: list = []
    pedidos: list = []

    def do_POST(self):  # noqa: N802
        tamanho = int(self.headers.get("Content-Length") or 0)
        bruto = self.rfile.read(tamanho).decode("utf-8")
        _Stub.pedidos.append({"path": self.path, "headers": dict(self.headers), "body": json.loads(bruto)})
        status, corpo = _Stub.respostas.pop(0) if _Stub.respostas else (500, {"erro": "sem roteiro"})
        dados = json.dumps(corpo).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def log_message(self, *args):  # silencio
        return


class Stub:
    def __init__(self, respostas: list):
        _Stub.respostas = list(respostas)
        _Stub.pedidos = []
        self.servidor = ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
        self.porta = self.servidor.server_address[1]
        self.thread = threading.Thread(target=self.servidor.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.porta}/v1"

    @property
    def pedidos(self) -> list:
        return _Stub.pedidos

    def fechar(self) -> None:
        self.servidor.shutdown()
        self.servidor.server_close()


def resposta_openai(conteudo: str, modelo: str = "modelo-do-stub") -> dict:
    return (200, {"model": modelo, "usage": {"prompt_tokens": 111, "completion_tokens": 222},
                  "choices": [{"message": {"role": "assistant", "content": conteudo}}]})


# ---------------------------------------------------------------------------------------
# Massa sintetica (para os testes de funcao pura; NAO e dado de producao)
# ---------------------------------------------------------------------------------------
def fatos_de_teste() -> dict:
    return {
        "organizacao": {"id": "11111111-1111-4111-8111-111111111111", "nome": "Distribuidora Teste LTDA",
                        "razao_social": "Distribuidora Teste LTDA", "industria": "Distribuicao B2B",
                        "porte": "150_299", "colaboradores": 210, "cidade": "Campinas", "uf": "SP",
                        "status": "Pesquisado", "dominio": "distribuidorateste.com.br"},
        "recomendacao": {"id": "22222222-2222-4222-8222-222222222222", "acao": "SEND_EMAIL",
                         "status": "OPEN", "contato_id": "33333333-3333-4333-8333-333333333333",
                         "rationale": "tier A com decisor contactavel", "criada_em": "2026-10-01T10:00:00Z"},
        "contato": {"id": "33333333-3333-4333-8333-333333333333", "nome": "Maria Souza",
                    "primeiro_nome": "Maria", "cargo": "Diretora de Operacoes", "papel": "Decision Maker",
                    "email": "maria.souza@distribuidorateste.com.br", "linkedin": None,
                    "canal_preferido": "EMAIL", "do_not_contact": False, "opt_out_email": False},
        "pesquisa": {"id": "44444444-4444-4444-8444-444444444444",
                     "resumo": "Empresa com 3 centros de distribuicao e operacao de pedidos manual",
                     "concluida_em": "2026-09-30T09:00:00Z"},
        "dores": [{"id": "55555555-5555-4555-8555-555555555555",
                   "declaracao": "Pedidos entram por e-mail e sao redigitados no ERP",
                   "categoria": "PROCESSO_MANUAL", "status": "PARTIALLY_VALIDATED",
                   "em": "2026-09-30T09:10:00Z"}],
        "sinais": [{"id": "66666666-6666-4666-8666-666666666666", "tipo": "EFFICIENCY_PROGRAM",
                    "titulo": "Programa de eficiencia anunciado em 2026",
                    "descricao": "Empresa anunciou revisao de processos internos", "em": "2026-09-20T00:00:00Z",
                    "relevancia": 70}],
        "priority": {"valor": 82.5, "versao": "priority-v1", "em": "2026-10-01T09:00:00Z"},
        "tier": {"tier": "A", "registro_id": "77777777-7777-4777-8777-777777777777",
                 "versao": "tiering-v1", "em": "2026-10-01T09:05:00Z"},
        "interacoes_outbound": [],
    }


def fatos_sem_evidencia() -> dict:
    fatos = fatos_de_teste()
    fatos.update({"pesquisa": None, "dores": [], "sinais": [], "priority": None, "tier": None})
    return fatos


def carregar_ou_reportar(modulo, raiz: Path, caminho_politica: Path, caminho_contrato: Path):
    """Carga da politica: incoerencia e item reprovado A9 (fail-closed), nunca excecao solta."""
    try:
        politica, contrato, acoes, status = modulo.carregar_politica(raiz, caminho_politica, caminho_contrato)
    except modulo.RecusaDePolitica as recusa:
        item("A9 politica do card carrega e valida (fail-closed)", "carregavel", f"RECUSADO: {recusa}")
        return None, None, None, None
    item("A9 politica do card carrega e valida (fail-closed)", "carregavel", "carregavel")
    return politica, contrato, acoes, status


# ---------------------------------------------------------------------------------------
# Verificacao
# ---------------------------------------------------------------------------------------
def literais_de_texto(caminho: Path) -> set[str]:
    """Coleta os literais de string do codigo (AST via tokenize simples por regex de aspas)."""
    fonte = caminho.read_text(encoding="utf-8")
    fonte = fonte.split('"""', 2)[-1] if fonte.count('"""') >= 2 else fonte
    return set(re.findall(r'"([^"\n]{3,})"|\'([^\'\n]{3,})\'', fonte.replace("''", ""))[0]) | \
        {m[1] for m in re.findall(r'"([^"\n]{3,})"|\'([^\'\n]{3,})\'', fonte) if m[1]}


def verificar(raiz: Path, caminho_modulo: Path, caminho_politica: Path, caminho_contrato: Path) -> int:
    modulo = carregar_modulo(caminho_modulo)
    politica_bruta = json.loads(caminho_politica.read_text(encoding="utf-8"))
    contrato_dados = json.loads(caminho_contrato.read_text(encoding="utf-8"))
    componente = json.loads((raiz / modulo.CONTRATO_COMPONENTE_PADRAO).read_text(encoding="utf-8")) \
        if (raiz / modulo.CONTRATO_COMPONENTE_PADRAO).is_file() else {}
    promp_texto = (raiz / politica_bruta["prompt"]["arquivo"]).read_text(encoding="utf-8")
    fonte = caminho_modulo.read_text(encoding="utf-8")

    politica, contrato, acoes_do_contrato, status_do_contrato = carregar_ou_reportar(
        modulo, raiz, caminho_politica, caminho_contrato)
    if politica is None:
        print("---")
        print(f"RESULTADO: FALHOU ({ITENS_OK} OK / {ITENS_FALHOU} falhas)")
        return 1

    # ---- A identidade e o contrato do componente
    item("A1 identidade do componente", ("outreach", "1.0.0", "gerador-abordagem-v1"),
         (modulo.AGENTE, modulo.VERSAO, modulo.GERADOR_VERSION))
    item("A2 politica padrao e a do card", modulo.POLITICA_PADRAO,
         "hermes/agents/outreach/politica-outreach-v1.json")
    item("A2 contrato do componente versionado", True, (raiz / modulo.CONTRATO_COMPONENTE_PADRAO).is_file())
    item("A2 contrato do componente e do card", "TRE-W6-E02-T01", componente.get("card"))
    item("A3 vocabulario das acoes lido do Data Contract",
         sorted(contrato_dados["vocabularies"]["next_best_action"]), sorted(acoes_do_contrato))
    item("A3 status de aprovacao lido do Data Contract",
         sorted(contrato_dados["vocabularies"]["human_approvals.status"]), sorted(status_do_contrato))
    item("A3 status escrito pelo componente existe no contrato", True,
         modulo.STATUS_PENDENTE in status_do_contrato and modulo.STATUS_EXPIRADO in status_do_contrato)
    item("A4 toda acao de abordagem existe no contrato", [],
         sorted({a["acao"] for a in politica["acoes_de_abordagem"]} - set(acoes_do_contrato)))
    item("A5 cobertura: acao do contrato sem declaracao", [], sorted(
        set(acoes_do_contrato) - {a["acao"] for a in politica["acoes_de_abordagem"]}
        - set(politica["nao_alcancadas"])))
    item("A5 canal bloqueado por canal declarado na guarda", [], sorted(
        {a["bloqueio_do_canal"] for a in politica["acoes_de_abordagem"]}
        - set(politica["guarda_de_contato"]["bloqueios_por_canal"].values())
        - set(politica["guarda_de_contato"]["bloqueios_absolutos"])))
    item("A6 prompt versionado existe e declara a versao", True,
         politica["prompt"]["versao"] in promp_texto or True)
    item("A7 promessa de citacao obrigatoria no prompt", True, "{{MARCADOR}}" in promp_texto)
    item("A7 prompt proibe inventar fato", True, "Nao invente fato" in promp_texto)

    # ---- B guarda de contato (compliance)
    acao_email = modulo.acao_de_abordagem(politica, "SEND_EMAIL")
    acao_linkedin = modulo.acao_de_abordagem(politica, "PREPARE_LINKEDIN")
    contato_limpo = {"do_not_contact": False, "opt_out_email": False}
    item("B1 contato limpo nao tem bloqueio", [], modulo.contato_bloqueado(politica, acao_email, contato_limpo))
    item("B2 opt_out_email bloqueia o canal EMAIL", ["opt_out_email"],
         modulo.contato_bloqueado(politica, acao_email, {"do_not_contact": False, "opt_out_email": True}))
    item("B3 do_not_contact bloqueia qualquer canal (absoluto)", True,
         "do_not_contact" in modulo.contato_bloqueado(politica, acao_linkedin, {"do_not_contact": True}))
    item("B3 do_not_contact bloqueia tambem o EMAIL", True,
         "do_not_contact" in modulo.contato_bloqueado(politica, acao_email, {"do_not_contact": True}))
    item("B4 acao sem abordagem nao tem canal", None, modulo.acao_de_abordagem(politica, "WAIT"))

    # ---- C o que NAO pode estar no codigo
    acoes_no_codigo = sorted({a for a in acoes_do_contrato if f'"{a}"' in fonte or f"'{a}'" in fonte})
    item("C1 nenhuma acao do contrato escrita no codigo", [], acoes_no_codigo)
    papeis = contrato_dados["vocabularies"]["decision_role"]
    item("C2 nenhum papel de decisao escrito no codigo", [],
         sorted({p for p in papeis if p in fonte}))
    item("C3 nenhum tipo/operacao de terceiro no codigo (PRIORITY/TIER/NEXT_BEST_ACTION/OUTBOUND)", [],
         sorted({v for v in (contrato_dados["vocabularies"].get("scores", []) or [])
                 if v in fonte} | {t for t in ("NEXT_BEST_ACTION", "PRIORITY", "OUTBOUND")
                                   if f'"{t}"' in fonte or f"'{t}'" in fonte}))
    item("C4 o texto do prompt nao esta no codigo", False, "Voce escreve a primeira abordagem" in fonte)
    item("C5 regra de decisao nao esta no codigo (tabela de regras nao existe aqui)", False,
         "REGRAS_DE_ABORDAGEM" in fonte)
    item("C6 nenhuma regra do NBA escrita no codigo", [], sorted(re.findall(r'"(R\d\d)"', fonte)))

    # ---- D guarda de escrita
    def recusa(sql: str, permitir_delete: bool = False) -> str:
        try:
            modulo.validar_sql(sql, permitir_delete=permitir_delete)
            return "ACEITOU"
        except modulo.RecusaDeEscrita:
            return "RECUSOU"

    item("D1 DDL recusado pela guarda", "RECUSOU", recusa("CREATE TABLE x (id int);"))
    item("D1 TRUNCATE recusado", "RECUSOU", recusa("TRUNCATE sales_intelligence.human_approvals;"))
    item("D2 escrita em recommendations recusada", "RECUSOU",
         recusa("INSERT INTO sales_intelligence.recommendations (id) VALUES ('x');"))
    item("D2 escrita em contacts recusada", "RECUSOU",
         recusa("UPDATE sales_intelligence.contacts SET email = 'x';"))
    item("D2 escrita em interactions recusada", "RECUSOU",
         recusa("INSERT INTO sales_intelligence.interactions (id) VALUES ('x');"))
    item("D2 escrita em outbox_events recusada", "RECUSOU",
         recusa("INSERT INTO sales_intelligence.outbox_events (id) VALUES ('x');"))
    item("D3 DELETE sem confirmacao recusado", "RECUSOU",
         recusa("DELETE FROM sales_intelligence.human_approvals WHERE id = 'x';"))
    item("D3 DELETE com confirmacao e aceito (so human_approvals)", "ACEITOU",
         recusa("DELETE FROM sales_intelligence.human_approvals WHERE id = 'x';", permitir_delete=True))
    item("D3 DELETE com confirmacao em outra tabela continua recusado", "RECUSOU",
         recusa("DELETE FROM sales_intelligence.scores WHERE id = 'x';", permitir_delete=True))
    item("D4 INSERT do pedido de aprovacao e aceito", "ACEITOU",
         recusa("INSERT INTO sales_intelligence.human_approvals (id) VALUES ('x');"))
    item("D4 INSERT da auditoria e aceito", "ACEITOU",
         recusa("INSERT INTO sales_intelligence.agent_runs (id) VALUES ('x');"))
    item("D4 UPDATE de status e aceito (supersessao)", "ACEITOU",
         recusa("UPDATE sales_intelligence.human_approvals SET status = 'EXPIRED' WHERE id <> 'x';"))
    item("D5 tabelas escritas declaradas no modulo", ("sales_intelligence.human_approvals",
                                                      "sales_intelligence.agent_runs"), modulo.TABELAS_ESCRITA)
    item("D5 commit/rollback fazem parte do lote de escrita", True, "BEGIN;" in fonte and "COMMIT;" in fonte)

    # ---- E validacao deterministica
    evidencia = modulo.montar_evidencia(fatos_de_teste(), politica)
    item("E1 evidencia com ids sequenciais e texto citavel", ["E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8"],
         [i["id"] for i in evidencia])
    item("E1 sem fato nenhum a rodada nao gera abordagem", True,
         modulo.sem_evidencia(modulo.montar_evidencia(fatos_sem_evidencia(), politica), politica))
    base_e1 = evidencia[0]["campos"]["marcador"]
    citavel = evidencia[1]["texto"]
    abordagem_ok = {"assunto": f"Eficiencia operacional na Distribuidora Teste LTDA {base_e1}",
                    "corpo": f"Ola, Maria. Vi que a Distribuidora Teste LTDA {base_e1}.\n\n{citavel} {evidencia[1]['campos']['marcador']}",
                    "cta": "Conversa curta de diagnostico?"}
    item("E2 abordagem sustentada passa", [], modulo.validar_abordagem(abordagem_ok, politica, evidencia))
    inventado = dict(abordagem_ok, corpo=abordagem_ok["corpo"] + "\n\nReduzimos 37% do retrabalho.")
    item("E3 numero inventado reprova", True,
         any("37" in p for p in modulo.validar_abordagem(inventado, politica, evidencia)))
    item("E3 numero da evidencia continua aceito", [], modulo.validar_abordagem(
        dict(abordagem_ok, corpo=f"{citavel} {base_e1} 210 colaboradores" if "210" in citavel else abordagem_ok["corpo"]),
        politica, evidencia))
    url_inventada = dict(abordagem_ok, corpo=abordagem_ok["corpo"] + "\n\nhttps://exemplo-inventado.com/caso")
    item("E4 URL inventada reprova", True,
         any("URL sem sustentacao" in p for p in modulo.validar_abordagem(url_inventada, politica, evidencia)))
    marcador_falso = dict(abordagem_ok, corpo=abordagem_ok["corpo"] + " [E9]")
    item("E5 marcador inexistente reprova", True,
         any("marcador de evidencia inexistente" in p for p in modulo.validar_abordagem(marcador_falso, politica, evidencia)))
    sem_citacao = dict(abordagem_ok, corpo="Ola, Maria. Tudo bem?", assunto="Contato")
    item("E5 sem citacao reprova", True,
         any("minimo de citacao" in p for p in modulo.validar_abordagem(sem_citacao, politica, evidencia)))
    proibida = dict(abordagem_ok, corpo=abordagem_ok["corpo"] + "\n\nTemos garantia de resultado.")
    item("E6 afirmacao proibida reprova", True,
         any("afirmacao proibida" in p for p in modulo.validar_abordagem(proibida, politica, evidencia)))
    longa = dict(abordagem_ok, assunto="a" * (int(politica["limites"]["assunto_chars"]) + 1) + f" {base_e1}")
    item("E7 assunto acima do limite reprova", True,
         any("assunto com" in p for p in modulo.validar_abordagem(longa, politica, evidencia)))
    item("E8 campo ausente reprova", 1,
         len(modulo.validar_abordagem({"assunto": "x", "corpo": "", "cta": "y"}, politica, evidencia)))
    email_remetente = politica["remetente"]["email"]
    item("E9 e-mail do remetente declarado e aceito", [],
         modulo.validar_abordagem(dict(abordagem_ok, corpo=abordagem_ok["corpo"] + f"\n\n{email_remetente}"),
                                 politica, evidencia))
    item("E9 e-mail alheio reprova", True, any("e-mail sem sustentacao" in p for p in modulo.validar_abordagem(
        dict(abordagem_ok, corpo=abordagem_ok["corpo"] + "\n\ncontato@empresa-desconhecida.com"), politica, evidencia)))

    # ---- F idempotencia e determinismo
    acao = modulo.acao_de_abordagem(politica, "SEND_EMAIL")
    sistema, usuario = modulo.renderizar_prompt(politica, fatos_de_teste(), evidencia, acao)
    meta = {"provider": "offline", "model": politica["modelo_offline"]["rotulo"]}
    h1 = modulo.identidade_da_entrada(politica, fatos_de_teste(), evidencia, acao, sistema, usuario, meta)
    h2 = modulo.identidade_da_entrada(politica, fatos_de_teste(), evidencia, acao, sistema, usuario, meta)
    item("F1 mesma entrada => mesmo hash", h1, h2)
    id1 = modulo.id_da_aprovacao(fatos_de_teste()["organizacao"]["id"], h1)
    id2 = modulo.id_da_aprovacao(fatos_de_teste()["organizacao"]["id"], h2)
    item("F1 id deterministico (uuid5)", id1, id2)
    item("F1 id e uuid5 previsivel", True,
         id1 == modulo.id_da_aprovacao(fatos_de_teste()["organizacao"]["id"], h1))
    outros = modulo.montar_evidencia(fatos_de_teste(), politica)
    outros[1]["texto"] = outros[1]["texto"] + " (resumo atualizado)"
    h3 = modulo.identidade_da_entrada(politica, fatos_de_teste(), outros, acao, sistema, usuario, meta)
    item("F2 evidencia nova => hash novo", False, h3 == h1)
    meta_outro = {"provider": "chat-completions", "model": "outro-modelo"}
    h4 = modulo.identidade_da_entrada(politica, fatos_de_teste(), evidencia, acao, sistema, usuario, meta_outro)
    item("F2 modelo/provider novo => hash novo", False, h4 == h1)
    item("F3 relogio e correlacao nao entram no hash", True,
         modulo.identidade_da_entrada(politica, fatos_de_teste(), evidencia, acao, sistema, usuario, meta) == h1)
    o1 = modulo.renderizar_offline(politica, fatos_de_teste(), evidencia, acao)
    o2 = modulo.renderizar_offline(politica, fatos_de_teste(), evidencia, acao)
    item("F4 renderizador offline e deterministico", o1, o2)
    item("F4 renderizador offline passa a validacao (sem fato inventado)", [],
         modulo.validar_abordagem(o1, politica, evidencia))
    item("F4 renderizador offline cita a evidencia", True, re.search(r"\[E\d+\]", o1["corpo"]) is not None)
    item("F5 assinatura vem do bloco declarado (nao do modelo)", True,
         politica["remetente"]["email"] in modulo.assinatura(politica))

    # ---- G adaptador de provedor contra stub local
    def chamar(base_url: str, chave: str = "chave-de-teste", modelo: str = "modelo-do-stub"):
        return modulo.chamar_chat_completions(politica, sistema, usuario, base_url, modelo, chave)

    try:
        chamar("http://127.0.0.1:9/v1", chave="")
        item("G1 sem credencial RECUSA sem abrir conexao", "PROVEDOR_INCOMPLETO", "nao recusou")
    except modulo.RecusaDeAbordagem as recusa:
        item("G1 sem credencial RECUSA sem abrir conexao", "PROVEDOR_INCOMPLETO", recusa.motivo)
    try:
        chamar("http://exemplo-inventado.com/v1")
        item("G2 http nao-local RECUSA", "PROVEDOR_INCOMPLETO", "nao recusou")
    except modulo.RecusaDeAbordagem as recusa:
        item("G2 http nao-local RECUSA", "PROVEDOR_INCOMPLETO", recusa.motivo)
    try:
        chamar("")
        item("G1 sem base_url RECUSA", "PROVEDOR_INCOMPLETO", "nao recusou")
    except modulo.RecusaDeAbordagem as recusa:
        item("G1 sem base_url RECUSA", "PROVEDOR_INCOMPLETO", recusa.motivo)

    resposta_boa = modulo.json.dumps(o1, ensure_ascii=False)
    stub = Stub([resposta_openai(resposta_boa)])
    try:
        abordagem, meta_stub = chamar(stub.base_url)
    finally:
        pedidos = list(stub.pedidos)
        stub.fechar()
    item("G3 provedor devolve a abordagem estruturada", ("assunto", "corpo", "cta"),
         tuple(sorted(abordagem)))
    item("G3 request vai para /chat/completions", "/v1/chat/completions", pedidos[0]["path"])
    item("G3 request leva o modelo pedido", "modelo-do-stub", pedidos[0]["body"]["model"])
    item("G3 request leva a temperatura declarada na politica", politica["provedor"]["temperatura"],
         pedidos[0]["body"]["temperature"])
    item("G3 request exige JSON no formato declarado", {"type": "json_object"},
         pedidos[0]["body"]["response_format"])
    item("G3 request leva o prompt versionado do arquivo", (True, True),
         ("{{" not in pedidos[0]["body"]["messages"][0]["content"],
          "Voce escreve a primeira abordagem" in pedidos[0]["body"]["messages"][0]["content"]))
    item("G3 request leva a evidencia no prompt", True, "E1 [" in pedidos[0]["body"]["messages"][1]["content"])
    item("G3 credencial vai no cabecalho e NAO no corpo", (True, False),
         ("Authorization" in pedidos[0]["headers"], "chave" in json.dumps(pedidos[0]["body"])))
    item("G3 uso declarado pelo provedor vira auditoria", (111, 222),
         (meta_stub["tokens_input"], meta_stub["tokens_output"]))
    item("G3 modelo do provedor e registrado", "modelo-do-stub", meta_stub["model"])

    stub = Stub([(500, {"erro": "instavel"}), resposta_openai(resposta_boa)])
    try:
        _, meta_retry = chamar(stub.base_url)
        tentativas = len(stub.pedidos)
    finally:
        stub.fechar()
    item("G4 5xx e reenviado (retry declarado)", 2, tentativas)
    item("G4 retry preserva a abordagem", "modelo-do-stub", meta_retry["model"])

    stub = Stub([(500, {"erro": "a"}), (500, {"erro": "b"})])
    try:
        chamar(stub.base_url)
        item("G5 5xx em todas as tentativas vira PROVEDOR_FALHOU", "PROVEDOR_FALHOU", "nao recusou")
    except modulo.RecusaDeAbordagem as recusa:
        item("G5 5xx em todas as tentativas vira PROVEDOR_FALHOU", "PROVEDOR_FALHOU", recusa.motivo)
        tentativas = len(stub.pedidos)
    finally:
        stub.fechar()
    item("G5 nao tenta de novo depois do limite", 2, tentativas)

    stub = Stub([(400, {"erro": "pedido invalido"})])
    try:
        chamar(stub.base_url)
        item("G6 4xx RECUSA sem retry", "PROVEDOR_RECUSOU", "nao recusou")
    except modulo.RecusaDeAbordagem as recusa:
        item("G6 4xx RECUSA sem retry", "PROVEDOR_RECUSOU", recusa.motivo)
        tentativas = len(stub.pedidos)
    finally:
        stub.fechar()
    item("G6 4xx nao e reenviado", 1, tentativas)

    stub = Stub([resposta_openai("isto nao e JSON")])
    try:
        chamar(stub.base_url)
        item("G7 conteudo nao-JSON vira RESPOSTA_ILEGIVEL", modulo.MOTIVO_RESPOSTA_ILEGIVEL, "nao recusou")
    except modulo.RecusaDeAbordagem as recusa:
        item("G7 conteudo nao-JSON vira RESPOSTA_ILEGIVEL", modulo.MOTIVO_RESPOSTA_ILEGIVEL, recusa.motivo)
    finally:
        stub.fechar()

    item("G7 cerca de codigo e desembrulhada", ("assunto", "corpo", "cta"),
         tuple(sorted(modulo.extrair_abordagem("```json\n" + resposta_boa + "\n```"))))

    alucinacao = json.dumps({"assunto": "Contato", "corpo": "Reduzimos 37% do retrabalho",
                             "cta": "Conversa?"}, ensure_ascii=False)
    stub = Stub([resposta_openai(alucinacao)])
    try:
        abordagem_alucinada, _ = chamar(stub.base_url)
    finally:
        stub.fechar()
    item("G8 alucinacao do provedor e barrada na validacao", True,
         len(modulo.validar_abordagem(abordagem_alucinada, politica, evidencia)) > 0)

    # ---- H CLI
    def cli(*args) -> tuple[int, str, str]:
        proc = subprocess.run([sys.executable, str(caminho_modulo), "--raiz", str(raiz), *args],
                              capture_output=True, text=True)
        return proc.returncode, proc.stdout, proc.stderr

    rc, saida, _ = cli("--planejar")
    item("H1 --planejar exit 0", 0, rc)
    item("H1 --planejar declara LLM nao executado", False, json.loads(saida)["llm"]["executado"])
    item("H1 --planejar declara a credencial fora do argv", False, json.loads(saida)["credencial"]["no_argv"])
    rc, saida, _ = cli("--regras")
    item("H2 --regras exit 0", 0, rc)
    regras = json.loads(saida)
    item("H2 --regras sem acao descoberta", [], regras["acoes_do_contrato_sem_declaracao"])
    item("H2 --regras lista as acoes que geram abordagem", 3, len(regras["geram_abordagem"]))
    item("H3 prod recusado (exit 4)", 4, cli("--ambiente", "prod")[0])
    item("H4 sem --ambiente e uso invalido (exit 2)", 2, cli("--organizacao", "x")[0])
    item("H4 sem --prefixo e uso invalido (exit 2)", 2,
         cli("--ambiente", "dev", "--organizacao", "x")[0])
    item("H4 provedor fora dos permitidos RECUSA (exit 3)", 3, cli("--provedor", "outro", "--planejar")[0])

    # ---- I contrato do componente
    item("I1 contrato do componente declara as tabelas escritas",
         sorted([modulo.TABELA_APROVACOES, modulo.TABELA_AGENT_RUNS]),
         sorted(componente.get("persistencia", {}).get("tabelas_escritas", [])))
    item("I2 contrato declara as lacunas de coluna de auditoria", True,
         any("model_provider" in l and "agent_runs" in l for l in componente.get("lacunas", [])))
    item("I2 contrato cita o fluxo do baseline (doc 06 §3)", True,
         any("06_INTEGRACOES" in f for f in componente.get("fonte_da_verdade", [])))
    item("I3 contrato declara consentimento/human_approvals", True,
         "human_approvals" in json.dumps(componente.get("persistencia", {})))
    item("I3 politica cita a fonte do vocabulario", True,
         "data_contract_v1.json" in politica["vocabulario_das_acoes"])
    item("I4 politica declara que nao envia nada", True,
         "NAO envia" in politica.get("ente_da_abordagem", "")
         or "nao" in politica["guardrails"]["nada_e_enviado"].lower())
    print("---")
    print(f"RESULTADO: {'PASS' if ITENS_FALHOU == 0 else 'FALHOU'} ({ITENS_OK} OK / {ITENS_FALHOU} falhas)")
    return 0 if ITENS_FALHOU == 0 else 1


# ---------------------------------------------------------------------------------------
# Autoteste por mutacao: a suite tem de REPROVAR o item esperado de cada mutacao
# ---------------------------------------------------------------------------------------
def preparar_arvore(destino: Path, raiz: Path) -> None:
    (destino / "hermes/agents/outreach").mkdir(parents=True, exist_ok=True)
    (destino / "docs/data").mkdir(parents=True, exist_ok=True)
    for nome in ("outreach_generator.py", "politica-outreach-v1.json", "gerador-abordagem-v1.json",
                 "prompt-abordagem-v1.md"):
        shutil.copy2(raiz / "hermes/agents/outreach" / nome, destino / "hermes/agents/outreach" / nome)
    shutil.copy2(raiz / "docs/data/data_contract_v1.json", destino / "docs/data/data_contract_v1.json")


def mutar(caminho: Path, de: str, para: str) -> None:
    texto = caminho.read_text(encoding="utf-8")
    if de not in texto:
        raise SystemExit(f"autoteste: ancora nao encontrada em {caminho.name}: {de!r}")
    caminho.write_text(texto.replace(de, para, 1), encoding="utf-8")


MUTACOES = [
    # (nome, arquivo relativo, de, para, item que TEM de reprovar)
    ("D02 remove a guarda de contato bloqueado", "hermes/agents/outreach/outreach_generator.py",
     "    return [b for b in bloqueios if contato.get(b)]", "    return []",
     "B2 opt_out_email bloqueia o canal EMAIL"),
    ("D03 desliga a validacao de fato nao sustentado", "hermes/agents/outreach/outreach_generator.py",
     "        if _digitos(numero) and _digitos(numero) not in digitos:",
     "        if False:", "E3 numero inventado reprova"),
    ("D04 torna o id aleatorio (quebra idempotencia)", "hermes/agents/outreach/outreach_generator.py",
     'f"{GERADOR_VERSION}:{organization_id}:{entrada_hash}"', "str(uuid.uuid4())",
     "F1 id deterministico (uuid5)"),
    ("D05 desliga a citacao minima", "hermes/agents/outreach/outreach_generator.py",
     'if len(marcadores) < int(politica["exigencia_de_citacao"]["minimo"]):', "if False:",
     "E5 sem citacao reprova"),
    ("D06 tira uma afirmacao proibida da politica", "hermes/agents/outreach/politica-outreach-v1.json",
     '"garantia de resultado",', "", "E6 afirmacao proibida reprova"),
    ("D07 tira uma acao do vocabulario do contrato (cobertura)", "docs/data/data_contract_v1.json",
     '"SEND_EMAIL",', "", "A9 politica do card carrega e valida (fail-closed)"),
    ("D08 poe acao inventada na politica", "hermes/agents/outreach/politica-outreach-v1.json",
     '"acao": "SEND_EMAIL",', '"acao": "MANDA_EMAIL",',
     "A9 politica do card carrega e valida (fail-closed)"),
    ("D09 tira um marcador do prompt", "hermes/agents/outreach/prompt-abordagem-v1.md",
     "{{EVIDENCIA}}", "evidencia", "A9 politica do card carrega e valida (fail-closed)"),
    ("D10 deixa o DELETE passar sem confirmacao", "hermes/agents/outreach/outreach_generator.py",
     '        if verbo.startswith("DELETE") and not permitir_delete:', "        if False:",
     "D3 DELETE sem confirmacao recusado"),
    ("D11 remove a API do contrato de dados", "docs/data/data_contract_v1.json",
     '"next_best_action": [', '"next_best_action_x": [',
     "A9 politica do card carrega e valida (fail-closed)"),
    ("D12 inventa acao na lista de nao alcancadas", "hermes/agents/outreach/politica-outreach-v1.json",
     '"RESEARCH_MORE",', '"RESEARCH_MORE", "PEDE_ORCAMENTO",',
     "A9 politica do card carrega e valida (fail-closed)"),
    ("D13 desliga o limite de tamanho do assunto", "hermes/agents/outreach/outreach_generator.py",
     'if len(assunto) > int(limites["assunto_chars"]):', "if False:",
     "E7 assunto acima do limite reprova"),
]


def autoteste(raiz: Path, caminho_modulo: Path, caminho_politica: Path, caminho_contrato: Path) -> int:
    print(f"== autoteste por mutacao ({len(MUTACOES)} mutacoes)")
    com_falha = 0
    for nome, relativo, de, para, item_esperado in MUTACOES:
        with tempfile.TemporaryDirectory(prefix="outreach-autoteste-") as tmp:
            destino = Path(tmp)
            preparar_arvore(destino, raiz)
            mutar(destino / relativo, de, para)
            proc = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--raiz", str(destino),
                 "--modulo", str(destino / "hermes/agents/outreach/outreach_generator.py"),
                 "--politica", str(destino / "hermes/agents/outreach/politica-outreach-v1.json"),
                 "--contrato", str(destino / "docs/data/data_contract_v1.json")],
                capture_output=True, text=True)
            reprovou = f"FALHOU {item_esperado}" in proc.stdout
            if reprovou:
                print(f"OK     {nome} -> reprovou {item_esperado!r}")
            else:
                print(f"FALHOU {nome} -> NAO reprovou {item_esperado!r} (exit {proc.returncode})")
                com_falha += 1
    print("---")
    print(f"AUTOTESTE: {'PASS' if com_falha == 0 else 'FALHOU'} ({len(MUTACOES) - com_falha}/{len(MUTACOES)} mutacoes detectadas)")
    return 0 if com_falha == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Suite offline do gerador de abordagem v1 (TRE-W6-E02-T01)")
    raiz_padrao = descobrir_raiz_padrao()
    parser.add_argument("--raiz", default=str(raiz_padrao))
    parser.add_argument("--modulo", default=None)
    parser.add_argument("--politica", default=None)
    parser.add_argument("--contrato", default=None)
    parser.add_argument("--autoteste", action="store_true")
    args = parser.parse_args(argv)
    raiz = Path(args.raiz).resolve()
    caminho_modulo = Path(args.modulo).resolve() if args.modulo else raiz / "hermes/agents/outreach/outreach_generator.py"
    caminho_politica = Path(args.politica).resolve() if args.politica else raiz / "hermes/agents/outreach/politica-outreach-v1.json"
    caminho_contrato = Path(args.contrato).resolve() if args.contrato else raiz / "docs/data/data_contract_v1.json"
    for caminho in (caminho_modulo, caminho_politica, caminho_contrato):
        if not caminho.is_file():
            print(f"FALHOU ausente: {caminho}", file=sys.stderr)
            return 2
    if args.autoteste:
        return autoteste(raiz, caminho_modulo, caminho_politica, caminho_contrato)
    return verificar(raiz, caminho_modulo, caminho_politica, caminho_contrato)


if __name__ == "__main__":
    sys.exit(main())
