#!/usr/bin/env bash
# Teste do defeito t_0f74266d — staging da publicacao em caminho FIXO e mensagem apontando a causa
# no lugar errado.
#
# Roda o `deploy/publicar.sh` REAL contra um "remoto" LOCAL: um `ssh` de mentira (shim) que executa
# o comando no sandbox e mapeia /opt/tre -> $TRE_TESTE_RAIZ. Nada aqui toca a VPS.
#
# Itens (todos medidos, nenhum "passa por constante"):
#   A) DEFEITO (versao antiga 3bf5e07, a que falhou no card t_1b2ab418): um arquivo desaparece do
#      staging entre o `find` e o `sha256sum` (escrita/limpeza concorrente). Esperado: exit 6 com a
#      mensagem culpando "a copia transferida" e SEM nomear o staging — o lugar ERRADO.
#   B) CONSERTO (versao nova), MESMO cenario: exit 6 nomeando a FASE (staging), o ARQUIVO, o hecho
#      de o destino nao ter sido tocado; diff completo em arquivo; linha PUBLICACAO_ABORTADA no log;
#      staging removido no fim (trap).
#   C) DEFEITO por colisao: duas publicacoes simultaneas (versao antiga, destinos e locks isolados,
#      janela alargada) se misturam no staging fixo -> pelo menos uma falha.
#   D) CONSERTO por colisao: as mesmas duas publicacoes simultaneas na versao nova passam as duas,
#      com stagings DIFERENTES e sem sobra de staging.
#   E) GUARDA: lock isolado + destino compartilhado -> exit 2, nada escrito.
#   F) CAMINHO BOM: publicacao normal seguida de `--conferir` -> PUBLICACAO_OK nas duas.
#   G) GUARDA (mesma familia, caminho fixo): destino isolado + artefato PADRAO do watchdog -> exit 2
#      (publicar em destino isolado com o artefato padrao faria o watchdog reparar A PRODUCAO).
#   H) `--destino` por CLI: destino isolado nao e confundido com PRODUCAO (o calculo de PRODUCAO
#      passou a ser pos-parse: antes, `--destino` para um ensaio pedia --producao; discrimina H2).
#
# Uso: deploy/teste-staging-unico.sh [commit-A] [commit-B]
#   commit-A/commit-B: dois commits do repositorio (padrao: HEAD e origin/develop).
# Variaveis: TRE_TESTE_RSYNC (caminho de um rsync de verdade, se nao houver no PATH),
#            TRE_TESTE_LD_LIBRARY_PATH (precisa se o rsync foi extraido de um .deb).
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NOVO="$REPO/deploy/publicar.sh"
SHA_A="${1:-$(git -C "$REPO" rev-parse HEAD)}"
SHA_B="${2:-$(git -C "$REPO" rev-parse origin/develop)}"
BASE_ANTIGA="3bf5e07"   # deploy/publicar.sh com o staging FIXO (a versao que falhou de verdade)

ROOT="$(mktemp -d "${TMPDIR:-/tmp}/teste-staging.XXXXXX")"
BIN="$ROOT/bin"; REMOTO="$ROOT/remoto"; OUT="$ROOT/saida"; DIFS="$ROOT/diffs"
SHIMLOG="$ROOT/shim.log"
mkdir -p "$BIN" "$REMOTO" "$OUT" "$DIFS"; : > "$SHIMLOG"
trap 'rm -rf "$ROOT"' EXIT

# ---------------------------------------------------------------- dependencias do sandbox
RSYNC="${TRE_TESTE_RSYNC:-$(command -v rsync || true)}"
if [ -z "$RSYNC" ]; then
  echo "FALHOU sem rsync no PATH (a troca no destino usa rsync -a --delete)." >&2
  echo "       instale rsync ou aponte TRE_TESTE_RSYNC=." >&2
  exit 2
fi
export LD_LIBRARY_PATH="${TRE_TESTE_LD_LIBRARY_PATH:-${LD_LIBRARY_PATH:-}}"
ln -sf "$RSYNC" "$BIN/rsync"

