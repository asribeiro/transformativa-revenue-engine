#!/usr/bin/env bash
# Publicacao versionada da copia operacional do TRE.
#
# Card: t_091cfea9 (DEFEITO F3 do TRE-W1-E06-T01) — a copia operacional /opt/tre/repo era
# sobrescrita por `tar -cz ... | ssh ... 'tar -xz'` de qualquer card: o ultimo a sincronizar
# mandava, o modo vinha do checkout (nao do git) e nao havia registro de qual commit estava
# publicado. Este script e o **unico** caminho de publicacao: publica um COMMIT (nunca a arvore
# de trabalho), preserva o modo do git, recusa arvore suja e grava o commit em `.publicado`.
#
# Uso:
#   deploy/publicar.sh --commit <sha|ref>      publica o commit na copia operacional
#   deploy/publicar.sh --conferir              confere a copia contra o commit registrado
#   deploy/publicar.sh --travar|--destravar    arma/desarma a trava de imutabilidade (chattr +i)
#   deploy/publicar.sh --manifesto <dir>       (interno) imprime o manifesto de um diretorio
#
# Enforcement (card t_daca4bda, recorrencia do defeito t_091cfea9): o caminho unico so vale se
# escrita ad-hoc FALHAR. Este script (a) deixa o commit publicado em artifact root-only FORA da
# copia ($ARTEFATO) — com isso o watchdog da VPS restaura a copia sem git; (b) arma `chattr +i`
# na copia publicada, entao `tar -xz`/`rsync` ad-hoc de outro card da EPERM em vez de reverter a
# copia em silencio; (c) desarma so durante a troca. O watchdog (`deploy/watchdog-publicacao.sh`,
# timer de 2 min na VPS) confere, alerta e repara. `--sem-trava` (ou TRE_PUBLICAR_TRAVA=0)
# publica sem armar a trava.
#
# Staging UNICO por publicacao (card t_0f74266d, defeito do staging em caminho fixo): a
# transferencia cria o staging com `mktemp -d` ao lado do DESTINO — um por destino e por
# execucao — e o remove no fim e no trap. Com o caminho fixo (/opt/tre/.publicacao-staging),
# duas publicacoes simultaneas se misturavam: arquivo listado pelo `find` sumia antes do
# `sha256sum` (~280 linhas "No such file or directory"), a falha era INTERMITENTE e a mensagem
# culpava "a copia transferida" quando o manifesto incompleto era o do STAGING. O mapa de modos
# tambem era fixo (/opt/tre/.publicacao-modos) e ficava para tras na falha: agora e irmao do
# staging e sai junto com ele.
#
# Saida final (uma linha, para automatizar):
#   PUBLICACAO_OK commit=<sha> digest=<sha256> arquivos=<n>   -> a copia E o commit
#   PUBLICACAO_DIVERGENTE ...                                 -> a copia NAO e o commit (exit 5)
#   PUBLICACAO_INDETERMINADA ...                              -> manifesto ilegivel/incompleto:
#                                                                nao da para afirmar divergencia
#   PUBLICACAO_FALHOU ...                                     -> nao publicou nada (exit != 0)
set -euo pipefail

AUTO="$(cd "$(dirname "${BASH_SOURCE[0]:-publicar.sh}")" 2>/dev/null && pwd)/$(basename "${BASH_SOURCE[0]:-publicar.sh}")"

ALVO="${TRE_PUBLICAR_ALVO:-root@169.58.24.102}"
DESTINO="${TRE_PUBLICAR_DESTINO:-/opt/tre/prod/repo}"
DONO="${TRE_PUBLICAR_DONO:-tre-deploy:tre-deploy}"
CHAVE="${TRE_SSH_CHAVE:-}"
if [ -z "$CHAVE" ]; then
  for c in "$HOME/.ssh/id_ed25519_ops" /opt/data/home/.ssh/id_ed25519_ops; do
    [ -f "$c" ] && { CHAVE="$c"; break; }
  done
fi
OPCOES_CHAVE=()
[ -n "$CHAVE" ] && OPCOES_CHAVE=(-i "$CHAVE")
CARD="${HERMES_KANBAN_TASK:-desconhecido}"
LOCK_PADRAO="/opt/tre/.publicacao.lock"
LOCK_REMOTO="${TRE_PUBLICAR_LOCK:-$LOCK_PADRAO}"
LOG_REMOTO="${TRE_PUBLICAR_LOG:-/opt/tre/.publicacoes.log}"
ARTEFATO_PADRAO="/opt/tre/.publicacao-artefato"
ARTEFATO="${TRE_PUBLICAR_ARTEFATO:-$ARTEFATO_PADRAO}"
TRAVA="${TRE_PUBLICAR_TRAVA:-1}"
# Rotulo de origem gravado no .publicado. Vazio = derivado do proprio commit (ver REF abaixo).
REF_DECLARADO="${TRE_PUBLICAR_REF:-}"
# O destino compartilhado e PRODUCAO (e o alvo do ExecStart dos timers). Substituir o commit que
# esta no ar la exige declaracao explicita (--producao / TRE_PUBLICAR_PRODUCAO=1): foi assim, sem
# querer, que a copia perdeu a correcao do backup (t_daca4bda). Destino de ensaio (TRE_PUBLICAR_DESTINO)
# nao pede nada.
ALVO_PRODUCAO="${TRE_PUBLICAR_ALVO_PRODUCAO:-/opt/tre/prod/repo}"
PRODUCAO=0
PRODUCAO_OK="${TRE_PUBLICAR_PRODUCAO:-0}"
LOCK_VALIDADE_S=1800
# Onde gravar o diff COMPLETO dos manifestos quando houver divergencia (o head -30 escondia o
# outro lado do diff — defeito t_0f74266d). Nome com carimbo de tempo: nao sobrescreve evidencia.
DIF_DIR="${TRE_PUBLICAR_DIFF_DIR:-${TMPDIR:-/tmp}}"

COMMIT=""; REF=""; ACAO="publicar"; ENSAIO=0; PERMITIR_SUJA=0; EXIGIR_MODOS=0; FORCAR_LOCK=0
MANIFESTO_DIR=""; NORM_DIR=""; NORM_MAPA=""
# Staging UNICO por publicacao (defeito t_0f74266d): vazio ate a transferencia comecar. O trap
# limpa o staging em QUALQUER saida (inclusive abort), entao nao sobra caminho fixo para tras.
STG=""; MAPA_REMOTO=""; STAGING_VIVO=0

