#!/usr/bin/env bash
# Watchdog da publicacao versionada da copia operacional do TRE (roda NA VPS).
#
# Card: t_daca4bda — RECORRENCIA do defeito do t_091cfea9 (F3 do TRE-W1-E06-T01):
# a copia operacional /opt/tre/repo foi reescrita POR FORA do caminho unico
# (`tar -xz` ad-hoc de outro card, com mtime preservado) e voltou para uma arvore
# PRE-correcao, enquanto `.publicado` continuava apontando para o commit consertado.
# O `deploy/publicar.sh --conferir` DETECTAVA depois (exit 5), mas ninguem rodava:
# o sinal falso (`RESULTADO: BACKUP_OK` cobrindo zero ambientes) ficava no ar.
# Caminho unico sem enforcement nao e caminho unico.
#
# Este script fecha o ciclo e roda no HOST da copia, sem git e sem o repositorio:
#   --conferir (padrao)      compara a copia com o manifesto do commit REGISTRADO,
#                            atribui a divergencia (arquivo alterado/plantado/removido,
#                            mtime x hora da publicacao) e ALERTA;
#   --reparar                faz isso e, se divergente, RESTAURA a copia a partir do
#                            artefato do proprio commit registrado (deixado pela
#                            publicacao): o efeito do defeito dura um ciclo de timer;
#   --travar / --destravar   arma/desarma a trava de imutabilidade (chattr +i) que faz
#                            a escrita ad-hoc FALHAR (EPERM) em vez de sobrescrever;
#   --estado                 imprime o estado em uma linha (para cron/inspecao).
#
# Referencia independente: o manifesto do commit registrado fica FORA da copia, em
# $ARTEFATO (root:root, 700) — quem escreve por fora teria de acertar dois lugares
# para nao ser visto. Sem artefato, a referencia cai para o `.publicado.manifest` da
# copia e o alerta diz isso (referencia enfraquecida).
#
# Saida final (uma linha, para automatizar):
#   PUBLICACAO_OK commit=<sha> digest=<sha256> arquivos=<n>
#   PUBLICACAO_EM_ANDAMENTO            => uma publicacao esta com o lock (nao interfere)
#   PUBLICACAO_SEM_REGISTRO ...        => nao ha .publicado/.publicado.manifest (exit 5)
#   PUBLICACAO_DIVERGENTE ...          => copia != commit registrado (exit 5)
#   PUBLICACAO_REPARO_OK ...           => estava divergente e foi restaurada
#   PUBLICACAO_REPARO_FALHOU ...       => divergente e nao deu para restaurar (exit 6)
#   PUBLICACAO_TRAVADA / PUBLICACAO_DESTRAVADA
set -uo pipefail

DESTINO="${TRE_WATCHDOG_DESTINO:-/opt/tre/repo}"
ARTEFATO="${TRE_WATCHDOG_ARTEFATO:-/opt/tre/.publicacao-artefato}"
LOG_DIV="${TRE_WATCHDOG_LOG:-/opt/tre/.publicacao-divergencias.log}"
ARQ_ALERTA="${TRE_WATCHDOG_ALERTA:-/opt/tre/.publicacao-ALERTA}"
LOCK="${TRE_WATCHDOG_LOCK:-/opt/tre/.publicacao.lock}"
LOG_PUB="${TRE_WATCHDOG_LOG_PUB:-/opt/tre/.publicacoes.log}"
DONO="${TRE_WATCHDOG_DONO:-tre-deploy:tre-deploy}"

ACAO=conferir
QUIETO=0
while [ $# -gt 0 ]; do
  case "$1" in
    --conferir)  ACAO=conferir; shift;;
    --reparar)   ACAO=reparar; shift;;
    --travar)    ACAO=travar; shift;;
    --destravar) ACAO=destravar; shift;;
    --estado)    ACAO=estado; shift;;
    --quieto)    QUIETO=1; shift;;
    -h|--help)   sed -n '2,40p' "$0"; exit 0;;
    *) echo "PUBLICACAO_FALHOU argumento desconhecido: $1" >&2; exit 2;;
  esac
done