# ---------------------------------------------------------------- ssh de mentira
cat > "$BIN/ssh" <<'SHIM'
#!/usr/bin/env bash
# ssh de mentira do teste local: executa o "comando remoto" aqui e mapeia /opt/tre -> $TRE_TESTE_RAIZ
set -uo pipefail
raiz="${TRE_TESTE_RAIZ:?TRE_TESTE_RAIZ nao definido}"
while [ $# -gt 0 ]; do
  case "$1" in
    -o|-i|-p|-l|-b|-c|-E|-F|-I|-J|-L|-R|-W) shift 2 2>/dev/null || shift;;
    -*) shift;;
    *) break;;
  esac
done
[ $# -gt 0 ] || { echo "shim-ssh: sem alvo" >&2; exit 2; }
shift
[ $# -gt 0 ] || { echo "shim-ssh: sem comando" >&2; exit 2; }
[ -n "${TRE_TESTE_LOG:-}" ] && printf 'CMD %s\n' "$*" >> "$TRE_TESTE_LOG"
map() { printf '%s' "${1//\/opt\/tre/$raiz}"; }
if [ $# -eq 1 ]; then           # comando unico em texto ("cmd && cmd")
  arq="$(mktemp)"
  map "$1" > "$arq"
  bash "$arq"
  rc=$?
  rm -f "$arq"
  exit $rc
fi
args=()
for a in "$@"; do args+=("$(map "$a")"); done   # argv (ex.: bash -s -- --manifesto <dir>)
dir=""; i=0
for a in "${args[@]}"; do
  [ "$a" = "--manifesto" ] && dir="${args[$((i+1))]:-}"
  i=$((i+1))
done
case "$dir" in (*.publicacao-staging*) staging=1;; (*) staging=0;; esac
if [ "$staging" -eq 1 ]; then
  if [ -n "${TRE_TESTE_SUMI_REL:-}" ]; then
    # simula o rm -rf/tar de OUTRA publicacao caindo no meio da montagem do manifesto
    ( sleep "${TRE_TESTE_SUMI_ATRASO:-0.3}"; rm -f "$dir/$TRE_TESTE_SUMI_REL" ) &
  fi
  [ -n "${TRE_TESTE_ATRASO_MANIFESTO:-}" ] && sleep "$TRE_TESTE_ATRASO_MANIFESTO"
fi
exec "${args[@]}"
SHIM
chmod +x "$BIN/ssh"

# ---------------------------------------------------------------- rodar a publicacao no sandbox
ALVO_PROD="$REMOTO/repo-prod"
rodar() { # $1=rotulo $2=script $3=dest $4=lock $5=log $6=artefato $7=commit $8..=flags
  local rot="$1" script="$2" dest="$3" lock="$4" log="$5" art="$6" commit="$7"; shift 7
  env PATH="$BIN:$PATH" TRE_TESTE_RAIZ="$REMOTO" TRE_TESTE_LOG="$SHIMLOG" \
      TRE_TESTE_SUMI_REL="${SABOTA_REL:-}" TRE_TESTE_ATRASO_MANIFESTO="${SABOTA_ATRASO:-}" \
      TRE_PUBLICAR_ALVO="sandbox@local" TRE_PUBLICAR_DESTINO="$dest" \
      TRE_PUBLICAR_LOCK="$lock" TRE_PUBLICAR_LOG="$log" \
      TRE_PUBLICAR_ARTEFATO="${ART_ENV-$art}" \
      TRE_PUBLICAR_ALVO_PRODUCAO="$ALVO_PROD" TRE_PUBLICAR_DIFF_DIR="$DIFS" \
      TRE_PUBLICAR_TRAVA=0 HERMES_KANBAN_TASK=t_0f74266d \
      bash "$script" --commit "$commit" --destino "$dest" --dono "$(id -un):$(id -gn)" \
      --card t_0f74266d --permitir-arvore-suja "$@" > "$OUT/$rot.out" 2> "$OUT/$rot.err"
  echo $? > "$OUT/$rot.rc"
}
rc_de()  { cat "$OUT/$1.rc" 2>/dev/null || echo 99; }
tem()    { grep -qF -- "$2" "$OUT/$1.err" 2>/dev/null || grep -qF -- "$2" "$OUT/$1.out" 2>/dev/null; }
sobra_staging() { find "$REMOTO" -maxdepth 1 -name '.publicacao-staging*' 2>/dev/null | wc -l; }
limpa_sobras() { # cada item parte de um "remoto" limpo (o staging fixo da versao antiga e limpo aqui)
  find "$REMOTO" -maxdepth 1 -name '.publicacao-staging*' -exec rm -rf {} + 2>/dev/null || true
  rm -rf "$REMOTO/.publicacao-modos" 2>/dev/null || true
}
SABOTA_REL=""; SABOTA_ATRASO=""; SABOTA_ATRASO_MANIFESTO=""
# ART_ENV: NAO definida => cada item usa o artefato isolado dele; definida e VAZIA => deixa o
# artefato PADRAO do watchdog (so o item G, que testa justamente essa guarda).
export SABOTA_REL SABOTA_ATRASO SABOTA_ATRASO_MANIFESTO

OK=0; FALHAS=0
verifica() { # $1=descricao $2=condicao (0 = passou)
  if [ "$2" -eq 0 ]; then printf 'PASS  %s\n' "$1"; OK=$((OK+1))
  else printf 'FALHA %s\n' "$1"; FALHAS=$((FALHAS+1)); fi
}
resumo_item() { printf '  rc=%s | %s\n' "$(rc_de "$1")" "$2"; }

# ---------------------------------------------------------------- versao antiga e commits
git -C "$REPO" show "$BASE_ANTIGA:deploy/publicar.sh" > "$ROOT/publicar-antigo.sh" || exit 2
chmod +x "$ROOT/publicar-antigo.sh"
ANTIGO="$ROOT/publicar-antigo.sh"
ULTIMO="$(git -C "$REPO" ls-tree -r --name-only "$SHA_A" | LC_ALL=C sort | tail -1)"   # ordena por ultimo
printf 'sandbox: %s\ncommit A: %s\ncommit B: %s\nultimo caminho do manifesto: %s\n\n' \
       "$ROOT" "$SHA_A" "$SHA_B" "$ULTIMO"

echo "== A) defeito (versao antiga, staging fixo): arquivo some no meio do manifesto =="
SABOTA_REL="$ULTIMO"; SABOTA_ATRASO=""; SABOTA_ATRASO_MANIFESTO=""; limpa_sobras
rodar A "$ANTIGO" "$REMOTO/dest-a" "$REMOTO/.lock-a" "$REMOTO/.log-a" "$REMOTO/.art-a" "$SHA_A"
resumo_item A "stderr: $(grep -m1 'PUBLICACAO_FALHOU' "$OUT/A.err" || echo '(sem PUBLICACAO_FALHOU)')"
verifica "A exit 6"                       "$([ "$(rc_de A)" = 6 ] && echo 0 || echo 1)"
verifica "A culpa 'a copia transferida' (lugar errado)" "$(tem A 'a copia transferida nao confere' && echo 0 || echo 1)"
verifica "A NAO nomeia o staging como o manifesto quebrado" \
         "$(tem A 'o manifesto de STAGING' && echo 1 || echo 0)"
verifica "A destino intacto (sem .publicado)"  "$([ ! -f "$REMOTO/dest-a/.publicado" ] && echo 0 || echo 1)"
verifica "A log sem linha de aborto (falha invisivel)" \
         "$(grep -q 'PUBLICACAO_ABORTADA' "$REMOTO/.log-a" 2>/dev/null && echo 1 || echo 0)"

echo
echo "== B) conserto: mesmo cenario, mensagem na fase certa + diff completo + log =="
SABOTA_REL="$ULTIMO"; SABOTA_ATRASO=""; SABOTA_ATRASO_MANIFESTO=""; limpa_sobras
rodar B "$NOVO" "$REMOTO/dest-b" "$REMOTO/.lock-b" "$REMOTO/.log-b" "$REMOTO/.art-b" "$SHA_A" --sem-trava
resumo_item B "$(grep -m1 'PUBLICACAO_FALHOU' "$OUT/B.err" || echo '(sem PUBLICACAO_FALHOU)')"
verifica "B exit 6"                            "$([ "$(rc_de B)" = 6 ] && echo 0 || echo 1)"
verifica "B nomeia a FASE (staging) como incompleta" "$(tem B 'o manifesto de STAGING' && echo 0 || echo 1)"
verifica "B nomeia o ARQUIVO que sumiu"        "$(tem B "$ULTIMO" && echo 0 || echo 1)"
verifica "B diz que a causa e escrita concorrente, nao divergencia" \
         "$(tem B 'escrita/limpeza concorrente' && echo 0 || echo 1)"
verifica "B NAO culpa 'a copia transferida'"   "$(tem B 'a copia transferida nao confere' && echo 1 || echo 0)"
verifica "B diz que o destino segue como estava" "$(tem B 'NADA foi publicado: o destino' && echo 0 || echo 1)"
verifica "B destino intacto (sem .publicado)"  "$([ ! -f "$REMOTO/dest-b/.publicado" ] && echo 0 || echo 1)"
verifica "B deixou linha PUBLICACAO_ABORTADA no log" \
         "$(grep -q 'PUBLICACAO_ABORTADA fase=staging-manifesto-incompleto' "$REMOTO/.log-b" 2>/dev/null && echo 0 || echo 1)"
verifica "B staging removido no fim (trap)"    "$([ "$(sobra_staging)" = 0 ] && echo 0 || echo 1)"

echo
echo "== C) defeito por colisao: 2 publicacoes simultaneas na versao antiga (staging fixo) =="
SABOTA_REL=""; SABOTA_ATRASO=2; SABOTA_ATRASO_MANIFESTO=""; limpa_sobras
rodar C1 "$ANTIGO" "$REMOTO/dest-c1" "$REMOTO/.lock-c1" "$REMOTO/.log-c1" "$REMOTO/.art-c1" "$SHA_A" --permitir-arvore-suja &
pid1=$!
sleep 1
SABOTA_REL=""; SABOTA_ATRASO=2; limpa_sobras
rodar C2 "$ANTIGO" "$REMOTO/dest-c2" "$REMOTO/.lock-c2" "$REMOTO/.log-c2" "$REMOTO/.art-c2" "$SHA_B" --permitir-arvore-suja &
pid2=$!
wait $pid1; wait $pid2
resumo_item C1 "$(grep -m1 'PUBLICACAO_FALHOU\|PUBLICACAO_OK' "$OUT/C1.err" "$OUT/C1.out" || echo '(nada)')"
resumo_item C2 "$(grep -m1 'PUBLICACAO_FALHOU\|PUBLICACAO_OK' "$OUT/C2.err" "$OUT/C2.out" || echo '(nada)')"
verifica "C pelo menos uma das duas falha (staging fixo se mistura)" \
         "$([ "$(rc_de C1)" != 0 ] || [ "$(rc_de C2)" != 0 ] && echo 0 || echo 1)"
verifica "C a que falha culpa 'a copia transferida' (lugar errado)" \
         "$(grep -h 'a copia transferida nao confere' "$OUT/C1.err" "$OUT/C2.err" >/dev/null 2>&1 && echo 0 || echo 1)"
verifica "C a que falha NAO nomeia a fase (staging)" \
         "$(grep -h 'o manifesto de STAGING' "$OUT/C1.err" "$OUT/C2.err" >/dev/null 2>&1 && echo 1 || echo 0)"

echo
echo "== D) conserto por colisao: as mesmas 2 publicacoes simultaneas na versao nova =="
: > "$SHIMLOG"     # a contagem de stagings DIFERENTES olha so este item
SABOTA_REL=""; SABOTA_ATRASO=2; SABOTA_ATRASO_MANIFESTO=""; limpa_sobras
rodar D1 "$NOVO" "$REMOTO/dest-d1" "$REMOTO/.lock-d1" "$REMOTO/.log-d1" "$REMOTO/.art-d1" "$SHA_A" --sem-trava &
pid1=$!
sleep 1
SABOTA_REL=""; SABOTA_ATRASO=2; limpa_sobras
rodar D2 "$NOVO" "$REMOTO/dest-d2" "$REMOTO/.lock-d2" "$REMOTO/.log-d2" "$REMOTO/.art-d2" "$SHA_B" --sem-trava &
pid2=$!
wait $pid1; wait $pid2
resumo_item D1 "$(grep -m1 'PUBLICACAO_OK\|PUBLICACAO_FALHOU' "$OUT/D1.err" "$OUT/D1.out" || echo '(nada)')"
resumo_item D2 "$(grep -m1 'PUBLICACAO_OK\|PUBLICACAO_FALHOU' "$OUT/D2.err" "$OUT/D2.out" || echo '(nada)')"
verifica "D as duas passam (exit 0)"           "$([ "$(rc_de D1)" = 0 ] && [ "$(rc_de D2)" = 0 ] && echo 0 || echo 1)"
verifica "D PUBLICACAO_OK nos dois"            "$(grep -q 'PUBLICACAO_OK' "$OUT/D1.out" && grep -q 'PUBLICACAO_OK' "$OUT/D2.out" && echo 0 || echo 1)"
STGS="$(grep -o '\.publicacao-staging\.[A-Za-z0-9]\{6,\}' "$SHIMLOG" | grep -v 'XXXXXX' | LC_ALL=C sort -u)"
N_STGS="$(printf '%s\n' "$STGS" | grep -c 'publicacao-staging' || true)"
verifica "D as duas usaram stagings DIFERENTES (${N_STGS} nomes: $(printf '%s ' $STGS))" \
         "$([ "$N_STGS" -ge 2 ] && echo 0 || echo 1)"
verifica "D nenhuma sobra de staging"          "$([ "$(sobra_staging)" = 0 ] && echo 0 || echo 1)"
verifica "D nenhum .publicacao-modos fixo sobrou" \
         "$([ ! -e "$REMOTO/.publicacao-modos" ] && echo 0 || echo 1)"

echo
echo "== E) guarda: lock isolado com destino compartilhado =="
rodar E "$NOVO" "$ALVO_PROD" "$REMOTO/.lock-x" "$REMOTO/.log-e" "$REMOTO/.art-e" "$SHA_A" --sem-trava
resumo_item E "$(grep -m1 'PUBLICACAO_FALHOU' "$OUT/E.err" || echo '(sem PUBLICACAO_FALHOU)')"
verifica "E exit 2"                            "$([ "$(rc_de E)" = 2 ] && echo 0 || echo 1)"
verifica "E recusa e explica (lock isolado)"   "$(tem E 'lock isolado' && echo 0 || echo 1)"
verifica "E nada escrito no destino"           "$([ ! -e "$ALVO_PROD/.publicado" ] && echo 0 || echo 1)"

echo
echo "== F) caminho bom: publicar + conferir =="
rodar F "$NOVO" "$REMOTO/dest-f" "$REMOTO/.lock-f" "$REMOTO/.log-f" "$REMOTO/.art-f" "$SHA_A" --sem-trava
resumo_item F "$(grep -m1 'PUBLICACAO_OK\|PUBLICACAO_FALHOU' "$OUT/F.out" "$OUT/F.err" || echo '(nada)')"
verifica "F publicacao exit 0"                 "$([ "$(rc_de F)" = 0 ] && echo 0 || echo 1)"
verifica "F PUBLICACAO_OK"                     "$(grep -q 'PUBLICACAO_OK' "$OUT/F.out" && echo 0 || echo 1)"
verifica "F .publicado aponta o commit"        "$(grep -q "commit: $SHA_A" "$REMOTO/dest-f/.publicado" && echo 0 || echo 1)"
env PATH="$BIN:$PATH" TRE_TESTE_RAIZ="$REMOTO" TRE_TESTE_LOG="$SHIMLOG" \
    TRE_PUBLICAR_ALVO="sandbox@local" TRE_PUBLICAR_DESTINO="$REMOTO/dest-f" \
    TRE_PUBLICAR_LOCK="$REMOTO/.lock-f" TRE_PUBLICAR_LOG="$REMOTO/.log-f" \
    TRE_PUBLICAR_ARTEFATO="$REMOTO/.art-f" TRE_PUBLICAR_DIFF_DIR="$DIFS" \
    bash "$NOVO" --conferir --destino "$REMOTO/dest-f" > "$OUT/F2.out" 2> "$OUT/F2.err"
echo $? > "$OUT/F2.rc"
verifica "F --conferir exit 0"                 "$([ "$(rc_de F2)" = 0 ] && echo 0 || echo 1)"
verifica "F --conferir PUBLICACAO_OK"          "$(grep -q 'PUBLICACAO_OK' "$OUT/F2.out" && echo 0 || echo 1)"
verifica "F staging removido depois do conferir" "$([ "$(sobra_staging)" = 0 ] && echo 0 || echo 1)"

echo "== G) guarda do artefato: destino isolado nao pode sobrescrever o artefato do watchdog =="
ART_ENV=""   # deixa o artefato PADRAO (o que o watchdog da copia compartilhada usa)
limpa_sobras
rodar G "$NOVO" "$REMOTO/dest-g" "$REMOTO/.lock-g" "$REMOTO/.log-g" "$REMOTO/.art-g" "$SHA_A" --sem-trava
unset ART_ENV
resumo_item G "$(grep -m1 'PUBLICACAO_FALHOU' "$OUT/G.err" || echo '(sem PUBLICACAO_FALHOU)')"
verifica "G exit 2"                            "$([ "$(rc_de G)" = 2 ] && echo 0 || echo 1)"
verifica "G recusa e explica (artefato padrao)" "$(tem G 'artefato PADRAO do watchdog' && echo 0 || echo 1)"
verifica "G nada escrito no destino"           "$([ ! -e "$REMOTO/dest-g/.publicado" ] && echo 0 || echo 1)"
verifica "G artefato padrao NAO foi tocado"    "$([ ! -e "$REMOTO/.publicacao-artefato" ] && echo 0 || echo 1)"

echo
echo "== H) --destino por CLI: destino isolado nao e confundido com PRODUCAO (recálculo pos-parse) =="
ALVO_PROD_H="/opt/tre/prod/repo"   # igual ao destino PADRAO: e exatamente o caso que confundia o calculo
h_run() { # $1=rotulo $2=commit
  env PATH="$BIN:$PATH" TRE_TESTE_RAIZ="$REMOTO" TRE_TESTE_LOG="$SHIMLOG" \
      TRE_PUBLICAR_ALVO="sandbox@local" TRE_PUBLICAR_LOCK="$REMOTO/.lock-h" \
      TRE_PUBLICAR_LOG="$REMOTO/.log-h" TRE_PUBLICAR_ARTEFATO="$REMOTO/.art-h" \
      TRE_PUBLICAR_ALVO_PRODUCAO="$ALVO_PROD_H" TRE_PUBLICAR_DIFF_DIR="$DIFS" \
      TRE_PUBLICAR_TRAVA=0 HERMES_KANBAN_TASK=t_0f74266d \
      bash "$NOVO" --commit "$2" --destino "$REMOTO/dest-h" --dono "$(id -un):$(id -gn)" \
      --card t_0f74266d --permitir-arvore-suja --sem-trava > "$OUT/$1.out" 2> "$OUT/$1.err"
  echo $? > "$OUT/$1.rc"
}
limpa_sobras
h_run H1 "$SHA_A"
h_run H2 "$SHA_B"
resumo_item H1 "$(grep -m1 'PUBLICACAO_OK\|PUBLICACAO_FALHOU' "$OUT/H1.out" "$OUT/H1.err" || echo '(nada)')"
resumo_item H2 "$(grep -m1 'PUBLICACAO_OK\|PUBLICACAO_FALHOU' "$OUT/H2.out" "$OUT/H2.err" || echo '(nada)')"
verifica "H1 publica em destino isolado por --destino (exit 0)" "$([ "$(rc_de H1)" = 0 ] && echo 0 || echo 1)"
verifica "H2 troca o commit do MESMO destino --destino (exit 0)" "$([ "$(rc_de H2)" = 0 ] && echo 0 || echo 1)"
verifica "H2 nao pede --producao para destino isolado" "$(tem H2 'e o destino COMPARTILHADO de producao' && echo 1 || echo 0)"
verifica "H .publicado do destino --destino aponta o commit B" \
         "$(grep -q "commit: $SHA_B" "$REMOTO/dest-h/.publicado" && echo 0 || echo 1)"

echo
echo "---------------------------------------------------------------"
echo "PASS=$OK FALHAS=$FALHAS   (saidas cruas em $OUT)"
[ "$FALHAS" -eq 0 ] || exit 1
exit 0
