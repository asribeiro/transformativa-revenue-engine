#!/usr/bin/env bash
# =====================================================================================
# verificar-backup.sh <diretorio_de_backup|arquivo.dump>
#
# TESTE DE RESTORE REAL. Nao confere so se o arquivo existe: sobe um PostgreSQL
# DESCARTAVEL, restaura o dump nele e compara com o que foi copiado:
#   - o dump e um arquivo de backup legivel (pg_restore --list)
#   - o schema sales_intelligence voltou, com o mesmo numero de tabelas e de indices
#   - TODAS as contagens por tabela batem com contagens.txt do backup
#   - nenhuma linha orfa nas relacoes entre tabelas
# O container e removido sempre (trap), inclusive em falha.
#
# Variaveis: TRE_BACKUP_IMAGEM (padrao postgres:16), TRE_RESTORE_PORTA (nao publica porta)
# =====================================================================================
set -uo pipefail

ALVO="${1:-}"
if [ -z "$ALVO" ]; then
  echo "uso: $0 <diretorio_de_backup|arquivo.dump>"
  exit 2
fi
if [ -d "$ALVO" ]; then
  DIR="$ALVO"
  # Com o Odoo no MESMO artefato existem DOIS *.dump (odoo_dev.dump e o do trio): `ls |
  # head -1` pegaria o do Odoo (ordem alfabetica) e o comparativo seria do banco errado.
  # O dump do trio e o que o manifesto declara em `banco:` (scripts/backup/backup-tre.sh).
  BANCO_ARTEFATO="$(awk -F': ' '/^banco:/{print $2; exit}' "$DIR/manifest.txt" 2>/dev/null | tr -d '[:space:]')"
  if [ -n "$BANCO_ARTEFATO" ] && [ -s "$DIR/$BANCO_ARTEFATO.dump" ]; then
    DUMP="$DIR/$BANCO_ARTEFATO.dump"
  else
    DUMP="$(ls "$DIR"/*.dump 2>/dev/null | grep -v '/odoo_' | head -1)"
  fi
  # dumps do Odoo ficam para o verificador proprio (scripts/backup/verificar-odoo.sh)
  ODOO_DUMP="$(ls "$DIR"/odoo*.dump 2>/dev/null | head -1)"
else
  DIR="$(cd "$(dirname "$ALVO")" && pwd)"
  DUMP="$ALVO"
  ODOO_DUMP=""
fi

IMAGEM="${TRE_BACKUP_IMAGEM:-postgres:16}"
NOME="tre-restore-$$-$RANDOM"
SENHA="$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)"
# O stderr do pg_restore NAO pode ser gravado DENTRO do artefato verificado: isso fazia a copia
# local divergir da externa (o `pg_restore.err` aparecia no artefato depois do envio ao bucket)
# e ainda exigia escrita no artefato (defeito registrado na revisao independente do card
# t_a5afde31). Arquivo temporario, dono de quem verifica.
ERRO_RESTORE="$(mktemp "${TMPDIR:-/tmp}/tre-restore-erro.XXXXXX" 2>/dev/null || printf '%s' "${TMPDIR:-/tmp}/tre-restore-erro.$$")"
trap 'docker rm -f "$NOME" >/dev/null 2>&1; rm -f "$ERRO_RESTORE"' EXIT INT TERM HUP
ITENS=0
FALHAS=0

ok()  { ITENS=$((ITENS + 1)); echo "OK    $*"; }
ko()  { ITENS=$((ITENS + 1)); FALHAS=$((FALHAS + 1)); echo "FALHOU $*"; }

echo "=================================================================="
echo "-- teste de restore: ${DUMP:-<nenhum dump encontrado>}"
echo "-- backup:           $DIR"
echo "-- container:        $NOME (imagem $IMAGEM)"
echo "=================================================================="

if [ -z "${DUMP:-}" ] || [ ! -s "$DUMP" ]; then
  ko "nenhum arquivo .dump legivel em $DIR"
  echo; echo "RESULTADO: RESTORE_FALHOU (0/1)"
  exit 1
fi
ok "dump encontrado ($(du -h "$DUMP" | cut -f1))"