log() { [ "$QUIETO" -eq 0 ] && echo "$@"; }
tsp() { date -u +%Y-%m-%dT%H:%M:%SZ; }
epoch_de() { date -u -d "$1" +%s 2>/dev/null || echo 0; }

# Manifesto identico ao do deploy/publicar.sh: "<modo> <sha256> <caminho>", ordenado.
# O sha256 do proprio manifesto e o DIGEST DA ARVORE do commit publicado.
manifesto_de() {
  local dir="$1"
  [ -d "$dir" ] || { echo "PUBLICACAO_FALHOU diretorio inexistente: $dir" >&2; return 1; }
  ( cd "$dir" && LC_ALL=C find . -type f \
        ! -name '.publicado' ! -name '.publicado.manifest' -printf '%P\n' \
      | LC_ALL=C sort | while IFS= read -r p; do
          printf '%s %s %s\n' "$(stat -c '%a' "$p")" "$(sha256sum -- "$p" | cut -d' ' -f1)" "$p"
        done )
}
digest_de() { sha256sum | cut -d' ' -f1; }

# ---------------------------------------------------------------- trava de imutabilidade
# Escrita ad-hoc na copia (tar/rsync/cp por qualquer card) passa a FALHAR com EPERM em vez
# de sobrescrever em silencio. Quem publica (deploy/publicar.sh) e o unico que desarma.
travar() {
  [ -d "$DESTINO" ] || { echo "PUBLICACAO_FALHOU destino inexistente: $DESTINO" >&2; return 1; }
  chattr -R +i "$DESTINO" 2>/dev/null || true
  # prova funcional: a trava so vale se criar um arquivo novo for RECUSADO
  if ( : > "$DESTINO/.trava-probe" ) 2>/dev/null; then
    rm -f "$DESTINO/.trava-probe"
    echo "PUBLICACAO_TRAVA_FALHOU a copia continua gravavel ($DESTINO) — chattr +i nao pegou" >&2
    return 1
  fi
  return 0
}
destravar() {
  [ -d "$DESTINO" ] || return 0
  chattr -R -i "$DESTINO" 2>/dev/null || true
  return 0
}

if [ "$ACAO" = travar ]; then
  travar || exit 6
  echo "PUBLICACAO_TRAVADA destino=$DESTINO em=$(tsp)"
  exit 0
fi
if [ "$ACAO" = destravar ]; then
  destravar
  echo "PUBLICACAO_DESTRAVADA destino=$DESTINO em=$(tsp)"
  exit 0
fi

# ---------------------------------------------------------------- divergencia: alerta e log
alerta() { # $1 = motivo curto, $2 = detalhe multilinha
  local motivo="$1" detalhe="${2:-}"
  {
    echo "quando: $(tsp)"
    echo "destino: $DESTINO"
    echo "motivo: $motivo"
    echo "acao: a copia operacional NAO e o commit registrado em .publicado."
    echo "      Publicacao so por ./deploy/publicar.sh. Teste use TRE_PUBLICAR_DESTINO."
    [ -n "$detalhe" ] && printf '%s\n' "$detalhe"
  } > "$ARQ_ALERTA" 2>/dev/null || true
  { echo "=== $(tsp) destino=$DESTINO motivo=$motivo"
    printf '%s\n' "$detalhe"; } >> "$LOG_DIV" 2>/dev/null || true
  printf 'ALERTA: %s — detalhe no journal e em %s (alerta: %s)\n' "$motivo" "$LOG_DIV" "$ARQ_ALERTA" >&2
}

limpar_alerta() {
  if [ -f "$ARQ_ALERTA" ]; then
    log "AVISO alerta anterior limpo (a copia voltou a ser o commit registrado)"
    rm -f "$ARQ_ALERTA"
  fi
}

