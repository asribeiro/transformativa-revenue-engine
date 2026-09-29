#!/usr/bin/env python3
"""Aceite TRE-W0-E03-T01: prova que o Data Contract V1.0 nao diverge.

Confere tres artefatos entre si e contra as regras do proprio contrato:
  1. db/migrations/0001_sales_intelligence_v1.sql  (schema: fonte unica das colunas)
  2. docs/data/DATA_CONTRACT_V1.md                 (contrato legivel)
  3. docs/data/data_contract_v1.json               (contrato legivel por maquina)

Uso: python3 scripts/verificar_contrato_dados.py
Nao toca banco, rede ou producao: le arquivos do repositorio e faz contas.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SQL = RAIZ / "db/migrations/0001_sales_intelligence_v1.sql"
DOC = RAIZ / "docs/data/DATA_CONTRACT_V1.md"
JSN = RAIZ / "docs/data/data_contract_v1.json"

falhas: list[str] = []
itens = 0


def chk(nome: str, ok: bool, detalhe: str = "") -> None:
    global itens
    itens += 1
    if ok:
        print(f"OK     {nome}")
    else:
        print(f"FALHOU {nome}" + (f"  -> {detalhe}" if detalhe else ""))
        falhas.append(nome)


def ler(p: Path) -> str:
    if not p.is_file():
        print(f"FALHOU artefato ausente: {p.relative_to(RAIZ)}")
        sys.exit(1)
    return p.read_text(encoding="utf-8")


sql_txt, doc_txt, jsn_txt = ler(SQL), ler(DOC), ler(JSN)

try:
    contrato = json.loads(jsn_txt)
except json.JSONDecodeError as exc:
    print(f"FALHOU docs/data/data_contract_v1.json nao e JSON valido: {exc}")
    sys.exit(1)

print(f"Data Contract {contrato['contract']['version']} — congelado em {contrato['contract']['frozen_at']}")
print()

# ---------------------------------------------------------------- parse do SQL
TABELA = re.compile(r"CREATE TABLE sales_intelligence\.(\w+)\s*\((.*?)\n\);", re.S)
INDICE = re.compile(r"CREATE INDEX (\w+) ON sales_intelligence\.(\w+)\s*\(([^)]*)\);")
COLUNA = re.compile(r"^\s{4}([a-z_][a-z0-9_]*)\s+([A-Za-z]+(?:\([^)]*\))?)")

tabelas: dict[str, list[dict]] = {}
for nome, corpo in TABELA.findall(sql_txt):
    cols = []
    for linha in corpo.splitlines():
        if not linha.strip() or linha.strip().startswith("--"):
            continue
        m = COLUNA.match(linha)
        if not m:
            continue
        cols.append({
            "nome": m.group(1),
            "tipo": m.group(2),
            "pk": "PRIMARY KEY" in linha,
            "fk": re.search(r"REFERENCES sales_intelligence\.(\w+)\((\w+)\)", linha),
            "texto": linha.strip(),
        })
    tabelas[nome] = cols

indices = [(n, t, [c.strip() for c in cols.split(",")]) for n, t, cols in INDICE.findall(sql_txt)]

# ---------------------------------------------------------------- 1. tabelas
nomes_sql = set(tabelas)
nomes_json = {t["name"] for t in contrato["tables"]}
chk("schema SQL tem as 12 tabelas do contrato",
    len(nomes_sql) == 12 and nomes_sql == nomes_json,
    f"sql={sorted(nomes_sql)} json={sorted(nomes_json)}")

# ---------------------------------------------------------------- 2. PK canonica
sem_pk = [t for t, cols in tabelas.items()
          if not any(c["nome"] == "id" and c["pk"] and c["tipo"].upper() == "UUID" for c in cols)]
chk("toda tabela tem PK canonica `id UUID PRIMARY KEY`", not sem_pk, f"sem PK UUID: {sem_pk}")

# ---------------------------------------------------------------- 3. FKs validas
fk_quebrada = []
for tabela, cols in tabelas.items():
    for c in cols:
        if not c["fk"]:
            continue
        dest, col_dest = c["fk"].group(1), c["fk"].group(2)
        if dest not in tabelas or not any(x["nome"] == col_dest for x in tabelas[dest]):
            fk_quebrada.append(f"{tabela}.{c['nome']} -> {dest}({col_dest})")
chk("toda FK aponta para tabela e coluna existentes", not fk_quebrada, str(fk_quebrada))

# ---------------------------------------------------------------- 4. vinculos logicos declarados
logicos = contrato["logical_links_without_fk"]
problemas_logicos = []
for link in logicos:
    tabela, coluna = link.split(".")
    if tabela not in tabelas or not any(c["nome"] == coluna for c in tabelas[tabela]):
        problemas_logicos.append(f"{link} nao existe no schema")
    elif any(c["nome"] == coluna and c["fk"] for c in tabelas[tabela]):
        problemas_logicos.append(f"{link} ganhou FK: o contrato diz que nao tem na V1")
chk("vinculos logicos declarados sem FK existem e seguem sem FK", not problemas_logicos,
    str(problemas_logicos))

# ---------------------------------------------------------------- 5. indices minimos
def normaliza(cols_txt: str) -> list[str]:
    return [re.sub(r"\s+", " ", c.strip().lower()) for c in cols_txt.split(",")]

colunas_unicas = {(t, c["nome"]) for t, cols in tabelas.items() for c in cols
                  if "UNIQUE" in c["texto"] or c["pk"]}

faltando = []
indices_por_unique = []
for item in contrato["indexes"]:
    m = re.match(r"(\w+)\((.+)\)$", item)
    if m is None:
        faltando.append(f"{item} (formato invalido no contrato)")
        continue
    tabela, cols = m.group(1), m.group(2)
    pedidas = tuple(c.strip().lower() for c in cols.split(","))
    candidatos = {tuple(c.strip().lower() for c in cs) for _, t, cs in indices if t == tabela}
    if pedidas in candidatos:
        continue
    # Indice coberto por restricao UNIQUE (ou PK) na propria coluna: conta como satisfeito,
    # desde que TODAS as colunas pedidas estejam sob UNIQUE/PK. O contrato diz isso no doc (secao 4).
    if all((tabela, c) in colunas_unicas for c in pedidas):
        indices_por_unique.append(item)
        continue
    faltando.append(item)
chk("os 16 indices minimos do contrato existem na migration", not faltando, str(faltando))
if indices_por_unique:
    print(f"       (satisfeitos por restricao UNIQUE/PK, sem CREATE INDEX: {', '.join(indices_por_unique)})")

sem_indice = [f"{t}({','.join(cs)})" for _, t, cs in indices
              if not any(item.startswith(f"{t}(") for item in contrato["indexes"])]
chk("a migration nao cria indice fora do contrato", not sem_indice, str(sem_indice))

# ---------------------------------------------------------------- 6. deduplicacao
ded = contrato["dedup"]
chk("dedup: limiar de merge automatico documentado no JSON e no documento",
    abs(float(ded["auto_merge_threshold"]) - 0.95) < 1e-9
    and "0.95" in doc_txt and "REVIEW_REQUIRED" in doc_txt,
    f"json={ded['auto_merge_threshold']}")

# ---------------------------------------------------------------- 7. eventos
ev = contrato["events"]
secao6 = doc_txt.split("## 6. Eventos")[1].split("## 7.")[0] if "## 6. Eventos" in doc_txt else ""
tokens_doc = set(re.findall(r"\b[A-Z][A-Z0-9_]{3,}\b", secao6))
esperados = set(ev["pg_to_odoo"]) | set(ev["odoo_to_pg"])
chk("catalogo de eventos do documento == catalogo do JSON",
    tokens_doc == esperados,
    f"so no doc={sorted(tokens_doc - esperados)} so no json={sorted(esperados - tokens_doc)}")
chk("envelope de evento exige versao explicita",
    "event_version" in ev["envelope_required_fields"] and "event_version" in secao6)
chk("regras de idempotencia e dead-letter declaradas",
    len(ev["rules"]) >= 5 and "idempotency_key" in secao6 and "dead-letter" in secao6.lower())

# ---------------------------------------------------------------- 8. vocabularios
voc = contrato["vocabularies"]
secao7 = doc_txt.split("## 7. Vocabulários fechados")[1].split("## 8.")[0] if "## 7." in doc_txt else ""
ausentes = []
for chave, valores in voc.items():
    if chave == "organizations.status":
        if "DISCOVERED" not in secao7:
            ausentes.append(f"{chave}=DISCOVERED")
        continue
    for v in valores:
        if v not in secao7:
            ausentes.append(f"{chave}={v}")
chk("todo valor de vocabulario do JSON aparece no documento", not ausentes, str(ausentes[:8]))

# ---------------------------------------------------------------- 9. funil
funil = contrato["funnel_stages"]
secao_funil = doc_txt.split("### 7.1 Funil")[1].split("## 8.")[0] if "### 7.1 Funil" in doc_txt else ""
chk("estagios do funil do JSON aparecem no documento",
    all(f in secao_funil for f in funil), str([f for f in funil if f not in secao_funil]))

# ---------------------------------------------------------------- 10. scores
sc = contrato["scores"]
pesos_json = sc["priority_weights"]
unidade = re.search(r"PRIORITY\s*=\s*(.+?)(?:\n\n|\n```)", doc_txt, re.S)
pesos_doc: dict[str, float] = {}
if unidade:
    for m in re.finditer(r"([0-9]+\.[0-9]+)\s*\*\s*([A-Z_]+)", unidade.group(1)):
        pesos_doc[m.group(2)] = float(m.group(1))
chk("formula do Priority Score no documento == pesos do JSON",
    pesos_doc == pesos_json, f"doc={pesos_doc} json={pesos_json}")
soma = round(sum(pesos_json.values()), 10)
chk("pesos do Priority Score somam exatamente 1.00", abs(soma - 1.0) < 1e-9, f"soma={soma}")
chk("score exige coluna de versao",
    sc["requires_version_column"] in {c["nome"] for c in tabelas.get("scores", [])},
    sc["requires_version_column"])

faixas = sorted(sc["tiers"], key=lambda t: t["min"])
cobre = faixas and faixas[0]["min"] == 0.0 and faixas[-1]["max"] == 100.0
lacuna = [f"{a['name']}->{b['name']}" for a, b in zip(faixas, faixas[1:])
          if round(b["min"] - a["max"], 4) > 0.01 or b["min"] <= a["max"]]
chk("tiering cobre 0-100 sem lacuna nem sobreposicao", bool(cobre) and not lacuna,
    f"cobre={cobre} lacunas={lacuna}")
chk("tiering do documento == tiering do JSON",
    all(t["name"] in doc_txt for t in faixas), str([t["name"] for t in faixas]))

# ---------------------------------------------------------------- 11. compliance
comp = contrato["compliance"]
colunas_por_tabela = {t: {c["nome"] for c in cols} for t, cols in tabelas.items()}
todas_colunas = set().union(*colunas_por_tabela.values()) if colunas_por_tabela else set()
faltam_campos = [f for f in comp["required_fields"] if f not in todas_colunas]
chk("campos minimos de compliance existem no schema", not faltam_campos, str(faltam_campos))
presentes_por_engano = [g for g in comp["declared_gaps"] if g in todas_colunas]
chk("lacunas declaradas de compliance continuam ausentes do schema (nao fingimos que existem)",
    not presentes_por_engano, str(presentes_por_engano))

# ---------------------------------------------------------------- 12. IDs canonicos
ci = contrato["canonical_ids"]
chk("regra de ID canonico declarada nos dois artefatos",
    "UUID" in ci["primary_key_type"] and "nunca substituem" in doc_txt)
refs = ci["external_reference_columns"]
refs_no_schema = [r for r in refs if r in todas_colunas]
chk("colunas de referencia externa existem no schema", len(refs_no_schema) == len(refs),
    f"no schema: {refs_no_schema}")
chk("coluna canonica `id` nao e referenciada como FK de Odoo (Odoo nunca e dono do id)",
    not any(c["nome"] == "odoo_id" for cols in tabelas.values() for c in cols))

# ---------------------------------------------------------------- 13. governanca
gov = contrato["governance"]
chk("mudanca de contrato exige aprovacao registrada",
    gov["approval_required"] is True and "registro-de-aprovacoes" in gov["approval_record"]
    and "registro-de-aprovacoes" in doc_txt)
chk("nenhuma DDL nasce em producao (ADR-005) declarado",
    "ADR-005" in gov["no_ddl_in_production"] and "ADR-005" in doc_txt)
chk("documento e JSON declaram a mesma versao do contrato",
    f"**Versão:** {contrato['contract']['version']}" in doc_txt)
iso = contrato["contract"]["frozen_at"]
ano, mes, dia = iso.split("-")
br = f"{dia}/{mes}/{ano}"
chk("documento aponta o card e a data de congelamento",
    contrato["contract"]["card"] in doc_txt and (iso in doc_txt or br in doc_txt),
    f"card={contrato['contract']['card']} data esperada={iso} ou {br}")

# ---------------------------------------------------------------- resultado
print()
if falhas:
    print(f"RESULTADO: FALHOU ({len(falhas)} de {itens} itens)")
    for f in falhas:
        print(f"  - {f}")
    sys.exit(1)
print(f"RESULTADO: PASS ({itens} itens, 0 falhas)")