uso() {
  cat <<'TXT'
Publicacao versionada da copia operacional do TRE (unico caminho de publicacao).

Uso:
  deploy/publicar.sh --commit <sha|ref> [opcoes]    publica um commit
  deploy/publicar.sh --conferir [opcoes]            confere a copia contra .publicado
  deploy/publicar.sh --travar [opcoes]              arma chattr +i na copia (escrita ad-hoc falha)
  deploy/publicar.sh --destravar [opcoes]           desarma chattr +i na copia
  deploy/publicar.sh --manifesto <dir>              (interno) manifesto de um diretorio
  deploy/publicar.sh --normalizar-modos <dir> <mapa> (interno) aplica o modo do git em <dir>

Opcoes:
  --commit <sha|ref>       commit a publicar (padrao: HEAD)
  --ref <nome>             rotulo de origem gravado no .publicado (ex.: homolog). Use quando o
                           commit tem mais de um branch: sem ele o rotulo lista todos os nomes.
  --alvo <user@host>       destino ssh (padrao: root@169.58.24.102)
  --destino <dir>          diretorio da copia operacional (padrao: /opt/tre/prod/repo)
  --dono <user:group>      dono final da copia (padrao: tre-deploy:tre-deploy)
  --chave <arquivo>        chave ssh (padrao: ~/.ssh/id_ed25519_ops)
  --card <id>              card que publica (padrao: $HERMES_KANBAN_TASK)
  --producao               declara que a publicacao SUBSTITUI o commit que a producao executa
                           (obrigatorio para trocar o commit do destino compartilhado)
  --sem-trava              nao arma a trava de imutabilidade nesta publicacao
  --ensaio                 mostra o que faria, sem escrever no destino
  --permitir-arvore-suja   publica mesmo com arquivo versionado modificado (conteudo continua
                           vindo do git; o desvio fica registrado em .publicado)
  --exigir-modos           falha (exit 4) se um ExecStart de unit nao for executavel no commit
  --forcar-lock            derruba lock obsoleto de outra publicacao
  -h|--help                esta ajuda

Variaveis: TRE_PUBLICAR_DESTINO (copia de teste/isolada), TRE_PUBLICAR_ARTEFATO,
           TRE_PUBLICAR_STAGING_BASE (onde criar o staging unico; padrao: diretorio pai do
           destino), TRE_PUBLICAR_DIFF_DIR (onde gravar o diff completo dos manifestos;
           padrao: $TMPDIR), TRE_PUBLICAR_TRAVA=0 (nao armar), TRE_PUBLICAR_PRODUCAO=1 (= --producao),
           TRE_PUBLICAR_ALVO_PRODUCAO (destino considerado producao; padrao /opt/tre/prod/repo),
           TRE_SSH_CHAVE, TRE_PUBLICAR_LOG, TRE_PUBLICAR_LOCK.
           Teste SEMPRE em destino isolado: o destino compartilhado e PRODUCAO e trocar o commit
           dele exige --producao declarado (com a aprovacao registrada). Isolar SO o lock
           (TRE_PUBLICAR_LOCK) mantendo o destino compartilhado e recusado: sem o lock padrao as
           duas publicacoes escreveriam no mesmo destino (defeito t_0f74266d). Destino isolado
           exige TAMBEM artefato isolado: o artefato padrao e a referencia do watchdog da copia
           compartilhada, que repararia a PRODUCAO para o commit do ensaio.

Codigos de saida: 0 OK | 1 falha | 2 uso/precondicao | 3 lock ocupado | 4 modos | 5 divergencia | 6 transferencia | 7 manifesto incompleto (--manifesto, interno)
TXT
}

while [ $# -gt 0 ]; do
  case "$1" in
    --commit)     COMMIT="${2:-}"; shift 2;;
    --ref)        REF_DECLARADO="${2:-}"; shift 2;;
    --alvo)       ALVO="${2:-}"; shift 2;;
    --destino)    DESTINO="${2:-}"; shift 2;;
    --dono)       DONO="${2:-}"; shift 2;;
    --chave)      CHAVE="${2:-}"; shift 2;;
    --card)       CARD="${2:-}"; shift 2;;
    --conferir)   ACAO="conferir"; shift;;
    --travar)     ACAO="travar"; shift;;
    --destravar)  ACAO="destravar"; shift;;
    --sem-trava)  TRAVA=0; shift;;
    --manifesto)  ACAO="manifesto"; MANIFESTO_DIR="${2:-}"; shift 2;;
    --normalizar-modos) ACAO="normalizar-modos"; NORM_DIR="${2:-}"; NORM_MAPA="${3:-}"; shift 3;;
    --ensaio)     ENSAIO=1; shift;;
    --producao)   PRODUCAO_OK=1; shift;;
    --permitir-arvore-suja) PERMITIR_SUJA=1; shift;;
    --exigir-modos) EXIGIR_MODOS=1; shift;;
    --forcar-lock) FORCAR_LOCK=1; shift;;
    -h|--help)    uso; exit 0;;
    *) echo "FALHOU argumento desconhecido: $1" >&2; uso >&2; exit 2;;
  esac
done

# ---------------------------------------------------------------- guarda: lock isolado exige destino isolado
# `PRODUCAO` e calculado DEPOIS de ler os argumentos: `--destino` (e nao so TRE_PUBLICAR_DESTINO)
# muda o destino, e medir isso antes do parse fazia o destino isolado passar por producao (a guarda
# de producao pedia --producao para um ensaio) e o inverso deixava a guarda de artefato dormir.
PRODUCAO=0
[ "$DESTINO" = "$ALVO_PRODUCAO" ] && PRODUCAO=1
# Reproducao medida (card t_c9a44f85, defeito t_0f74266d): isolar o LOCK (TRE_PUBLICAR_LOCK) e
# manter o DESTINO compartilhado tira a exclusao mutua SEM tirar o alvo — duas publicacoes
# escrevem no mesmo destino ao mesmo tempo. Fail-closed, antes de qualquer escrita.
if [ "$ACAO" = "publicar" ] && [ "$DESTINO" = "$ALVO_PRODUCAO" ] && [ "$LOCK_REMOTO" != "$LOCK_PADRAO" ]; then
  echo "PUBLICACAO_FALHOU lock isolado ($LOCK_REMOTO) com o destino COMPARTILHADO ($DESTINO): a" >&2
  echo "                  exclusao mutua desse destino e o lock padrao ($LOCK_PADRAO). Para ensaiar," >&2
  echo "                  isole TAMBEM o destino (TRE_PUBLICAR_DESTINO) — foi isolando so o lock que" >&2
  echo "                  duas publicacoes concorrentes se misturaram (defeito t_0f74266d)." >&2
  exit 2
fi

# Mesma familia (caminho FIXO compartilhado), achado ao medir o staging: $ARTEFATO e a fonte de
# verdade do watchdog da copia COMPARTILHADA. Publicar em destino isolado com o artefato padrao
# faz o watchdog da copia de producao (/opt/tre/prod/repo) reparar A PRODUCAO para o commit do ensaio. Fail-closed.
if [ "$ACAO" = "publicar" ] && [ "$PRODUCAO" -ne 1 ] && [ "$ARTEFATO" = "$ARTEFATO_PADRAO" ]; then
  echo "PUBLICACAO_FALHOU destino isolado ($DESTINO) com o artefato PADRAO do watchdog" >&2
  echo "                  ($ARTEFATO): o watchdog de $ALVO_PRODUCAO usa esse artefato como" >&2
  echo "                  referencia e repararia a PRODUCAO para o commit deste ensaio." >&2
  echo "                  Isole TAMBEM o artefato: TRE_PUBLICAR_ARTEFATO=\$DESTINO-artefato." >&2
  exit 2
fi