# ---------------------------------------------------------------- atribuicao da divergencia
# Diz QUEM mexeu: arquivo alterado (modo/sha), plantado (nao existe no commit) ou removido,
# com mtime e se o mtime e POSTERIOR a hora da publicacao registrada.
atribuir() { # $1 = manifesto de referencia, $2 = manifesto atual, $3 = epoch da publicacao
  local ref="$1" atual="$2" pub_epoch="$3"
  declare -A R_MODO=() R_SHA=() A_MODO=() A_SHA=() MTIME=()
  local modo sha caminho
  while read -r modo sha caminho; do
    [ -n "${caminho:-}" ] || continue
    R_MODO["$caminho"]="$modo"; R_SHA["$caminho"]="$sha"
  done <<< "$ref"
  while read -r modo sha caminho; do
    [ -n "${caminho:-}" ] || continue
    A_MODO["$caminho"]="$modo"; A_SHA["$caminho"]="$sha"
  done <<< "$atual"
  local n_alt=0 n_plan=0 n_rem=0 mostrados=0
  for caminho in $(printf '%s\n' "${!A_MODO[@]}" "${!R_MODO[@]}" | LC_ALL=C sort -u); do
    [ -n "$caminho" ] || continue
    if [ -z "${R_MODO[$caminho]:-}" ]; then
      MTIME["$caminho"]="$(stat -c %Y "$DESTINO/$caminho" 2>/dev/null || echo 0)"
      n_plan=$((n_plan+1))
      [ "$mostrados" -lt 25 ] && printf '  PLANTADO (%s, nao existe no commit) %s\n' "$(date -u -d "@${MTIME[$caminho]}" +%H:%M:%SZ)" "$caminho"
      [ "$mostrados" -lt 25 ] && mostrados=$((mostrados+1))
    elif [ -z "${A_MODO[$caminho]:-}" ]; then
      n_rem=$((n_rem+1))
      [ "$mostrados" -lt 25 ] && printf '  REMOVIDO %s\n' "$caminho"
      [ "$mostrados" -lt 25 ] && mostrados=$((mostrados+1))
    elif [ "${R_SHA[$caminho]}" != "${A_SHA[$caminho]}" ] || [ "${R_MODO[$caminho]}" != "${A_MODO[$caminho]}" ]; then
      MTIME["$caminho"]="$(stat -c %Y "$DESTINO/$caminho" 2>/dev/null || echo 0)"
      n_alt=$((n_alt+1))
      if [ "$mostrados" -lt 25 ]; then
        printf '  ALTERADO (%s%s) %s  %s %s -> %s %s\n' \
          "$(date -u -d "@${MTIME[$caminho]}" +%H:%M:%SZ)" \
          "$( [ "${MTIME[$caminho]}" -gt "$pub_epoch" ] && echo ', depois da publicacao' )" \
          "$caminho" "${R_MODO[$caminho]}" "${R_SHA[$caminho]}" "${A_MODO[$caminho]}" "${A_SHA[$caminho]}"
        mostrados=$((mostrados+1))
      fi
    fi
  done
  local n_linhas
  n_linhas="$(diff <(printf '%s\n' "$ref") <(printf '%s\n' "$atual") | grep -c '^[<>]' || true)"
  printf '%s\n' "linhas do manifesto: referencia=$(printf '%s\n' "$ref" | grep -c . || true) copia=$(printf '%s\n' "$atual" | grep -c . || true) | alterados=$n_alt removidos=$n_rem plantados=$n_plan | diff=$n_linhas linha(s)"
}

