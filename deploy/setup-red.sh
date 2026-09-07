#!/bin/bash
# Agrega a la red de la escuela:
#   1) SSH FreeBSD por QEMU   4010 → 2224 → 22
#   2) MySQL solo en localhost; el celular lee por el tablero, no por 3306
#
# En el Debian (sudo):
#   cd /opt/biometric-clock-server/deploy
#   cp .env.ejemplo .env   # editar claves
#   sudo bash setup-red.sh
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

if [ ! -f .env ]; then
  echo "Falta deploy/.env. Copia .env.ejemplo y poné las claves."
  exit 1
fi

# shellcheck disable=SC1091
set -a
source .env
set +a

echo "== Docker =="
if ! command -v docker >/dev/null; then
  apt-get update
  apt-get install -y docker.io docker-compose-v2
  systemctl enable --now docker
fi

chmod +x freebsd-boot.sh mysql-init/01-solo-propio.sh

echo "== MySQL (127.0.0.1:3306) y QEMU FreeBSD (:2224 → :22) =="
docker compose up -d

echo "== Firewall: SSH QEMU sí; MySQL no sale a la LAN =="
if command -v ufw >/dev/null; then
  ufw allow 2224/tcp comment 'QEMU FreeBSD SSH (el router manda 4010 aqui)'
  ufw allow 80/tcp comment 'tablero'
  ufw allow 443/tcp comment 'tablero tls'
  ufw allow 8000/tcp comment 'api (solo si no hay nginx)'
  ufw deny 3306/tcp comment 'MySQL solo localhost'
  ufw --force enable
fi

echo
echo "Listo."
echo "  SSH desde afuera:  ssh -p 4010 USUARIO@IP-DEL-ROUTER"
echo "                     el router reenvía 4010/tcp → IP-DEBIAN:2224"
echo "  SSH en la LAN:     ssh -p 2224 root@IP-DEBIAN"
echo "  Primera vez FreeBSD: docker attach asistencia-freebsd-qemu"
echo "                     passwd  &&  echo 'sshd_enable=\"YES\"' >> /etc/rc.conf"
echo "                     service sshd start"
echo "  MySQL:             solo 127.0.0.1:3306  (API / prisma)"
echo "  Celular/tablet:    el tablero (login). Rol «Consulta propia» = solo lo suyo."
echo
echo "DATABASE_URL de la API:"
echo "  mysql://asistencia:${MYSQL_CLAVE}@127.0.0.1:3306/asistencia_db"
