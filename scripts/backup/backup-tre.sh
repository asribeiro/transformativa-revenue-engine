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
#   Quando o ambiente DECLARA Odoo (deploy/environments/<ambiente>.env, secao "Odoo do
#   ambiente"), o MESMO artefato recebe tambem o Odoo (card TRE-W2-E01-T01-F01):
#     odoo_dev.dump             dump -Fc do banco do Odoo (container proprio pg-odoo-dev)
#     odoo_dev.dump.sha256
#     odoo-contagens.txt        <tabela>|<linhas> do schema public do Odoo
#     odoo-filestore.tar.gz     volume do filestore (odoo-data-dev) empacotado inteiro
#     odoo-filestore.tar.gz.sha256
#     odoo-manifest.txt         metadados do Odoo (imagem, digest, tamanhos, arquivos)
#   Um Odoo por ambiente dentro do MESMO artefato de proposito: restaurar o dev no meio de
#   um incidente precisa dos DOIS lados; dois artefatos em timers diferentes produziriam
#   restauracao pela metade. `verificar-backup.sh` escolhe o dump do trio pelo `banco:` do
#   manifesto (senao `ls *.dump | head -1` pegaria o do Odoo) e `verificar-odoo.sh` faz o
#   restore do Odoo descartavel, subindo o Odoo contra o banco restaurado.
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

