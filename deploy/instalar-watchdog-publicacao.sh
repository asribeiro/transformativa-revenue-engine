#!/usr/bin/env bash
# Instala/atualiza o watchdog da publicacao versionada na VPS.
#
# Card: t_daca4bda (recorrencia do defeito t_091cfea9). O `publicar.sh --conferir` so
# DETECTA quando alguem roda; o watchdog roda sozinho a cada 2 min na VPS e, ao achar
# divergencia, alerta e restaura a copia a partir do artefato do commit registrado.
#
# Propriedade importante: o que e instalado sao os BYTES DA COPIA PUBLICADA
# ($DESTINO/deploy/...), nao a arvore de trabalho deste host — instalar tambem passa
# pelo caminho unico. A instalacao falha se os bytes nao conferirem entre os dois lados.
#
# Uso:
#   deploy/instalar-watchdog-publicacao.sh                 instala e habilita o timer
#   deploy/instalar-watchdog-publicacao.sh --travar        instala e ja arma a trava (+i)
#   deploy/instalar-watchdog-publicacao.sh --sem-habilitar instala sem habilitar o timer
#   deploy/instalar-watchdog-publicacao.sh --remover       desabilita e remove
#
# Saida final: WATCHDOG_INSTALADO sha=<sha256 do script instalado> timer=<estado> trava=<estado>
set -euo pipefail

ALVO="${TRE_PUBLICAR_ALVO:-root@169.58.24.102}"
DESTINO="${TRE_PUBLICAR_DESTINO:-/opt/tre/prod/repo}"
CHAVE="${TRE_SSH_CHAVE:-}"
if [ -z "$CHAVE" ]; then
  for c in "$HOME/.ssh/id_ed25519_ops" /opt/data/home/.ssh/id_ed25519_ops; do
    [ -f "$c" ] && { CHAVE="$c"; break; }
  done
fi
OPCOES_CHAVE=()
[ -n "$CHAVE" ] && OPCOES_CHAVE=(-i "$CHAVE")
LIB=/usr/local/lib/tre
UNITS=/etc/systemd/system

FAZER_TRAVA=0; HABILITAR=1; REMOVER=0
while [ $# -gt 0 ]; do
  case "$1" in
    --travar)        FAZER_TRAVA=1; shift;;
    --sem-habilitar) HABILITAR=0; shift;;
    --remover)       REMOVER=1; shift;;
    --alvo)          ALVO="${2:-}"; shift 2;;
    --destino)       DESTINO="${2:-}"; shift 2;;
    --chave)         CHAVE="${2:-}"; OPCOES_CHAVE=(-i "$CHAVE"); shift 2;;
    -h|--help)       sed -n '2,30p' "$0"; exit 0;;
    *) echo "WATCHDOG_FALHOU argumento desconhecido: $1" >&2; exit 2;;
  esac
done

R() {
  # ControlMaster: uma conexao TCP para a instalacao inteira (a VPS penaliza rajadas do mesmo IP).
  ssh "${OPCOES_CHAVE[@]}" -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
      -o ConnectTimeout=15 -o ControlMaster=auto -o ControlPersist=30 \
      -o ControlPath="${TRE_SSH_CONTROLE:-/tmp/tre-ssh-control-%r@%h:%p}" "$ALVO" "$@"
}

if [ "$REMOVER" -eq 1 ]; then
  R "systemctl disable --now tre-publicacao-watchdog.timer 2>/dev/null; \
     rm -f '$UNITS/tre-publicacao-watchdog.timer' '$UNITS/tre-publicacao-watchdog.service' '$LIB/watchdog-publicacao.sh'; \
     systemctl daemon-reload; systemctl reset-failed tre-publicacao-watchdog.service 2>/dev/null; \
     rmdir '$LIB' 2>/dev/null; echo WATCHDOG_REMOVIDO"
  exit 0
fi

echo "== instalacao do watchdog da publicacao =="
echo "origem dos bytes: $ALVO:$DESTINO/deploy/{watchdog-publicacao.sh,systemd/...}"

