#!/usr/bin/env bash
# =====================================================================================
# teste_ponteiros_de_registro.sh — dente ANTES/DEPOIS do verificador da CLASSE do D04-D01:
# ponteiro de commit citado em doc de registro que nao se alcanca por ref nenhuma.
#
# Motivo (card t_37db9564, recomendacao do defeito t_26be11c7): os DOIS defeitos da classe
# (`t_44cbc48c`/D04-D01 e `t_26be11c7`) foram achados por varredura MANUAL — nenhum verificador
# da esteira olhava para hash de commit em doc de registro (`verificar_estrutura.sh` confere que
# o ARQUIVO esta versionado, nao resolve hash). Este teste prova que a esteira passa a olhar:
#
#   1. ATUAL   — a arvore de trabalho passa: `RESULTADO: PONTEIROS_OK`, exit 0;
#   2. MUTACAO — `--autoteste` planta ponteiro morto NOVO no doc (sem marca, sem crase, com marca
#                na mesma unidade, com marca em OUTRA unidade) + sha256 de conteudo + ponteiro
#                vivo, e exige o veredito de cada mutacao;
#   3. ANTES   — a arvore CONGELADA `3f104ac` (docs da classe ANTES do conserto) REPROVA:
#                `RESULTADO: PONTEIROS_FALHOU`, exit 1, com `f1f1cb6b` (registro L190/L192,
#                runbook L192/L203/L212/L220/L248) e `c41822e` SEM marca — 9 ponteiros mortos,
#                9 sem marca;
#   4. FAIL-CLOSED — janela de varredura vazia nao vira PASS vazio: exit 2;
#   5. CLASSIFICACAO — prefixo ambiguo e identificador sem objeto sao classificados (regra 3),
#                nao reprovados: a ambiguidade e construida em repo descartavel, porque o repo
#                canonico nao tem colisao de prefixo de 7 chars (0 em 2305 objetos, medido).
#
# O lado ANTES e PULADO (NOTA, nao falha) se o objeto congelado nao existir mais no repositorio:
# a prova de mutacao (item 2) independe de historia.
#
# Uso: scripts/teste_ponteiros_de_registro.sh
# Variaveis:
#   RAIZ_REPO     raiz do checkout (default: deduzida do proprio script)
#   PYTHON        interpretador (default: /opt/hermes/.venv/bin/python, senao python3)
#   ARVORE_ANTES  commit congelado do lado "antes" (default: 3f104ac)
#
# Saida: OK/FALHOU/NOTA por item; `RESULTADO: PONTEIROS_TESTE_OK|PONTEIROS_TESTE_FALHOU`.
# Exit: 0 = passou; 1 = falhou.
# =====================================================================================
set -u

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAIZ_REPO="${RAIZ_REPO:-$(cd "$AQUI/.." && pwd)}"
PYTHON="${PYTHON:-/opt/hermes/.venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON="python3"
ARVORE_ANTES="${ARVORE_ANTES:-3f104ac}"
VERIFICADOR="$RAIZ_REPO/scripts/verificar_ponteiros_de_registro.py"

ITENS=0
FALHAS=0
chk() { ITENS=$((ITENS + 1)); if [ "$2" = "1" ]; then echo "OK     $1"; else echo "FALHOU $1"; FALHAS=$((FALHAS + 1)); fi; }
nota() { ITENS=$((ITENS + 1)); echo "NOTA   $1"; }

WORKTREE=""
limpar() { [ -n "$WORKTREE" ] && git -C "$RAIZ_REPO" worktree remove --force "$WORKTREE" >/dev/null 2>&1; [ -n "$WORKTREE" ] && rm -rf "$(dirname "$WORKTREE")"; }
trap limpar EXIT

echo "== raiz:   $RAIZ_REPO"
echo "== python: $PYTHON"
echo "== antes:  $ARVORE_ANTES (arvore congelada dos docs da classe)"

if [ ! -f "$VERIFICADOR" ]; then
  chk "verificador versionado existe ($VERIFICADOR)" 0
  echo
  echo "RESULTADO: PONTEIROS_TESTE_FALHOU ($ITENS itens, $FALHAS falhas)"
  exit 1
fi
chk "verificador versionado existe" 1

# ------------------------------------------------------------------------------------
# 1. ATUAL: a arvore de trabalho passa.
# ------------------------------------------------------------------------------------
SAIDA_ATUAL="$(cd "$RAIZ_REPO" && "$PYTHON" "$VERIFICADOR" 2>&1)"
RC_ATUAL=$?
chk "arvore atual: exit 0 (medido: $RC_ATUAL)" "$([ "$RC_ATUAL" = "0" ] && echo 1 || echo 0)"
chk "arvore atual: RESULTADO PONTEIROS_OK" \
    "$(printf '%s' "$SAIDA_ATUAL" | grep -q 'RESULTADO: PONTEIROS_OK' && echo 1 || echo 0)"
