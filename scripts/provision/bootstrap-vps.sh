#!/usr/bin/env bash
# Bootstrap da VPS do TRE (Contabo Cloud VPS 6). Rodar UMA VEZ como root.
# Cria usuario de deploy dedicado, instala Docker, endurece o SSH e prepara a arvore dos 3 ambientes.
# Card: TRE-W1/W2 (provisionamento). Nao instala PostgreSQL/Odoo ainda — isso e feito por compose, depois.
set -euo pipefail

USUARIO="${TRE_USER:-tre-deploy}"
PUBKEY="${TRE_PUBKEY:-}"
TZ_ALVO="${TRE_TZ:-America/Sao_Paulo}"

[ "$(id -u)" -eq 0 ] || { echo "FALHOU: rodar como root"; exit 1; }
[ -n "$PUBKEY" ] || { echo "FALHOU: defina TRE_PUBKEY com a chave publica do Hermes"; exit 1; }

echo "== 1. pacotes base =="
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ca-certificates curl gnupg git ufw fail2ban unattended-upgrades jq unzip >/dev/null
timedatectl set-timezone "$TZ_ALVO" || true

echo "== 2. Docker (repo oficial) =="
install -m 0755 -d /etc/apt/keyrings
if [ ! -f /etc/apt/keyrings/docker.asc ]; then
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
fi
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update -qq
apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null
systemctl enable --now docker >/dev/null 2>&1

echo "== 3. usuario de deploy =="
if ! id "$USUARIO" >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" "$USUARIO" >/dev/null
fi
usermod -aG docker,sudo "$USUARIO"
install -d -m 700 -o "$USUARIO" -g "$USUARIO" "/home/$USUARIO/.ssh"
# merge idempotente da chave
touch "/home/$USUARIO/.ssh/authorized_keys"
grep -qF "$PUBKEY" "/home/$USUARIO/.ssh/authorized_keys" || echo "$PUBKEY" >> "/home/$USUARIO/.ssh/authorized_keys"
chown "$USUARIO:$USUARIO" "/home/$USUARIO/.ssh/authorized_keys"
chmod 600 "/home/$USUARIO/.ssh/authorized_keys"
# sudo sem senha (equivalente ao acesso ao Docker; documentado no ADR-0007 e no runbook)
printf '%s ALL=(ALL) NOPASSWD:ALL\n' "$USUARIO" > "/etc/sudoers.d/90-$USUARIO"
chmod 440 "/etc/sudoers.d/90-$USUARIO"
visudo -c >/dev/null

echo "== 4. arvore dos ambientes =="
for envdir in dev homolog prod; do
  mkdir -p "/opt/tre/$envdir"/{pg,odoo,n8n,backups,compose}
done
mkdir -p /opt/tre/repo /opt/tre/backup
chown -R "$USUARIO:$USUARIO" /opt/tre

echo "== 5. firewall e SSH =="
ufw --force reset >/dev/null 2>&1 || true
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow 22/tcp >/dev/null
ufw --force enable >/dev/null
systemctl enable --now fail2ban >/dev/null 2>&1
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true

echo "== 6. atualizacoes automaticas de seguranca =="
echo 'Unattended-Upgrade::Automatic-Reboot "false";' > /etc/apt/apt.conf.d/51tre-unattended
dpkg-reconfigure -f noninteractive unattended-upgrades >/dev/null 2>&1 || true

echo
echo "RESUMO"
echo "  usuario:        $USUARIO (grupos: docker, sudo; sudo sem senha)"
echo "  docker:         $(docker --version)"
echo "  compose:        $(docker compose version --short 2>/dev/null || echo n/d)"
echo "  arvore:         /opt/tre/{dev,homolog,prod}/{pg,odoo,n8n,backups,compose}"
echo "  firewall:       $(ufw status | head -1)"
echo "  ssh:            senha desabilitada, root sem senha"
echo "  fuso:           $(timedatectl show -p Timezone --value)"
echo "RESULTADO: BOOTSTRAP_OK"