# ---------------------------------------------------------------- manifesto
# Manifesto = "<modo> <sha256-do-conteudo> <caminho>", ordenado por caminho.
# O sha256 do manifesto e o DIGEST DA ARVORE: o mesmo commit tem de dar o mesmo digest em
# qualquer maquina, e a copia operacional tem de dar exatamente esse digest.
# `.publicado` e `.publicado.manifest` ficam de fora por serem METADADOS da publicacao
# (nao existem no commit); tudo o mais tem de estar identico.
manifesto_de() { # $1 = dir. Imprime "<modo> <sha256> <caminho>" ordenado por caminho.
  # Guarda contra manifesto INCOMPLETO (defeito t_0f74266d): se o `find` lista um arquivo e ele
  # desaparece/fecha antes do `stat`/`sha256sum` (escrita/limpeza concorrente no diretorio), o
  # manifesto saia com uma linha malformada (hash vazio) e a comparacao acusava divergencia de
  # CONTEUDO. Aqui a linha vira "ILEGIVEL - <caminho>" e o manifesto inteiro e reprovado com
  # exit 7 — quem compara decide a mensagem e o exit code, e nunca compara manifesto quebrado.
  # Contar so as LINHAS nao bastava: o defeito real mantinha a contagem e zerava o campo do hash.
  local dir="$1" tmp rc
  [ -d "$dir" ] || { echo "FALHOU diretorio inexistente: $dir" >&2; return 1; }
  tmp="$(mktemp -d "${TMPDIR:-/tmp}/manifesto.XXXXXX")"
  # O laco roda DENTRO do `cd "$dir"` (os caminhos do manifesto sao relativos ao diretorio): fora
  # dele o `stat`/`sha256sum` leria os caminhos do diretorio de trabalho de quem chama.
  (
    cd "$dir" || exit 1
    LC_ALL=C find . -type f \
        ! -name '.publicado' ! -name '.publicado.manifest' -printf '%P\n' \
      | LC_ALL=C sort > "$tmp/lista"
    n_listados="$(grep -c . "$tmp/lista" || true)"
    : > "$tmp/saida"
    while IFS= read -r p; do
      [ -n "$p" ] || continue
      mod=""; h=""
      if ! mod="$(stat -c '%a' -- "$p" 2>/dev/null)"; then mod=""; fi
      if ! h="$(sha256sum -- "$p" 2>/dev/null | cut -d' ' -f1)"; then h=""; fi
      case "$h" in (*[!0-9a-f]*|"") h="";; esac
      if [ -z "$mod" ] || [ -z "$h" ]; then
        printf '%s %s %s\n' "ILEGIVEL" "-" "$p" >> "$tmp/saida"
      else
        printf '%s %s %s\n' "$mod" "$h" "$p" >> "$tmp/saida"
      fi
    done < "$tmp/lista"
    n_ilegiveis="$(grep -c '^ILEGIVEL ' "$tmp/saida" || true)"
    if [ "$n_ilegiveis" -gt 0 ]; then
      echo "MANIFESTO_INCOMPLETO dir=$dir listados=$n_listados ilegiveis=$n_ilegiveis" >&2
      grep -m10 '^ILEGIVEL ' "$tmp/saida" >&2 || true
      exit 7
    fi
    cat "$tmp/saida"
  )
  rc=$?
  rm -rf "$tmp"
  return "$rc"
}

if [ "$ACAO" = "manifesto" ]; then
  [ -n "$MANIFESTO_DIR" ] || { echo "FALHOU --manifesto exige um diretorio" >&2; exit 2; }
  manifesto_de "$MANIFESTO_DIR"
  exit 0
fi

# Normaliza o modo de uma arvore extraida com o modo EXATO do git (100755 -> 755, 100644 -> 644).
# O `tar` extrai o modo do arquivo mascarado pelo umask de quem extrai (por isso a copia nascia 775
# onde o git diz 755). Aqui o mapa vem de `git ls-tree`, entao o modo publicado e o modo do git —
# em qualquer maquina e com qualquer umask. Uso: --normalizar-modos <dir> <arquivo-do-mapa>
if [ "$ACAO" = "normalizar-modos" ]; then
  [ -n "$NORM_DIR" ] && [ -n "$NORM_MAPA" ] || { echo "FALHOU --normalizar-modos exige <dir> <mapa>" >&2; exit 2; }
  [ -d "$NORM_DIR" ] || { echo "FALHOU diretorio inexistente: $NORM_DIR" >&2; exit 2; }
  [ -f "$NORM_MAPA" ] || { echo "FALHOU mapa inexistente: $NORM_MAPA" >&2; exit 2; }
  while IFS=' ' read -r m p; do
    [ -n "$p" ] || continue
    case "$m" in
      100755) chmod 755 "$NORM_DIR/$p" 2>/dev/null || true;;
      100644) chmod 644 "$NORM_DIR/$p" 2>/dev/null || true;;
    esac
  done < "$NORM_MAPA"
  find "$NORM_DIR" -type d -exec chmod 755 {} + 2>/dev/null || true
  exit 0
fi

digest_de() { sha256sum | cut -d' ' -f1; }

R() {
  # ControlMaster: UMA conexao TCP para a publicacao inteira. Sao ~25 chamadas remotas por
  # publicacao; uma conexao por chamada (o que este script fazia) e o que derrubava o acesso:
  # medido em 30/09, o SSH da VPS passou a responder `Connection refused` para o host inteiro
  # (todos os cards) por ~12 min depois de uma rodada de publicacoes — assinatura de penalidade
  # por fonte (OpenSSH PerSourcePenalties)/fail2ban, agravada por retentativas. O socket fica em
  # $TRE_SSH_CONTROLE (padrao /tmp) e e reaproveitado entre chamadas do mesmo script.
  ssh "${OPCOES_CHAVE[@]}" -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
      -o ConnectTimeout=15 -o ControlMaster=auto -o ControlPersist="${TRE_SSH_CONTROLE_PERSIST:-30}" \
      -o ControlPath="${TRE_SSH_CONTROLE:-/tmp/tre-ssh-control-%r@%h:%p}" "$ALVO" "$@"
}
# O proprio script e enviado por stdin: funciona antes da primeira publicacao (quando o
# destino ainda nao tem o script) e nao depende de o destino ter git.
manifesto_remoto() {
  local dir="$1"
  R bash -s -- --manifesto "$dir" < "$AUTO"
}

# ---------------------------------------------------------------- falhas que dizem ONDE doeram
# Manifesto INCOMPLETO (exit 7) NAO e divergencia de conteudo: e o diretorio sendo escrito por
# outro processo enquanto o manifesto era montado. A mensagem tem de nomear a fase e o arquivo —
# era isso que faltava (defeito t_0f74266d: culpava "a copia transferida" e o quebrado era o staging).
limpar_staging() { # trap: o staging UNICO nunca fica para tras, nem quando a publicacao aborta
  [ "${STAGING_VIVO:-0}" -eq 1 ] || return 0
  STAGING_VIVO=0
  R "rm -rf '${STG:-/nenhum}' '${STG:-/nenhum}.modos'" >/dev/null 2>&1 || true
}

falhar_manifesto() { # $1 = fase, $2 = arquivo com o stderr do manifesto, $3 = diretorio
  local fase="$1" err="$2" dir="$3"
  echo "PUBLICACAO_FALHOU o manifesto de $fase ficou INCOMPLETO — arquivo listado que sumiu ou ficou" >&2
  echo "                  ilegivel entre o find e o sha256sum (nao e divergencia de conteudo):" >&2
  grep -m1 '^MANIFESTO_INCOMPLETO' "$err" 2>/dev/null | sed 's/^/                  /' >&2 || true
  grep -m10 '^ILEGIVEL ' "$err" 2>/dev/null | sed 's/^/                  /' >&2 || true
  echo "                  causa: escrita/limpeza concorrente em $dir durante a montagem do manifesto." >&2
}

