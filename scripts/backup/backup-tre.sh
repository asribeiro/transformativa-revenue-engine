#!/usr/bin/env bash
# =====================================================================================
# backup-tre.sh <ambiente|todos> — backup do PostgreSQL de um ambiente do TRE.
#
# Artefato por execucao (diretorio proprio, saida autoexplicativa):
#   <DEST>/tre_<ambiente>_<YYYYmmddTHHMMSSZ>/
#     sales_intelligence.dump   dump em formato custom (restauravel, seletivo, verificavel)
#     sales_intelligence.dump.sha256
#     globals.sql               papeis/globais (pg_dumpall --globals-only)
#     contagens.txt             <tabela>|<linhas> — usado pelo teste de restore para comparar
#     manifest.txt              metadados da execucao (origem, versao, tamanho, indices, externo)
#     pg_dump.err               stderr do pg_dump (vazio em caso de sucesso)
#
# Variaveis: TRE_PG_SERVICO, TRE_PG_USER, TRE_PG_DB, TRE_BACKUP_DIR, TRE_BACKUP_RETENCAO_DIAS,
#            TRE_BACKUP_EXTERNO (destino remoto via rclone, ex.: s3:tre-backup), TRE_RAIZ
#
# SEGREDOS: o backup NAO copia `.env` de proposito. Segredo se recupera do cofre
# (docs/operations/gestao-de-secrets.md), nao de arquivo de backup.
#
# Ambiente inexistente e PULADO (nao falha): assim o mesmo timer cobre dev/homolog/prod
# desde o primeiro dia, antes de os tres existirem.
# =====================================================================================
set -uo pipefail

AMBIENTE="${1:-todos}"
RAIZ="${TRE_RAIZ:-/opt/tre}"
DEST="${TRE_BACKUP_DIR:-$RAIZ/backup}"
RETENCAO="${TRE_BACKUP_RETENCAO_DIAS:-14}"
EXTERNO="${TRE_BACKUP_EXTERNO:-}"
FALHAS=0

ok() { echo "OK    $*"; }
ko() { echo "FALHOU $*"; FALHAS=$((FALHAS + 1)); }

