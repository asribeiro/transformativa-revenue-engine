#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verificador OFFLINE do funil (`funil-v1`) — card TRE-W8-E01-T01 (W8 / Analytics).

Mede o contrato e o componente SEM banco: derivacao pura, guardas de ambiente, auditoria da fonte,
determinismo, HTML auto-contido, ausencia de PII. Cada item imprime OK/FALHOU.

Uso:
  python3 scripts/agentes/verificar_funil.py                     # mede o repo (raiz = 3 niveis acima)
  python3 scripts/agentes/verificar_funil.py --alvo-dir <dir>     # mede uma copia (usado pelos dentes)
  python3 scripts/agentes/verificar_funil.py --autoteste          # itens + N mutacoes, cada uma tem de REPROVAR

Exit 0 = PASS com autoteste 100%; 1 = FALHOU.
"""

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ_PADRAO = os.path.abspath(os.path.join(AQUI, "..", ".."))
REL_COMPONENTE = os.path.join("hermes", "agentes", "analytics", "funil.py")
REL_CONTRATO = os.path.join("hermes", "agentes", "analytics", "funil-v1.json")
REL_DADOS = os.path.join("docs", "data", "data_contract_v1.json")

OK = 0
FALHAS = 0
FALHAS_ITENS = []


def item(nome, condicao, detalhe=""):
    global OK, FALHAS
    if condicao:
        print("OK    %s" % nome)
        OK += 1
    else:
        print("FALHOU %s %s" % (nome, detalhe))
        FALHAS += 1
        FALHAS_ITENS.append(nome)


def carregar_componente(alvo):
    caminho = os.path.join(alvo, REL_COMPONENTE)
    spec = importlib.util.spec_from_file_location("funil_alvo", caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.CONTRATO_PADRAO = os.path.join(alvo, REL_CONTRATO)
    return mod


def carregar_contratos(alvo):
    with open(os.path.join(alvo, REL_CONTRATO), "r", encoding="utf-8") as fh:
        contrato = json.load(fh)
    with open(os.path.join(alvo, REL_DADOS), "r", encoding="utf-8") as fh:
        dados = json.load(fh)
    return contrato, dados


def O(i, n=1):
    """UUID falso, deterministico, de teste (nunca de base real)."""
    return "0000000%d-0000-0000-0000-00000000000%d" % (i, n)


def fixture():
    """Base sintetica com evidencia em cada nivel. Valores conferidos A MAO no aceite tambem."""
    A, B, C, D, E, F, G, H = [O(i) for i in range(1, 9)]
    return {
        "BASE_ORGANIZACOES": [
            "%s|SITE|DISCOVERED" % A, "%s|SITE|Reunião" % B, "%s|EVENTO_CAPTURA|DISCOVERED" % C,
            "%s|META|Won" % D, "%s|GOOGLE|Lost" % E, "%s|WHATSAPP|Nurture" % F,
            "%s|SITE|PERDIDO_NA_PRACA" % G, "%s|SITE|DISCOVERED" % H],
        "PESQUISA_CONCLUIDA": [B, D, E, F, C],
        "SINAIS": [D, E],
        "SCORE_PRIORITY": [B, D, E, F],
        "CONTATO_COM_CANAL": [B, D, E, F],
        "INTERACAO_OUTBOUND": [B, D, E],
        "INTERACAO_INBOUND_CLASSIFICADA": [B, D],
        "RECOMENDACAO_REUNIAO": [H],
        "TRILHA_ESTAGIO": [
            "%s|Proposta|STAGE_CHANGED" % B,
            "%s|Won|OPPORTUNITY_WON" % D,
            "%s|Lost|OPPORTUNITY_LOST" % E,
            "%s|Nurture|STAGE_CHANGED" % F,
            "%s|Estagio Inventado|STAGE_CHANGED" % H,
            "99999999-9999-9999-9999-999999999999|Won|OPPORTUNITY_WON"],
    }


# -------------------------------------------------------------------------------------------- #
def itens(alvo):
    mod = carregar_componente(alvo)
    contrato, dados = carregar_contratos(alvo)
    estagios = contrato["estagios"]
    nomes = [e["nome"] for e in estagios]

    # 1 -------------------------------------------------------------------------------------
    item("1. contrato legivel, versao e card declarados",
         contrato.get("versao") == "funil-v1" and contrato.get("card") == "TRE-W8-E01-T01"
         and len(nomes) == len(set(nomes)), "versao=%s card=%s" % (contrato.get("versao"), contrato.get("card")))

    # 2 -------------------------------------------------------------------------------------
    item("2. estagios batem com funnel_stages do contrato de dados (ordem e rotulos)",
         nomes == list(dados.get("funnel_stages") or []),
         "componente=%s dados=%s" % (nomes, dados.get("funnel_stages")))

    # 3 -------------------------------------------------------------------------------------
    lineares = [e for e in estagios if not e.get("lateral")]
    niveis = [e["nivel"] for e in lineares]
    laterais = [e for e in estagios if e.get("lateral")]
    ok3 = all(isinstance(n, int) for n in niveis)
    ok3 = ok3 and sorted(set(niveis)) == list(range(len(set(niveis))))
    ok3 = ok3 and niveis.count(max(niveis)) == 2 and all(niveis.count(n) == 1 for n in set(niveis) if n != max(niveis))
    ok3 = ok3 and len(laterais) == 1 and laterais[0]["nome"] == "Nurture" and laterais[0]["nivel"] is None
    item("3. niveis consecutivos, Won/Lost no mesmo terminal e Nurture lateral sem nivel", ok3, str(niveis))

    # 4 -------------------------------------------------------------------------------------
    fontes = contrato["fontes"]
    ok4 = all(e["fontes"] for e in estagios) and all(fid in fontes for e in estagios for fid in e["fontes"])
    item("4. todo estagio tem fonte declarada e toda fonte usada existe em fontes", ok4)

    # 5 -------------------------------------------------------------------------------------
    divergencia = ""
    try:
        consultas = mod.montar_consultas(contrato)
        ok5 = set(consultas) == set(fontes)
        if not ok5:
            divergencia = "contrato=%s componente=%s" % (sorted(fontes), sorted(consultas))
    except mod.Recusa as exc:
        ok5, divergencia = False, "%s %s" % (exc.motivo, exc.detalhe)
    item("5. componente monta EXATAMENTE as fontes declaradas (nos dois sentidos)", ok5, divergencia)

    # 6 -------------------------------------------------------------------------------------
    try:
        mod.auditar_fonte(mod.montar_consultas(contrato))
        ok6 = True
    except mod.Recusa as exc:
        ok6, detalhe6 = False, "%s %s" % (exc.motivo, exc.detalhe)
    else:
        detalhe6 = ""
    sujo = mod.auditar_sql("SELECT 1; INSERT INTO x VALUES (1)")
    item("6. auditoria da fonte: SQL montado e' so' SELECT e verbo de escrita e' detectado",
         ok6 and "INSERT" in sujo and mod.auditar_sql("SELECT 1 FROM t") == [],
         detalhe6 or str(sujo))

    # 7 -------------------------------------------------------------------------------------
    rc = _cli(alvo, ["--ambiente", "prod", "--saida", "/tmp/nao-deve-existir"])
    item("7. producao RECUSA por desenho (exit 4)", rc == 4, "exit=%s" % rc)

    # 8 -------------------------------------------------------------------------------------
    rc = _cli(alvo, ["--ambiente", "dev", "--porta-banco", "ssh root@10.0.0.1 psql"])
    rc_nome = _cli(alvo, ["--ambiente", "dev", "--porta-banco", "docker exec -i pg-outro psql"])
    nome_recusado = False
    try:
        mod.validar_ambiente("dev", "docker exec -i pg-outro psql", False)
    except mod.Recusa as exc:
        nome_recusado = exc.motivo == "CONTAINER_NAO_LOCAL_DE_DEV"
    item("8. dev RECUSA prefixo remoto (BANCO_NAO_E_DEV) e container fora do padrao de dev (exit 3)",
         rc == 3 and rc_nome == 3 and nome_recusado, "remoto=%s nome=%s guarda=%s" % (rc, rc_nome, nome_recusado))

    # 9 -------------------------------------------------------------------------------------
    ok9 = False
    try:
        mod.validar_ambiente("dev", "docker exec -i pg-funil-acc psql -U sales_ai -d sales_intelligence", False)
        ok9 = True
    except mod.Recusa:
        ok9 = False
    recusa_prod = False
    try:
        mod.validar_ambiente("prod", "docker exec -i pg-funil-acc psql", False)
    except mod.Recusa as exc:
        recusa_prod = exc.codigo == 4
    recusa_homolog = False
    try:
        mod.validar_ambiente("homolog", "docker exec -i pg-funil-acc psql", False)
    except mod.Recusa:
        recusa_homolog = True
    item("9. guarda de ambiente: dev ACEITA porta local, prod RECUSA (4) e homolog exige --confirmo",
         ok9 and recusa_prod and recusa_homolog,
         "dev=%s prod=%s homolog=%s" % (ok9, recusa_prod, recusa_homolog))

    # 10 ------------------------------------------------------------------------------------
    ok10 = (mod.resolver_rotulo(contrato, "Reunião") == "Reunião"
            and mod.resolver_rotulo(contrato, "reuniao") == "Reunião"
            and mod.resolver_rotulo(contrato, "  REUNIÃO  ") == "Reunião"
            and mod.resolver_rotulo(contrato, "DISCOVERED") is None)
    item("10. rotulo normalizado casa por igualdade exata (acento/caixa/espaco) e DISCOVERED nao e' estagio", ok10)

    # 11 ------------------------------------------------------------------------------------
    ok11 = (mod.resolver_rotulo(contrato, "Estagio Inventado") is None
            and mod.resolver_rotulo(contrato, "Reuniao Extra") is None
            and mod.resolver_rotulo(contrato, "nao-negociacao") is None)
    item("11. rotulo nao declarado NAO vira estagio (sem casamento por semelhanca)", ok11)

    # 12/13/14/15 ----------------------------------------------------------------------------
    brutas = fixture()
    rel = mod.montar_relatorio(contrato, brutas, "dev", {"desde": None, "ate": None},
                               gerado_em="2026-10-03T00:00:00Z", sha_contrato="a" * 64)
    por_nome = {e["nome"]: e for e in rel["estagios"]}

    def A(nome):
        return por_nome.get(nome, {}).get("alcancadas", -1)

    def C(nome, campo):
        return por_nome.get(nome, {}).get(campo)
    esperado = {"Descoberto": 8, "Pesquisado": 6, "Qualificado": 5, "Contato identificado": 5,
                "Abordagem iniciada": 4, "Engajamento": 4, "Reunião": 4, "Diagnóstico": 3,
                "Proposta": 3, "Negociação": 2, "Won": 1, "Lost": 1, "Nurture": 1}
    obtido = {k: v["alcancadas"] for k, v in por_nome.items()}
    item("12. alcance cumulativo conferido a mao (org em Won conta ate' Negociacao)", obtido == esperado,
         "obtido=%s" % obtido)
    item("13. Won e Lost NAO se somam (organizacao perdida nao conta em Won) e propria != alcancada",
         A("Won") == 1 and A("Lost") == 1
         and C("Won", "evidencia_propria") == 1 and C("Diagnóstico", "evidencia_propria") == 0,
         "won=%s lost=%s" % (por_nome.get("Won"), por_nome.get("Lost")))
    item("14. Nurture e' lateral: conta so' por evidencia propria e converte de Qualificado",
         C("Nurture", "lateral") and A("Nurture") == 1
         and C("Nurture", "conversao_de") == "Qualificado"
         and C("Nurture", "conversao_da_anterior_pct") == 20.0,
         str(por_nome.get("Nurture")))
    item("15. monotonicidade da sequencia linear e terminal cabe em Negociacao",
         all(A(a) >= A(b)
             for a, b in zip(["Descoberto", "Pesquisado", "Qualificado", "Contato identificado",
                              "Abordagem iniciada", "Engajamento", "Reunião", "Diagnóstico", "Proposta"],
                             ["Pesquisado", "Qualificado", "Contato identificado", "Abordagem iniciada",
                              "Engajamento", "Reunião", "Diagnóstico", "Proposta", "Negociação"]))
         and rel["resumo"]["negociacao"] >= rel["resumo"]["won"] + rel["resumo"]["lost"])

    # 16 ------------------------------------------------------------------------------------
    ok16 = (C("Proposta", "conversao_da_anterior_pct") == 100.0
            and C("Negociação", "conversao_da_anterior_pct") == 66.67
            and C("Won", "conversao_da_anterior_pct") == 50.0
            and por_nome.get("Descoberto", {}).get("conversao_da_anterior_pct") is None
            and mod._pct(0, 0) is None and mod._pct(1, 0) is None)
    item("16. conversoes conferidas a mao e denominador zero devolve null (nao 0 e nao erro)", ok16)

    # 17 ------------------------------------------------------------------------------------
    vazio = mod.montar_relatorio(contrato, {"BASE_ORGANIZACOES": []}, "dev",
                                 {"desde": None, "ate": None}, gerado_em="2026-10-03T00:00:00Z")
    item("17. base vazia: zero organizacoes, zero estagios e nenhuma conversao",
         vazio["resumo"]["total_organizacoes"] == 0
         and all(e["alcancadas"] == 0 for e in vazio["estagios"])
         and all(e["conversao_do_topo_pct"] is None for e in vazio["estagios"]))

    # 18 ------------------------------------------------------------------------------------
    outro = mod.montar_relatorio(contrato, brutas, "dev", {"desde": None, "ate": None},
                                 gerado_em="2030-01-01T00:00:00Z", sha_contrato="a" * 64)
    item("18. determinismo: mesma base -> mesmo hash; gerado_em nao entra no hash",
         rel["hash_do_relatorio"] == outro["hash_do_relatorio"] and rel["gerado_em"] != outro["gerado_em"]
         and len(rel["hash_do_relatorio"]) == 64)

    # 19 ------------------------------------------------------------------------------------
    html = mod.emitir_html(rel, contrato.get("lacunas_declaradas"))
    ok19 = ("http://" not in html and "https://" not in html and "<script" not in html
            and "<link" not in html and "src=" not in html and "Estágio" in html
            and all(e["nome"] in html for e in estagios))
    item("19. dashboard HTML auto-contido (nenhum recurso externo) e com todos os estagios",
         ok19, "len=%d" % len(html))

    # 20 ------------------------------------------------------------------------------------
    texto = json.dumps(rel, ensure_ascii=False) + html
    import re as _re
    padroes_pii = [_re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), _re.compile(r"\d{3}\.\d{3}\.\d{3}-\d{2}"),
                   _re.compile(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}"), _re.compile(r"\+?\d{2}[\s-]9?\d{4}[\s-]?\d{4}")]
    achados_pii = [p.pattern for p in padroes_pii if p.search(texto)]
    consultas = mod.montar_consultas(contrato)
    colunas_proibidas = ("email", "phone", "whatsapp", "cnpj", "full_name", "legal_name")
    selecao_suja = []
    for fid, sql in consultas.items():
        if fid in ("CONTATO_COM_CANAL",):  # a UNICA que cita a coluna, e so' dentro do WHERE (existencia do canal)
            cabeca = sql.split(" FROM ")[0]
        else:
            cabeca = sql.split(" FROM ")[0]
        for coluna in colunas_proibidas:
            if _re.search(r"\b%s\b" % coluna, cabeca, _re.IGNORECASE):
                selecao_suja.append("%s:%s" % (fid, coluna))
    item("20. evidencia sem PII: nenhum padrao de e-mail/CPF/CNPJ/telefone E nenhuma coluna de PII no SELECT",
         not achados_pii and not selecao_suja, "%s %s" % (achados_pii, selecao_suja))

    # 21 ------------------------------------------------------------------------------------
    lac = rel["lacunas"]
    ok21 = (lac["estagios_desconhecidos"] == 1 and lac["status_nao_declarado"] == 1
            and lac["eventos_sem_atribuicao"].get("OPPORTUNITY_WON") == 1
            and "estagio inventado" in lac["rotulos_nao_declarados"])
    item("21. lacunas medidas: rotulo inventado e evento sem atribuicao NAO entram em estagio", ok21, str(lac))

    # 22 ------------------------------------------------------------------------------------
    rc_plan = _cli(alvo, ["--ambiente", "dev", "--planejar"])
    rc_conf = _cli(alvo, ["--ambiente", "dev", "--conferir"])
    item("22. --planejar e --conferir funcionam sem banco (exit 0)", rc_plan == 0 and rc_conf == 0,
         "plan=%s conf=%s" % (rc_plan, rc_conf))

    # 23 ------------------------------------------------------------------------------------
    item("23. contrato declara as lacunas L1..L5 e o relatorio as carrega",
         len(contrato.get("lacunas_declaradas") or []) == 5
         and len(rel["lacunas_declaradas"]) == 5)

    # 24 ------------------------------------------------------------------------------------
    rc_janela = _cli(alvo, ["--ambiente", "dev", "--porta-banco",
                            "docker exec -i pg-funil-acc psql", "--desde", "ontem"])
    item("24. janela invalida RECUSA (exit 2) antes de tocar o banco", rc_janela == 2, "exit=%s" % rc_janela)

    return contrato


def _cli(alvo, args):
    comando = [sys.executable, os.path.join(alvo, REL_COMPONENTE), "--raiz", alvo,
               "--contrato", os.path.join(alvo, REL_CONTRATO)] + args
    proc = subprocess.run(comando, capture_output=True, text=True, timeout=120)
    return proc.returncode


# -------------------------------------------------------------------------------------------- #
def _aplicar_mutacao(origem, destino, mutacao):
    os.makedirs(os.path.join(destino, "hermes", "agentes", "analytics"), exist_ok=True)
    os.makedirs(os.path.join(destino, "docs", "data"), exist_ok=True)
    shutil.copy2(os.path.join(origem, REL_DADOS), os.path.join(destino, REL_DADOS))
    for rel in (REL_COMPONENTE, REL_CONTRATO):
        shutil.copy2(os.path.join(origem, rel), os.path.join(destino, rel))
    alvo_arquivo = os.path.join(destino, REL_CONTRATO if mutacao["arquivo"] == "contrato" else REL_COMPONENTE)
    with open(alvo_arquivo, "r", encoding="utf-8") as fh:
        texto = fh.read()
    if mutacao["de"] not in texto:
        return False
    texto = texto.replace(mutacao["de"], mutacao["para"])
    with open(alvo_arquivo, "w", encoding="utf-8") as fh:
        fh.write(texto)
    return True


MUTACOES = [
    {"nome": "d1 rotulo de estagio trocado no contrato", "arquivo": "contrato",
     "de": '"nome": "Pesquisado"', "para": '"nome": "Pesquisado de novo"'},
    {"nome": "d2 estagio sem fonte declarada", "arquivo": "contrato",
     "de": '"fontes": ["SCORE_PRIORITY"]', "para": '"fontes": []'},
    {"nome": "d3 verbo de escrita injetado no SQL", "arquivo": "componente",
     "de": '"SELECT DISTINCT organization_id::text FROM sales_intelligence.signals "',
     "para": '"SELECT 1; INSERT INTO sales_intelligence.signals VALUES (1); SELECT organization_id::text FROM sales_intelligence.signals "'},
    {"nome": "d4 guarda de producao desligada na funcao de guarda", "arquivo": "componente",
     "de": 'if ambiente == "prod":', "para": 'if False:'},
    {"nome": "d5 guarda de porta remota desligada", "arquivo": "componente",
     "de": 'if not CONTAINERS_LOCAIS_DEV.match(m.group(1)):', "para": 'if False:'},
    {"nome": "d6 alcance cumulativo quebrado", "arquivo": "componente",
     "de": "or (nivel_max.get(org) is not None and nivel_max[org] >= nivel)", "para": "or False"},
    {"nome": "d7 casamento de rotulo vira semelhanca", "arquivo": "componente",
     "de": 'return rotulos_declarados(contrato).get(chave)',
     "para": "return next((n for k, n in rotulos_declarados(contrato).items() if k.startswith(chave[:4])), None)"},
    {"nome": "d8 Nurture deixa de ser lateral", "arquivo": "contrato",
     "de": '"lateral": true', "para": '"lateral": false'},
]


def _rodar_verificador(alvo):
    """Roda a suite num alvo e devolve (exit, nomes dos itens reprovados)."""
    proc = subprocess.run([sys.executable, os.path.join(AQUI, "verificar_funil.py"), "--alvo-dir", alvo],
                          capture_output=True, text=True, timeout=240)
    nomes = []
    for linha in proc.stdout.splitlines():
        if linha.startswith("VERIFICADOR_FUNIL_FALHOU"):
            try:
                nomes = json.loads(linha[len("VERIFICADOR_FUNIL_FALHOU "):])
            except json.JSONDecodeError:
                nomes = []
    return proc.returncode, nomes


def autoteste(alvo, falhas_base):
    print("== autoteste: %d mutacoes, cada uma tem de REPROVAR um item que o alvo limpo nao reprova =="
          % len(MUTACOES))
    detectadas = 0
    for mut in MUTACOES:
        with tempfile.TemporaryDirectory(prefix="dente-funil-") as tmp:
            copia = os.path.join(tmp, "repo")
            if not _aplicar_mutacao(alvo, copia, mut):
                print("FALHOU %s (mutacao NAO aplicada — ancora de texto mudou)" % mut["nome"])
                continue
            rc, nomes = _rodar_verificador(copia)
            novos = [n for n in nomes if n not in falhas_base]
            if rc != 0 and novos:
                detectadas += 1
                print("OK    %s -> REPROVOU (%s)" % (mut["nome"], novos[0][:80]))
            else:
                print("FALHOU %s -> passou com a mutacao aplicada (buraco na suite)" % mut["nome"])
    print("AUTOTESTE %d/%d mutacoes detectadas" % (detectadas, len(MUTACOES)))
    return detectadas == len(MUTACOES)


def main():
    parser = argparse.ArgumentParser(description="Verificador offline do funil (TRE-W8-E01-T01)")
    parser.add_argument("--alvo-dir", default=RAIZ_PADRAO)
    parser.add_argument("--autoteste", action="store_true")
    args = parser.parse_args()
    alvo = os.path.abspath(args.alvo_dir)

    try:
        itens(alvo)
    except Exception as exc:  # noqa: BLE001 — item isolado: excecao nao tratada e' FALHA, nunca silencio
        item("0. suite roda ate' o fim sem excecao nao tratada", False,
             "%s: %s" % (type(exc).__name__, str(exc)[:120]))
    autoteste_ok = True
    if args.autoteste:
        autoteste_ok = autoteste(alvo, set(FALHAS_ITENS))

    print("RESULTADO: %s (%d itens, %d falhas)" % ("PASS" if FALHAS == 0 else "FALHOU", OK, FALHAS))
    if FALHAS == 0 and autoteste_ok:
        print("VERIFICADOR_FUNIL_PASS")
        return 0
    print("VERIFICADOR_FUNIL_FALHOU " + json.dumps(FALHAS_ITENS, ensure_ascii=False))
    return 1


if __name__ == "__main__":
    sys.exit(main())