registrar_aborto() { # $1 = fase, $2 = motivo. A falha nao deixava linha nenhuma no log (auditoria).
  local fase="$1" motivo="$2"
  R "printf '%s\n' '$(date -u +%Y-%m-%dT%H:%M:%SZ) PUBLICACAO_ABORTADA fase=$fase motivo=$motivo commit=$SHA card=$CARD destino=$DESTINO' >> '$LOG_REMOTO'" >/dev/null 2>&1 || true
}

dif_manifestos() { # $1 = rotulo, $2 = esperado, $3 = obtido. Diff COMPLETO em arquivo + head -200.
  local rot="$1" esp="$2" obt="$3" arq n
  mkdir -p "$DIF_DIR" 2>/dev/null || true
  arq="$DIF_DIR/publicacao-diff-$rot-$(date -u +%Y%m%dT%H%M%SZ)-$$.txt"
  printf '%s\n' "$esp" > "$arq.esperado"
  printf '%s\n' "$obt" > "$arq.obtido"
  diff -u "$arq.esperado" "$arq.obtido" > "$arq" 2>&1 || true
  rm -f "$arq.esperado" "$arq.obtido"
  n="$(grep -c '^[+-][^+-]' "$arq" 2>/dev/null || true)"
  echo "--- diferencas ($rot): ${n:-0} linha(s) — diff COMPLETO em $arq" >&2
  head -200 "$arq" >&2 || true
  [ "${n:-0}" -le 200 ] || echo "--- (o resto segue no mesmo arquivo: $arq)" >&2
}

# ---------------------------------------------------------------- trava de imutabilidade
# Escrita ad-hoc na copia publicada (tar/rsync/cp de outro card, o defeito t_daca4bda) passa a
# FALHAR com EPERM em vez de reverter a copia em silencio. Fugir da trava exige `chattr -i`
# explicito — o erro deixa de ser silencioso, que era o problema.
travar_remoto()    { R "chattr -R +i '$DESTINO' 2>/dev/null || true"; }
destravar_remoto() { R "chattr -R -i '$DESTINO' 2>/dev/null || true"; }
trava_estado() { # le a trava do diretorio raiz (somente leitura)
  R "lsattr -d '$DESTINO' 2>/dev/null | awk '{print \$1}' | grep -q i && echo travada || echo ausente"
}
if [ "$ACAO" = "travar" ] || [ "$ACAO" = "destravar" ]; then
  if [ "$ACAO" = "travar" ]; then
    travar_remoto
    # prova funcional: a trava so vale se criar arquivo novo for RECUSADO
    ESTADO="$(R "if ( : > '$DESTINO/.trava-probe' ) 2>/dev/null; then rm -f '$DESTINO/.trava-probe'; echo gravavel; else echo travada; fi")"
    [ "$ESTADO" = "travada" ] || { echo "PUBLICACAO_FALHOU a trava nao pegou em $ALVO:$DESTINO (a copia continua gravavel)" >&2; exit 6; }
    echo "PUBLICACAO_TRAVADA destino=$DESTINO em=$(date -u +%Y-%m-%dT%H:%M:%SZ) (escrita ad-hoc falha com EPERM)"
    exit 0
  fi
  destravar_remoto
  echo "PUBLICACAO_DESTRAVADA destino=$DESTINO em=$(date -u +%Y-%m-%dT%H:%M:%SZ) (copia gravavel ate nova publicacao)"
  exit 0
fi

# ---------------------------------------------------------------- pre-condicoes locais
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
  echo "FALHOU nao estou num repositorio git (a publicacao sai do git, nunca da arvore de trabalho)" >&2; exit 2; }

extrair_arvore() { # $1 = sha, $2 = diretorio de trabalho (recebe commit.tar e arvore/)
  git archive --format=tar "$1" > "$2/commit.tar"
  mkdir -p "$2/arvore"
  tar --same-permissions -xf "$2/commit.tar" -C "$2/arvore"
}

extrair_e_normalizar() { # $1 = sha, $2 = diretorio de trabalho
  extrair_arvore "$1" "$2"
  git ls-tree -r "$1" | awk '{print $1" "$4}' > "$2/modos.txt"
  "$AUTO" --normalizar-modos "$2/arvore" "$2/modos.txt"
}

# ---------------------------------------------------------------- --conferir (verificacao pos-publicacao)
# Le o commit registrado em .publicado e compara a copia operacional COM ESSE COMMIT, arquivo a
# arquivo e modo a modo. E a prova de que a copia nao foi reescrita por fora do caminho unico.
if [ "$ACAO" = "conferir" ]; then
  PUB="$(R "cat '$DESTINO/.publicado' 2>/dev/null" || true)"
  if [ -z "$PUB" ]; then
    echo "PUBLICACAO_DIVERGENTE sem .publicado em $ALVO:$DESTINO — nao ha registro de qual commit" >&2
    echo "                     esta publicado (o defeito). Nada foi conferido." >&2
    exit 5
  fi
  COMMIT_REG="$(printf '%s' "$PUB" | sed -n 's/^commit:[[:space:]]*//p' | head -1)"
  DIG_REG="$(printf '%s' "$PUB" | sed -n 's/^digest:[[:space:]]*//p' | head -1)"
  case "$COMMIT_REG" in (*[!0-9a-f]*|"") echo "PUBLICACAO_DIVERGENTE .publicado sem commit valido: '$COMMIT_REG'" >&2; exit 5;; esac
  SHA_REG="$(git rev-parse --verify "$COMMIT_REG^{commit}" 2>/dev/null)" || {
    echo "PUBLICACAO_DIVERGENTE o commit registrado ($COMMIT_REG) nao existe neste repositorio —" >&2
    echo "                     nao da para conferir; publique um commit que exista aqui." >&2; exit 5; }
  TMPC="$(mktemp -d "${TMPDIR:-/tmp}/conferir.XXXXXX")"
  trap 'rm -rf "$TMPC"' EXIT
  extrair_e_normalizar "$SHA_REG" "$TMPC"
  RC_MAN=0
  MAN_ESPERADO="$(manifesto_de "$TMPC/arvore" 2>"$TMPC/err-man-local")" || RC_MAN=$?
  if [ "$RC_MAN" -ne 0 ]; then
    falhar_manifesto "ARVORE LOCAL do commit $SHA_REG (extraida com git archive)" "$TMPC/err-man-local" "$TMPC/arvore"
    echo "PUBLICACAO_INDETERMINADA a conferencia NAO pode concluir (o local nao deu um manifesto confiavel)." >&2
    exit 5
  fi
  RC_MAN=0
  MAN_ATUAL="$(manifesto_remoto "$DESTINO" 2>"$TMPC/err-man-dest")" || RC_MAN=$?
  if [ "$RC_MAN" -ne 0 ]; then
    falhar_manifesto "COPIA OPERACIONAL ($ALVO:$DESTINO)" "$TMPC/err-man-dest" "$DESTINO"
    echo "PUBLICACAO_INDETERMINADA a conferencia NAO concluiu: o manifesto da copia ficou INCOMPLETO" >&2
    echo "                       — isso NAO e divergencia de conteudo. Nenhuma afirmacao sobre a copia." >&2
    exit 5
  fi
  echo "== conferencia da copia operacional =="
  echo "destino:  $ALVO:$DESTINO"
  echo "registro: $PUB"
  if [ "$MAN_ATUAL" = "$MAN_ESPERADO" ]; then
    DIG_ATUAL="$(printf '%s\n' "$MAN_ATUAL" | digest_de)"
    echo "digest agora: $DIG_ATUAL"
    [ "$DIG_ATUAL" = "$DIG_REG" ] || echo "AVISO o digest registrado em .publicado ($DIG_REG) difere do medido agora ($DIG_ATUAL)"
    echo "PUBLICACAO_OK commit=$SHA_REG digest=$DIG_ATUAL arquivos=$(printf '%s\n' "$MAN_ATUAL" | grep -c . || true) conferido_em=$ALVO:$DESTINO trava=$(trava_estado)"
    exit 0
  fi
  dif_manifestos "conferencia" "$MAN_ESPERADO" "$MAN_ATUAL"
  echo "PUBLICACAO_DIVERGENTE a copia operacional NAO e o commit registrado ($SHA_REG)" >&2
  exit 5