# Contagens exatas por tabela (nao usa n_live_tup: depende de ANALYZE e mente).
# O UNION ALL e montado DENTRO do SQL (string_agg): juntar as linhas fora da consulta nao
# funciona — `paste -d` roda uma lista de caracteres e `psql -c` com varios comandos sem `;`
# e erro de sintaxe.
sql_contagens() {
  local servico="$1" usuario="$2" banco="$3"
  local uniao
  uniao="$(docker exec "$servico" psql -U "$usuario" -d "$banco" -tAc \
    "SELECT string_agg('SELECT '''||table_name||''' AS tabela, count(*)::bigint AS linhas FROM sales_intelligence.'||table_name, ' UNION ALL ' ORDER BY table_name) FROM information_schema.tables WHERE table_schema='sales_intelligence' AND table_type='BASE TABLE'")"
  uniao="$(printf '%s' "$uniao" | tr -d '\r' | sed '/^[[:space:]]*$/d')"
  [ -n "$uniao" ] || return 1
  docker exec "$servico" psql -U "$usuario" -d "$banco" -tAF'|' -c "$uniao ORDER BY 1"
}

backup_ambiente() {
  local amb="$1"
  local servico="${TRE_PG_SERVICO:-pg-$amb}"
  local usuario="${TRE_PG_USER:-tre}"
  local banco="${TRE_PG_DB:-sales_intelligence}"
  local selo saida
  selo="$(date -u +%Y%m%dT%H%M%SZ)"
  saida="$DEST/tre_${amb}_${selo}"

  echo "=================================================================="
  echo "-- ambiente: $amb   servico: $servico   data: $selo"
  echo "=================================================================="

  if ! docker inspect "$servico" >/dev/null 2>&1; then
    echo "PULADO ambiente $amb: container '$servico' nao existe (ambiente ainda nao provisionado)"
    return 0
  fi
  mkdir -p "$saida" && chmod 700 "$saida"

  # 1. o servico responde?
  if docker exec "$servico" pg_isready -U "$usuario" >/dev/null 2>&1; then
    ok "postgres responde"
  else
    ko "postgres nao responde em '$servico' (backup abortado para nao gerar artefato vazio)"
    rm -rf "$saida"
    return 1
  fi

  # 2. dump da base
  local versao_pg
  versao_pg="$(docker exec "$servico" psql -U "$usuario" -d "$banco" -tAc 'SHOW server_version' 2>/dev/null | tr -d '[:space:]')"
  if docker exec "$servico" pg_dump -U "$usuario" -d "$banco" -Fc \
       >"$saida/$banco.dump" 2>"$saida/pg_dump.err"; then
    ok "dump: $(du -h "$saida/$banco.dump" | cut -f1) (postgres $versao_pg)"
  else
    ko "pg_dump falhou: $(head -c 300 "$saida/pg_dump.err")"
    return 1
  fi

  # 3. globais (papeis) — sem senhas, so definicoes
  if docker exec "$servico" pg_dumpall -U "$usuario" --globals-only >"$saida/globals.sql" 2>/dev/null; then
    ok "globais exportadas"
  else
    ko "pg_dumpall --globals-only falhou"
  fi

  # 4. contagens por tabela (base da comparacao no restore)
  tabelas_existentes="$(docker exec "$servico" psql -U "$usuario" -d "$banco" -tAc \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema='sales_intelligence' AND table_type='BASE TABLE'" 2>/dev/null | tr -d '[:space:]')"
  if [ "${tabelas_existentes:-0}" = "0" ]; then
    ko "schema sales_intelligence ausente em '$banco' (migration nao aplicada?) — este backup nao tem o que restaurar"
  elif sql_contagens "$servico" "$usuario" "$banco" >"$saida/contagens.txt" && [ -s "$saida/contagens.txt" ]; then
    ok "contagens: $(wc -l <"$saida/contagens.txt") tabelas, $(awk -F'|' '{s+=$2} END {print s+0}' "$saida/contagens.txt") linhas"
  else
    ko "nao consegui extrair as contagens por tabela (schema existe, com $tabelas_existentes tabelas)"
  fi

  # 5. indices e integridade do arquivo
  local indices
  indices="$(docker exec "$servico" psql -U "$usuario" -d "$banco" -tAc \
    "SELECT count(*) FROM pg_indexes WHERE schemaname='sales_intelligence'" 2>/dev/null | tr -d '[:space:]')"
  (cd "$saida" && sha256sum "$banco.dump" >"$banco.dump.sha256") && ok "sha256 gravado" || ko "sha256 falhou"

  # 6. metadados
  {
    echo "ambiente: $amb"
    echo "servico: $servico"
    echo "banco: $banco"
    echo "selo_utc: $selo"
    echo "host_origem: $(hostname)"
    echo "postgres: ${versao_pg:-n/d}"
    echo "tabelas: $(wc -l <"$saida/contagens.txt" 2>/dev/null || echo 0)"
    echo "indices: ${indices:-n/d}"
    echo "bytes_dump: $(stat -c%s "$saida/$banco.dump" 2>/dev/null || echo 0)"
    echo "sha256: $(cut -d' ' -f1 "$saida/$banco.dump.sha256" 2>/dev/null)"
    echo "retencao_dias: $RETENCAO"
    echo "segredos: fora do artefato (ver docs/operations/gestao-de-secrets.md)"
  } >"$saida/manifest.txt"
  ok "manifesto gravado"

  # 7. destino externo
  if [ -n "$EXTERNO" ] && command -v rclone >/dev/null 2>&1; then
    if rclone copy "$saida" "$EXTERNO/$(basename "$saida")" >/dev/null 2>&1; then
      ok "copiado para o destino externo ($EXTERNO)"
      echo "externo: enviado ($EXTERNO)" >>"$saida/manifest.txt"
    else
      ko "copia para o destino externo falhou ($EXTERNO)"
      echo "externo: falhou ($EXTERNO)" >>"$saida/manifest.txt"
    fi
  else
    echo "PENDENTE destino externo nao configurado (TRE_BACKUP_EXTERNO vazio) — backup apenas local"
    echo "externo: pendente (sem destino configurado)" >>"$saida/manifest.txt"
  fi

  # 8. retencao (so o prefixo deste ambiente; nunca toca em outro diretorio)
  if [ "${RETENCAO:-0}" -gt 0 ] 2>/dev/null; then
    local removidos=0
    while IFS= read -r antigo; do
      [ -n "$antigo" ] || continue
      echo "  retencao: removendo $(basename "$antigo")"
      rm -rf "$antigo"
      removidos=$((removidos + 1))
    done < <(find "$DEST" -maxdepth 1 -type d -name "tre_${amb}_*" -mtime +"$RETENCAO" 2>/dev/null)
    ok "retencao aplicada ($RETENCAO dias; $removidos artefato(s) antigo(s) removido(s))"
  else
    echo "PULADO retencao desativada (TRE_BACKUP_RETENCAO_DIAS=$RETENCAO)"
  fi

  echo "artefato: $saida"
  return 0
}

if [ "$AMBIENTE" = "todos" ]; then
  for amb in dev homolog prod; do backup_ambiente "$amb"; done
else
  backup_ambiente "$AMBIENTE"
fi

echo
if [ "$FALHAS" -eq 0 ]; then
  echo "RESULTADO: BACKUP_OK ($AMBIENTE)"
  exit 0
else
  echo "RESULTADO: BACKUP_FALHOU ($FALHAS falha(s))"
  exit 1
fi