# ---------------------------------------------------------------- reparo pelo artefato do commit registrado
# Restaura a copia a partir do artefato que a publicacao deixou em $ARTEFATO (root, 700,
# FORA da copia). Nunca restaura outro commit: quem manda e o `.publicado`. Se o artefato
# nao estiver integro ou for de outro commit, RECUSA (nao ha como inventar o conteudo).
reparo() { # $1 = manifesto de referencia (do commit registrado), $2 = detalhe da divergencia
  local man_ref="$1"
  for f in commit commit.tar modos.txt manifesto; do
    [ -f "$ARTEFATO/$f" ] || {
      printf '  reparo recusado: artefato incompleto em %s (falta %s)\n' "$ARTEFATO" "$f" >&2
      return 6; }
  done
  local c_art d_art stg man_dep
  c_art="$(cat "$ARTEFATO/commit")"
  [ "$c_art" = "$COMMIT_REG" ] || {
    printf '  reparo recusado: o artefato e do commit %s e o registro pede %s\n' "$c_art" "$COMMIT_REG" >&2
    return 6; }
  d_art="$(sha256sum "$ARTEFATO/manifesto" | cut -d' ' -f1)"
  [ "$d_art" = "$DIG_REG" ] || {
    printf '  reparo recusado: o manifesto do artefato (%s) nao confere com o digest registrado (%s)\n' \
      "$d_art" "$DIG_REG" >&2
    return 6; }

  stg="$(mktemp -d "${TMPDIR:-/tmp}/reparo.XXXXXX")" || return 6
  if ! tar -xpf "$ARTEFATO/commit.tar" -C "$stg"; then
    printf '  reparo falhou: nao consegui extrair o artefato\n' >&2; rm -rf "$stg"; return 6
  fi
  local m p
  while IFS=' ' read -r m p; do
    [ -n "${p:-}" ] || continue
    case "$m" in
      100755) chmod 755 "$stg/$p" 2>/dev/null || true;;
      100644) chmod 644 "$stg/$p" 2>/dev/null || true;;
    esac
  done < "$ARTEFATO/modos.txt"
  find "$stg" -type d -exec chmod 755 {} + 2>/dev/null || true

  destravar
  if ! rsync -a --delete --exclude '/.publicado' --exclude '/.publicado.manifest' "$stg/" "$DESTINO/"; then
    printf '  reparo falhou: rsync do artefato para %s\n' "$DESTINO" >&2; rm -rf "$stg"; return 6
  fi
  find "$DESTINO" -type d -exec chmod 755 {} + 2>/dev/null || true
  chown -R "$DONO" "$DESTINO" 2>/dev/null || true
  rm -rf "$stg"

  man_dep="$(manifesto_de "$DESTINO")"
  if [ "$man_dep" != "$man_ref" ]; then
    printf '  reparo falhou: a copia nao ficou igual ao commit registrado depois do rsync\n' >&2
    return 6
  fi
  printf '%s\n' "$AGORA commit=$COMMIT_REG digest=$DIG_REG arquivos=$(printf '%s\n' "$man_dep" | grep -c . || true) card=watchdog-reparo destino=$DESTINO digest_antes=<divergente> commit_antes=$COMMIT_REG divergencia_antes=<restaurado do artefato>" >> "$LOG_PUB" 2>/dev/null || true
  travar || true
  return 0
}

# ---------------------------------------------------------------- estado resumido
if [ "$ACAO" = estado ]; then
  if [ -f "$DESTINO/.publicado" ]; then
    COMMIT_REG="$(sed -n 's/^commit:[[:space:]]*//p' "$DESTINO/.publicado" | head -1)"
    N_FILES="$(manifesto_de "$DESTINO" | grep -c . || true)"
    TRAVA=ausente
    if ! ( : > "$DESTINO/.trava-probe" ) 2>/dev/null; then TRAVA=armada; else rm -f "$DESTINO/.trava-probe"; fi
    echo "PUBLICACAO_ESTADO commit=$COMMIT_REG arquivos=${N_FILES:-0} trava=$TRAVA alerta=$([ -f "$ARQ_ALERTA" ] && echo presente || echo ausente)"
  else
    echo "PUBLICACAO_ESTADO sem-registro arquivos=0 trava=ausente alerta=$([ -f "$ARQ_ALERTA" ] && echo presente || echo ausente)"
  fi
  exit 0
fi

# ---------------------------------------------------------------- conferir / reparar
AGORA="$(tsp)"
if [ -e "$LOCK" ]; then
  log "PUBLICACAO_EM_ANDAMENTO lock=$LOCK (publicacao em curso; o watchdog nao interfere)"
  exit 0
fi

if [ ! -f "$DESTINO/.publicado" ] || [ ! -f "$DESTINO/.publicado.manifest" ]; then
  DETALHE="  registro ausente em $DESTINO:$([ -f "$DESTINO/.publicado" ] || echo ' .publicado')$([ -f "$DESTINO/.publicado.manifest" ] || echo ' .publicado.manifest')"
  alerta "sem registro da publicacao" "$DETALHE"
  echo "PUBLICACAO_SEM_REGISTRO $DESTINO nao tem .publicado/.publicado.manifest — nao ha como dizer qual commit" >&2
  echo "                     esta publicado (isto E o defeito t_091cfea9/t_daca4bda)." >&2
  exit 5