# 1) os bytes publicados tem de existir na copia
for f in deploy/watchdog-publicacao.sh deploy/systemd/tre-publicacao-watchdog.service deploy/systemd/tre-publicacao-watchdog.timer; do
  R "test -f '$DESTINO/$f'" || {
    echo "WATCHDOG_FALHOU $DESTINO/$f nao existe — publique o commit que traz o watchdog" >&2
    echo "                (deploy/publicar.sh --commit <sha>) antes de instalar." >&2
    exit 2; }
done

TMP="$(mktemp -d "${TMPDIR:-/tmp}/watchdog.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

baixar_e_conferir() { # $1 = caminho relativo na copia, $2 = nome local
  R "cat '$DESTINO/$1'" > "$TMP/$2"
  local remoto local_s local_r
  remoto="$(R "sha256sum '$DESTINO/$1' | cut -d' ' -f1")"
  local_s="$(sha256sum "$TMP/$2" | cut -d' ' -f1)"
  local_r="$local_s"
  if [ "$remoto" != "$local_r" ]; then
    echo "WATCHDOG_FALHOU os bytes de $1 nao conferem (remoto=$remoto local=$local_r) — abortado" >&2
    exit 6
  fi
  echo "  $1 sha256=$local_s (remoto == local)"
}

baixar_e_conferir deploy/watchdog-publicacao.sh watchdog-publicacao.sh
baixar_e_conferir deploy/systemd/tre-publicacao-watchdog.service tre-publicacao-watchdog.service
baixar_e_conferir deploy/systemd/tre-publicacao-watchdog.timer tre-publicacao-watchdog.timer
SHA_WD="$(sha256sum "$TMP/watchdog-publicacao.sh" | cut -d' ' -f1)"

# 2) instala fora da copia (o watchdog tem de sobreviver a copia quebrada)
R "install -d -m 755 '$LIB'"
R "cat > '$LIB/watchdog-publicacao.sh'" < "$TMP/watchdog-publicacao.sh"
R "chmod 755 '$LIB/watchdog-publicacao.sh'"
R "cat > '$UNITS/tre-publicacao-watchdog.service'" < "$TMP/tre-publicacao-watchdog.service"
R "cat > '$UNITS/tre-publicacao-watchdog.timer'" < "$TMP/tre-publicacao-watchdog.timer"
R "chmod 644 '$UNITS/tre-publicacao-watchdog.service' '$UNITS/tre-publicacao-watchdog.timer'"
R "systemctl daemon-reload"
echo "  instalado: $LIB/watchdog-publicacao.sh sha256=$SHA_WD"

# 3) habilita o timer
TIMER=nao-habilitado
if [ "$HABILITAR" -eq 1 ]; then
  R "systemctl enable tre-publicacao-watchdog.timer >/dev/null 2>&1 && systemctl restart tre-publicacao-watchdog.timer"
  TIMER="$(R "systemctl is-enabled tre-publicacao-watchdog.timer 2>/dev/null || echo falhou")"
  echo "  timer: $TIMER ($(R "systemctl list-timers tre-publicacao-watchdog.timer --no-pager | sed -n 2p"))"
fi

# 4) estado medido pela primeira conferencia do watchdog recem-instalado
ESTADO="$(R "bash '$LIB/watchdog-publicacao.sh' --estado")"
echo "  $ESTADO"

# 5) trava de imutabilidade: reporta o estado real; --travar arma se ainda nao estiver armada
TRAVA="$(R "bash '$LIB/watchdog-publicacao.sh' --estado | sed -n 's/.*trava=\\([a-z]*\\).*/\\1/p'")"
if [ "$FAZER_TRAVA" -eq 1 ] && [ "$TRAVA" != "armada" ]; then
  R "bash '$LIB/watchdog-publicacao.sh' --travar"
  TRAVA="$(R "bash '$LIB/watchdog-publicacao.sh' --estado | sed -n 's/.*trava=\\([a-z]*\\).*/\\1/p'")"
fi

echo "WATCHDOG_INSTALADO sha=$SHA_WD timer=$TIMER trava=$TRAVA"