# O artefato e legivel por quem verifica? Ilegivel por PERMISSAO nao e "backup vazio/incompleto"
# (rodada 2 da revisao do card t_a5afde31: artefato root:root 700 verificado como tre-deploy
# saia como "nenhum arquivo .dump legivel" — diagnostico de conteudo para problema de dono).
if [ -d "${DIR:-}" ] && { [ ! -r "$DIR" ] || [ ! -x "$DIR" ]; }; then
  ko "artefato '$DIR' existe mas NAO e legivel por '$(id -un)': dono $(stat -c '%U:%G' "$DIR" 2>/dev/null || echo n/d), modo $(stat -c '%a' "$DIR" 2>/dev/null || echo n/d) — e PERMISSAO, nao artefato vazio/incompleto"
  echo; echo "RESULTADO: RESTORE_FALHOU ($ITENS itens, $FALHAS falha(s))"
  exit 1
fi

# 1. sobe o destino descartavel
if ! docker run -d --name "$NOME" \
      -e POSTGRES_PASSWORD="$SENHA" -e POSTGRES_USER=tre -e POSTGRES_DB=verificacao \
      "$IMAGEM" >/dev/null 2>&1; then
  ko "nao consegui subir o container de verificacao ($IMAGEM)"
  echo; echo "RESULTADO: RESTORE_FALHOU ($ITENS itens, $FALHAS falha(s))"
  exit 1
fi
ok "container de verificacao no ar"

# 2. espera o servidor DEFINITIVO (a imagem oficial derruba um servidor temporario no init —
#    `pg_isready` responde OK nele e quem confia nisso restaura no servidor que esta caindo)
pronto=0
for _ in $(seq 1 90); do
  if docker exec "$NOME" psql -U tre -d verificacao -tAc 'SELECT 1' >/dev/null 2>&1; then
    sleep 3
    if docker exec "$NOME" psql -U tre -d verificacao -tAc 'SELECT 1' >/dev/null 2>&1; then pronto=1; break; fi
  fi
  sleep 1
done
if [ "$pronto" = "1" ]; then
  ok "postgres de destino pronto ($(docker exec "$NOME" psql -U tre -d verificacao -tAc 'SHOW server_version' | tr -d '[:space:]'))"
else
  ko "postgres de destino nao ficou pronto em 60s"
  docker logs --tail 15 "$NOME" 2>&1 | sed 's/^/      /'
  echo; echo "RESULTADO: RESTORE_FALHOU ($ITENS itens, $FALHAS falha(s))"
  exit 1
fi

# 3. o dump e um arquivo valido?
objetos="$(docker exec -i "$NOME" pg_restore --list <"$DUMP" 2>/dev/null | grep -cE '^[0-9]+;')"
if [ "${objetos:-0}" -gt 0 ]; then
  ok "dump legivel pelo pg_restore ($objetos objetos no indice)"
else
  ko "o dump nao pode ser lido pelo pg_restore (arquivo corrompido ou formato errado)"
fi

# 4. restaura
if docker exec -i "$NOME" pg_restore -U tre -d verificacao --no-owner --no-privileges \
     <"$DUMP" 2>"$ERRO_RESTORE"; then
  ok "pg_restore concluido sem erro"
else
  ko "pg_restore retornou erro: $(head -c 300 "$ERRO_RESTORE" | tr '\n' ' ')"
fi

# 5. schema, tabelas e indices
tabelas_restauradas="$(docker exec "$NOME" psql -U tre -d verificacao -tAc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='sales_intelligence' AND table_type='BASE TABLE'" 2>/dev/null | tr -d '[:space:]')"
tabelas_origem="$(wc -l <"$DIR/contagens.txt" 2>/dev/null | tr -d '[:space:]')"
if [ -n "$tabelas_origem" ] && [ "$tabelas_restauradas" = "$tabelas_origem" ]; then
  ok "tabelas: $tabelas_restauradas (igual ao backup)"
else
  ko "tabelas: restaurado=$tabelas_restauradas backup=$tabelas_origem"
fi

# backup sem tabela nenhuma nao e backup: se o dump nao tem schema, nada aqui prova nada
if [ "${tabelas_origem:-0}" -ge 1 ] 2>/dev/null; then
  ok "o backup tem conteudo ($tabelas_origem tabelas registradas)"