SEM_MARCA_ATUAL="$(printf '%s' "$SAIDA_ATUAL" | grep -c 'SEM MARCA')"
chk "arvore atual: 0 ponteiro morto SEM marca (medido: $SEM_MARCA_ATUAL)" \
    "$([ "$SEM_MARCA_ATUAL" = "0" ] && echo 1 || echo 0)"

# ------------------------------------------------------------------------------------
# 2. MUTACAO: ponteiro morto NOVO plantado no doc — com e sem marca, mesma e outra unidade.
# ------------------------------------------------------------------------------------
SAIDA_MUT="$(cd "$RAIZ_REPO" && "$PYTHON" "$VERIFICADOR" --autoteste 2>&1)"
RC_MUT=$?
chk "autoteste: exit 0 (medido: $RC_MUT)" "$([ "$RC_MUT" = "0" ] && echo 1 || echo 0)"
chk "autoteste: nenhuma mutacao passou em silencio (buraco)" \
    "$(printf '%s' "$SAIDA_MUT" | grep -q 'AUTOTESTE: [0-9]*/[0-9]* mutacoes com o veredito esperado' \
        && ! printf '%s' "$SAIDA_MUT" | grep -q 'FALHOU mutacao' && echo 1 || echo 0)"
printf '%s\n' "$SAIDA_MUT" | grep 'AUTOTESTE:' | sed 's/^/       /'

# ------------------------------------------------------------------------------------
# 3. ANTES: arvore congelada reprova listando os ponteiros mortos sem marca.
# ------------------------------------------------------------------------------------
if git -C "$RAIZ_REPO" cat-file -e "${ARVORE_ANTES}^{commit}" 2>/dev/null; then
  WORKTREE="$(mktemp -d "${TMPDIR:-/tmp}/ponteiros_antes.XXXXXX")/wt"
  if git -C "$RAIZ_REPO" worktree add --detach "$WORKTREE" "$ARVORE_ANTES" >/dev/null 2>&1; then
    SAIDA_ANTES="$("$PYTHON" "$VERIFICADOR" --raiz "$WORKTREE" 2>&1)"
    RC_ANTES=$?
    chk "arvore $ARVORE_ANTES: exit 1 (medido: $RC_ANTES)" "$([ "$RC_ANTES" = "1" ] && echo 1 || echo 0)"
    chk "arvore $ARVORE_ANTES: RESULTADO PONTEIROS_FALHOU" \
        "$(printf '%s' "$SAIDA_ANTES" | grep -q 'RESULTADO: PONTEIROS_FALHOU' && echo 1 || echo 0)"
    N_SEM_MARCA="$(printf '%s' "$SAIDA_ANTES" | grep -c 'SEM MARCA')"
    chk "arvore $ARVORE_ANTES: 9 ponteiros mortos SEM marca (medido: $N_SEM_MARCA)" \
        "$([ "$N_SEM_MARCA" = "9" ] && echo 1 || echo 0)"
    chk "arvore $ARVORE_ANTES: lista f1f1cb6b em registro-de-execucoes.md:190 e :192" \
        "$(printf '%s' "$SAIDA_ANTES" | grep -q 'registro-de-execucoes.md:190 f1f1cb6b' \
            && printf '%s' "$SAIDA_ANTES" | grep -q 'registro-de-execucoes.md:192 f1f1cb6b' && echo 1 || echo 0)"
    chk "arvore $ARVORE_ANTES: lista f1f1cb6b em runbook L192/L203/L212/L220/L248" \
        "$(for l in 192 203 212 220 248; do printf '%s' "$SAIDA_ANTES" | grep -q "backup-restore-rollback.md:$l f1f1cb6b" || exit 1; done; echo 1)"
    chk "arvore $ARVORE_ANTES: lista c41822e SEM marca em registro-de-execucoes.md" \
        "$(printf '%s' "$SAIDA_ANTES" | grep -q 'registro-de-execucoes.md:320 c41822e' \
            && printf '%s' "$SAIDA_ANTES" | grep -q 'c41822e ponteiro morto (commit fora de ref nenhuma) SEM MARCA' && echo 1 || echo 0)"
    chk "arvore $ARVORE_ANTES: resumo 9 mortos / 9 sem marca" \
        "$(printf '%s' "$SAIDA_ANTES" | grep -q '9 ponteiro(s) morto(s) citado(s) — 0 marcado(s), 9 sem marca' && echo 1 || echo 0)"
  else
    chk "worktree temporario de $ARVORE_ANTES" 0
  fi
