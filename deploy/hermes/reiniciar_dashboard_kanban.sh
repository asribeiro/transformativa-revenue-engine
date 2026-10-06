#!/usr/bin/env bash
# Reinicia o servico do dashboard para ele carregar o codigo do plugin EM USO.
#
# Por que existe: o `hermes dashboard` importa os modulos do plugin UMA vez, no start, e os mantem em
# memoria. Gravar `plugin_api.py`/`dist/index.js` no disco NAO muda o que o servidor responde ate
# reiniciar — e o sintoma que chega ao dono e' sempre o mesmo: "a tela ainda nao mudou".
#
# Caso REAL que motivated este arquivo (06/10/2026): dashboard com pid iniciado 05/10 20:19 e
# `plugin_api.py` gravado 05/10 20:23 (o backup ao lado chamava-se `...CONTAMINADO-por-reaplicacao`).
# Ou seja: por um dia a tela serviu o backend PRE-conserto mantido em memoria. O script existia so'
# no container — nao sobrevivia a troca de imagem, que e' exatamente o buraco que causou isso.
#
# Duas provas que valem, nesta ordem (o resto engana):
#   1. data do PROCESSO (`ps -o lstart=`) POSTERIOR ao mtime de `plugin_api.py` e de `dist/index.js`;
#   2. ESTADO do arquivo em uso pela cadeia canonica versionada (`compor_plugin_api.py --check`).
# `.pyc` NAO serve: o servico roda com PYTHONDONTWRITEBYTECODE=1 e o `.pyc` da pasta e' residuo velho.
#
# Cuidado historico: a conferencia deste script ja' leu `/opt/hermes/plugins/...` — a copia EMPACOTADA
# na imagem, que fica SOMBREADA e inerte quando existe a do usuario. O plugin em uso e' o de
# `$TRE_PLUGIN_DIR` (get_hermes_home() -> <home>/plugins vence o bundled).
#
# Uso:
#   deploy/hermes/reiniciar_dashboard_kanban.sh --conferir    so' mede (nao toca em nada)
#   deploy/hermes/reiniciar_dashboard_kanban.sh [--reiniciar]  reinicia e confere
#   -h | --help
#
# Variaveis: TRE_PLUGIN_DIR (padrao /opt/data/plugins/kanban/dashboard), TRE_SVC (/run/service/dashboard),
#            TRE_S6 (/command/s6-svc), TRE_ESPERA (segundos apos o pedido; padrao 12).
#
# AVISO: reiniciar o dashboard INVALIDA as sessoes abertas do dono — ele tera' de entrar de novo e
# recarregar a pagina com Ctrl+Shift+R. Nao toca no gateway (a sessao do agente continua de pe).
set -uo pipefail

PLUGIN_DIR="${TRE_PLUGIN_DIR:-/opt/data/plugins/kanban/dashboard}"
SVC="${TRE_SVC:-/run/service/dashboard}"
S6="${TRE_S6:-/command/s6-svc}"
ESPERA="${TRE_ESPERA:-12}"
PY=/opt/hermes/.venv/bin/python3
[ -x "$PY" ] || PY="$(command -v python3)"

falhar() { echo "FALHOU $*" >&2; exit 1; }

MODO="reiniciar"
for arg in "$@"; do
  case "$arg" in
    --conferir) MODO="conferir" ;;
    --reiniciar) MODO="reiniciar" ;;
    -h|--help) sed -n '1,40p' "$0"; exit 0 ;;
    *) falhar "argumento desconhecido: '$arg' (uso: $0 [--conferir|--reiniciar])" ;;
  esac
done

