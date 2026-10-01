#!/usr/bin/env bash
# =====================================================================================
# lib-ambiente.sh — resolucao do trio (container, usuario, banco) POR AMBIENTE.
#
# Defeito que originou (card t_1b2ab418, achado F2 do TRE-W1-E06-T01): backup-tre.sh
# resolvia o container com UMA variavel (TRE_PG_SERVICO) valendo para os tres ambientes
# de uma vez e, sem ela, caia na convencao `pg-<amb>` / usuario `tre`. O dev real e
# `pg-sales-dev` / `sales_ai` / `sales_intelligence` (declarado so em
# `deploy/environments/dev.env`) e nenhum timer lia esse arquivo. Medido na VPS:
# `PULADO` nos tres ambientes, `RESULTADO: BACKUP_OK`, exit 0 e nenhum artefato —
# sucesso silencioso cobrindo ZERO ambientes.
#
# Precedencia da resolucao, por ambiente <amb> (a primeira que existir manda):
#   1. variavel por ambiente: TRE_PG_SERVICO_<AMB> / TRE_PG_USER_<AMB> / TRE_PG_DB_<AMB>
#      (<AMB> em MAIUSCULAS: TRE_PG_SERVICO_DEV)
#   2. arquivo do ambiente: ${TRE_ENV_DIR:-<raiz do checkout>/deploy/environments}/<amb>.env
#      (par NAO-SECRETO versionado; e a fonte do trio real do dev)
#   3. TRE_PG_SERVICO / TRE_PG_USER / TRE_PG_DB globais — SO em chamada de UM ambiente
#   4. convencao: pg-<amb> / tre / sales_intelligence
#
# POR QUE O PASSO 3 NAO VALE EM `todos` (e a razao de a correcao nao ser "declarar o
# trio em /etc/tre/backup.env", como propunha o corpo do card): TRE_PG_SERVICO e UMA
# variavel. Se ela atravessasse os tres ambientes, `backup-tre.sh todos` copiaria o
# banco do dev TRES vezes, rotulado como dev, homolog e prod — artefato de mentira,
# pior que nenhum, e exatamente a falha silenciosa que este defeito trata. Em `todos`,
# ambiente sem declaracao propria usa a convencao `pg-<amb>`; se o container nao existir,
# e PULADO (e o card do ambiente, se existir, e FALHA — ver tre_estado_ambiente).
#
# Declarado x provisionado:
#   DECLARADO    = existe o passo 1 ou o passo 2 (alguem disse "este ambiente existe")
#   PROVISIONADO = declarado OU o container resolvido existe
# Ambiente DECLARADO cujo container nao existe e FALHA (exit != 0 no consumidor): e o
# estado que produzia o `BACKUP_OK` sem artefato. Ambiente sem declaracao e sem
# container e PULADO (honesto: nao ha o que copiar).
#
# Uso:
#   AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
#   . "$AQUI/lib-ambiente.sh"
#   tre_resolver_ambiente dev todos || { echo "FALHOU $TRE_AMB_ERRO"; exit 1; }
#   tre_estado_ambiente          # TRE_AMB_ESTADO=COBRIR|PULAR|FALHAR + TRE_AMB_MOTIVO
#
# Quem usa: scripts/backup/backup-tre.sh, scripts/backup/verificar-ultimo-backup.sh,
# scripts/backup/teste-rotina-ambiente.sh (teste da propria resolucao).
# =====================================================================================