else
  nota "objeto $ARVORE_ANTES ausente no repositorio: lado ANTES PULADO (a prova de mutacao acima segue valendo)"
fi

# ------------------------------------------------------------------------------------
# 4. FAIL-CLOSED: glob errado nao vira PASS vazio.
# ------------------------------------------------------------------------------------
SAIDA_VAZIO="$(cd "$RAIZ_REPO" && "$PYTHON" "$VERIFICADOR" --docs 'docs/inexistente-*/*.md' 2>&1)"
RC_VAZIO=$?
chk "janela de varredura vazia: exit 2 (medido: $RC_VAZIO)" "$([ "$RC_VAZIO" = "2" ] && echo 1 || echo 0)"
chk "janela de varredura vazia: mensagem FAIL-CLOSED" \
    "$(printf '%s' "$SAIDA_VAZIO" | grep -q 'FAIL-CLOSED' && echo 1 || echo 0)"

# ------------------------------------------------------------------------------------
# 5. CLASSIFICACAO (regra 3): prefixo ambiguo e identificador sem objeto NAO reprovam e nao
#    passam em silencio — sao classificados. O repo canonico nao tem colisao de prefixo de 7
#    chars (medido: 0 em 2305 objetos), entao a ambiguidade e construida em repo DESCARTABLE
#    (2 blobs com prefixo de 4 chars colidido) e a classificacao e chamada no resolvedor.
# ------------------------------------------------------------------------------------
TMPREPO="$(mktemp -d "${TMPDIR:-/tmp}/ponteiros_amb.XXXXXX")/repo"
git init -q "$TMPREPO" >/dev/null 2>&1
CLASSES="$("$PYTHON" - "$VERIFICADOR" "$TMPREPO" <<'PY'
import hashlib
import importlib.util
import pathlib
import subprocess
import sys

verificador, repo = sys.argv[1], sys.argv[2]
spec = importlib.util.spec_from_file_location("vp", verificador)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def corpo(i):
    return ("blob %d\n" % i).encode()


def sha_de(c):
    return hashlib.sha1(b"blob %d\x00" % len(c) + c).hexdigest()


# colisao de prefixo curto, determinista e local (nao toca o object DB do repo canonico).
# O git so resolve/desambigua a partir de 4 hex (`rev-parse --disambiguate`), entao a busca
# comeca em 4 — abaixo disso `cat-file -t` responde "not a valid object name", nao "ambiguous".
vistos, colisao = {}, None
for i in range(1, 8000):
    c = corpo(i)
    sha = sha_de(c)
    for n in range(4, 9):
        p = sha[:n]
        if p in vistos and vistos[p][0] != sha:
            colisao = (p, vistos[p], (sha, c))
            break
        vistos.setdefault(p, (sha, c))
    if colisao:
        break
if not colisao:
    print("SEM-COLISAO")
    sys.exit(0)
prefixo, (sha_a, corpo_a), (sha_b, corpo_b) = colisao
for corpo_ in (corpo_a, corpo_b):
    subprocess.run(["git", "-C", repo, "hash-object", "-w", "--stdin"], input=corpo_,
                   capture_output=True)

r = mod.Repo(pathlib.Path(repo))
amb, _ = r.resolver(prefixo)
ausente, _ = r.resolver("deadbeef1234567890abcdef1234567890abcdef")
print(f"{amb} {ausente} | prefixo ambiguo medido = {prefixo} ({sha_a[:8]}/{sha_b[:8]})")
PY
)"
chk "prefixo ambiguo classificado como 'ambiguo' (nao reprovado): ${CLASSES:-$(echo vazio)}" \
    "$(printf '%s' "$CLASSES" | grep -q '^ambiguo ' && echo 1 || echo 0)"
chk "identificador sem objeto classificado como 'ausente' (nao reprovado)" \
    "$(printf '%s' "$CLASSES" | grep -q 'ausente' && echo 1 || echo 0)"
rm -rf "$(dirname "$TMPREPO")"

echo
if [ "$FALHAS" = "0" ]; then
  echo "RESULTADO: PONTEIROS_TESTE_OK ($ITENS itens, 0 falhas)"
  exit 0
fi
echo "RESULTADO: PONTEIROS_TESTE_FALHOU ($ITENS itens, $FALHAS falhas)"
exit 1