fi

COMMIT_REG="$(sed -n 's/^commit:[[:space:]]*//p' "$DESTINO/.publicado" | head -1)"
DIG_REG="$(sed -n 's/^digest:[[:space:]]*//p' "$DESTINO/.publicado" | head -1)"
PUBLICADO_EM="$(sed -n 's/^publicado_em:[[:space:]]*//p' "$DESTINO/.publicado" | head -1)"
PUBLICADO_POR="$(sed -n 's/^publicado_por:[[:space:]]*//p' "$DESTINO/.publicado" | head -1)"
PUB_EPOCH="$(epoch_de "$PUBLICADO_EM")"

# Referencia: o manifesto do artefato (fora da copia) e a verdade; o `.publicado.manifest`
# da copia e a segunda opiniao. Divergencia entre os dois e alerta por si so.
REF_ORIGEM="artefato:$ARTEFATO/manifesto"
if [ -f "$ARTEFATO/manifesto" ]; then
  DIG_REF="$(sha256sum "$ARTEFATO/manifesto" | cut -d' ' -f1)"
  if [ -n "$DIG_REG" ] && [ "$DIG_REF" != "$DIG_REG" ]; then
    REF_ORIGEM="$REF_ORIGEM (AVISO: digest do artefato $DIG_REF != digest registrado $DIG_REG)"
  fi
  MAN_REF="$(cat "$ARTEFATO/manifesto")"
else
  MAN_REF="$(cat "$DESTINO/.publicado.manifest")"
  REF_ORIGEM=".publicado.manifest da copia (artefato ausente — referencia enfraquecida, sem reparo)"
fi

MAN_ATUAL="$(manifesto_de "$DESTINO")"
log "== watchdog da copia operacional =="
log "destino:    $DESTINO"
log "registro:   commit=${COMMIT_REG:-?} digest=${DIG_REG:-?} publicado_em=${PUBLICADO_EM:-?} por=${PUBLICADO_POR:-?}"
log "referencia: $REF_ORIGEM"

if [ "$MAN_ATUAL" = "$MAN_REF" ]; then
  DIG_ATUAL="$(printf '%s\n' "$MAN_ATUAL" | digest_de)"
  log "digest agora: $DIG_ATUAL"
  [ -z "$DIG_REG" ] || [ "$DIG_ATUAL" = "$DIG_REG" ] || log "AVISO digest registrado ($DIG_REG) difere do medido agora ($DIG_ATUAL)"
  limpar_alerta
  echo "PUBLICACAO_OK commit=${COMMIT_REG:-?} digest=$DIG_ATUAL arquivos=$(printf '%s\n' "$MAN_ATUAL" | grep -c . || true) em=$(tsp)"
  exit 0
fi

# Divergente: mede, atribui, alerta (e repara, se pedido).
log "--- divergencia medida $(tsp)"
DETALHE="$(atribuir "$MAN_REF" "$MAN_ATUAL" "$PUB_EPOCH")"
log "$DETALHE"
log "--- fim da divergencia"
alerta "copia operacional divergente do commit registrado" "  $DETALHE
  modificado_em: $(stat -c %y "$DESTINO" 2>/dev/null | cut -c1-19)"

if [ "$ACAO" = reparar ]; then
  if reparo "$MAN_REF" "$DETALHE"; then
    DIG_DEPOIS="$(manifesto_de "$DESTINO" | digest_de)"
    limpar_alerta
    echo "PUBLICACAO_REPARO_OK commit=$COMMIT_REG digest=$DIG_DEPOIS em=$(tsp)"
    exit 0
  fi
  echo "PUBLICACAO_REPARO_FALHOU a copia segue divergente do commit registrado ($COMMIT_REG)" >&2
  exit 6
fi

echo "PUBLICACAO_DIVERGENTE a copia operacional NAO e o commit registrado ($COMMIT_REG)" >&2
exit 5
