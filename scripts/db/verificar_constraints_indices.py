#!/usr/bin/env python3
"""Aceite TRE-W1-E03-T01 (criterios 1 e 2): prova ITEM A ITEM que as constraints (PK/FK/UNIQUE)
e os indices do Data Contract V1.0 existem no alvo, com o MESMO nome, a MESMA tabela, a MESMA
unicidade e a MESMA lista de colunas (com direcao DESC) — e que nada sobrou.

O ESPERADO NAO e lista escrita a mao: sai dos proprios artefatos congelados, na mesma execucao.
  1. `db/migrations/0001_sales_intelligence_v1.sql` — PK inline, UNIQUE inline, REFERENCES e os
     `CREATE INDEX` (fonte unica do schema, ja congelada em sha256 pelo runner de migrations);
  2. `docs/data/data_contract_v1.json` — os 16 itens de indice minimo do contrato.
Se a migration ou o contrato mudarem, o esperado muda junto — este verificador nao envelhece.

O que ele conta (criterio 1): `pg_indexes` do alvo tem EXATAMENTE 30 indices — 12 PK
(`<tabela>_pkey`), 15 `CREATE INDEX` nomeados e 3 UNIQUE declaradas inline
(`organizations.odoo_partner_id`, `contacts.odoo_partner_id`, `sync_events.idempotency_key`),
cada um conferido nome a nome, coluna a coluna.

O que ele compara (criterio 2), item a item: PRIMARY KEY (uma por tabela, coluna `id`),
FOREIGN KEY (par tabela.coluna -> tabela.coluna de destino) e UNIQUE (tabela + colunas).

Leitura pura: so SELECT. Nenhum DDL/DML no alvo (a prova de que o verificador REPROVA fica em
`scripts/db/teste-constraints-indices.sh`, que roda em container descartavel).

Uso:
  python3 scripts/db/verificar_constraints_indices.py --esperado
  python3 scripts/db/verificar_constraints_indices.py --banco 'docker exec pg-sales-dev psql -U sales_ai -d sales_intelligence'

O prefixo NAO leva `-i`: este processo roda o psql com stdin=DEVNULL (defeito D03 do E01 — um
`docker exec -i` herda o stdin de quem chamou e mata em silencio o script entregue por SSH).
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent.parent
MIGRATION = RAIZ / "db/migrations/0001_sales_intelligence_v1.sql"
JSN = RAIZ / "docs/data/data_contract_v1.json"

PARSER = argparse.ArgumentParser(
    description="Confere constraints (PK/FK/UNIQUE) e indices do Data Contract V1.0 contra o alvo, item a item."
)
PARSER.add_argument("--banco", metavar="'<prefixo psql>'", default=None,
                    help="prefixo do comando psql do alvo (ex.: 'docker exec pg-sales-dev psql "
                         "-U sales_ai -d sales_intelligence'). Sem `-i`. So leitura.")
PARSER.add_argument("--esperado", action="store_true",
                    help="imprime o esperado derivado dos artefatos versionados e sai (nao toca banco)")
ARGS = PARSER.parse_args()

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


def norm_cols(texto: str) -> tuple[str, ...]:
    """Normaliza lista de colunas: sem espaco superfluo, minuscula (preserva a direcao `desc`)."""
    return tuple(re.sub(r"\s+", " ", c.strip()).lower() for c in texto.split(",") if c.strip() != "")


def descreve(idx: tuple[str, str, str, tuple[str, ...]]) -> str:
    return f"{idx[1]} [{'UNIQUE' if idx[2] == 'U' else 'INDEX'}] {idx[0]}({', '.join(idx[3])})"


# ---------------------------------------------------------------- parse dos artefatos
sql_txt = ler(MIGRATION)
try:
    contrato = json.loads(ler(JSN))
except json.JSONDecodeError as exc:
    print(f"FALHOU docs/data/data_contract_v1.json nao e JSON valido: {exc}")
    sys.exit(1)

TABELA = re.compile(r"CREATE TABLE sales_intelligence\.(\w+)\s*\((.*?)\n\);", re.S)
COLUNA = re.compile(r"^\s{4}([a-z_][a-z0-9_]*)\s+([A-Za-z]+(?:\s*\([^)]*\))?)")
INDICE = re.compile(r"CREATE INDEX (\w+) ON sales_intelligence\.(\w+)\s*\(([^)]*)\);")
FK_SQL = re.compile(r"REFERENCES sales_intelligence\.(\w+)\((\w+)\)")

tabelas: dict[str, list[tuple[str, str]]] = {}
for nome_t, corpo in TABELA.findall(sql_txt):
    cols: list[tuple[str, str]] = []
    for linha in corpo.splitlines():
        if not linha.strip() or linha.strip().startswith("--"):
            continue
        m = COLUNA.match(linha)
        if m:
            cols.append((m.group(1), linha.strip()))
    tabelas[nome_t] = cols

pk_esperada: dict[str, tuple[str, ...]] = {}
uq_esperada: dict[str, list[tuple[str, ...]]] = {}
fk_esperada: dict[tuple[str, str], tuple[str, str]] = {}
idx_esperados: set[tuple[str, str, str, tuple[str, ...]]] = set()

for nome_t, cols in tabelas.items():
    for col, texto in cols:
        if "PRIMARY KEY" in texto:
            pk_esperada[nome_t] = pk_esperada.get(nome_t, ()) + (col,)
        elif "UNIQUE" in texto:
            uq_esperada.setdefault(nome_t, []).append((col,))
            idx_esperados.add((nome_t, f"{nome_t}_{col}_key", "U", (col,)))
    for col, texto in cols:
        m = FK_SQL.search(texto)
        if m:
            fk_esperada[(nome_t, col)] = (m.group(1), m.group(2))

for nome_i, nome_t, cols_i in INDICE.findall(sql_txt):
    idx_esperados.add((nome_t, nome_i, "N", norm_cols(cols_i)))

# As 12 PK nao aparecem como `CREATE INDEX` na migration: o Postgres cria o indice de apoio
# `<tabela>_pkey` (UNIQUE) ao criarem a constraint. Sao eles que fecham a conta em 30.
for nome_t, cols_pk in pk_esperada.items():
    idx_esperados.add((nome_t, f"{nome_t}_pkey", "U", cols_pk))

n_pk = len(pk_esperada)
n_uq = sum(len(v) for v in uq_esperada.values())
n_exp = len(INDICE.findall(sql_txt))
n_idx = len(idx_esperados)
n_fk = len(fk_esperada)

if ARGS.esperado:
    print(f"ESPERADO derivado de {MIGRATION.relative_to(RAIZ)} e {JSN.relative_to(RAIZ)}")
    print(f"-- indices: {n_idx} ({n_pk} PK + {n_exp} CREATE INDEX + {n_uq} UNIQUE inline)")
    for e in sorted(idx_esperados):
        print(f"   I  {descreve(e)}")
    print(f"-- PRIMARY KEY: {n_pk}")
    for t in sorted(pk_esperada):
        print(f"   PK {t}({', '.join(pk_esperada[t])})")
    print(f"-- FOREIGN KEY: {n_fk}")
    for t, c in sorted(fk_esperada):
        print(f"   FK {t}.{c} -> {fk_esperada[(t, c)][0]}.{fk_esperada[(t, c)][1]}")
    print(f"-- UNIQUE: {n_uq}")
    for t in sorted(uq_esperada):
        for u in uq_esperada[t]:
            print(f"   UQ {t}({', '.join(u)})")
    sys.exit(0)

if not ARGS.banco:
    print(__doc__)
    print("uso: --banco '<prefixo psql>' ou --esperado (fail-closed: sem alvo nao ha veredito)")
    sys.exit(2)

# ---------------------------------------------------------------- 1. coerencia dos artefatos
nomes_sql = set(tabelas)
nomes_json = {t["name"] for t in contrato["tables"]}
chk("a migration declara as 12 tabelas do contrato",
    len(nomes_sql) == 12 and nomes_sql == nomes_json,
    f"sql={len(nomes_sql)} json={len(nomes_json)}")

sem_pk_id = [t for t in tabelas if pk_esperada.get(t) != ("id",)]
chk("a migration declara PK `id` em toda tabela", not sem_pk_id, str(sem_pk_id))

chk("o esperado fecha em 30 indices = 12 PK + 15 CREATE INDEX + 3 UNIQUE inline",
    (n_pk, n_exp, n_uq, n_idx) == (12, 15, 3, 30),
    f"pk={n_pk} explicitos={n_exp} unique={n_uq} total={n_idx}")

faltam_contrato = []
for item in contrato["indexes"]:
    m = re.match(r"(\w+)\((.+)\)$", item)
    if m is None:
        faltam_contrato.append(f"{item} (formato invalido no contrato)")
        continue
    alvo_i = (m.group(1), norm_cols(m.group(2)))
    if not any(e[0] == alvo_i[0] and e[3] == alvo_i[1] for e in idx_esperados):
        faltam_contrato.append(item)
chk(f"os {len(contrato['indexes'])} itens de indice do contrato tem indice correspondente na migration",
    not faltam_contrato, str(faltam_contrato))

print(f"       (esperado: {n_idx} indices, {n_pk} PK, {n_fk} FK, {n_uq} UNIQUE)")
print()

# ---------------------------------------------------------------- 2. alvo (so leitura)
def psql(sql: str) -> list[list[str]]:
    cmd = shlex.split(ARGS.banco) + ["-A", "-t", "-F", "|", "-c", sql]
    proc = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        raise RuntimeError(f"exit {proc.returncode}: {proc.stderr.strip()[:300]}")
    return [linha.split("|") for linha in proc.stdout.splitlines() if linha.strip() != ""]


print(f"--- alvo: {ARGS.banco}")
try:
    ident = psql("SELECT current_database() || ' @ ' || current_user")
    chk("o alvo responde ao prefixo de comando", bool(ident), "sem resposta")
    if ident:
        print(f"       (alvo: {ident[0][0]})")
    schema = psql("SELECT count(*) FROM information_schema.schemata WHERE schema_name='sales_intelligence'")
    chk("o alvo tem o schema `sales_intelligence`",
        bool(schema) and schema[0][0] == "1", f"linhas={schema}")

    # ---- indices: nome, tabela, unicidade e colunas com direcao (indexdef do pg_indexes)
    IDX_DEF = re.compile(r"^CREATE (UNIQUE )?INDEX (\S+) ON \S+ USING (\w+) \((.*)\)$")
    idx_atual: set[tuple[str, str, str, tuple[str, ...]]] = set()
    nao_interpretados: list[str] = []
    for linha in psql("SELECT tablename, indexname, indexdef FROM pg_indexes "
                      "WHERE schemaname='sales_intelligence' ORDER BY tablename, indexname"):
        if len(linha) < 3:
            nao_interpretados.append("|".join(linha))
            continue
        tabela, nome, ddl = linha[0], linha[1], linha[2]
        m = IDX_DEF.match(ddl)
        if m is None:
            nao_interpretados.append(f"{nome}: {ddl}")
            continue
        idx_atual.add((tabela, nome, "U" if m.group(1) else "N", norm_cols(m.group(4))))

    print()
    print(f"-- pg_indexes do alvo ({len(idx_atual)} indices lidos):")
    for e in sorted(idx_atual):
        print(f"   I  {descreve(e)}")
    print()

    chk("todos os indexdef do alvo foram interpretados", not nao_interpretados, str(nao_interpretados[:4]))
    chk("o alvo tem exatamente os 30 indices do contrato (12 PK + 15 CREATE INDEX + 3 UNIQUE)",
        len(idx_atual) == 30, f"total no alvo={len(idx_atual)}")
    faltando = sorted(idx_esperados - idx_atual)
    chk("todo indice esperado existe no alvo com o mesmo nome, tabela, unicidade e colunas",
        not faltando, "; ".join(descreve(e) for e in faltando[:6]))
    sobrando = sorted(idx_atual - idx_esperados)
    chk("nao ha indice no alvo fora do contrato (nem sobra de nome/coluna/unicidade)",
        not sobrando, "; ".join(descreve(e) for e in sobrando[:6]))

    faltam_contrato_alvo = []
    for item in contrato["indexes"]:
        m = re.match(r"(\w+)\((.+)\)$", item)
        if m is None:
            faltam_contrato_alvo.append(item)
            continue
        alvo_i = (m.group(1), norm_cols(m.group(2)))
        if not any(e[0] == alvo_i[0] and e[3] == alvo_i[1] for e in idx_atual):
            faltam_contrato_alvo.append(item)
    chk(f"os {len(contrato['indexes'])} itens de indice do contrato aparecem no pg_indexes do alvo",
        not faltam_contrato_alvo, str(faltam_contrato_alvo))

    # ---- constraints (PK/FK/UNIQUE) item a item
    pk_atual: dict[str, tuple[str, ...]] = {}
    for r in psql("SELECT tc.table_name, kcu.column_name, kcu.ordinal_position "
                  "FROM information_schema.table_constraints tc "
                  "JOIN information_schema.key_column_usage kcu "
                  "ON kcu.constraint_name=tc.constraint_name AND kcu.table_schema=tc.table_schema "
                  "WHERE tc.constraint_type='PRIMARY KEY' AND tc.table_schema='sales_intelligence' "
                  "ORDER BY 1,3"):
        pk_atual[r[0]] = pk_atual.get(r[0], ()) + (r[1],)

    uq_atual: dict[str, list[tuple[str, ...]]] = {}
    for r in psql("SELECT cl.relname, string_agg(a.attname, ',' ORDER BY k.ord) "
                  "FROM pg_constraint c "
                  "JOIN pg_class cl ON cl.oid=c.conrelid "
                  "JOIN pg_namespace n ON n.oid=cl.relnamespace "
                  "JOIN LATERAL unnest(c.conkey) WITH ORDINALITY k(attnum, ord) ON true "
                  "JOIN pg_attribute a ON a.attrelid=cl.oid AND a.attnum=k.attnum "
                  "WHERE c.contype='u' AND n.nspname='sales_intelligence' "
                  "GROUP BY cl.relname, c.oid ORDER BY 1"):
        uq_atual.setdefault(r[0], []).append(norm_cols(r[1]))

    fk_atual: dict[tuple[str, str], tuple[str, str]] = {}
    for r in psql("SELECT cl.relname, a.attname, cl2.relname, b.attname "
                  "FROM pg_constraint c "
                  "JOIN pg_class cl ON cl.oid=c.conrelid "
                  "JOIN pg_namespace n ON n.oid=cl.relnamespace "
                  "JOIN pg_class cl2 ON cl2.oid=c.confrelid "
                  "JOIN LATERAL unnest(c.conkey) WITH ORDINALITY k(attnum, ord) ON true "
                  "JOIN pg_attribute a ON a.attrelid=cl.oid AND a.attnum=k.attnum "
                  "JOIN LATERAL unnest(c.confkey) WITH ORDINALITY kf(attnum, ord2) ON kf.ord2=k.ord "
                  "JOIN pg_attribute b ON b.attrelid=cl2.oid AND b.attnum=kf.attnum "
                  "WHERE c.contype='f' AND n.nspname='sales_intelligence' ORDER BY 1,2"):
        fk_atual[(r[0], r[1])] = (r[2], r[3])

    print(f"-- constraints do alvo (PK: {len(pk_atual)}, FK: {len(fk_atual)}, "
          f"UNIQUE: {sum(len(v) for v in uq_atual.values())}):")
    for t in sorted(pk_atual):
        print(f"   PK {t}({', '.join(pk_atual[t])})")
    for t, c in sorted(fk_atual):
        print(f"   FK {t}.{c} -> {fk_atual[(t, c)][0]}.{fk_atual[(t, c)][1]}")
    for t in sorted(uq_atual):
        for u in uq_atual[t]:
            print(f"   UQ {t}({', '.join(u)})")
    print()

    pk_div = {t: (list(pk_atual.get(t, ())), list(pk_esperada[t])) for t in pk_esperada
              if pk_atual.get(t) != pk_esperada[t]}
    chk("toda tabela tem PRIMARY KEY de coluna unica `id` (item a item, tabela por tabela)",
        not pk_div and len(pk_atual) == len(pk_esperada), str(pk_div))

    uq_falta = [f"{t}({','.join(u)})" for t in uq_esperada for u in uq_esperada[t]
                if u not in uq_atual.get(t, [])]
    uq_sobra = [f"{t}({','.join(u)})" for t in uq_atual for u in uq_atual[t]
                if u not in uq_esperada.get(t, [])]
    chk("as UNIQUE do alvo sao exatamente as do contrato (item a item)",
        not uq_falta and not uq_sobra, f"faltam={uq_falta} sobram={uq_sobra}")

    chk("as FOREIGN KEY do alvo sao exatamente as do contrato (item a item)",
        fk_atual == fk_esperada,
        f"so no alvo={[(k, fk_atual[k]) for k in sorted(set(fk_atual) - set(fk_esperada))]} "
        f"so no contrato={[(k, fk_esperada[k]) for k in sorted(set(fk_esperada) - set(fk_atual))]}")

    com_fk_indevida = [l for l in contrato["logical_links_without_fk"]
                       if tuple(l.split(".")) in fk_atual]
    chk("os vinculos logicos declarados continuam SEM FK no alvo (contrato manda)",
        not com_fk_indevida, str(com_fk_indevida))

    apoio = {nome for _, nome, _, _ in idx_atual if nome.endswith(("_pkey", "_key"))}
    constraint_sem_apoio = [f"{t}_pkey" for t in pk_esperada if f"{t}_pkey" not in apoio]
    for t in uq_esperada:
        for u in uq_esperada[t]:
            if f"{t}_{u[0]}_key" not in apoio:
                constraint_sem_apoio.append(f"{t}_{u[0]}_key")
    chk("toda PK/UNIQUE do alvo tem o indice de apoio no pg_indexes (e por isso fecham os 30)",
        not constraint_sem_apoio, str(constraint_sem_apoio))

except RuntimeError as exc:
    chk("consultas de leitura no alvo executaram", False, str(exc))

print()
if falhas:
    print(f"RESULTADO: FALHOU ({len(falhas)} de {itens} itens)")
    for f in falhas:
        print(f"  - {f}")
    sys.exit(1)
print(f"RESULTADO: PASS ({itens} itens, 0 falhas)")
