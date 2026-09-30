#!/usr/bin/env bash
# =====================================================================================
# configurar-destino-externo.sh — liga o destino externo (Object Storage Contabo) no backup.
#
# As chaves sao digitadas AQUI, no prompt da propria VPS, com eco desligado:
#   - nao passam pelo chat
#   - nao entram no historico do shell
#   - ficam em /etc/tre/rclone.conf com permissao 600, dono do usuario de deploy
#
# Roteiro: pergunta endpoint, access key, secret key e bucket; grava a configuracao do
# rclone; cria o bucket; sobe um arquivo de teste, confirma que ele chegou e o remove.
# Se o provedor recusar a assinatura, tenta de novo com a regiao declarada (auto-correcao).
#
# Os valores NAO aparecem na tela em nenhum momento (nem em mensagem de erro).
# =====================================================================================
set -uo pipefail

ENVFILE="/etc/tre/backup.env"
RCLONECONF="/etc/tre/rclone.conf"
REMOTO="contabo"
# O backup roda como este usuario (tre-backup.service declara User=). Se o script for
# executado como root, o arquivo de credenciais ainda precisa ser legivel por ele.
DONO="${TRE_USUARIO_DEPLOY:-tre-deploy}"
id -u "$DONO" >/dev/null 2>&1 || DONO="$(id -un)"
ITENS=0
FALHAS=0
ok() { ITENS=$((ITENS + 1)); echo "OK    $*"; }
ko() { ITENS=$((ITENS + 1)); FALHAS=$((FALHAS + 1)); echo "FALHOU $*"; }

command -v rclone >/dev/null 2>&1 || { echo "FALHOU rclone nao instalado (sudo apt-get install -y rclone)"; exit 1; }
echo "=================================================================="
echo "-- destino externo do backup (Object Storage S3-compativel)"
echo "-- a secret key nao aparece na tela e nao fica no historico"
echo "=================================================================="

read -r -p "Endpoint [https://eu2.contabostorage.com]: " ENDPOINT
ENDPOINT="${ENDPOINT:-https://eu2.contabostorage.com}"
read -r -p "Access Key: " AK
read -r -s -p "Secret Key (nao aparece): " SK; echo
read -r -p "Bucket [tre-backup]: " BUCKET
BUCKET="${BUCKET:-tre-backup}"

if [ -z "${AK:-}" ] || [ -z "${SK:-}" ]; then
  echo "FALHOU access key ou secret key vazia"; exit 1
fi

escrever_conf() {
  local com_regiao="$1" tmp
  tmp="$(mktemp)"; chmod 600 "$tmp"
  {
    echo "[$REMOTO]"
    echo "type = s3"
    echo "provider = Other"
    echo "access_key_id = $AK"
    echo "secret_access_key = $SK"
    echo "endpoint = $ENDPOINT"
    # A Contabo usa path style (o bucket no caminho, nao no subdominio) — documentado por eles.
    echo "force_path_style = true"
    echo "acl = private"
    [ "$com_regiao" = "1" ] && echo "region = default"
  } >"$tmp"
  sudo mv "$tmp" "$RCLONECONF"
  sudo chown "$DONO:$DONO" "$RCLONECONF"
  chmod 600 "$RCLONECONF"
}

echo
echo "== 1. gravando a configuracao do rclone (600, dono $DONO) =="
escrever_conf 0
[ "$(stat -c %a "$RCLONECONF")" = "600" ] && ok "config gravada com permissao 600 (dono $DONO)" || ko "permissao inesperada em $RCLONECONF"

export RCLONE_CONFIG="$RCLONECONF"
export RCLONE_CONFIG_PASS=""
echo
echo "== 2. criando o bucket (com auto-correcao de regiao) =="
if saida_mkdir="$(rclone mkdir "$REMOTO:$BUCKET" 2>&1)"; then
  ok "bucket '$BUCKET' acessivel/criado"
else
  echo "   primeira tentativa falhou; repetindo com a regiao declarada"
  escrever_conf 1
  if rclone mkdir "$REMOTO:$BUCKET" >/dev/null 2>&1; then
    ok "bucket '$BUCKET' criado (com regiao declarada)"
  else
    ko "nao consegui criar/acessar o bucket: $(printf '%s' "$saida_mkdir" | head -2 | tr '\n' ' ' | sed 's/[A-Za-z0-9_\-]\{20,\}/[MASKED]/g')"
  fi
fi

echo
echo "== 3. prova de ida e volta (sobe, confere, remove) =="
PROBE="$(mktemp)"; echo "prova de destino externo do backup TRE — $(date -u +%FT%TZ)" >"$PROBE"
if rclone copy "$PROBE" "$REMOTO:$BUCKET/" >/dev/null 2>&1; then
  ok "arquivo de teste enviado"
  if rclone lsf "$REMOTO:$BUCKET/" 2>/dev/null | grep -q "$(basename "$PROBE")"; then
    ok "arquivo de teste aparece no bucket (ida confirmada)"
  else
    ko "arquivo enviado mas nao aparece na listagem"
  fi
  rclone delete "$REMOTO:$BUCKET/$(basename "$PROBE")" >/dev/null 2>&1 \
    && ok "arquivo de teste removido (bucket limpo)" || ko "nao consegui remover o arquivo de teste"
else
  ko "falha ao enviar o arquivo de teste (chaves ou endpoint incorretos?)"
fi
rm -f "$PROBE"

echo
if [ "$FALHAS" -eq 0 ]; then
  echo "== 4. ligando o destino no backup =="
  sudo cp -n "$ENVFILE" "$ENVFILE.bak" 2>/dev/null || true
  if sudo grep -q '^TRE_BACKUP_EXTERNO=' "$ENVFILE" 2>/dev/null; then
    sudo sed -i "s|^TRE_BACKUP_EXTERNO=.*|TRE_BACKUP_EXTERNO=$REMOTO:$BUCKET|" "$ENVFILE"
  else
    echo "TRE_BACKUP_EXTERNO=$REMOTO:$BUCKET" | sudo tee -a "$ENVFILE" >/dev/null
  fi
  if sudo grep -q '^RCLONE_CONFIG=' "$ENVFILE" 2>/dev/null; then
    sudo sed -i "s|^RCLONE_CONFIG=.*|RCLONE_CONFIG=$RCLONECONF|" "$ENVFILE"
  else
    echo "RCLONE_CONFIG=$RCLONECONF" | sudo tee -a "$ENVFILE" >/dev/null
  fi
  ok "backup apontado para $REMOTO:$BUCKET (arquivo: $ENVFILE)"
else
  echo "== 4. ligando o destino: NAO feito =="
  echo "PULADO o destino so e ligado no backup depois de uma prova de ida e volta bem-sucedida."
  echo "       $ENVFILE NAO foi alterado. Corrija as chaves/endpoint e rode de novo."
fi

echo
if [ "$FALHAS" -eq 0 ]; then
  echo "RESULTADO: DESTINO_EXTERNO_OK"
  echo "Proximo passo: sudo systemctl start tre-backup.service  (o backup ja envia para o destino externo)"
else
  echo "RESULTADO: DESTINO_EXTERNO_FALHOU ($FALHAS falha(s))"
fi
