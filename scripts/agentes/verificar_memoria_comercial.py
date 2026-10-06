#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Suite offline da memoria comercial (`memoria-comercial-v1`) — card TRE-W9-E06-T01.

Mede o COMPONENTE (`hermes/memoria/memoria_comercial.py`) e o CONTRATO, sem banco e sem Qdrant:
as regras que podem ser provadas puras (contrato, guardas de ambiente, auditoria da fonte, embedding
declarado, id determinístico, PII como lacuna, payload fechado, pré-condição, piso de score, hash do
relatorio e HTML auto-contido) sao exercitadas aqui; a indexacao REAL (Qdrant descartavel + PostgreSQL
descartavel com a migration 0001) e' medida no aceite `teste_memoria_comercial_aceite.sh`.

`--autoteste` muta COPIA temporaria do componente e exige que o item correspondente REPROVE: mutacao
que nao pega e' buraco da suite, nao do codigo. Saida: OK/FALHOU por item, e no fim
`VERIFICADOR_MEMORIA_COMERCIAL_PASS (N itens, 0 falhas)` + `AUTOTESTE M/M mutacoes detectadas`.
"""

import argparse
import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.abspath(os.path.join(AQUI, "..", ".."))
COMPONENTE_PADRAO = os.path.join(RAIZ, "hermes", "memoria", "memoria_comercial.py")
CONTRATO_PADRAO = os.path.join(RAIZ, "hermes", "memoria", "memoria-comercial-v1.json")

ITENS = []


def item(nome):
    def deco(fn):
        ITENS.append((nome, fn))
        return fn
    return deco


def carregar_modulo(caminho, nome="memoria_comercial_em_teste"):
    spec = importlib.util.spec_from_file_location(nome, caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def ler_contrato(caminho):
    with open(caminho, "r", encoding="utf-8") as fh:
        return json.load(fh)


def produto_escalar(a, b):
    return sum(x * y for x, y in zip(a, b))


def documento(tipo="MENSAGEM", origem_id="00000000-0000-0000-0000-00000000000%d", n=1, texto=None,
             organization_id="00000001-0000-0000-0000-000000000000", canal="EMAIL", ocorrido_em="2026-09-01"):
    return {
        "tipo": tipo, "origem_tabela": "sales_intelligence.interactions",
        "origem_id": origem_id % n if "%d" in origem_id else origem_id,
        "organization_id": organization_id, "canal": canal, "ocorrido_em": ocorrido_em,
        "texto": texto or "mensagem comercial de teste sobre eficiencia operacional %d" % n,
    }


def corpus_no_piso(mod, contrato, tipo="__todos__"):
    """Corpus exatamente no piso declarado (ou no piso de um tipo especifico), derivado por derivar_documentos."""
    linhas = {}
    for t in contrato["tipos_de_documento"]:
        quantos = contrato["minimos"]["por_tipo"].get(t["nome"], 1)
        if tipo != "__todos__" and t["nome"] != tipo:
            quantos = 0
        linhas[t["nome"]] = [
            {"origem_id": "%s-%08d" % (t["nome"], i), "organization_id": "00000001-0000-0000-0000-000000000000",
             "canal": "EMAIL", "ocorrido_em": "2026-09-01",
             "texto": "conteudo comercial %s numero %d sobre eficiencia operacional" % (t["nome"], i)}
            for i in range(quantos)
        ]
    return mod.derivar_documentos(contrato, linhas)[0]


def relatorio_minimo(mod, contrato, documentos, caminho_contrato, agora="2026-10-03T00:00:00Z"):
    class A:
        ambiente = "dev"
        qdrant_url = "http://127.0.0.1:6333"
        agora = "2026-10-03T00:00:00Z"
    base = mod.base_do_relatorio(contrato, caminho_contrato, "dev", "http://127.0.0.1:6333", agora)
    base["pre_condicao"] = mod.medir_pre_condicao(contrato, documentos)
    base["corpus"] = {
        "total_documentos": len(documentos),
        "por_tipo": base["pre_condicao"]["por_tipo"],
        "documentos": [{"tipo": d["tipo"], "origem_id": d["origem_id"], "canal": d["canal"],
                        "ocorrido_em": d["ocorrido_em"], "conteudo_sha256": "0" * 64, "texto": d["texto"]}
                       for d in documentos],
    }
    base["lacunas"] = []
    return base


# ------------------------------------------------------------------------------------------------
# Itens de suite
# ------------------------------------------------------------------------------------------------
@item("contrato: carrega, valida e mantem payload fechado sem campo de contato")
def i01(mod, contrato, ctx):
    mod.validar_contrato(contrato)
    proibidos = [c for c in contrato["payload_fechado"] if c in ("email", "telefone", "whatsapp", "nome", "cnpj", "cpf")]
    ok = not proibidos and "texto" in contrato["payload_fechado"] and contrato["versao"] == mod.VERSAO
    return ok, "payload fechado=%s" % contrato["payload_fechado"] if ok else "campo de contato no payload: %s" % proibidos


@item("guarda: prod RECUSA por desenho (exit 4)")
def i02(mod, contrato, ctx):
    try:
        mod.validar_ambiente("prod", "http://127.0.0.1:6333", False)
    except mod.Recusa as exc:
        return exc.codigo == 4 and exc.motivo == "RECUSA_POR_DESENHO", "%s/%s" % (exc.motivo, exc.codigo)
    return False, "prod nao recusou"


@item("guarda: Qdrant remoto RECUSA em dev (exit 3)")
def i03(mod, contrato, ctx):
    try:
        mod.validar_ambiente("dev", "http://qdrant.exemplo.com:6333", False)
    except mod.Recusa as exc:
        return exc.motivo == "QDRANT_NAO_E_DEV" and exc.codigo == 3, "%s/%s" % (exc.motivo, exc.codigo)
    return False, "host remoto aceito"


@item("guarda: homolog exige --confirmo e dev aceita local")
def i04(mod, contrato, ctx):
    try:
        mod.validar_ambiente("homolog", "http://localhost:6333", False)
        return False, "homolog sem --confirmo passou"
    except mod.Recusa as exc:
        if exc.motivo != "HOMOLOG_SEM_CONFIRMO":
            return False, exc.motivo
    mod.validar_ambiente("dev", "http://127.0.0.1:6333", False)
    mod.validar_ambiente("homolog", "http://[::1]:6333", True)
    return True, ""


@item("guardas: ambiente inexistente = uso errado (exit 2)")
def i05(mod, contrato, ctx):
    try:
        mod.validar_ambiente("staging", "http://127.0.0.1:6333", False)
    except mod.Recusa as exc:
        return exc.codigo == 2 and exc.motivo == "AMBIENTE_INVALIDO", "%s/%s" % (exc.motivo, exc.codigo)
    return False, "staging aceito"


@item("fonte: auditoria reprova verbo de escrita e aprova as consultas do contrato")
def i06(mod, contrato, ctx):
    for sql in ("INSERT INTO sales_intelligence.interactions (id) VALUES (1)",
                "delete from sales_intelligence.interactions where true",
                "DROP TABLE sales_intelligence.interactions"):
        try:
            mod.auditar_fonte(sql)
            return False, "escrita passou: %s" % sql[:40]
        except mod.Recusa as exc:
            if exc.motivo != "ESCRITA_NO_CODIGO":
                return False, "%s -> %s" % (sql[:30], exc.motivo)
    for tipo in contrato["tipos_de_documento"]:
        mod.auditar_fonte(mod.montar_consulta(tipo))
    return True, "3 verbos reprovados, %d receitas aprovadas" % len(contrato["tipos_de_documento"])


@item("fonte: consulta nasce do contrato (sem nome de tabela literal no componente)")
def i07(mod, contrato, ctx):
    for tipo in contrato["tipos_de_documento"]:
        sql = mod.montar_consulta(tipo)
        if "SELECT" not in sql or tipo["tabela"] not in sql or tipo["id_coluna"] not in sql:
            return False, "receita incompleta de %s" % tipo["nome"]
        if tipo.get("canal_coluna") and tipo["canal_coluna"] not in sql:
            return False, "canal ausente em %s" % tipo["nome"]
    with open(ctx["componente"], "r", encoding="utf-8") as fh:
        fonte = fh.read()
    # Nenhum NOME DE TABELA pode estar no codigo: os nomes vivem no contrato. O prefixo do schema
    # aparece UMA vez, como guarda de validacao do contrato (nao como fonte de dado).
    literais = [l for l in ("interactions", "pain_hypotheses", "recommendations") if l in fonte]
    if fonte.count("sales_intelligence.") != 1:
        literais.append("sales_intelligence. x%d" % fonte.count("sales_intelligence."))
    return (not literais), ("tabela literal no componente: %s" % literais) if literais else "4 receitas do contrato"


@item("embedding: deterministico, com a dimensao do contrato e normalizado em L2")
def i08(mod, contrato, ctx):
    p = contrato["provedor_de_embedding"]
    a, b = mod.embed("objecao de preco no projeto", p), mod.embed("objecao de preco no projeto", p)
    if a != b:
        return False, "duas chamadas divergiram"
    if len(a) != p["dimensao"]:
        return False, "dimensao %d != contrato %d" % (len(a), p["dimensao"])
    norma = sum(v * v for v in a) ** 0.5
    if abs(norma - 1.0) > 1e-6:
        return False, "norma %.6f" % norma
    if mod.embed("", p) != [0.0] * p["dimensao"]:
        return False, "texto vazio nao e' vetor nulo"
    return True, "norma=%.12f" % norma


@item("embedding: sem acento ('objeção preço' == 'objecao preco')")
def i09(mod, contrato, ctx):
    p = contrato["provedor_de_embedding"]
    iguais = mod.embed("objeção preço", p) == mod.embed("objecao preco", p)
    return iguais, "" if iguais else "acento mudou o vetor"


@item("embedding: semelhanca lexical ordena certo e consulta sem correspondencia zera")
def i10(mod, contrato, ctx):
    p = contrato["provedor_de_embedding"]
    consulta = mod.embed("objecao sobre o preco do projeto", p)
    com_preco = mod.embed("objecao preco alto demais para o projeto", p)
    com_agenda = mod.embed("confirmar agenda da reuniao na proxima terca", p)
    s1, s2 = produto_escalar(consulta, com_preco), produto_escalar(consulta, com_agenda)
    s3 = produto_escalar(consulta, mod.embed("zzz qqq", p))
    ok = s1 > s2 >= s3 and s3 < contrato["busca"]["score_minimo"]
    return ok, "preco=%.4f agenda=%.4f ruido=%.4f" % (s1, s2, s3)


@item("id do ponto: UUIDv5 deterministico, estavel e distinto por origem")
def i11(mod, contrato, ctx):
    a = mod.id_do_ponto("memoria_comercial_v1", "sales_intelligence.interactions", "abc")
    b = mod.id_do_ponto("memoria_comercial_v1", "sales_intelligence.interactions", "abc")
    c = mod.id_do_ponto("memoria_comercial_v1", "sales_intelligence.interactions", "def")
    if a != b or a == c:
        return False, "%s %s %s" % (a, b, c)
    import uuid
    if str(uuid.UUID(a)) != a:
        return False, "id nao e' UUID"
    return True, a


@item("derivar: origem/PII/texto viram lacuna nomeada e nunca viram documento")
def i12(mod, contrato, ctx):
    linhas = {
        "MENSAGEM": [
            {"origem_id": "ok-1", "organization_id": "org", "canal": "EMAIL", "ocorrido_em": "2026-09-01",
             "texto": "mensagem limpa sobre eficiencia"},
            {"origem_id": "", "organization_id": "org", "canal": "EMAIL", "ocorrido_em": "2026-09-01",
             "texto": "sem origem"},
            {"origem_id": "sem-texto", "organization_id": "org", "canal": "EMAIL", "ocorrido_em": "2026-09-01",
             "texto": "   "},
            {"origem_id": "com-pii", "organization_id": "org", "canal": "EMAIL", "ocorrido_em": "2026-09-01",
             "texto": "responda para cliente@empresa.test sobre a proposta"},
            {"origem_id": "com-fone", "organization_id": "org", "canal": "EMAIL", "ocorrido_em": "2026-09-01",
             "texto": "ligar no (11) 98888-7777 amanha"},
        ]
    }
    documentos, lacunas = mod.derivar_documentos(contrato, linhas)
    nomes_docs = [d["origem_id"] for d in documentos]
    tipos_lacuna = sorted(l["lacuna"] for l in lacunas)
    ok = (nomes_docs == ["ok-1"] and tipos_lacuna == ["DOCUMENTO_SEM_TEXTO", "ORIGEM_SEM_ID",
                                                      "PII_SUSPEITA", "PII_SUSPEITA"])
    return ok, "docs=%s lacunas=%s" % (nomes_docs, tipos_lacuna)


@item("payload: exatamente os campos declarados, sem campo de contato")
def i13(mod, contrato, ctx):
    doc = documento()
    ponto = mod.montar_ponto(contrato, doc)
    chaves = sorted(ponto["payload"])
    if chaves != sorted(contrato["payload_fechado"]):
        return False, "chaves %s" % chaves
    if ponto["payload"]["conteudo_sha256"] != mod.hashlib.sha256(doc["texto"].encode("utf-8")).hexdigest():
        return False, "hash de conteudo divergente"
    texto = json.dumps(ponto["payload"], ensure_ascii=False).lower()
    sujeira = [t for t in ("@", "telefone", "whatsapp", "cnpj") if t in texto and t != "@"]
    return (not sujeira), ("conteudo suspeito: %s" % sujeira) if sujeira else "9 campos"


@item("payload: campo nao declarado e' RECUSADO (fechado, nao 'mais um')")
def i14(mod, contrato, ctx):
    contrato2 = copy.deepcopy(contrato)
    contrato2["payload_fechado"] = [c for c in contrato2["payload_fechado"] if c != "texto"]
    try:
        mod.montar_ponto(contrato2, documento())
    except mod.Recusa as exc:
        return exc.motivo == "PAYLOAD_FORA_DO_CONTRATO", exc.motivo
    return False, "payload fora do contrato passou"


@item("pre-condicao: corpus abaixo do piso NAO publica (faltando nomeado)")
def i15(mod, contrato, ctx):
    docs = corpus_no_piso(mod, contrato)
    docs = docs[: max(1, contrato["minimos"]["documentos"] - 3)]
    pre = mod.medir_pre_condicao(contrato, docs)
    return (not pre["atendida"]) and pre["faltando"] and pre["nome"] == "corpus comercial estavel", \
        "atendida=%s faltando=%s" % (pre["atendida"], pre["faltando"])


@item("pre-condicao: corpus no piso publica (faltando vazio)")
def i16(mod, contrato, ctx):
    docs = corpus_no_piso(mod, contrato)
    pre = mod.medir_pre_condicao(contrato, docs)
    return pre["atendida"] and pre["faltando"] == [] and len(pre["tipos_com_base"]) >= contrato["minimos"]["tipos_com_base"] \
        and pre["por_tipo"] == contrato["minimos"]["por_tipo"], \
        "documentos=%d tipos=%s" % (len(docs), pre["tipos_com_base"])


@item("pre-condicao: um tipo abaixo do piso reprova e nomeia o tipo")
def i17(mod, contrato, ctx):
    docs = corpus_no_piso(mod, contrato)
    tipo = contrato["tipos_de_documento"][0]["nome"]
    docs = [d for d in docs if d["tipo"] != tipo]
    pre = mod.medir_pre_condicao(contrato, docs)
    return (not pre["atendida"]) and any(f.startswith(tipo + ":") for f in pre["faltando"]), \
        "faltando=%s" % pre["faltando"]


@item("busca: filtro declarado por tipo/organizacao e tipo desconhecido = uso errado")
def i18(mod, contrato, ctx):
    f = mod.montar_filtro(contrato, tipo="OBJECAO", organization_id="org-1")
    if [m["key"] for m in f] != ["tipo", "organization_id"]:
        return False, f
    try:
        mod.montar_filtro(contrato, tipo="INVENTADO")
        return False, "tipo inventado passou"
    except mod.Recusa as exc:
        if exc.codigo != 2:
            return False, exc.motivo
    return mod.montar_filtro(contrato) == [], ""


@item("busca: piso de score descarta ruido (abaixo do minimo nao volta)")
def i19(mod, contrato, ctx):
    piso = contrato["busca"]["score_minimo"]
    achados = [{"id": "1", "score": 0.9}, {"id": "2", "score": piso}, {"id": "3", "score": piso - 0.001}]
    mantidos = [a["id"] for a in mod.filtrar_por_score(contrato, achados)]
    return mantidos == ["1", "2"], "piso=%s mantidos=%s" % (piso, mantidos)


@item("hash do relatorio: mesma medicao -> mesmo hash (relogio e estado do Qdrant fora)")
def i20(mod, contrato, ctx):
    docs = corpus_no_piso(mod, contrato)
    r1 = relatorio_minimo(mod, contrato, docs, ctx["contrato"], agora="2026-10-03T00:00:00Z")
    r2 = relatorio_minimo(mod, contrato, docs, ctx["contrato"], agora="2026-10-04T11:22:33Z")
    r2["indexacao"] = {"memoria_publicada": True, "pontos_antes": 0, "pontos_depois": 11}
    h1, h2 = mod.hash_do_relatorio(r1), mod.hash_do_relatorio(r2)
    r3 = copy.deepcopy(r1)
    r3["corpus"]["documentos"][0]["texto"] = "outro conteudo comercial"
    h3 = mod.hash_do_relatorio(r3)
    return h1 == h2 and len(h1) == 64 and h3 != h1, "%s %s %s" % (h1[:12], h2[:12], h3[:12])


@item("relatorio: nenhum documento derivado carrega PII (nem e-mail, nem telefone)")
def i21(mod, contrato, ctx):
    linhas = {"MENSAGEM": [{"origem_id": "a", "organization_id": "org", "canal": "EMAIL",
                            "ocorrido_em": "2026-09-01", "texto": "proposta enviada, sem dado pessoal"},
                           {"origem_id": "b", "organization_id": "org", "canal": "EMAIL",
                            "ocorrido_em": "2026-09-01", "texto": "email joao@cliente.test e telefone 11 98888-7777"}]}
    documentos, lacunas = mod.derivar_documentos(contrato, linhas)
    achados = [d["origem_id"] for d in documentos if mod.pii_em_texto(d["texto"], contrato)]
    return documentos and not achados and len(lacunas) == 1, "docs=%s lacunas=%s" % (
        [d["origem_id"] for d in documentos], [l["lacuna"] for l in lacunas])


@item("CLI: --planejar declara tipos/dimensao/provedor sem banco (exit 0)")
def i22(mod, contrato, ctx):
    proc = subprocess.run([sys.executable, ctx["componente"], "--planejar", "--contrato", ctx["contrato"]],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        return False, "exit=%s %s" % (proc.returncode, proc.stderr.strip()[:120])
    plano = json.loads(proc.stdout)
    ok = (plano["dimensao"] == contrato["provedor_de_embedding"]["dimensao"]
          and plano["colecao"] == contrato["colecao"]["nome"]
          and [t["nome"] for t in plano["tipos_de_documento"]] == [t["nome"] for t in contrato["tipos_de_documento"]])
    return ok, plano["provedor_de_embedding"]


@item("CLI: prod = exit 4, sem --porta-banco = exit 2, colecao ausente na busca = exit 3")
def i23(mod, contrato, ctx):
    proc = subprocess.run([sys.executable, ctx["componente"], "--ambiente", "prod", "--planejar",
                           "--contrato", ctx["contrato"]], capture_output=True, text=True)
    if proc.returncode != 4:
        return False, "prod exit=%s" % proc.returncode
    proc = subprocess.run([sys.executable, ctx["componente"], "--ambiente", "dev", "--contrato", ctx["contrato"]],
                          capture_output=True, text=True, env=dict(os.environ, TRE_QDRANT_URL="http://127.0.0.1:6333"))
    if proc.returncode != 2:
        return False, "sem porta-banco exit=%s" % proc.returncode
    proc = subprocess.run([sys.executable, ctx["componente"], "--buscar", "qualquer coisa", "--qdrant-url",
                           "http://127.0.0.1:9", "--contrato", ctx["contrato"]], capture_output=True, text=True)
    recusa = json.loads(proc.stdout).get("recusa") if proc.stdout.strip() else ""
    return proc.returncode == 3 and recusa in ("QDRANT_INDISPONIVEL", "COLECAO_AUSENTE"), "busca exit=%s %s" % (
        proc.returncode, recusa)


@item("saida: HTML auto-contido (sem rede/script) com pre-condicao e corpus")
def i24(mod, contrato, ctx):
    docs = corpus_no_piso(mod, contrato)
    r = relatorio_minimo(mod, contrato, docs, ctx["contrato"])
    r["hash_do_relatorio"] = mod.hash_do_relatorio(r)
    html = mod.render_html(r)
    proibido = [t for t in ("http://", "https://", "<script", "<link") if t in html]
    ok = not proibido and "Pre-condicao" in html and "Corpus comercial" in html and len(html) > 600
    return ok, ("proibido: %s" % proibido) if proibido else "html=%d bytes" % len(html)


@item("segredo: valor vigiado na evidencia = exit 5 (recusa antes de publicar)")
def i25(mod, contrato, ctx):
    os.environ["TRE_MEMORIA_COMERCIAL_TOKEN"] = "segredo-que-nao-pode-vazar"
    try:
        try:
            mod.conferir_segredos({"detalhe": "segredo-que-nao-pode-vazar"})
            return False, "segredo passou"
        except mod.Recusa as exc:
            if exc.codigo != 5:
                return False, "codigo %s" % exc.codigo
        mod.conferir_segredos({"detalhe": "conteudo limpo"})
        return True, ""
    finally:
        os.environ.pop("TRE_MEMORIA_COMERCIAL_TOKEN", None)


# ------------------------------------------------------------------------------------------------
# Autoteste por mutacao: cada mutacao tem de REPROVAR o item declarado
# ------------------------------------------------------------------------------------------------
@item("fonte: resposta multi-linha do psql e' lida inteira (armadilha medida no aceite)")
def i26(mod, contrato, ctx):
    multilinha = '[\n  {"a": "um"},\n  {"a": "dois"}\n]\n'
    if mod.extrair_json(multilinha) != [{"a": "um"}, {"a": "dois"}]:
        return False, "array multi-linha lido errado"
    if mod.extrair_json('[{"a": "um"}]') != [{"a": "um"}]:
        return False, "linha unica lida errado"
    if mod.extrair_json("[]") != []:
        return False, "lista vazia lida errado"
    try:
        mod.extrair_json("SET\nnao e json")
        return False, "lixo passou como JSON"
    except mod.Recusa as exc:
        if exc.motivo != "FONTE_JSON_ILEGIVEL":
            return False, exc.motivo
    return True, "multi-linha, linha unica, lista vazia e lixo"


MUTACOES = [
    ("M1 vetor sem normalizar L2", "    return [round(v / norma, 12) for v in vetor]", "    return vetor",
     "embedding: deterministico, com a dimensao do contrato e normalizado em L2"),
    ("M2 id aleatorio (quebra idempotencia)",
     '    return str(uuid.uuid5(NAMESPACE_DO_ID, "%s|%s|%s" % (colecao, origem_tabela, origem_id)))',
     "    return str(uuid.uuid4())",
     "id do ponto: UUIDv5 deterministico, estavel e distinto por origem"),
    ("M3 PII indexada", "            if achados:", "            if False:",
     "derivar: origem/PII/texto viram lacuna nomeada e nunca viram documento"),
    ("M4 pre-condicao sempre atendida", ' "atendida": not faltando,', ' "atendida": True,',
     "pre-condicao: corpus abaixo do piso NAO publica (faltando nomeado)"),
    ("M5 prod deixa de recusar", '    if ambiente == "prod":', '    if ambiente == "prod-but-not-really":',
     "guarda: prod RECUSA por desenho (exit 4)"),
    ("M6 dimensao fixa fora do contrato", '    dimensao = provedor["dimensao"]\n    semente',
     '    dimensao = 128\n    semente',
     "embedding: deterministico, com a dimensao do contrato e normalizado em L2"),
    ("M7 piso de score desligado",
     '    return [a for a in achados if a["score"] >= contrato["busca"]["score_minimo"]]', "    return list(achados)",
     "busca: piso de score descarta ruido (abaixo do minimo nao volta)"),
    ("M8 auditoria de escrita desligada", "    for verbo in VERBOS_DE_ESCRITA:", "    for verbo in ():",
     "fonte: auditoria reprova verbo de escrita e aprova as consultas do contrato"),
    ("M9 payload com campo de contato",
     '        "colecao_versao": contrato["colecao"]["versao"],\n',
     '        "colecao_versao": contrato["colecao"]["versao"],\n        "email": "vazado@test",\n',
     "payload: exatamente os campos declarados, sem campo de contato"),
    ("M10 HTML com script", '        "linhas": "".join(linhas),', '        "linhas": "<script>x</script>",',
     "saida: HTML auto-contido (sem rede/script) com pre-condicao e corpus"),
    ("M11 leitura por linha (fragmento do psql)",
     '    try:\n        return json.loads(texto)',
     '    return json.loads([l for l in texto.splitlines() if l.strip()][-1])\n    try:\n        return json.loads(texto)',
     "fonte: resposta multi-linha do psql e' lida inteira (armadilha medida no aceite)"),
]


def rodar(caminho_componente, caminho_contrato, apenas=None):
    mod = carregar_modulo(caminho_componente)
    contrato = ler_contrato(caminho_contrato)
    ctx = {"componente": caminho_componente, "contrato": caminho_contrato}
    ok_total, falhas, linhas = 0, 0, []
    for nome, fn in ITENS:
        if apenas and nome != apenas:
            continue
        try:
            ok, detalhe = fn(mod, contrato, ctx)
        except Exception as exc:  # qualquer excecao no item = FALHOU (com o motivo)
            ok, detalhe = False, "%s: %s" % (type(exc).__name__, exc)
        if ok:
            ok_total += 1
            linhas.append("OK    %s" % nome)
        else:
            falhas += 1
            linhas.append("FALHOU %s — %s" % (nome, detalhe))
    return ok_total, falhas, linhas


def autoteste(caminho_componente, caminho_contrato):
    with open(caminho_componente, "r", encoding="utf-8") as fh:
        fonte = fh.read()
    detectadas, total, linhas = 0, 0, []
    tmp = tempfile.mkdtemp(prefix="autoteste-memoria-")
    try:
        for nome, de, para, item_alvo in MUTACOES:
            total += 1
            if de not in fonte:
                linhas.append("MUTACAO_NAO_APLICADA %s (ancora de texto ausente)" % nome)
                continue
            destino = os.path.join(tmp, "mut_%d.py" % total)
            with open(destino, "w", encoding="utf-8") as fh:
                fh.write(fonte.replace(de, para, 1))
            _, falhas, _ = rodar(destino, caminho_contrato, apenas=item_alvo)
            if falhas:
                detectadas += 1
                linhas.append("OK    mutacao detectada: %s -> '%s' reprovou" % (nome, item_alvo[:60]))
            else:
                linhas.append("FALHOU mutacao NAO detectada: %s (item '%s' passou mutado)"
                              % (nome, item_alvo[:60]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for linha in linhas:
        print(linha)
    return detectadas, total


def main(argv=None):
    p = argparse.ArgumentParser(description="Suite offline da memoria comercial (TRE-W9-E06-T01)")
    p.add_argument("--componente", default=COMPONENTE_PADRAO)
    p.add_argument("--contrato", default=CONTRATO_PADRAO)
    p.add_argument("--autoteste", action="store_true")
    args = p.parse_args(argv)
    if not os.path.exists(args.componente) or not os.path.exists(args.contrato):
        print("FALHOU componente/contrato ausente: %s | %s" % (args.componente, args.contrato))
        return 1
    ok, falhas, linhas = rodar(args.componente, args.contrato)
    for linha in linhas:
        print(linha)
    if args.autoteste:
        detectadas, total = autoteste(args.componente, args.contrato)
        if detectadas != total:
            print("VERIFICADOR_MEMORIA_COMERCIAL_FALHOU (autoteste %d/%d)" % (detectadas, total))
            return 1
        print("AUTOTESTE %d/%d mutacoes detectadas" % (detectadas, total))
    if falhas == 0:
        print("VERIFICADOR_MEMORIA_COMERCIAL_PASS (%d itens, 0 falhas)" % ok)
        return 0
    print("VERIFICADOR_MEMORIA_COMERCIAL_FALHOU (%d itens, %d falhas)" % (ok, falhas))
    return 1


if __name__ == "__main__":
    sys.exit(main())