# --- conferencia (somente leitura) -------------------------------------------------------------
conferir() {
  local pid mtime_api mtime_js
  echo "### 1. processo em servico"
  ps -eo pid,lstart,args 2>/dev/null | grep "hermes dashboard" | grep -v grep | sed 's/^/  /'
  pid="$(pgrep -f 'hermes dashboard' | head -1 || true)"

  echo
  echo "### 2. arquivos do plugin EM USO ($PLUGIN_DIR)"
  if [ -f "$PLUGIN_DIR/plugin_api.py" ]; then
    mtime_api="$(date -r "$PLUGIN_DIR/plugin_api.py" '+%Y-%m-%d %H:%M:%S')"
    echo "  plugin_api.py mtime=$mtime_api"
  else
    echo "  AVISO: $PLUGIN_DIR/plugin_api.py nao existe — o servidor esta' carregando outra copia?"
  fi
  if [ -f "$PLUGIN_DIR/dist/index.js" ]; then
    echo "  dist/index.js mtime=$(date -r "$PLUGIN_DIR/dist/index.js" '+%Y-%m-%d %H:%M:%S')"
  fi

  echo
  echo "### 3. as duas provas"
  if [ -n "$pid" ] && [ -n "${mtime_api:-}" ]; then
    local inicio inicio_s mtime_s veredito
    inicio="$(ps -o lstart= -p "$pid" 2>/dev/null)"
    inicio_s="$(date -d "$inicio" +%s 2>/dev/null || echo 0)"
    mtime_s="$(date -r "$PLUGIN_DIR/plugin_api.py" +%s)"
    if [ "$inicio_s" -ge "$mtime_s" ]; then veredito="OK (processo posterior ao arquivo)"; else veredito="VELHO: o processo e' anterior ao plugin_api.py"; fi
    echo "  pid=$pid inicio=$inicio"
    echo "  PROCESSO_vs_ARQUIVO: $veredito"
  else
    echo "  PROCESSO_vs_ARQUIVO: nao medido (falta pid ou arquivo)"
  fi

  echo
  echo "### 4. cadeia canonica versionada (estado do arquivo EM USO)"
  local compor="" cand
  for cand in "$(cd "$(dirname "$0")" && pwd)/plugin-composicao/compor_plugin_api.py" \
              /opt/data/repos/tre-mirror-entrega/deploy/hermes/plugin-composicao/compor_plugin_api.py \
              /opt/data/cache/scratch/lote17A/repo/deploy/hermes/plugin-composicao/compor_plugin_api.py; do
    [ -f "$cand" ] && { compor="$cand"; break; }
  done
  if [ -n "$compor" ]; then
    "$PY" "$compor" --alvo "$PLUGIN_DIR/plugin_api.py" --check 2>&1 | sed 's/^/  /'
  else
    echo "  (cadeia canonica nao encontrada — confira o estado do plugin por outro caminho)"
  fi

  echo
  echo "### 5. colunas que o backend EM USO declara"
  grep -n -A16 "^BOARD_COLUMNS" "$PLUGIN_DIR/plugin_api.py" 2>/dev/null | sed 's/^/  /' || echo "  (arquivo ausente)"
  echo
  echo "### 6. colunas que o frontend EM USO ordena"
  grep -o 'COLUMN_ORDER = \[[^]]*\]' "$PLUGIN_DIR/dist/index.js" 2>/dev/null | head -1 | sed 's/^/  /' || echo "  (bundle ausente)"
}

# --- reinicio -----------------------------------------------------------------------------------
reiniciar() {
  [ -x "$S6" ] || falhar "$S6 nao existe (fora do container? o caminho completo e' obrigatorio)"
  [ -e "$SVC" ] || falhar "servico $SVC nao existe"
  echo "### 1. processo ANTES"
  ps -eo pid,lstart,args 2>/dev/null | grep "hermes dashboard" | grep -v grep | head -3 | sed 's/^/  /'
  echo
  echo "### 2. reiniciando $SVC"
  "$S6" -r "$SVC" && echo "  pedido enviado"
  sleep "$ESPERA"
  echo
  echo "### 3. processo DEPOIS (tem de ter PID e hora novos)"
  ps -eo pid,lstart,args 2>/dev/null | grep "hermes dashboard" | grep -v grep | head -3 | sed 's/^/  /'
  echo
  echo "### 4. dashboard respondendo"
  "$PY" - <<'PYEOF'
import urllib.request
try:
    with urllib.request.urlopen("http://127.0.0.1:4860/", timeout=10) as r:
        print("  HTTP %s" % r.status)
except Exception as exc:
    print("  %s: %s" % (type(exc).__name__, exc))
PYEOF
  echo
  conferir
}

[ "$MODO" = "conferir" ] && { conferir; } || { reiniciar; }
echo
echo "REINICIAR_DASHBOARD=FIM"
[ "$MODO" = "reiniciar" ] && echo "(recarregue a pagina do board — Ctrl+Shift+R se nao atualizar)"
exit 0