fi

[ -n "$COMMIT" ] || COMMIT="HEAD"
SHA="$(git rev-parse --verify "$COMMIT^{commit}" 2>/dev/null)" || {
  echo "FALHOU commit/branch nao resolve: $COMMIT" >&2; exit 2; }
ARVORE="$(git rev-parse --verify "$SHA^{tree}")"
# Rotulo de origem. `git name-rev` escolhe UM nome e a escolha e arbitraria quando mais de um
# branch aponta para o mesmo commit — foi assim que a copia de homolog nasceu rotulada "develop".
# Regra: nome declarado (--ref) vence; senao TODOS os nomes que apontam para o commit, juntos;
# so cai no name-rev quando nenhum branch aponta (commit solto).
if [ -n "$REF_DECLARADO" ]; then
  REF="$REF_DECLARADO"
else
  REF="$(git for-each-ref --points-at "$SHA" --format='%(refname:short)' refs/heads refs/remotes 2>/dev/null \
        | sed 's|^origin/||' | grep -v '^HEAD$' | sort -u | paste -sd+ - || true)"
  if [ -z "$REF" ]; then
    REF="$(git name-rev --name-only "$SHA" 2>/dev/null || echo desconhecido)"
  elif [ "$(printf '%s' "$REF" | tr -cd '+' | wc -c)" -gt 0 ]; then
    echo "AVISO ref: $REF — mais de um branch neste commit; use --ref <nome> para declarar a origem." >&2
  fi
fi

SUJO=0
if [ -n "$(git status --porcelain --untracked-files=no 2>/dev/null)" ]; then
  SUJO=1
  echo "--- arvore de trabalho com alteracao em arquivo versionado:"
  git status --porcelain --untracked-files=no | sed 's/^/    /'
  if [ "$PERMITIR_SUJA" -eq 0 ]; then
    echo "PUBLICACAO_FALHOU arvore suja — commite (ou use --permitir-arvore-suja, que publica o" >&2
    echo "                  commit do git e registra o desvio). Nada foi publicado." >&2
    exit 2
  fi
  echo "AVISO arvore suja aceita por --permitir-arvore-suja: o conteudo publicado vem do commit, nao do disco."
fi
NAO_RASTREADO="$(git status --porcelain --untracked-files=normal 2>/dev/null | grep -c '^??' || true)"

echo "== publicacao versionada =="
echo "commit:   $SHA"
echo "arvore:   $ARVORE"
echo "ref:      $REF"
echo "card:     $CARD"
echo "origem:   $(git config --get remote.origin.url 2>/dev/null || echo '(sem remote)')"
echo "destino:  $ALVO:$DESTINO"

