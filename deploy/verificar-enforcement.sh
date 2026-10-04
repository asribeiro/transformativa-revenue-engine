#!/usr/bin/env bash
# Verificador COM DENTE do enforcement do caminho unico (card t_daca4bda).
#
# Ele nao verifica "o script esta bonito": ele TENTA o caminho ad-hoc e exige que ele FALHE.
# Toda a bateria roda em DESTINO ISOLADO (nunca /opt/tre/repo) — inclusive a sabotagem.
#
# Uso:
#   bash deploy/verificar-enforcement.sh                     # bateria completa (destino isolado)
#   TRE_ENF_SEM_TRAVA=1 bash deploy/verificar-enforcement.sh # auto-sabotagem: publica SEM a trava
#                                                            # (TEM de REPROVAR: prova que o
#                                                            #  verificador tem dente)
#
# Saida final (uma linha):
#   VERIFICADOR_ENFORCEMENT_OK itens=<n> destino=<...>
#   VERIFICADOR_ENFORCEMENT_FALHOU ...   (exit 1: o guard/detector NAO reprovou onde tinha de reprovar)
#
# Itens medidos:
#   1. publicacao no destino isolado (caminho unico) termina PUBLICACAO_OK e ARMA a trava
#   2. guarda: `>>`, `sed -i`, `tar -xz` de arvore alheia e arquivo novo FALHAM com EPERM
#   3. conteudo segue intacto depois das tentativas (sha256 do arquivo alvo)
#   4. sabotagem (depois de `chattr -i`, como o defeito real): detector TEM de reprovar (exit 5)
#   5. reparo: o watchdog restaura do artefato, rearma a trava e a conferencia volta a OK
set -uo pipefail
AUTO="$(cd "$(dirname "${BASH_SOURCE[0]:-verificar-enforcement.sh}")" && pwd)/$(basename "${BASH_SOURCE[0]:-verificar-enforcement.sh}")"
BASE_DIR="$(dirname "$AUTO")"
# A publicacao sai do git: rode sempre a partir da raiz do repositorio (nao do cwd de quem chama).
REPO_DIR="$(cd "$BASE_DIR/.." && pwd)"
cd "$REPO_DIR" || { echo "VERIFICADOR_NAO_RODOU nao consegui entrar em $REPO_DIR"; exit 3; }
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "VERIFICADOR_NAO_RODOU $REPO_DIR nao e um repositorio git (a publicacao sai do git)"; exit 3
fi

CHAVE="${TRE_SSH_CHAVE:-}"
if [ -z "$CHAVE" ]; then
  for c in "$HOME/.ssh/id_ed25519_ops" /opt/data/home/.ssh/id_ed25519_ops; do
    [ -f "$c" ] && { CHAVE="$c"; break; }
  done
fi
OPCOES_CHAVE=()
[ -n "$CHAVE" ] && OPCOES_CHAVE=(-i "$CHAVE")
ALVO="${TRE_PUBLICAR_ALVO:-root@169.58.24.102}"

DEST="${TRE_ENF_DESTINO:-/opt/tre/.teste-enforcement-${HERMES_KANBAN_TASK:-local}}"
LOCK="${TRE_ENF_LOCK:-$DEST.lock}"
ARTEFATO="${TRE_ENF_ARTEFATO:-$DEST-artefato}"
LOG="${TRE_ENF_LOG:-$DEST.log}"
ALERTA="${TRE_ENF_ALERTA:-$DEST-ALERTA}"
DIV_LOG="${TRE_ENF_DIV_LOG:-$DEST-divergencias.log}"
SEM_TRAVA="${TRE_ENF_SEM_TRAVA:-0}"     # auto-sabotagem do proprio verificador
FALHAS=0; ITENS=0
MSG=()

ok()    { ITENS=$((ITENS+1)); echo "  ok    $1"; }
falha() { FALHAS=$((FALHAS+1)); ITENS=$((ITENS+1)); echo "  FALHA $1"; MSG+=("$1"); }

# ---- qualquer chamada remota usa UMA conexao (ControlMaster): a VPS penaliza rajadas de conexoes
SSH_R() {
  ssh "${OPCOES_CHAVE[@]}" -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 \
      -o ControlMaster=auto -o ControlPersist=30 -o ControlPath="${TRE_SSH_CONTROLE:-/tmp/tre-ssh-control-%r@%h:%p}" \
      "$ALVO" "$@"
}