else
  ko "backup VAZIO (0 tabelas registradas) — nao ha o que restaurar; refaca o backup da origem"
fi

indices_restaurados="$(docker exec "$NOME" psql -U tre -d verificacao -tAc \
  "SELECT count(*) FROM pg_indexes WHERE schemaname='sales_intelligence'" 2>/dev/null | tr -d '[:space:]')"
indices_origem="$(awk -F': ' '/^indices:/{print $2}' "$DIR/manifest.txt" 2>/dev/null | tr -d '[:space:]')"
if [ -n "$indices_origem" ] && [ "$indices_restaurados" = "$indices_origem" ]; then
  ok "indices: $indices_restaurados (igual ao backup)"
else
  ko "indices: restaurado=$indices_restaurados backup=${indices_origem:-n/d}"
fi

# 6. contagens por tabela — o comparativo que realmente prova a restauracao
# (UNION ALL montado dentro do SQL: string_agg. Juntar linhas fora da consulta nao funciona.)
uniao="$(docker exec "$NOME" psql -U tre -d verificacao -tAc \
  "SELECT string_agg('SELECT '''||table_name||''' AS tabela, count(*)::bigint AS linhas FROM sales_intelligence.'||table_name, ' UNION ALL ' ORDER BY table_name) FROM information_schema.tables WHERE table_schema='sales_intelligence' AND table_type='BASE TABLE'" 2>/dev/null)"
restauradas="$(docker exec "$NOME" psql -U tre -d verificacao -tAF'|' -c "$uniao ORDER BY 1" 2>/dev/null | tr -d '\r')"
origem="$(tr -d '\r' <"$DIR/contagens.txt" 2>/dev/null)"

if [ -n "$restauradas" ] && [ "$restauradas" = "$origem" ]; then
  ok "contagens por tabela: todas as $(wc -l <<<"$restauradas") batem (linha a linha)"
else
  ko "contagens divergem — diferencas:"
  diff <(echo "$origem") <(echo "$restauradas") | sed 's/^/      /'
fi

# 7. integridade referencial (a restauracao resolveu os vinculos?)
orfas="$(docker exec "$NOME" psql -U tre -d verificacao -tAc \
  "SELECT count(*) FROM sales_intelligence.contacts c LEFT JOIN sales_intelligence.organizations o ON o.id=c.organization_id WHERE o.id IS NULL" 2>/dev/null | tr -d '[:space:]')"
if [ "${orfas:-1}" = "0" ]; then
  ok "sem linhas orfas em contacts -> organizations"
else
  ko "linhas orfas apos restore: ${orfas:-n/d}"
fi

# 8. dados de verdade? (uma tabela com linhas copiadas precisa ter linhas restauradas)
total="$(awk -F'|' '{s+=$2} END {print s+0}' "$DIR/contagens.txt" 2>/dev/null)"
total_restaurado="$(docker exec "$NOME" psql -U tre -d verificacao -tAc \
  "SELECT COALESCE(SUM(c),0) FROM (SELECT count(*) AS c FROM sales_intelligence.organizations UNION ALL SELECT count(*) FROM sales_intelligence.signals UNION ALL SELECT count(*) FROM sales_intelligence.outbox_events) x" 2>/dev/null | tr -d '[:space:]')"
if [ "${total:-0}" -gt 0 ] && [ "${total_restaurado:-0}" -gt 0 ]; then
  ok "conteudo restaurado de fato ($total_restaurado linhas em 3 tabelas amostradas; backup tinha $total no total)"
elif [ "${total:-0}" -eq 0 ]; then
  echo "NOTA  backup sem linhas (base vazia) — comparacao de contagens e trivial"
  ok "base vazia declarada (comparacao de contagens continua valida)"
else
  ko "restaurei zero linhas em tabelas que tinham dados no backup"
fi

echo
if [ "$FALHAS" -eq 0 ]; then
  echo "RESULTADO: RESTORE_OK ($ITENS itens, 0 falhas)"
  exit 0
else
  echo "RESULTADO: RESTORE_FALHOU ($ITENS itens, $FALHAS falha(s))"
  exit 1
fi
