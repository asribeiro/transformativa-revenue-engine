#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Suite offline do componente de atualizacao do Odoo a partir das respostas (TRE-W6-E06-T01).

Nao precisa de banco, de Odoo e nem de rede externa: usa porta de banco FAKE, cliente HTTP FAKE e
(somente nos itens de envelope) um servidor HTTP local em 127.0.0.1.

  python3 scripts/agentes/verificar_atualizacao_odoo.py                 # suite
  python3 scripts/agentes/verificar_atualizacao_odoo.py --prova-de-dente # mutacoes: cada uma DEVE reprovar

Saida: linhas `OK n. <item>` / `FALHOU n. <item>: <detalhe>` e, no fim, `PASS (N itens, M falhas)`
(exit 0) ou `FALHOU (N itens, M falhas)` (exit 1).
"""

from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.abspath(os.path.join(AQUI, "..", ".."))
MODULO = os.path.join(RAIZ, "hermes", "agentes", "respostas", "atualizacao_odoo.py")
CONTRATO = os.path.join(RAIZ, "hermes", "agentes", "respostas", "atualizacao-odoo-respostas-v1.json")

ITENS = []
FALHAS = []


def item(nome: str, condicao: bool, detalhe: str = "") -> None:
    ITENS.append(nome)
    if condicao:
        print(f"OK {len(ITENS)}. {nome}")
    else:
        FALHAS.append(nome)
        print(f"FALHOU {len(ITENS)}. {nome}: {detalhe}")


def carregar(caminho_modulo: str = MODULO, nome: str = "atualizacao_odoo"):
    spec = importlib.util.spec_from_file_location(nome, caminho_modulo)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


M = carregar()
CONTRATO_DADOS = json.load(open(CONTRATO, encoding="utf-8"))


def interacao(**campos) -> dict:
    base = {"interaction_id": "11111111-1111-1111-1111-111111111111", "response_category": "INTERESSE",
            "intent": "PEDIDO_DE_CONVERSA", "sentiment": "POSITIVO", "subject": "Podemos conversar?",
            "odoo_lead_id": 77, "contact_id": "22222222-2222-2222-2222-222222222222",
            "contato_odoo_id": 12, "email": "lead@cliente.com.br", "full_name": "Lead",
            "empresa_odoo_id": 34, "empresa": "Cliente"}
    base.update(campos)
    return base


class ClienteFake:
    """Cliente de API falso: registra as chamadas e responde o envelope do controlador."""

    def __init__(self, lead=None, falha_em=None):
        self.chamadas = []
        self.lead = lead if lead is not None else {"id": 77, "name": "Cliente — oportunidade",
                                                   "tf_opportunity_id": "abc-123",
                                                   "tf_next_best_action": None}
        self.falha_em = falha_em

    def pedir(self, operacao, parametros, chave=None, dry_run=False, correlation_id=None):
        self.chamadas.append({"operacao": operacao, "parametros": copy.deepcopy(parametros),
                              "chave": chave, "dry_run": dry_run})
        if self.falha_em == operacao:
            raise M.Recusa("API_RECUSOU", f"{operacao}: HTTP 422 — campo_nao_declarado")
        dados = ({"registros": [self.lead], "total": 1} if operacao == "crm_registros_ler"
                 else {"acao_efetiva": "atualizar", "ids": [77]})
        if dry_run:
            dados = {"dry_run": True, "acao_efetiva": "atualizar", "id": 77}
        return {"ok": True, "operacao": operacao, "ambiente": "dev", "politica_versao": "1.3.0",
                "correlation_id": correlation_id, "idempotency_key": chave, "dry_run": dry_run,
                "dados": dados}


class PortaFake:
    def __init__(self, interacoes=None, trilha=None):
        self.interacoes = interacoes if interacoes is not None else [interacao()]
        self.trilha = trilha or []
        self.escritas = []

    def consultar(self, sql):
        if "FROM sales_intelligence.sync_events" in sql:
            alvo = sql.split("idempotency_key = '")[1].split("'")[0]
            return [l for l in self.trilha if l["idempotency_key"] == alvo][:1]
        if "FROM sales_intelligence.interactions" in sql:
            return self.interacoes
        return []

    def executar(self, sql):
        self.escritas.append(sql)
        if "RETURNING" in sql.upper():
            return [{"id": "33333333-3333-3333-3333-333333333333"}]
        return []


def config(**env) -> "M.Configuracao":
    base = {"TRE_AMBIENTE": "dev", "TRE_ODOO_API_URL": "http://127.0.0.1:8799",
            "TRE_ODOO_API_KEY": "chave-de-teste-1234",
            "TRE_ODOO_RESPOSTAS_PORTA_BANCO": "docker exec -i pg-e06-acc psql -U sales_ai -d sales_intelligence",
            "TRE_ODOO_TIPO_ATIVIDADE_RESPOSTA": "1"}
    base.update({k: v for k, v in env.items() if v is not None})
    return M.Configuracao(base)


# ---------------------------------------------------------------- 1. contrato
def testar_contrato():
    falhas = M.validar_contrato(CONTRATO_DADOS)
    item("1. contrato versionado carrega e valida", falhas == [], str(falhas))
    item("2. contrato declara a versao", CONTRATO_DADOS["versao"] == "atualizacao-odoo-respostas-v1")
    vocab = set(CONTRATO_DADOS["vocabulario_fechado"])
    com_ato = {a["categoria"] for a in CONTRATO_DADOS["atos_por_categoria"]}
    sem_ato = set(CONTRATO_DADOS["categorias_sem_ato"])
    item("3. toda categoria do vocabulario tem ato declarado ou exclusao declarada",
         vocab == (com_ato | sem_ato), f"sem lugar: {sorted(vocab - (com_ato | sem_ato))}")
    # Os campos que o CODIGO escreve tem de caber nos campos DECLARADOS para a operacao: o contrato
    # declara `campos_escritos` e o item 5 confere contra o plano real.
    fora = []
    for operacao, declaracao in CONTRATO_DADOS["campos_por_operacao"].items():
        for campo in declaracao.get("campos_escritos") or []:
            if campo not in (declaracao.get("campos") or []):
                fora.append((operacao, campo))
    item("4. todo campo escrito esta' entre os campos declarados da operacao", fora == [], str(fora))
    # categoria fora do vocabulario tem de RECUSAR o contrato (nunca cair em ato generico)
    ruim = copy.deepcopy(CONTRATO_DADOS)
    ruim["atos_por_categoria"][0]["categoria"] = "CATEGORIA_INVENTADA"
    item("5. categoria fora do vocabulario RECUSA o contrato",
         any("fora do vocabulario" in f for f in M.validar_contrato(ruim)))
    ruim2 = copy.deepcopy(CONTRATO_DADOS)
    ruim2["atos_por_categoria"][0].pop("evento")
    item("6. ato sem evento declarado RECUSA o contrato",
         any("sem evento" in f for f in M.validar_contrato(ruim2)))
    ruim3 = copy.deepcopy(CONTRATO_DADOS)
    ruim3["categorias_sem_ato"] = list(ruim3["categorias_sem_ato"]) + ["INTERESSE"]
    item("7. categoria com ato E em categorias_sem_ato RECUSA (ambiguidade)",
         any("ambiguidade" in f for f in M.validar_contrato(ruim3)))


# ---------------------------------------------------------------- 2. fonte e guardas
def testar_fonte_e_guardas():
    item("8. auditoria da propria fonte nao acusa violacao",
         M.auditar_fonte() == [], str(M.auditar_fonte()))
    with tempfile.TemporaryDirectory() as tmp:
        alvo = os.path.join(tmp, "mut.py")
        texto = open(MODULO, encoding="utf-8").read().replace(
            "def auditar_fonte", "DELETE FROM sales_intelligence.interactions\n\ndef auditar_fonte", 1)
        open(alvo, "w", encoding="utf-8").write(texto)
        violacoes = M.auditar_fonte(alvo)
    item("9. auditoria de fonte REPROVA quando o modulo carrega escrita crua (dente do invariante 4)",
         any(v["padrao"] == "sql_de_escrita_crua" for v in violacoes), str(violacoes))

    item("10. prod RECUSA por desenho (exit 4)",
         ("PRODUCAO_RECUSADA", "prod recusa") in [(m, d[:11]) for m, d in M.validar(config(TRE_AMBIENTE="prod"))]
         or any(m == "PRODUCAO_RECUSADA" for m, _ in M.validar(config(TRE_AMBIENTE="prod"))))
    p = M.validar(config(TRE_ODOO_API_URL="http://odoo-dev.interno:8069"))
    item("11. dev recusa API fora de loopback (HOST_NAO_E_DEV)",
         any(m == "HOST_NAO_E_DEV" for m, _ in p), str(p))
    p = M.validar(config(TRE_ODOO_RESPOSTAS_PORTA_BANCO="ssh root@10.0.0.9 psql -U sales_ai -d sales_intelligence"))
    item("12. dev recusa porta de banco remota (BANCO_NAO_E_DEV)",
         any(m == "BANCO_NAO_E_DEV" for m, _ in p), str(p))
    p = M.validar(config(TRE_AMBIENTE="homolog", TRE_ODOO_API_URL="https://odoo.transformativa.com.br",
                         TRE_ODOO_RESPOSTAS_APROVACAO_HUMANA=None))
    item("13. homolog sem aprovacao registrada RECUSA",
         any(m == "HOMOLOG_SEM_APROVACAO" for m, _ in p), str(p))
    p = M.validar(config(TRE_ODOO_API_KEY=""))
    item("14. config incompleta nomeia o que falta",
         any(m == "CONFIG_INCOMPLETA" and "TRE_ODOO_API_KEY" in d for m, d in p), str(p))
    p = M.validar(config(TRE_ODOO_TIPO_ATIVIDADE_RESPOSTA="abc"))
    item("15. tipo de atividade nao numerico RECUSA (dado de instancia, fail-closed)",
         any(m == "CONFIG_INCOERENTE" for m, _ in p), str(p))
    resumo = config().resumo()
    item("16. a chave da API nunca aparece no resumo (mascarada)",
         "chave-de-teste-1234" not in json.dumps(resumo) and resumo["api"]["chave"] != "chave-de-teste-1234",
         json.dumps(resumo))
    item("17. dev sem porta de banco declarada RECUSA antes de qualquer conexao",
         any(m == "BANCO_NAO_DECLARADO" for m, _ in M.validar(config(TRE_ODOO_RESPOSTAS_PORTA_BANCO=""))))


# ---------------------------------------------------------------- 3. plano (puro)
def testar_plano():
    plano = M.montar_plano(interacao(), CONTRATO_DADOS, tipo_atividade="9")
    ordem = [a["operacao"] for a in plano["atos"]]
    item("18. INTERESSE monta leitura do lead -> upsert -> atividade, nessa ordem",
         ordem == ["crm_registros_ler", "oportunidade_upsert", "atividade_criar"], str(ordem))
    item("19. o plano so' tem escrita nas operacoes de escrita",
         [a["operacao"] for a in plano["atos"] if a["escrita"]] == ["oportunidade_upsert", "atividade_criar"])
    upsert = [a for a in plano["atos"] if a["operacao"] == "oportunidade_upsert"][0]
    item("20. a escrita do lead usa evento/proxima acao declarados no contrato",
         upsert["parametros"]["valores"]["tf_last_event_type"] == "RESPOSTA_INTERESSE"
         and upsert["parametros"]["valores"]["tf_next_best_action"] == "RESPONDER_AGORA")
    declarados = set(CONTRATO_DADOS["campos_por_operacao"]["oportunidade_upsert"]["campos_escritos"])
    escritos = set(upsert["parametros"]["valores"]) - {"tf_opportunity_id", "name"}
    item("21. nenhum campo escrito no lead esta' fora do declarado",
         escritos <= declarados, str(sorted(escritos - declarados)))
    ativ = [a for a in plano["atos"] if a["operacao"] == "atividade_criar"][0]
    decl_ativ = set(CONTRATO_DADOS["campos_por_operacao"]["atividade_criar"]["campos"])
    item("22. nenhum campo escrito na atividade esta' fora do declarado",
         set(ativ["parametros"]["valores"]) <= decl_ativ,
         str(sorted(set(ativ["parametros"]["valores"]) - decl_ativ)))
    item("23. atividade e' criada no contato vinculado (res_id do odoo_partner_id)",
         ativ["parametros"]["valores"]["res_id"] == 12)
    item("24. resumo da atividade cita a categoria e o assunto da resposta",
         "INTERESSE" in ativ["parametros"]["valores"]["summary"]
         and "Podemos conversar?" in ativ["parametros"]["valores"]["summary"])
    item("25. prazo da atividade vem do contrato (INTERESSE = 1 dia)",
         ativ["parametros"]["valores"]["date_deadline"] > M.agora()[:10])
    item("26. chave de idempotencia do lead derivada do interaction_id",
         upsert["idempotency_key"] == "w6-e06:11111111-1111-1111-1111-111111111111:lead")
    item("27. usuario da atividade entra no payload quando declarado",
         "user_id" not in ativ["parametros"]["valores"]
         and "user_id" in [a for a in M.montar_plano(interacao(), CONTRATO_DADOS, tipo_atividade="9",
                                                    usuario_atividade="7")["atos"]
                           if a["operacao"] == "atividade_criar"][0]["parametros"]["valores"])

    pl_interesse = M.montar_plano(interacao(response_category="SEM_INTERESSE"), CONTRATO_DADOS, "9")
    item("28. SEM_INTERESSE tem evento e prazo proprios (nao reaproveita INTERESSE)",
         pl_interesse["dados"]["evento"] == "RESPOSTA_SEM_INTERESSE"
         and pl_interesse["dados"]["proxima_acao"] == "ENCERRAR_COM_CORTESIA")
    pl_out = M.montar_plano(interacao(response_category="OPT_OUT"), CONTRATO_DADOS, "9")
    item("29. OPT_OUT registra NAO_CONTATAR no lead",
         pl_out["dados"]["evento"] == "RESPOSTA_OPT_OUT" and pl_out["dados"]["proxima_acao"] == "NAO_CONTATAR")
    texto_out = json.dumps(pl_out["atos"])
    item("30. OPT_OUT NAO escreve campo de supressao nao declarado na politica (lacuna declarada)",
         not any(t in texto_out for t in ("do_not_contact", "opt_out", "supress", "unsubscribe")))
    for categoria in ("BOUNCE", "AUTO_RESPOSTA", "RUIDO", "INDEFINIDO", "NAO_RESPOSTA"):
        pl = M.montar_plano(interacao(response_category=categoria), CONTRATO_DADOS, "9")
        item(f"31.{categoria} resposta {categoria} nao gera ato de CRM",
             pl["status"] == "SEM_ATO" and pl["atos"] == [], str(pl["status"]))
    pl = M.montar_plano(interacao(odoo_lead_id=None), CONTRATO_DADOS, "9")
    item("32. resposta sem lead no CRM vira SEM_VINCULO (nenhum ato, nenhum chute)",
         pl["status"] == "SEM_VINCULO" and pl["atos"] == [])
    pl = M.montar_plano(interacao(contato_odoo_id=None, email=None), CONTRATO_DADOS, "9")
    item("33. categoria que exige contato sem contato nem e-mail vira SEM_VINCULO",
         pl["status"] == "SEM_VINCULO" and pl["atos"] == [])
    pl = M.montar_plano(interacao(), CONTRATO_DADOS, tipo_atividade="")
    item("34. sem o tipo de atividade do ambiente o ato RECUSA (nada parcial: nenhum ato executado)",
         pl["status"] == "SEM_CONFIG_DE_ATIVIDADE" and pl["atos"] == [],
         str(pl["status"]))
    pl = M.montar_plano(interacao(subject="a" * 400), CONTRATO_DADOS, "9")
    ativ = [a for a in pl["atos"] if a["operacao"] == "atividade_criar"][0]
    item("35. resumo da atividade respeita o limite declarado de assunto",
         len(ativ["parametros"]["valores"]["summary"]) < 200)


# ---------------------------------------------------------------- 4. execucao
def testar_execucao():
    plano = M.montar_plano(interacao(), CONTRATO_DADOS, "9")
    cliente = ClienteFake()
    resultado = M.executar_plano(cliente, plano, dry_run=False)
    item("36. execucao chama a API na ordem do plano",
         [c["operacao"] for c in cliente.chamadas] == ["crm_registros_ler", "oportunidade_upsert",
                                                       "atividade_criar"], str(cliente.chamadas))
    upsert = cliente.chamadas[1]["parametros"]["valores"]
    item("37. a escrita do lead carrega a identidade canonica lida do CRM (tf_opportunity_id)",
         upsert.get("tf_opportunity_id") == "abc-123" and upsert.get("name") == "Cliente — oportunidade",
         json.dumps(upsert, ensure_ascii=False))
    item("38. toda escrita vai com idempotency_key propria",
         all(c["chave"] for c in cliente.chamadas[1:]), str([c["chave"] for c in cliente.chamadas]))
    cliente = ClienteFake()
    M.executar_plano(cliente, M.montar_plano(interacao(), CONTRATO_DADOS, "9"), dry_run=True)
    item("39. dry-run e' propagado a TODAS as chamadas",
         all(c["dry_run"] for c in cliente.chamadas), str([c["dry_run"] for c in cliente.chamadas]))
    cliente = ClienteFake(lead={"id": 77, "name": "Sem identidade"})
    resultado = M.executar_plano(cliente, M.montar_plano(interacao(), CONTRATO_DADOS, "9"), dry_run=False)
    item("40. lead sem tf_opportunity_id RECUSA a escrita (nada e' escrito por heuristica)",
         resultado["status"] == "SEM_IDENTIDADE_CANONICA" and len(cliente.chamadas) == 1, str(resultado))
    cliente = ClienteFake(falha_em="oportunidade_upsert")
    try:
        M.executar_plano(cliente, M.montar_plano(interacao(), CONTRATO_DADOS, "9"), dry_run=False)
        item("41. recusa da API interrompe a rodada do interaction", False, "nao levantou")
    except M.Recusa as e:
        item("41. recusa da API interrompe a rodada do interaction", e.motivo == "API_RECUSOU")
    item("42. a chave da API nao aparece em nenhum registro de chamada",
         all("chave-de-teste-1234" not in json.dumps(c) for c in cliente.chamadas))


# ---------------------------------------------------------------- 5. envelope HTTP real
class Respondedor(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    modo = "ok"
    visto = {}

    def log_message(self, formato, *args):
        return

    def do_POST(self):  # noqa: N802
        bruto = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8", "replace")
        Respondedor.visto = {"path": self.path, "corpo": json.loads(bruto or "{}"),
                             "autorizacao": self.headers.get("Authorization")}
        if Respondedor.modo == "nao_json":
            corpo, http = b"nao sou json", 200
        elif Respondedor.modo == "recusa":
            corpo = json.dumps({"ok": False, "codigo": "campo_nao_declarado",
                                "correlation_id": "x"}).encode()
            http = 422
        elif Respondedor.modo == "sem_envelope":
            corpo = json.dumps({"resultado": "sem ok"}).encode()
            http = 200
        else:
            corpo = json.dumps({"ok": True, "operacao": "sistema_capacidades", "acao": "capacidades",
                                "ambiente": "dev", "politica_versao": "1.3.0",
                                "correlation_id": "corr-1", "idempotency_key": None,
                                "dry_run": False, "dados": {"capacidades": {}}}).encode()
            http = 200
        self.send_response(http)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)


def testar_envelope_http():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        porta = s.getsockname()[1]
    servidor = ThreadingHTTPServer(("127.0.0.1", porta), Respondedor)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    cfg = config(TRE_ODOO_API_URL=f"http://127.0.0.1:{porta}")
    cliente = M.ClienteApi(cfg)
    try:
        cliente.capacidade()
        item("43. envelope de sucesso e' aceito", True)
        visto = Respondedor.visto
        item("44. o pedido vai com o parametro de ambiente declarado na rota",
             visto["path"].startswith("/tf/api/v1/sistema_capacidades?tf.api.ambiente=dev"), visto["path"])
        item("45. o pedido vai autenticado por bearer (chave nunca em stdout)",
             visto["autorizacao"] == "Bearer chave-de-teste-1234")
        item("46. o corpo do pedido usa o envelope declarado (parametros/idempotency_key/correlation_id/dry_run)",
             set(visto["corpo"]) <= {"parametros", "idempotency_key", "correlation_id", "dry_run"}
             and "parametros" in visto["corpo"], str(sorted(visto["corpo"])))
        Respondedor.modo = "recusa"
        try:
            cliente.capacidade()
            item("47. recusa da API (ok=false / HTTP 422) vira Recusa", False, "nao levantou")
        except M.Recusa as e:
            item("47. recusa da API (ok=false / HTTP 422) vira Recusa", e.motivo == "API_RECUSOU", e.motivo)
        Respondedor.modo = "sem_envelope"
        try:
            cliente.capacidade()
            item("48. resposta sem o envelope declarado RECUSA (nunca segue com dado suposto)", False, "nao levantou")
        except M.Recusa as e:
            item("48. resposta sem o envelope declarado RECUSA (nunca segue com dado suposto)",
                 e.motivo == "RESPOSTA_FORA_DO_ENVELOPE", e.motivo)
        Respondedor.modo = "nao_json"
        try:
            cliente.capacidade()
            item("49. resposta nao-JSON RECUSA", False, "nao levantou")
        except M.Recusa as e:
            item("49. resposta nao-JSON RECUSA", e.motivo == "RESPOSTA_FORA_DO_ENVELOPE", e.motivo)
    finally:
        servidor.shutdown()


# ---------------------------------------------------------------- 6b. envelope da porta de banco
class ProcessoFalso:
    def __init__(self, stdout):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = 0


def envelope_falso(payload_texto: str, corromper: bool = False) -> ProcessoFalso:
    b64 = base64.b64encode(payload_texto.encode("utf-8")).decode("ascii")
    digest = hashlib.md5(payload_texto.encode("utf-8")).hexdigest()
    if corromper:
        digest = hashlib.md5(b"outro payload").hexdigest()
    # O QUE O ACEITE MEDIU: psql quebra a saida longa em varias linhas. A quebra aqui e' inocente
    # (sem espaco) e o md5 prova que a costura foi exata.
    pedacos = [b64[i:i + 40] for i in range(0, len(b64), 40)]
    return ProcessoFalso("\n".join([digest + " " + pedacos[0]] + pedacos[1:] + [""]))


def testar_envelope_de_banco():
    porta = M.PortaBanco("docker exec -i pg-e06-acc psql -U sales_ai -d sales_intelligence", "dev")
    original = M.subprocess.run
    try:
        M.subprocess.run = lambda *a, **k: envelope_falso(json.dumps([{"interaction_id": "x"}]))
        linhas = porta.consultar("SELECT 1")
        item("57. envelope do banco sobrevive a quebra de linha do psql (defeito medido no aceite)",
             linhas == [{"interaction_id": "x"}], str(linhas))
        M.subprocess.run = lambda *a, **k: envelope_falso(json.dumps([{"a": 1}]), corromper=True)
        try:
            porta.consultar("SELECT 1")
            item("58. envelope com md5 divergente RECUSA (nunca segue com dado pela metade)", False,
                 "nao levantou")
        except M.Recusa as e:
            item("58. envelope com md5 divergente RECUSA (nunca segue com dado pela metade)",
                 e.motivo == "BANCO_RESPOSTA_CORROMPIDA", e.motivo)
    finally:
        M.subprocess.run = original


# ---------------------------------------------------------------- 7. propagacao e desfazer
def testar_propagacao():
    modulo = carregar()
    porta = PortaFake()
    modulo.PortaBanco = lambda prefixo, ambiente: porta
    saida = modulo.Saida(None, None, "chave-de-teste-1234")
    classe = modulo.ClienteApi
    modulo.ClienteApi = lambda cfg: ClienteFake()
    try:
        rc = modulo.propagar(config(), CONTRATO_DADOS, saida, confirmo=False, porta_banco=None)
        item("50. dry-run devolve DRY_RUN e nao escreve na trilha",
             rc == modulo.CODIGO_OK and porta.escritas == [], f"rc={rc} escritas={len(porta.escritas)}")
        porta = PortaFake()
        modulo.PortaBanco = lambda prefixo, ambiente: porta
        rc = modulo.propagar(config(), CONTRATO_DADOS, saida, confirmo=True, porta_banco=None)
        item("51. rodada confirmada grava UMA linha de trilha por interaction",
             rc == modulo.CODIGO_OK and len(porta.escritas) == 1, f"rc={rc} escritas={len(porta.escritas)}")
        sql = porta.escritas[0] if porta.escritas else ""
        item("52. a trilha vai para sync_events por INSERT",
             "INSERT INTO sales_intelligence.sync_events" in sql and "ATUALIZADO" in sql, sql[:120])
        item("53. a trilha nunca carrega a chave da API",
             "chave-de-teste-1234" not in sql)
        porta2 = PortaFake(trilha=[{"idempotency_key": "odoo-resposta:11111111-1111-1111-1111-111111111111",
                                    "status": "ATUALIZADO", "entity_id": "x"}])
        modulo.PortaBanco = lambda prefixo, ambiente: porta2
        rc = modulo.propagar(config(), CONTRATO_DADOS, modulo.Saida(None, None, None), confirmo=True,
                             porta_banco=None)
        item("54. replay nao grava de novo e nao chama a API",
             rc == modulo.CODIGO_OK and porta2.escritas == [], f"rc={rc} escritas={len(porta2.escritas)}")
        porta3 = PortaFake(interacoes=[interacao(response_category="BOUNCE")])
        modulo.PortaBanco = lambda prefixo, ambiente: porta3
        rc = modulo.propagar(config(), CONTRATO_DADOS, modulo.Saida(None, None, None), confirmo=True,
                             porta_banco=None)
        item("55. categoria sem ato grava trilha SEM_ATO e nao chama a API",
             len(porta3.escritas) == 1 and "SEM_ATO" in porta3.escritas[0], str(porta3.escritas[:1]))
        porta4 = PortaFake(trilha=[])
        modulo.PortaBanco = lambda prefixo, ambiente: porta4
        modulo.ClienteApi = classe
        rc = modulo.desfazer(config(), modulo.Saida(None, None, None), "odoo-resposta:sem-alvo", confirmo=False)
        item("56. desfazer sem atualizacao registrada RECUSA (o DRY_RUN nao serve de alvo)",
             rc == modulo.CODIGO_RECUSA, f"rc={rc}")
    finally:
        modulo.PortaBanco = M.PortaBanco
        modulo.ClienteApi = M.ClienteApi


# ---------------------------------------------------------------- dentes
DENTES = [
    ("sem-guarda-de-producao", "PRODUCAO_RECUSADA", "if config.ambiente == \"prod\":",
     "if config.ambiente == \"prod_x\":", "10. prod RECUSA por desenho (exit 4)"),
    ("sem-guarda-de-loopback", "HOST_NAO_E_DEV", "if config.url and not loopback(",
     "if False and not loopback(", "11. dev recusa API fora de loopback (HOST_NAO_E_DEV)"),
    ("contrato-aceita-categoria-inventada", "fora do vocabulario",
     "if cat not in vocab:\n            falhas.append(f\"ato {i} categoria fora do vocabulario: {cat}\")",
     "if False:\n            falhas.append(f\"ato {i} categoria fora do vocabulario: {cat}\")",
     "5. categoria fora do vocabulario RECUSA o contrato"),
    ("ato-generico-para-categoria-sem-ato", "SEM_ATO",
     "    if ato is None:\n        return {\"status\": \"SEM_ATO\", \"atos\": [], \"motivo\": f\"categoria {categoria} declarada sem ato de CRM\",\n                \"categoria\": categoria}",
     "    if ato is None:\n        ato = sorted(contrato[\"atos_por_categoria\"], key=lambda a: a[\"ordem\"])[0]",
     "31.BOUNCE resposta BOUNCE nao gera ato de CRM"),
    ("escrita-sem-o-campo-de-leitura-do-lead", "SEM_IDENTIDADE_CANONICA",
     "            if not identidade:", "            if False:", "40. lead sem tf_opportunity_id RECUSA a escrita (nada e' escrito por heuristica)"),
    ("envelope-nao-conferido", "RESPOSTA_FORA_DO_ENVELOPE",
     "        for campo in (\"ok\", \"operacao\", \"correlation_id\", \"dados\"):",
     "        for campo in ():", "48. resposta sem o envelope declarado RECUSA (nunca segue com dado suposto)"),
    ("auditoria-de-fonte-morta", "trilha_por_insert",
     "    if \"INSERT INTO\" not in corpo:", "    if True:",
     "8. auditoria da propria fonte nao acusa violacao"),
    ("sem-conferencia-de-md5", "BANCO_RESPOSTA_CORROMPIDA",
     "        if hashlib.md5(dados).hexdigest() != digest:", "        if False:",
     "58. envelope com md5 divergente RECUSA (nunca segue com dado pela metade)"),
]


def prova_de_dente() -> int:
    print("== prova de dente (mutacoes em copia temporaria; o artefato real nao e' tocado) ==")
    falhas = []
    with tempfile.TemporaryDirectory() as tmp:
        original = open(MODULO, encoding="utf-8").read()
        for nome, esperado, de, para, item_alvo in DENTES:
            if de not in original:
                print(f"FALHOU dente {nome}: ancora nao encontrada (mutacao NAO aplicada)")
                falhas.append(nome)
                continue
            copia = os.path.join(tmp, f"mut_{nome.replace('-', '_')}.py")
            open(copia, "w", encoding="utf-8").write(original.replace(de, para, 1))
            codigo = (
                "import importlib.util, sys, json, tempfile, os\n"
                f"spec = importlib.util.spec_from_file_location('mut', {copia!r})\n"
                "modulo = importlib.util.module_from_spec(spec); spec.loader.exec_module(modulo)\n"
                "sys.exit(0)\n")
            proc = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True)
            if proc.returncode != 0:
                # mutacao que nem importa: o dente nao mede nada de util
                print(f"FALHOU dente {nome}: a mutacao nao executa ({proc.stderr.strip()[-160:]})")
                falhas.append(nome)
                continue
            saida = subprocess.run([sys.executable, os.path.abspath(__file__), "--mutacao", copia],
                                   capture_output=True, text=True, cwd=RAIZ)
            marca = f"FALHOU" in saida.stdout and any(
                linha.startswith("FALHOU") and item_alvo.split(". ", 1)[-1][:25] in linha
                for linha in saida.stdout.splitlines())
            if marca:
                print(f"OK dente {nome}: reprovou o item que ele nomeia -> {item_alvo}")
            else:
                print(f"FALHOU dente {nome}: o item '{item_alvo}' NAO reprovou com a mutacao aplicada")
                falhas.append(nome)
    print(f"== dentes: {len(DENTES) - len(falhas)}/{len(DENTES)} reprovaram como esperado ==")
    return 0 if not falhas else 1


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--prova-de-dente":
        return prova_de_dente()
    if len(sys.argv) > 2 and sys.argv[1] == "--mutacao":
        global M, CONTRATO_DADOS
        M = carregar(sys.argv[2], "atualizacao_odoo_mut")
        testar_contrato()
        testar_fonte_e_guardas()
        testar_plano()
        testar_execucao()
        testar_envelope_http()
        testar_envelope_de_banco()
        testar_propagacao()
        print(f"{'PASS' if not FALHAS else 'FALHOU'} ({len(ITENS)} itens, {len(FALHAS)} falhas)")
        return 0 if not FALHAS else 1
    testar_contrato()
    testar_fonte_e_guardas()
    testar_plano()
    testar_execucao()
    testar_envelope_http()
    testar_envelope_de_banco()
    testar_propagacao()
    veredito = "PASS" if not FALHAS else "FALHOU"
    print(f"{veredito} ({len(ITENS)} itens, {len(FALHAS)} falhas)")
    return 0 if not FALHAS else 1


if __name__ == "__main__":
    raise SystemExit(main())