# ---------------------------------------------------------------- modos do ExecStart
# O bit executavel vive no GIT. O unit do systemd chama o script direto: se o commit tem o
# alvo do ExecStart como 100644, a publicacao devolve 644 para a copia operacional e o servico
# morre com 203/EXEC (foi o que aconteceu com scripts/backup/). Contado e registrado aqui.
checar_modos() {
  local sha="$1" u linha alvo rel modo faltas=0
  for u in deploy/systemd/*.service; do
    [ -f "$u" ] || continue
    while IFS= read -r linha; do
      alvo="$(printf '%s' "$linha" | sed -e 's/^ExecStart=//' -e 's/^-//' -e 's/[[:space:]].*$//')"
      case "$alvo" in "$DESTINO"/*) ;; *) continue;; esac
      rel="${alvo#"$DESTINO"/}"
      modo="$(git ls-tree "$sha" -- "$rel" | awk '{print $1}')"
      if [ "$modo" != "100755" ]; then
        echo "AVISO modo: $rel esta $modo no commit $sha, mas $u o executa direto (ExecStart) —" >&2
        echo "            o systemd vai recusar com 203/EXEC. O bit tem de ser corrigido NO GIT." >&2
        faltas=$((faltas+1))
      fi
    done < <(grep -h '^ExecStart=' "$u" 2>/dev/null || true)
  done
  echo "$faltas"
}

FALTAS_MODOS="$(checar_modos "$SHA")"
if [ "$EXIGIR_MODOS" -eq 1 ] && [ "$FALTAS_MODOS" -gt 0 ]; then
  echo "PUBLICACAO_FALHOU $FALTAS_MODOS alvo(s) de ExecStart sem bit executavel no commit $SHA" >&2
  exit 4
fi
[ "$FALTAS_MODOS" -gt 0 ] && echo "AVISO $FALTAS_MODOS alvo(s) de ExecStart sem bit executavel neste commit (registrado em .publicado)"

# ---------------------------------------------------------------- arvore do commit (local)
# Guardas do que o caminho suporta, fail-closed: so arquivo comum (sem symlink/submodulo) e sem
# espaco/tab no nome — o manifesto e o mapa de modos sao texto separado por espaco.
OUTROS="$(git ls-tree -r "$SHA" | awk '$1 != "100644" && $1 != "100755" {print $1" "$4}' | head -5)"
[ -z "$OUTROS" ] || { echo "PUBLICACAO_FALHOU o commit $SHA tem entrada que nao e arquivo comum (symlink/submodulo): $OUTROS" >&2; exit 2; }
if git ls-tree -r --name-only "$SHA" | grep -q '[[:space:]]'; then
  echo "PUBLICACAO_FALHOU o commit $SHA tem caminho com espaco/tab — nao suportado pelo manifesto" >&2
  exit 2
fi
MAPA="$(git ls-tree -r "$SHA" | awk '{print $1" "$4}')"
[ -n "$MAPA" ] || { echo "PUBLICACAO_FALHOU o commit $SHA nao tem arquivo nenhum" >&2; exit 2; }

TMP="$(mktemp -d "${TMPDIR:-/tmp}/publicar.XXXXXX")"
trap 'limpar_staging; rm -rf "$TMP"' EXIT
# Modo EXATO do git (extrair com tar mascara o modo com o umask/mascara de ACL de quem extrai).
extrair_e_normalizar "$SHA" "$TMP"
RC_MAN=0
MAN_LOCAL="$(manifesto_de "$TMP/arvore" 2>"$TMP/err-man-local")" || RC_MAN=$?
if [ "$RC_MAN" -eq 7 ]; then
  falhar_manifesto "ARVORE LOCAL do commit $SHA (extraida com git archive)" "$TMP/err-man-local" "$TMP/arvore"
  echo "PUBLICACAO_FALHOU a extracao local do commit nao deu um manifesto confiavel — nada foi publicado." >&2
  exit 2
elif [ "$RC_MAN" -ne 0 ]; then
  echo "PUBLICACAO_FALHOU nao consegui montar o manifesto da arvore local do commit $SHA (exit $RC_MAN) —" >&2
  echo "                  nada foi publicado." >&2
  exit 2
fi
DIG_LOCAL="$(printf '%s\n' "$MAN_LOCAL" | digest_de)"
N_ARQ="$(printf '%s\n' "$MAN_LOCAL" | grep -c . || true)"
echo "arquivos: $N_ARQ | digest do commit: $DIG_LOCAL"
[ "$N_ARQ" -gt 0 ] || { echo "PUBLICACAO_FALHOU commit $SHA nao tem arquivo nenhum" >&2; exit 6; }

# ---------------------------------------------------------------- lock remoto (uma publicacao por vez)
LOCK_PEGO=0
liberar_lock() {
  if [ "$LOCK_PEGO" -eq 1 ]; then R "rm -rf '$LOCK_REMOTO'" >/dev/null 2>&1 || true; fi
}
trap 'limpar_staging; liberar_lock; rm -rf "$TMP"' EXIT

if [ "$ENSAIO" -eq 0 ]; then
  if R "mkdir '$LOCK_REMOTO' 2>/dev/null"; then
    LOCK_PEGO=1
    R "printf '%s\n' 'card=$CARD host=$(hostname) inicio=$(date -u +%Y-%m-%dT%H:%M:%SZ) commit=$SHA destino=$DESTINO' > '$LOCK_REMOTO/quem'"
  else
    QUEM="$(R "cat '$LOCK_REMOTO/quem' 2>/dev/null" || true)"
    AGORA="$(date +%s)"
    # Idade do lock: mtime em epoch e, se ela nao for medivel, o `inicio` que o proprio lock grava.
    # Defeito medido (relatado pelo card t_c7281fce): quando `stat` devolvia vazio, `AGORA-0` virava
    # "~56 anos" e uma publicacao derrubava o lock VIVO de outra. Sem idade confiavel, NAO derruba
    # (fail-closed) — o custo de esperar 30 min e menor que o de duas publicacoes em paralelo.
    IDADE_EPOCH="$(R "stat -c %Y '$LOCK_REMOTO' 2>/dev/null" || true)"
    case "$IDADE_EPOCH" in (*[!0-9]*|"") IDADE_EPOCH=0;; esac
    INICIO_ISO="$(printf '%s' "$QUEM" | sed -n 's/.*inicio=\([0-9][0-9T:+-]*Z*\).*/\1/p')"
    if [ "$IDADE_EPOCH" -le 1 ] && [ -n "$INICIO_ISO" ]; then
      IDADE_EPOCH="$(date -u -d "$INICIO_ISO" +%s 2>/dev/null || echo 0)"
      case "$IDADE_EPOCH" in (*[!0-9]*|"") IDADE_EPOCH=0;; esac
      [ "$IDADE_EPOCH" -le 1 ] || echo "AVISO lock: mtime nao medivel — idade pelo 'inicio' do proprio lock" >&2
    fi
    if [ "$IDADE_EPOCH" -le 1 ]; then
      echo "AVISO lock ocupado: ${QUEM:-sem identificacao} — idade NAO MEDIVEL (mtime nem 'inicio')" >&2
      echo "PUBLICACAO_FALHOU outra publicacao em andamento e idade nao medida (lock $LOCK_REMOTO) —" >&2
      echo "                  nao derrubo lock sem idade confiavel. Use --forcar-lock se tiver certeza." >&2
      exit 3
    fi
    IDADE_S=$((AGORA-IDADE_EPOCH))
    [ "$IDADE_S" -ge 0 ] || IDADE_S=$LOCK_VALIDADE_S
    echo "AVISO lock ocupado: ${QUEM:-sem identificacao} (idade ${IDADE_S}s)" >&2
    if [ "$FORCAR_LOCK" -eq 1 ] || [ "$IDADE_S" -gt "$LOCK_VALIDADE_S" ]; then
      echo "AVISO lock obsoleto (> ${LOCK_VALIDADE_S}s ou --forcar-lock): derrubando." >&2
      R "rm -rf '$LOCK_REMOTO'" >/dev/null 2>&1 || true
      R "mkdir '$LOCK_REMOTO'" >/dev/null 2>&1 && { LOCK_PEGO=1; } || true
      [ "$LOCK_PEGO" -eq 1 ] || { echo "PUBLICACAO_FALHOU nao consegui pegar o lock $LOCK_REMOTO" >&2; exit 3; }
    else
      echo "PUBLICACAO_FALHOU outra publicacao em andamento (lock $LOCK_REMOTO) — nada foi escrito" >&2
      exit 3
    fi
  fi
fi

# ---------------------------------------------------------------- estado antes
MAN_ANTES=""; DIG_ANTES="(inexistente)"; N_ANTES=0
RC_MAN=0
MAN_ANTES="$(manifesto_remoto "$DESTINO" 2>"$TMP/err-man-antes")" || RC_MAN=$?
if [ "$RC_MAN" -eq 7 ]; then
  falhar_manifesto "COPIA OPERACIONAL ANTES DA TROCA ($ALVO:$DESTINO)" "$TMP/err-man-antes" "$DESTINO"
  MAN_ANTES=""; DIG_ANTES="(indeterminado)"
  echo "AVISO nao sei dizer se a copia estava integra antes da troca: registrar divergencia_antes=(indeterminado)"
elif [ "$RC_MAN" -ne 0 ]; then
  MAN_ANTES=""
  echo "AVISO $ALVO:$DESTINO ainda nao existe (ou nao respondeu) — a publicacao vai criar"
else
  DIG_ANTES="$(printf '%s\n' "$MAN_ANTES" | digest_de)"
  N_ANTES="$(printf '%s\n' "$MAN_ANTES" | grep -c . || true)"
fi
PUBLICADO_ANTES="$(R "cat '$DESTINO/.publicado' 2>/dev/null" || true)"
COMMIT_ANTES="$(printf '%s' "$PUBLICADO_ANTES" | sed -n 's/^commit:[[:space:]]*//p' | head -1)"
CARD_ANTES="$(printf '%s' "$PUBLICADO_ANTES" | sed -n 's/^publicado_por:[[:space:]]*//p' | head -1)"
echo "antes:    ${N_ANTES} arquivo(s), digest ${DIG_ANTES}${COMMIT_ANTES:+, .publicado diz commit $COMMIT_ANTES (card $CARD_ANTES)}"
[ -n "$COMMIT_ANTES" ] || echo "antes:    sem .publicado — nao havia registro de qual commit estava publicado (o defeito)"

# ---------------------------------------------------------------- guarda de PRODUCAO (substituicao)
# Publicar o MESMO commit ja registrado (reparo/conferencia) ou publicar em destino de ensaio passa
# sem cerimonia. Substituir o commit que a PRODUCAO executa por outro exige declaracao explicita —
# a copia operacional e o alvo do ExecStart dos timers, e trocar a arvore dela "para testar" foi o
# que reverteu a correcao do backup (t_daca4bda). Fail-closed, antes de escrever qualquer coisa.
if [ "$ENSAIO" -eq 0 ] && [ "$PRODUCAO" -eq 1 ] && [ "$PRODUCAO_OK" -ne 1 ] \
   && [ -n "$COMMIT_ANTES" ] && [ "$COMMIT_ANTES" != "$SHA" ]; then
  echo "PUBLICACAO_FALHOU $ALVO:$DESTINO e o destino COMPARTILHADO de producao e ja executa" >&2
  echo "                  $COMMIT_ANTES (card $CARD_ANTES); a publicacao pediria $SHA (card $CARD)." >&2
  echo "                  Substituir o codigo que a producao executa exige --producao" >&2
  echo "                  (TRE_PUBLICAR_PRODUCAO=1) com o card e a aprovacao registrados." >&2
  echo "                  Para ensaiar com a sua arvore use destino isolado (TRE_PUBLICAR_DESTINO)." >&2
  exit 2
fi
[ "$PRODUCAO" -eq 1 ] && [ "$PRODUCAO_OK" -eq 1 ] && echo "aviso:    publicacao em PRODUCAO declarada (--producao) — substitui o commit registrado em $DESTINO"

CONCORRENCIA=""
if [ -n "$CARD_ANTES" ] && [ "$CARD_ANTES" != "$CARD" ] && [ "$COMMIT_ANTES" != "$SHA" ]; then
  CONCORRENCIA="card $CARD_ANTES publicou $COMMIT_ANTES antes deste card ($CARD)"
  echo "AVISO concorrencia: $CONCORRENCIA — a publicacao deste card substitui a dele (registrado no log)."
fi

# A copia estava igual ao commit que ela dizia ser? `.publicado` sozinho nao responde: quem escreve por
# fora do caminho unico (tar/rsync ad-hoc) NAO reescreve o registro, entao o .publicado continua
# apontando para o commit antigo. O manifesto responde — e a diferenca vai para o registro e o log.
DIVERGENCIA_ANTES="(nao conferido)"
if [ -n "$MAN_ANTES" ]; then
  MAN_ANTES_ESPERADO="$MAN_LOCAL"
  if [ -n "$COMMIT_ANTES" ] && [ "$COMMIT_ANTES" != "$SHA" ]; then
    if git rev-parse --verify "$COMMIT_ANTES^{commit}" >/dev/null 2>&1; then
      TMPA="$(mktemp -d "${TMPDIR:-/tmp}/publicar-antes.XXXXXX")"
      extrair_e_normalizar "$COMMIT_ANTES" "$TMPA"
      MAN_ANTES_ESPERADO="$(manifesto_de "$TMPA/arvore")"
      rm -rf "$TMPA"
    else
      MAN_ANTES_ESPERADO=""
      echo "AVISO o commit registrado ($COMMIT_ANTES) nao existe neste repositorio — nao da para dizer se a copia estava integra"
    fi
  fi
  if [ -n "$MAN_ANTES_ESPERADO" ]; then
    DIVERGENCIA_ANTES="$(diff <(printf '%s\n' "$MAN_ANTES_ESPERADO") <(printf '%s\n' "$MAN_ANTES") | grep -c '^[<>]' || true)"
    if [ "$DIVERGENCIA_ANTES" -eq 0 ]; then
      echo "antes:    a copia estava IDENTICA ao commit que .publicado registrava (${COMMIT_ANTES:-$SHA})"
    else
      echo "AVISO divergencia: a copia NAO estava igual ao commit registrado (${COMMIT_ANTES:-$SHA}) —"
      echo "                   $DIVERGENCIA_ANTES linha(s) do manifesto diferem: alguem escreveu por fora do"
      echo "                   caminho unico (tar/rsync ad-hoc) ou outro card publicou durante a janela."
      DIVERGENCIA_ANTES="$DIVERGENCIA_ANTES linha(s) do manifesto"
    fi
  fi
fi

if [ "$ENSAIO" -eq 1 ]; then
  echo "ENSAIO: nada escrito. Publicaria $SHA ($N_ARQ arquivos, digest $DIG_LOCAL)."
  echo "PUBLICACAO_ENSAIO commit=$SHA digest=$DIG_LOCAL arquivos=$N_ARQ"
  exit 0
fi

# ---------------------------------------------------------------- transferencia (staging UNICO por publicacao)
# O staging era o caminho FIXO /opt/tre/.publicacao-staging — um so para TODA publicacao, de todo
# card (defeito t_0f74266d). Duas publicacoes ao mesmo tempo se misturavam: o `find` de uma
# listava o que o `rm -rf`/`tar -x` da outra apagava, o `sha256sum` falhava (~280 linhas
# "sha256sum: ... No such file or directory"), a falha era INTERMITENTE (a mesma invocacao passava
# depois) e a mensagem culpava "a copia transferida". Agora o staging e `mktemp -d` ao lado do
# DESTINO — um por destino e por execucao — e e removido no trap (inclusive quando aborta).
STG_BASE="${TRE_PUBLICAR_STAGING_BASE:-$(dirname "$DESTINO")}"
STG="$(R "mktemp -d -p '$STG_BASE' '.publicacao-staging.XXXXXX' 2>/dev/null" || true)"
case "$STG" in
  /*) STAGING_VIVO=1;;
  *) STG=""
     echo "PUBLICACAO_FALHOU nao consegui criar o staging unico em $ALVO:$STG_BASE (mktemp):" >&2
     echo "                  sem staging proprio a publicacao nao transfere nada — nada foi publicado." >&2
     registrar_aborto "staging-mktemp" "mktemp-falhou"
     exit 6;;
esac
MAPA_REMOTO="${STG}.modos"
# A copia publicada fica imutavel (chattr +i): destrava SO aqui, durante a troca, e rearma no fim.
if [ "$TRAVA" -eq 1 ]; then
  destravar_remoto
  echo "trava:    desarmada em $ALVO:$DESTINO (so durante a troca)"
fi
echo "staging:  $ALVO:$STG (unico desta publicacao)"
R "rm -rf '$STG' && install -d -m 755 '$STG' && tar -xpf - -C '$STG'" < "$TMP/commit.tar"
R "cat > '$MAPA_REMOTO'" < "$TMP/modos.txt"
R "bash -s -- --normalizar-modos '$STG' '$MAPA_REMOTO'" < "$AUTO"

RC_MAN=0
MAN_STG="$(manifesto_remoto "$STG" 2>"$TMP/err-man-stg")" || RC_MAN=$?
if [ "$RC_MAN" -eq 7 ]; then
  falhar_manifesto "STAGING ($ALVO:$STG)" "$TMP/err-man-stg" "$STG"
  echo "                  NADA foi publicado: o destino $ALVO:$DESTINO segue como estava." >&2
  registrar_aborto "staging-manifesto-incompleto" "arquivo listado sumiu antes do sha256sum"
  exit 6
elif [ "$RC_MAN" -ne 0 ]; then
  echo "PUBLICACAO_FALHOU nao consegui ler o manifesto do STAGING ($ALVO:$STG): exit $RC_MAN —" >&2
  echo "                  NADA foi publicado: o destino $ALVO:$DESTINO segue como estava." >&2
  registrar_aborto "staging-manifesto-ilegivel" "exit=$RC_MAN"
  exit 6
fi
if [ "$MAN_STG" != "$MAN_LOCAL" ]; then
  echo "PUBLICACAO_FALHOU o conteudo transferido para o STAGING DIVERGE do commit $SHA —" >&2
  echo "                  (os dois manifestos estao integros: e divergencia de CONTEUDO, nao staging" >&2
  echo "                  incompleto. NADA foi publicado: o destino segue como estava.)" >&2
  dif_manifestos "staging" "$MAN_LOCAL" "$MAN_STG"
  registrar_aborto "staging-divergente" "manifesto do staging difere do commit"
  exit 6
fi
echo "transferencia: OK (digest identico na origem e no staging)"

# ---------------------------------------------------------------- artefato do commit (para o watchdog)
# Guarda o commit publicado (tar com o modo do git, mapa de modos e manifesto) FORA da copia,
# em root-only. E com isso que o watchdog da VPS restaura a copia sem git e sem este repositorio
# (defeito t_daca4bda). Fail-closed: sem artefato gravado, NADA e trocado no destino.
R "install -d -m 700 '$ARTEFATO'"
R "cat > '$ARTEFATO/commit.tar'" < "$TMP/commit.tar"
R "cat > '$ARTEFATO/modos.txt'" < "$TMP/modos.txt"
printf '%s\n' "$MAN_LOCAL" | R "cat > '$ARTEFATO/manifesto'"
printf '%s\n' "$SHA"       | R "cat > '$ARTEFATO/commit'"
printf '%s\n' "$DIG_LOCAL" | R "cat > '$ARTEFATO/digest'"
R "chmod 600 '$ARTEFATO'/* 2>/dev/null || true"
ART_SHA="$(R "sha256sum '$ARTEFATO/manifesto' | cut -d' ' -f1")"
[ "$ART_SHA" = "$DIG_LOCAL" ] || {
  echo "PUBLICACAO_FALHOU o artefato gravado em $ALVO:$ARTEFATO nao confere com o commit ($ART_SHA != $DIG_LOCAL) — nada foi trocado" >&2
  registrar_aborto "artefato-divergente" "manifesto do artefato != digest do commit"
  exit 6; }
echo "artefato:  $ALVO:$ARTEFATO (tar + manifesto do commit, para o watchdog restaurar)"

# ---------------------------------------------------------------- troca no destino (espelho do commit)
# O `--normalizar-modos` depois do rsync e o que garante o modo do git tambem quando o rsync pula um
# arquivo (mesmo tamanho e mesma data, modo diferente — a checagem rapida dele nao olha modo).
R "rsync -a --delete --exclude '/.publicado' --exclude '/.publicado.manifest' '$STG/' '$DESTINO/' \
   && bash -s -- --normalizar-modos '$DESTINO' '$MAPA_REMOTO' \
   && chown -R '$DONO' '$DESTINO' && rm -rf '$STG' '$MAPA_REMOTO'" < "$AUTO"
STAGING_VIVO=0   # a propria troca ja removeu o staging UNICO e o mapa de modos

AGORA_ISO="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
PUBLICADO="$(cat <<TXT
commit: $SHA
arvore: $ARVORE
ref: $REF
digest: $DIG_LOCAL
arquivos: $N_ARQ
origem: $(git config --get remote.origin.url 2>/dev/null || echo '(sem remote)')
destino: $DESTINO
publicado_em: $AGORA_ISO
publicado_por: $CARD
publicado_de: $(hostname)
arvore_suja: $SUJO
nao_rastreado_no_checkout: $NAO_RASTREADO
execstart_sem_bit: $FALTAS_MODOS
producao_declarado: $PRODUCAO_OK
divergencia_antes: $DIVERGENCIA_ANTES
concorrencia: ${CONCORRENCIA:-(nenhuma)}
TXT
)"
printf '%s\n' "$PUBLICADO" | R "cat > '$DESTINO/.publicado'"
printf '%s\n' "$MAN_LOCAL" | R "cat > '$DESTINO/.publicado.manifest'"
R "chown '$DONO' '$DESTINO/.publicado' '$DESTINO/.publicado.manifest'"

# ---------------------------------------------------------------- pos-conferencia (o que prova o aceite)
RC_MAN=0
MAN_DEPOIS="$(manifesto_remoto "$DESTINO" 2>"$TMP/err-man-depois")" || RC_MAN=$?
if [ "$RC_MAN" -eq 7 ]; then
  falhar_manifesto "COPIA OPERACIONAL JA TROCADA ($ALVO:$DESTINO)" "$TMP/err-man-depois" "$DESTINO"
  echo "                  a troca JA aconteceu (o destino foi escrito): repita --conferir para o veredito." >&2
  registrar_aborto "destino-manifesto-incompleto" "manifesto do destino incompleto depois da troca"
  exit 6
elif [ "$RC_MAN" -ne 0 ]; then
  echo "PUBLICACAO_FALHOU nao consegui ler o manifesto do destino ($ALVO:$DESTINO) depois da troca: exit $RC_MAN —" >&2
  echo "                  repetir --conferir da o veredito." >&2
  registrar_aborto "destino-manifesto-ilegivel" "exit=$RC_MAN"
  exit 6
fi
DIG_DEPOIS="$(printf '%s\n' "$MAN_DEPOIS" | digest_de)"
if [ "$MAN_DEPOIS" != "$MAN_LOCAL" ]; then
  echo "PUBLICACAO_FALHOU a copia operacional NAO ficou igual ao commit $SHA:" >&2
  dif_manifestos "destino" "$MAN_LOCAL" "$MAN_DEPOIS"
  exit 6
fi

R "printf '%s\n' '$AGORA_ISO commit=$SHA digest=$DIG_LOCAL arquivos=$N_ARQ card=$CARD destino=$DESTINO digest_antes=$DIG_ANTES commit_antes=${COMMIT_ANTES:-nenhum} divergencia_antes=$DIVERGENCIA_ANTES' >> '$LOG_REMOTO'"

# ---------------------------------------------------------------- trava de volta (copia imutavel)
TRAVA_ESTADO="ausente"
if [ "$TRAVA" -eq 1 ]; then
  travar_remoto
  TRAVA_ESTADO="$(R "if ( : > '$DESTINO/.trava-probe' ) 2>/dev/null; then rm -f '$DESTINO/.trava-probe'; echo gravavel; else echo travada; fi")"
  if [ "$TRAVA_ESTADO" != "travada" ]; then
    echo "AVISO a trava de imutabilidade nao pegou em $DESTINO — a copia esta correta, mas escrita" >&2
    echo "      ad-hoc nao vai falhar (o watchdog segue detectando). Verifique chattr no destino." >&2
    TRAVA_ESTADO="nao-pegou"
  fi
  echo "trava:    armada em $DESTINO (escrita ad-hoc falha com EPERM)"
fi

liberar_lock; LOCK_PEGO=0

if [ "$DIG_ANTES" = "$DIG_LOCAL" ]; then
  echo "idempotente: a copia ja estava neste commit — mesmo digest antes e depois ($DIG_LOCAL)"
fi
echo "PUBLICACAO_OK commit=$SHA digest=$DIG_LOCAL arquivos=$N_ARQ digest_antes=$DIG_ANTES trava=$TRAVA_ESTADO"