# Raiz do checkout deduzida do proprio arquivo (scripts/backup/lib-ambiente.sh -> raiz).
_LIB_AMBIENTE_AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Diretorio dos arquivos por ambiente. TRE_ENV_DIR vence (e o que o operador declara em
# /etc/tre/backup.env); sem ela, a raiz do checkout de onde a lib foi carregada.
tre_env_dir() {
  if [ -n "${TRE_ENV_DIR:-}" ]; then
    printf '%s\n' "$TRE_ENV_DIR"
  else
    printf '%s\n' "$(cd "$_LIB_AMBIENTE_AQUI/../.." && pwd)/deploy/environments"
  fi
}

# Valor de uma variavel que pode nao existir (funciona sob `set -u`, ao contrario de
# ${!nome} direto). Ve variavel de shell nao exportada tambem: `. /etc/tre/backup.env`
# sem `set -a` continua funcionando.
tre_valor_de() {
  local nome="$1"
  if declare -p "$nome" >/dev/null 2>&1; then printf '%s' "${!nome}"; fi
}

# Container existe? (o unico lugar do repo que decide isso; dublado no teste)
tre_container_existe() {
  docker inspect "$1" >/dev/null 2>&1
}

# Resolve o ambiente <amb> no modo <um|todos> e preenche:
#   TRE_AMB_NOME, TRE_AMB_SERVICO, TRE_AMB_USUARIO, TRE_AMB_BANCO,
#   TRE_AMB_DECLARADO (1|0), TRE_AMB_FONTE, TRE_AMB_ARQUIVO, TRE_AMB_AVISO
# Devolve 1 (com TRE_AMB_ERRO) se o arquivo do ambiente existir mas nao puder ser lido:
# configuracao quebrada e fail-closed, nunca "cai no padrao e segue".
tre_resolver_ambiente() {
  local amb="${1:-}" modo="${2:-um}"
  TRE_AMB_NOME="$amb"
  TRE_AMB_SERVICO=""; TRE_AMB_USUARIO=""; TRE_AMB_BANCO=""
  TRE_AMB_DECLARADO=0; TRE_AMB_FONTE=""; TRE_AMB_ARQUIVO=""; TRE_AMB_ERRO=""; TRE_AMB_AVISO=""
  if [ -z "$amb" ]; then TRE_AMB_ERRO="nome de ambiente vazio"; return 1; fi

  local maiuscula arquivo
  maiuscula="$(printf '%s' "$amb" | tr '[:lower:]' '[:upper:]')"
  arquivo="$(tre_env_dir)/$amb.env"

  local v_servico v_usuario v_banco
  local f_servico="" f_usuario="" f_banco=""
  v_servico="$(tre_valor_de "TRE_PG_SERVICO_$maiuscula")"
  v_usuario="$(tre_valor_de "TRE_PG_USER_$maiuscula")"
  v_banco="$(tre_valor_de "TRE_PG_DB_$maiuscula")"

  if [ -f "$arquivo" ]; then
    local trio
    if ! trio="$(. "$arquivo" >/dev/null 2>&1 && \
         printf '%s\n%s\n%s\n' "${TRE_PG_SERVICO:-}" "${TRE_PG_USER:-}" "${TRE_PG_DB:-}")"; then
      TRE_AMB_ERRO="arquivo de ambiente invalido ou ilegivel: $arquivo"
      return 1
    fi
    f_servico="$(printf '%s\n' "$trio" | sed -n 1p)"
    f_usuario="$(printf '%s\n' "$trio" | sed -n 2p)"
    f_banco="$(printf '%s\n' "$trio" | sed -n 3p)"
    TRE_AMB_DECLARADO=1
    TRE_AMB_FONTE="arquivo $arquivo"
    TRE_AMB_ARQUIVO="$arquivo"
  fi
  if [ -n "$v_servico" ]; then
    TRE_AMB_DECLARADO=1
    TRE_AMB_FONTE="variavel TRE_PG_SERVICO_$maiuscula"
  fi

  local g_servico g_usuario g_banco
  g_servico="$(tre_valor_de TRE_PG_SERVICO)"
  g_usuario="$(tre_valor_de TRE_PG_USER)"
  g_banco="$(tre_valor_de TRE_PG_DB)"
  if [ "$modo" = "todos" ] && [ -n "$g_servico" ] && [ "$TRE_AMB_DECLARADO" = "0" ]; then
    # o duble global ignorado: avisa em vez de copiar o banco de um ambiente com o nome
    # de outro (era o risco escondido na correcao proposta no corpo do card)
    TRE_AMB_AVISO="TRE_PG_SERVICO global ignorado para '$amb': uma variavel unica nao vale para os tres ambientes. Declare deploy/environments/$amb.env (ou TRE_PG_SERVICO_$maiuscula)."
    g_servico=""; g_usuario=""; g_banco=""
  fi

  if [ "$modo" = "todos" ]; then
    TRE_AMB_SERVICO="${v_servico:-${f_servico:-pg-$amb}}"
    TRE_AMB_USUARIO="${v_usuario:-${f_usuario:-tre}}"
    TRE_AMB_BANCO="${v_banco:-${f_banco:-sales_intelligence}}"
  else
    TRE_AMB_SERVICO="${v_servico:-${f_servico:-${g_servico:-pg-$amb}}}"
    TRE_AMB_USUARIO="${v_usuario:-${f_usuario:-${g_usuario:-tre}}}"
    TRE_AMB_BANCO="${v_banco:-${f_banco:-${g_banco:-sales_intelligence}}}"
  fi

  if [ "$TRE_AMB_DECLARADO" = "0" ]; then
    if [ -n "$g_servico" ]; then
      TRE_AMB_FONTE="variavel global TRE_PG_SERVICO (aceita porque a chamada e de um ambiente)"
    else
      TRE_AMB_FONTE="convencao pg-<amb> (nenhuma configuracao propria)"
    fi
  fi
  return 0
}

# Veredito do ambiente ja resolvido (usa TRE_AMB_*). Preenche:
#   TRE_AMB_ESTADO = COBRIR | PULAR | FALHAR
#   TRE_AMB_MOTIVO = frase pronta para o log
tre_estado_ambiente() {
  TRE_AMB_ESTADO=""; TRE_AMB_MOTIVO=""
  if [ -n "${TRE_AMB_ERRO:-}" ]; then
    TRE_AMB_ESTADO="FALHAR"; TRE_AMB_MOTIVO="$TRE_AMB_ERRO"; return 0
  fi
  if tre_container_existe "$TRE_AMB_SERVICO"; then
    TRE_AMB_ESTADO="COBRIR"
    TRE_AMB_MOTIVO="container '$TRE_AMB_SERVICO' existe ($TRE_AMB_FONTE)"
    return 0
  fi
  if [ "$TRE_AMB_DECLARADO" = "1" ]; then
    TRE_AMB_ESTADO="FALHAR"
    TRE_AMB_MOTIVO="ambiente '$TRE_AMB_NOME' esta DECLARADO ($TRE_AMB_FONTE) e o container '$TRE_AMB_SERVICO' nao existe — ambiente provisionado sem backup e FALHA, nao 'pulado'"
    return 0
  fi
  TRE_AMB_ESTADO="PULAR"
  TRE_AMB_MOTIVO="ambiente '$TRE_AMB_NOME' nao provisionado (sem configuracao propria e sem container '$TRE_AMB_SERVICO')"
  return 0
}

# Guarda de origem repetida: dois ambientes apontando para o MESMO container na mesma
# execucao produziriam um artefato de 'homolog' com o banco do dev. Recusa o segundo.
# Uso: tre_registrar_origem "$servico" || echo "colisao"
TRE_AMB_ORIGENS_USADAS=""
tre_registrar_origem() {
  local servico="$1"
  local usada
  for usada in $TRE_AMB_ORIGENS_USADAS; do
    [ "$usada" = "$servico" ] && return 1
  done
  TRE_AMB_ORIGENS_USADAS="$TRE_AMB_ORIGENS_USADAS $servico"
  return 0
}

# =====================================================================================
# Odoo do ambiente (card TRE-W2-E01-T01-F01) — banco PROPRIO + filestore.
#
# O Odoo nao reusa nada do trio acima: banco `odoo_dev` no container `pg-odoo-dev` e
# filestore no volume `odoo-data-dev` (deploy/environments/dev.env, secao "Odoo do
# ambiente"). Nem todo ambiente tem Odoo — ambiente que NAO declara nada de Odoo e
# PULADO (nao e falha); ambiente que DECLARA Odoo e nao tem o container e FALHA
# (ambiente provisionado sem backup e falha, mesma regra do trio).
#
# Precedencia (a primeira que existir manda), por ambiente <amb>:
#   1. TRE_ODOO_PG_SERVICO_<AMB> / TRE_ODOO_PG_USER_<AMB> / TRE_ODOO_PG_DB_<AMB> /
#      TRE_ODOO_FILESTORE_<AMB>   (<AMB> em MAIUSCULAS)
#   2. ${TRE_ENV_DIR:-<raiz do checkout>/deploy/environments}/<amb>.env
#      (chaves TRE_ODOO_PG_SERVICO / TRE_ODOO_PG_USER / TRE_ODOO_PG_DB / TRE_ODOO_FILESTORE)
#
# NAO existe passo "global" de proposito: uma unica variavel de Odoo atravessando os tres
# ambientes em `backup-tre.sh todos` copiaria o banco do dev com o rotulo de homolog — o
# mesmo defeito que a resolucao do trio por ambiente (t_1b2ab418) fechou.
# =====================================================================================
tre_resolver_odoo() {
  local amb="${1:-}"
  TRE_ODOO_NOME="$amb"; TRE_ODOO_SERVICO=""; TRE_ODOO_USUARIO=""; TRE_ODOO_BANCO=""
  TRE_ODOO_FILESTORE=""; TRE_ODOO_DECLARADO=0; TRE_ODOO_FONTE=""; TRE_ODOO_ERRO=""
  if [ -z "$amb" ]; then TRE_ODOO_ERRO="nome de ambiente vazio"; return 1; fi

  local maiuscula arquivo
  maiuscula="$(printf '%s' "$amb" | tr '[:lower:]' '[:upper:]')"
  arquivo="$(tre_env_dir)/$amb.env"

  local v_servico v_usuario v_banco v_fs
  local f_servico="" f_usuario="" f_banco="" f_fs=""
  v_servico="$(tre_valor_de "TRE_ODOO_PG_SERVICO_$maiuscula")"
  v_usuario="$(tre_valor_de "TRE_ODOO_PG_USER_$maiuscula")"
  v_banco="$(tre_valor_de "TRE_ODOO_PG_DB_$maiuscula")"
  v_fs="$(tre_valor_de "TRE_ODOO_FILESTORE_$maiuscula")"

  if [ -f "$arquivo" ]; then
    local vals
    if ! vals="$(. "$arquivo" >/dev/null 2>&1 && \
         printf '%s\n%s\n%s\n%s\n' "${TRE_ODOO_PG_SERVICO:-}" "${TRE_ODOO_PG_USER:-}" \
                                   "${TRE_ODOO_PG_DB:-}" "${TRE_ODOO_FILESTORE:-}")"; then
      TRE_ODOO_ERRO="arquivo de ambiente invalido ou ilegivel: $arquivo"
      return 1
    fi
    f_servico="$(printf '%s\n' "$vals" | sed -n 1p)"
    f_usuario="$(printf '%s\n' "$vals" | sed -n 2p)"
    f_banco="$(printf '%s\n' "$vals" | sed -n 3p)"
    f_fs="$(printf '%s\n' "$vals" | sed -n 4p)"
  fi

  TRE_ODOO_SERVICO="${v_servico:-$f_servico}"
  TRE_ODOO_USUARIO="${v_usuario:-${f_usuario:-odoo}}"
  TRE_ODOO_BANCO="${v_banco:-${f_banco:-odoo_dev}}"
  TRE_ODOO_FILESTORE="${v_fs:-${f_fs:-odoo-data-dev}}"
  if [ -n "$TRE_ODOO_SERVICO" ]; then
    TRE_ODOO_DECLARADO=1
    if [ -n "$v_servico" ]; then
      TRE_ODOO_FONTE="variavel TRE_ODOO_PG_SERVICO_$maiuscula"
    else
      TRE_ODOO_FONTE="arquivo $arquivo"
    fi
  fi
  return 0
}

# Veredito do Odoo ja resolvido. Preenche TRE_ODOO_ESTADO = COBRIR | PULAR | FALHAR.
tre_estado_odoo() {
  TRE_ODOO_ESTADO=""; TRE_ODOO_MOTIVO=""
  if [ -n "${TRE_ODOO_ERRO:-}" ]; then
    TRE_ODOO_ESTADO="FALHAR"; TRE_ODOO_MOTIVO="$TRE_ODOO_ERRO"; return 0
  fi
  if [ "${TRE_ODOO_DECLARADO:-0}" = "0" ]; then
    TRE_ODOO_ESTADO="PULAR"
    TRE_ODOO_MOTIVO="ambiente '$TRE_ODOO_NOME' nao declara Odoo (sem TRE_ODOO_PG_SERVICO em $TRE_ODOO_FONTE${TRE_ODOO_FONTE:+ })"
    return 0
  fi
  if tre_container_existe "$TRE_ODOO_SERVICO"; then
    TRE_ODOO_ESTADO="COBRIR"
    TRE_ODOO_MOTIVO="container '$TRE_ODOO_SERVICO' existe ($TRE_ODOO_FONTE)"
  else
    TRE_ODOO_ESTADO="FALHAR"
    TRE_ODOO_MOTIVO="ambiente '$TRE_ODOO_NOME' DECLARA Odoo ($TRE_ODOO_FONTE) e o container '$TRE_ODOO_SERVICO' nao existe — Odoo declarado sem backup e FALHA, nao 'pulado'"
  fi
  return 0
}