publicar() { # $1 = publicar|sem-trava|conferir
  export TRE_PUBLICAR_DESTINO="$DEST" TRE_PUBLICAR_LOCK="$LOCK" TRE_PUBLICAR_ARTEFATO="$ARTEFATO" \
         TRE_PUBLICAR_LOG="$LOG"
  case "$1" in
    publicar)  TRE_PUBLICAR_TRAVA=1 bash "$BASE_DIR/publicar.sh" --commit "${2:-HEAD}" --card "${HERMES_KANBAN_TASK:-verificador}";;
    sem-trava) TRE_PUBLICAR_TRAVA=0 bash "$BASE_DIR/publicar.sh" --commit "${2:-HEAD}" --sem-trava --card "${HERMES_KANBAN_TASK:-verificador}";;
    conferir)  bash "$BASE_DIR/publicar.sh" --conferir;;
  esac
}

detectar() { # $1 = --conferir | --reparar  (watchdog instalado, rodando onde a copia esta)
  SSH_R "export TRE_WATCHDOG_DESTINO='$DEST' TRE_WATCHDOG_ARTEFATO='$ARTEFATO' \
                TRE_WATCHDOG_ALERTA='$ALERTA' TRE_WATCHDOG_DIV_LOG='$DIV_LOG' \
                TRE_WATCHDOG_LOG_PUB='$LOG'; bash /usr/local/lib/tre/watchdog-publicacao.sh $1"
}

echo "=== verificar-enforcement (destino isolado: $DEST)"
# Fail-fast: publicar() recusa arvore suja (correto), mas um "FALHOU ... falhas=8" por arvore suja
# parece defeito de enforcement. Melhor parar aqui com a razao clara.
SUJO="$(git -C "$REPO_DIR" status --porcelain --untracked-files=no 2>/dev/null)"
if [ -n "$SUJO" ]; then
  echo "VERIFICADOR_NAO_RODOU arvore suja: commite (ou faca stash) antes — a publicacao recusa arvore suja"
  printf '%s\n' "$SUJO" | head -5
  exit 3
fi
echo "--- limpeza do destino de ensaio"
SSH_R "chattr -R -i '$DEST' 2>/dev/null; rm -rf '$DEST' '$ARTEFATO' '$LOCK' '$ALERTA' '$DIV_LOG' '$LOG' 2>/dev/null; true"

# ---------------------------------------------------------------- 1. o caminho unico publica e arma
echo "--- 1. publicacao pelo caminho unico (isolado) + trava"
if [ "$SEM_TRAVA" = "1" ]; then
  echo "  (auto-sabotagem: publicando SEM a trava — o item 2 TEM de reprovar)"
  SAIDA="$(publicar sem-trava 2>&1)"; RC=$?
else
  SAIDA="$(publicar publicar 2>&1)"; RC=$?
fi
printf '%s\n' "$SAIDA" | tail -3
if printf '%s' "$SAIDA" | grep -q '^PUBLICACAO_OK' && [ "$RC" -eq 0 ]; then
  ok "publicacao no destino isolado terminou PUBLICACAO_OK (exit 0)"
else
  falha "publicacao nao terminou PUBLICACAO_OK (exit $RC)"
fi
SHA="$(printf '%s' "$SAIDA" | sed -n 's/.*\bcommit=\([0-9a-f]\{40\}\).*/\1/p' | tail -1)"
DIG="$(printf '%s' "$SAIDA" | sed -n 's/.*\bdigest=\([0-9a-f]\{64\}\).*/\1/p' | tail -1)"
{ [ -n "$SHA" ] && [ -n "$DIG" ]; } || falha "nao consegui ler commit/digest da publicacao"
TRAVA_ANTES="$(SSH_R "lsattr -d '$DEST' 2>/dev/null | awk '{print \$1}'" | tr -d '\n')"
case "$TRAVA_ANTES" in (*i*) ok "trava armada no destino isolado ($TRAVA_ANTES)";;
                      (*)   falha "trava NAO armada no destino isolado ($TRAVA_ANTES)";; esac
SHA_ALVO="$(SSH_R "sha256sum '$DEST/scripts/backup/backup-tre.sh' 2>/dev/null | cut -d' ' -f1")"
echo "  commit=$SHA digest=$DIG backup-tre.sh=$SHA_ALVO"

