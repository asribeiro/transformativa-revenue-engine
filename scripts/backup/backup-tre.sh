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
# Variaveis: TRE_PG_SERVICO, TRE_PG_USER, TRE_PG_DB (globais — valem em chamada de UM
#            ambiente), TRE_ENV_DIR, TRE_BACKUP_DIR, TRE_BACKUP_RETENCAO_DIAS,
#            TRE_BACKUP_EXTERNO (destino remoto via rclone, ex.: s3:tre-backup), TRE_RAIZ.
#            O trio de CADA ambiente vem de deploy/environments/<ambiente>.env ou de
#            TRE_PG_SERVICO_<AMBIENTE> — regra completa em scripts/backup/lib-ambiente.sh.
#
# SEGREDOS: o backup NAO copia `.env` de proposito. Segredo se recupera do cofre
# (docs/operations/gestao-de-secrets.md), nao de arquivo de backup.
#
# AMBIENTE NAO PROVISIONADO e PULADO; ambiente DECLARADO cujo container nao existe e
# FALHA (exit != 0). Essa distincao e o conserto do defeito t_1b2ab418: a rotina imprimia
# `BACKUP_OK` com exit 0 cobrindo ZERO ambientes (procurava `pg-dev`; o dev real e
# `pg-sales-dev`, e nenhum timer lia deploy/environments/dev.env).
#
# Resultados possiveis:
#   BACKUP_OK            — todos os ambientes provisionados foram copiados, nenhuma falha
#   BACKUP_SEM_AMBIENTE  — nenhum ambiente provisionado: nada foi copiado (nunca "OK")
#   BACKUP_FALHOU        — pelo menos uma falha (ambiente declarado sem container, dump
#                          que falhou, destino externo que falhou...): exit 1
# =====================================================================================
set -uo pipefail

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib-ambiente.sh
. "$AQUI/lib-ambiente.sh"

AMBIENTE="${1:-todos}"
RAIZ="${TRE_RAIZ:-/opt/tre}"
DEST="${TRE_BACKUP_DIR:-$RAIZ/backup}"
RETENCAO="${TRE_BACKUP_RETENCAO_DIAS:-14}"
EXTERNO="${TRE_BACKUP_EXTERNO:-}"
FALHAS=0
COBERTOS=0
PULADOS=0

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
  local amb="$1" modo="${2:-um}"
  local selo saida

  # Resolucao do trio POR AMBIENTE (lib-ambiente.sh). O container NAO vem de uma variavel
  # global quando a chamada e `todos`: uma variavel unica atravessando os tres ambientes
  # copiaria o banco do dev tres vezes, rotulado como dev/homolog/prod.
  if ! tre_resolver_ambiente "$amb" "$modo"; then
    echo "=================================================================="
    echo "-- ambiente: $amb   (resolucao de configuracao FALHOU)"
    ko "$TRE_AMB_ERRO"
    return 1
  fi
  local servico="$TRE_AMB_SERVICO" usuario="$TRE_AMB_USUARIO" banco="$TRE_AMB_BANCO"
  tre_estado_ambiente

  selo="$(date -u +%Y%m%dT%H%M%SZ)"
  saida="$DEST/tre_${amb}_${selo}"

  echo "=================================================================="
  echo "-- ambiente: $amb   servico: $servico   usuario: $usuario   banco: $banco"
  echo "-- config:   $TRE_AMB_FONTE"
  echo "-- data:     $selo"
  echo "=================================================================="
  [ -n "${TRE_AMB_AVISO:-}" ] && echo "NOTA  $TRE_AMB_AVISO"

  case "$TRE_AMB_ESTADO" in
    FALHAR)
      # Era aqui que a rotina mentia: container ausente virava "PULADO" e a execucao
      # terminava em BACKUP_OK com zero artefato.
      ko "$TRE_AMB_MOTIVO"
      return 1
      ;;
    PULAR)
      echo "PULADO $TRE_AMB_MOTIVO"
      PULADOS=$((PULADOS + 1))
      return 0
      ;;
  esac

  # Dois ambientes apontando para o MESMO container na mesma execucao produziriam um
  # artefato de 'homolog' com o banco do dev. Recusa o segundo; nunca copia por cima.
  if ! tre_registrar_origem "$servico"; then
    ko "ambiente '$amb' aponta para o container '$servico', ja usado por outro ambiente desta execucao — artefato de '$amb' com o banco de outro ambiente e pior que nenhum artefato"
    return 1
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
    echo "usuario: $usuario"
    echo "config: $TRE_AMB_FONTE"
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
      # o manifesto sobe ANTES de saber o resultado do envio; reenvia para que a copia
      # externa nao fique dizendo "pendente" quando o envio deu certo
      rclone copyto "$saida/manifest.txt" "$EXTERNO/$(basename "$saida")/manifest.txt" >/dev/null 2>&1 \
        && echo "  (manifesto externo atualizado com o resultado do envio)" \
        || echo "  (aviso: nao consegui atualizar o manifesto externo)"
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
  COBERTOS=$((COBERTOS + 1))
  return 0
}

if [ "$AMBIENTE" = "todos" ]; then
  for amb in dev homolog prod; do backup_ambiente "$amb" todos; done
else
  backup_ambiente "$AMBIENTE" um
fi

echo
if [ "$FALHAS" -gt 0 ]; then
  echo "RESULTADO: BACKUP_FALHOU ($AMBIENTE; $FALHAS falha(s), $COBERTOS ambiente(s) coberto(s), $PULADOS pulado(s))"
  exit 1
elif [ "$COBERTOS" -eq 0 ]; then
  # nunca chamar isso de BACKUP_OK: nenhum ambiente foi coberto e nenhum artefato existe
  echo "RESULTADO: BACKUP_SEM_AMBIENTE ($AMBIENTE; 0 ambiente coberto, $PULADOS pulado(s)) — nenhum ambiente provisionado, nenhum artefato produzido"
  exit 0
else
  echo "RESULTADO: BACKUP_OK ($AMBIENTE; $COBERTOS ambiente(s) coberto(s), $PULADOS pulado(s))"
  exit 0
fi
