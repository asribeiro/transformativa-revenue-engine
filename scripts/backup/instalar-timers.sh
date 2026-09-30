#!/usr/bin/env bash
# =====================================================================================
# instalar-timers.sh — instala os timers systemd do backup do TRE (precisa de sudo).
#
#   tre-backup.timer         backup diario 02:30 (America/Sao_Paulo)
#   tre-backup-verify.timer  RESTORE de verdade no artefato mais recente, domingo 04:00
#
# O arquivo de configuracao /etc/tre/backup.env guarda apenas caminhos e destino
# (nunca segredo: o backup nao copia `.env`).
# =====================================================================================
set -euo pipefail

RAIZ_REPO="${TRE_RAIZ_REPO:-/opt/tre/repo}"
DEST_ENV="/etc/tre/backup.env"

echo "== 1. diretorios =="
sudo mkdir -p /etc/tre /opt/tre/backup
sudo chown "$(id -un):$(id -gn)" /opt/tre/backup
sudo chmod 750 /opt/tre/backup
echo "  ok"

echo "== 2. arquivo de configuracao (sem segredo) =="
if [ ! -f "$DEST_ENV" ]; then
  sudo tee "$DEST_ENV" >/dev/null <<'ENV'
# Configuracao dos timers de backup do Transformativa Revenue Engine.
# SEM SEGREDO AQUI: o backup nao copia `.env` (segredo se recupera do cofre).
TRE_RAIZ=/opt/tre
TRE_BACKUP_DIR=/opt/tre/backup
TRE_BACKUP_RETENCAO_DIAS=14
# Destino externo (S3-compativel via rclone). Vazio = backup so local.
# TRE_BACKUP_EXTERNO=s3:tre-backup
ENV
  sudo chmod 644 "$DEST_ENV"
  echo "  criado: $DEST_ENV"
else
  echo "  mantido: $DEST_ENV (ja existia)"
fi

echo "== 3. unidades systemd =="
sudo cp "$RAIZ_REPO/deploy/systemd/tre-backup.service" /etc/systemd/system/
sudo cp "$RAIZ_REPO/deploy/systemd/tre-backup.timer" /etc/systemd/system/
sudo cp "$RAIZ_REPO/deploy/systemd/tre-backup-verify.service" /etc/systemd/system/
sudo cp "$RAIZ_REPO/deploy/systemd/tre-backup-verify.timer" /etc/systemd/system/
sudo chmod 644 /etc/systemd/system/tre-backup*.service /etc/systemd/system/tre-backup*.timer
sudo systemctl daemon-reload
echo "  copiadas"

echo "== 4. bit executavel dos scripts chamados pelos units =="
# O ExecStart= chama o script direto: sem bit executavel (100755 no git) o systemd
# falha com 203/EXEC e o timer "instalado" nunca roda. Conferir ANTES de habilitar.
if ! bash "$RAIZ_REPO/scripts/backup/verificar-modos-executaveis.sh"; then
  echo
  echo "ABORTADO: script de unit sem bit executavel — timer NAO habilitado."
  echo "  Corrija no repositorio (git update-index --chmod=+x <arquivo>) e reinstale."
  exit 1
fi

echo "== 5. habilitar =="
sudo systemctl enable --now tre-backup.timer tre-backup-verify.timer >/dev/null
echo "  habilitados"

echo "== 6. agenda =="
systemctl list-timers 'tre-backup*' --no-pager
echo
echo "RESULTADO: TIMERS_OK"