# ---------------------------------------------------------------- 2. a guarda recusa os 4 caminhos
echo "--- 2. guarda: as 4 tentativas ad-hoc TEM de falhar (EPERM)"
recusou() { # $1 = descricao, $2.. = comando remoto cru
  local out rc d
  out="$(SSH_R "$2" 2>&1)"; rc=$?
  if [ "$rc" -ne 0 ] || printf '%s' "$out" | grep -qi "Permission denied\|Operation not permitted"; then
    ok "$1 recusado"
  else
    falha "$1 NAO foi recusado (exit $rc; a escrita ad-hoc passou!)"
  fi
}
recusou ">> no arquivo da copia"     "echo 'x' >> '$DEST/scripts/backup/backup-tre.sh'"
recusou "sed -i no arquivo da copia" "sed -i 's/^/x/' '$DEST/scripts/backup/backup-tre.sh'"
recusou "arquivo novo plantado"      "printf 'x' > '$DEST/scripts/db/plantado-verificador.sh'"
recusou "tar -xz de arvore alheia"   "d=\$(mktemp -d); mkdir -p \$d/scripts/backup; printf 'x' > \$d/scripts/backup/backup-tre.sh; touch -d @0 \$d/scripts/backup/backup-tre.sh; tar -czf \$d/t.tgz -C \$d scripts; tar -xzf \$d/t.tgz -C '$DEST'; rc=\$?; rm -rf \$d; exit \$rc"

# ---------------------------------------------------------------- 3. conteudo intacto
echo "--- 3. conteudo da copia intacto depois das tentativas"
SHA_DEP="$(SSH_R "sha256sum '$DEST/scripts/backup/backup-tre.sh' 2>/dev/null | cut -d' ' -f1")"
# Hash vazio nao pode passar por "inalterado" (verificador que passa por construcao nao vale).
if [ -z "$SHA_ALVO" ] || [ -z "$SHA_DEP" ]; then
  falha "nao consegui medir o sha256 do arquivo alvo (alvo='$SHA_ALVO' depois='$SHA_DEP')"
elif [ "$SHA_ALVO" = "$SHA_DEP" ]; then
  ok "backup-tre.sh inalterado ($SHA_DEP)"
else
  falha "backup-tre.sh MUDOU ($SHA_ALVO -> $SHA_DEP)"
fi
DET="$(publicar conferir 2>&1 | tail -1)"
printf '%s' "$DET" | grep -q '^PUBLICACAO_OK' && ok "conferencia do destino isolado segue PUBLICACAO_OK" \
  || falha "conferencia do destino isolado nao esta OK: $DET"

# ---------------------------------------------------------------- 4. sabotagem TEM de reprovar
echo "--- 4. sabotagem (o defeito real) e o detector TEM de reprovar"
SSH_R "chattr -R -i '$DEST' >/dev/null 2>&1; printf 'x' >> '$DEST/scripts/backup/backup-tre.sh'; printf 'x' > '$DEST/scripts/db/plantado-sabotagem.sh'; touch -d @0 '$DEST/scripts/db/plantado-sabotagem.sh'; true"
DIV="$(detectar --conferir 2>&1)"; RC_DIV=$?
printf '%s\n' "$DIV" | tail -3
if printf '%s' "$DIV" | grep -q 'PUBLICACAO_DIVERGENTE' && [ "$RC_DIV" -eq 5 ]; then
  ok "detector reprovou a sabotagem (PUBLICACAO_DIVERGENTE, exit 5)"
  printf '%s' "$DIV" | grep -q 'plantado-sabotagem.sh' && ok "detector atribuiu o arquivo plantado" \
    || falha "detector nao atribuiu o arquivo plantado"
else
  falha "detector NAO reprovou a sabotagem (exit $RC_DIV) — detector sem dente"
fi

# ---------------------------------------------------------------- 5. reparo restaura e rearma
echo "--- 5. reparo (a partir do artefato) e trava de volta"
REP="$(detectar --reparar 2>&1)"; RC_REP=$?
printf '%s\n' "$REP" | tail -2
printf '%s' "$REP" | grep -q 'PUBLICACAO_REPARO_OK' && ok "reparo restaurou a copia do artefato" \
  || falha "reparo nao restaurou (exit $RC_REP)"
DET2="$(publicar conferir 2>&1 | tail -1)"
printf '%s' "$DET2" | grep -q '^PUBLICACAO_OK' && ok "depois do reparo a conferencia e PUBLICACAO_OK" \
  || falha "depois do reparo a conferencia NAO esta OK: $DET2"
TRAVA_POS="$(SSH_R "lsattr -d '$DEST' 2>/dev/null | awk '{print \$1}'" | tr -d '\n')"
case "$TRAVA_POS" in (*i*) ok "trava rearmada ($TRAVA_POS)";; (*) falha "trava NAO rearmada ($TRAVA_POS)";; esac
SSH_R "rm -f '$ALERTA' 2>/dev/null; true"

echo
if [ "$FALHAS" -eq 0 ]; then
  echo "VERIFICADOR_ENFORCEMENT_OK itens=$ITENS destino=$DEST"
  exit 0
fi
echo "VERIFICADOR_ENFORCEMENT_FALHOU itens=$ITENS falhas=$FALHAS destino=$DEST"
for m in "${MSG[@]}"; do echo "  - $m"; done
exit 1