# Contagens por tabela do Odoo (schema public inteiro). Mesma tecnica e mesmo motivo do
# trio: o UNION ALL e montado DENTRO do SQL e `n_live_tup` nao e usado (depende de ANALYZE
# e mente). `quote_ident` protege nome de tabela com maiuscula/espaco (o Odoo tem tabelas
# como `ir_model_fields`, mas o quoting e barato e evita surpresa com modelo customizado).
sql_contagens_odoo() {
  local servico="$1" usuario="$2" banco="$3"
  local uniao
  uniao="$(docker exec "$servico" psql -U "$usuario" -d "$banco" -tAc \
    "SELECT string_agg('SELECT '''||table_name||''' AS tabela, count(*)::bigint AS linhas FROM public.'||quote_ident(table_name), ' UNION ALL ' ORDER BY table_name) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'")"
  uniao="$(printf '%s' "$uniao" | tr -d '\r' | sed '/^[[:space:]]*$/d')"
  [ -n "$uniao" ] || return 1
  docker exec "$servico" psql -U "$usuario" -d "$banco" -tAF'|' -c "$uniao ORDER BY 1"
}

# ---------------------------------------------------------------------------------
# Odoo do ambiente: banco PROPRIO (pg-odoo-dev/odoo_dev) + filestore (odoo-data-dev).
# Nada do Odoo entra no dump do trio e vice-versa: sao bancos, containers e volumes
# separados (docs/runbooks/odoo-dev.md §2).
#
# O filestore e empacotado pelo DOCKER (container efemero montando o volume), nao pelo
# caminho do host: /var/lib/docker so e legivel por quem tem o socket, e o unit roda
# como tre-deploy — ler `docker volume inspect` e copiar o caminho seria depender de um
# detalhe do daemon que nao e contrato.
#
# Segredos: o dump NAO leva senha nenhuma do cofre — `/etc/tre/odoo-dev/{pg.env,odoo.conf}`
# ficam de fora (o dump do banco traz o que o proprio Odoo guarda, nao a credencial de
# infraestrutura). Ver docs/operations/gestao-de-secrets.md.
# ---------------------------------------------------------------------------------
backup_odoo_ambiente() {
  local amb="$1" saida="$2"
  local servico="$TRE_ODOO_SERVICO" usuario="$TRE_ODOO_USUARIO" banco="$TRE_ODOO_BANCO"
  local volume="$TRE_ODOO_FILESTORE"
  local imagem_aux="${TRE_BACKUP_IMAGEM_AUX:-${TRE_BACKUP_IMAGEM:-postgres:16}}"
  local imagem="${TRE_ODOO_IMAGEM:-odoo:19.0}"
  local digest="${TRE_ODOO_IMAGEM_DIGEST:-n/d}"
  local man="$saida/odoo-manifest.txt"
  local arq_fs="$saida/odoo-filestore.tar.gz"

  echo "------------------------------------------------------------------"
  echo "-- odoo do ambiente: $amb   servico: $servico   banco: $banco   filestore: $volume"

  # 1. o servico responde?
  if docker exec "$servico" pg_isready -U "$usuario" >/dev/null 2>&1; then
    ok "odoo: postgres responde em '$servico'"
  else
    ko "odoo: postgres nao responde em '$servico' (backup do Odoo abortado para nao gerar artefato vazio)"
    return 1
  fi

  # 2. dump do banco do Odoo (formato custom, igual ao do trio: restauravel e verificavel)
  local versao_pg
  versao_pg="$(docker exec "$servico" psql -U "$usuario" -d "$banco" -tAc 'SHOW server_version' 2>/dev/null | tr -d '[:space:]')"
  if docker exec "$servico" pg_dump -U "$usuario" -d "$banco" -Fc \
       >"$saida/$banco.dump" 2>"$saida/odoo-pg_dump.err"; then
    ok "odoo: dump de $banco: $(du -h "$saida/$banco.dump" | cut -f1) (postgres $versao_pg)"
  else
    ko "odoo: pg_dump de $banco falhou: $(head -c 300 "$saida/odoo-pg_dump.err")"
    return 1
  fi
  (cd "$saida" && sha256sum "$banco.dump" >"$banco.dump.sha256") \
    && ok "odoo: sha256 do dump gravado" || ko "odoo: sha256 do dump falhou"

  # 3. contagens por tabela (o que o restore vai comparar linha a linha)
  local tabelas_odoo
  tabelas_odoo="$(docker exec "$servico" psql -U "$usuario" -d "$banco" -tAc \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'" 2>/dev/null | tr -d '[:space:]')"
  if [ "${tabelas_odoo:-0}" = "0" ]; then
    ko "odoo: banco '$banco' sem tabela nenhuma em public (base nao inicializada?) — este backup nao tem o que restaurar"
  elif sql_contagens_odoo "$servico" "$usuario" "$banco" >"$saida/odoo-contagens.txt" \
       && [ -s "$saida/odoo-contagens.txt" ]; then
    ok "odoo: contagens: $(wc -l <"$saida/odoo-contagens.txt") tabelas, $(awk -F'|' '{s+=$2} END {print s+0}' "$saida/odoo-contagens.txt") linhas"
  else
    ko "odoo: nao consegui extrair as contagens por tabela de '$banco' (base tem $tabelas_odoo tabelas)"
  fi

  # 4. filestore: o volume inteiro (filestore do banco + sessoes + addons do data_dir)
  if docker run --rm --entrypoint tar \
       -v "$volume:/origem:ro" -v "$saida:/destino" \
       "$imagem_aux" -czf "/destino/$(basename "$arq_fs")" -C /origem . \
       >"$saida/odoo-filestore.err" 2>&1; then
    ok "odoo: volume '$volume' empacotado ($(du -h "$arq_fs" | cut -f1))"
  else
    ko "odoo: falha ao empacotar o volume '$volume': $(head -c 300 "$saida/odoo-filestore.err" | tr '\n' ' ')"
    return 1
  fi
  local bytes_fs arquivos_fs
  bytes_fs="$(stat -c%s "$arq_fs" 2>/dev/null || echo 0)"
  arquivos_fs="$(tar -tzf "$arq_fs" 2>/dev/null | grep -vc '/$' || true)"
  (cd "$saida" && sha256sum "$(basename "$arq_fs")" >"$(basename "$arq_fs").sha256") \
    && ok "odoo: sha256 do filestore gravado" || ko "odoo: sha256 do filestore falhou"

  # 5. metadados do Odoo (em arquivo proprio; o manifesto principal o incorpora)
  {
    echo "odoo_servico: $servico"
    echo "odoo_usuario: $usuario"
    echo "odoo_banco: $banco"
    echo "odoo_filestore_volume: $volume"
    echo "odoo_imagem_restore: $imagem"
    echo "odoo_imagem_digest: $digest"
    echo "odoo_postgres: ${versao_pg:-n/d}"
    echo "odoo_tabelas: $(wc -l <"$saida/odoo-contagens.txt" 2>/dev/null || echo 0)"
    echo "odoo_linhas: $(awk -F'|' '{s+=$2} END {print s+0}' "$saida/odoo-contagens.txt" 2>/dev/null)"
    echo "odoo_bytes_dump: $(stat -c%s "$saida/$banco.dump" 2>/dev/null || echo 0)"
    echo "odoo_sha256_dump: $(cut -d' ' -f1 "$saida/$banco.dump.sha256" 2>/dev/null)"
    echo "odoo_filestore_bytes: $bytes_fs"
    echo "odoo_filestore_arquivos: $arquivos_fs"
    echo "odoo_filestore_sha256: $(cut -d' ' -f1 "$arq_fs.sha256" 2>/dev/null)"
    echo "odoo_segredos: fora do artefato (o dump nao leva /etc/tre/odoo-dev/*)"
  } >"$man"
  ok "odoo: manifesto gravado ($(basename "$man"))"
  return 0
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

  # 5b. Odoo do ambiente (banco proprio + filestore), quando o ambiente declara um.
  # Ambiente que nao declara Odoo e PULADO; declarado sem container e FALHA (mesma regra
  # do trio) — nunca "pulado" em silencio.
  tre_resolver_odoo "$amb" || true
  tre_estado_odoo
  case "$TRE_ODOO_ESTADO" in
    COBRIR)
      echo "OK    odoo: $TRE_ODOO_MOTIVO"
      backup_odoo_ambiente "$amb" "$saida" || FALHAS=$((FALHAS + 1))
      ;;
    PULAR)
      echo "PULADO odoo: $TRE_ODOO_MOTIVO"
      ;;
    *)
      ko "odoo: $TRE_ODOO_MOTIVO"
      ;;
  esac

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
    # bloco do Odoo, quando o ambiente tem um (card TRE-W2-E01-T01-F01)
    if [ -s "$saida/odoo-manifest.txt" ]; then
      grep -v '^$' "$saida/odoo-manifest.txt"
    else
      echo "odoo: ausente neste ambiente ($TRE_ODOO_MOTIVO)"
    fi
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
