#!/bin/bash
# Arranca la imagen oficial de FreeBSD dentro de QEMU.
# El contenedor publica 2224; el router de la escuela manda 4010 → esta máquina:2224.
set -euo pipefail

DISK_DIR=/var/lib/freebsd
DISK="$DISK_DIR/freebsd.qcow2"
IMAGE_XZ="$DISK_DIR/freebsd.qcow2.xz"
URL="${FREEBSD_IMAGE_URL:?}"

apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  qemu-system-x86 qemu-utils wget ca-certificates xz-utils

mkdir -p "$DISK_DIR"

if [ ! -f "$DISK" ]; then
  echo "Descargando imagen FreeBSD (primera vez, varios minutos)..."
  wget -O "$IMAGE_XZ" "$URL"
  xz -T0 -dk "$IMAGE_XZ"
  SRC="${IMAGE_XZ%.xz}"
  if [ "$SRC" != "$DISK" ]; then
    mv -f "$SRC" "$DISK"
  fi
  rm -f "$IMAGE_XZ"
  qemu-img resize "$DISK" 20G || true
fi

ACCEL=(-accel tcg)
if [ -e /dev/kvm ]; then
  ACCEL=(-enable-kvm -cpu host)
fi

exec qemu-system-x86_64 \
  -machine q35 \
  -m 2048 \
  -smp 2 \
  "${ACCEL[@]}" \
  -drive file="$DISK",if=virtio,format=qcow2 \
  -netdev user,id=net0,hostfwd=tcp::2224-:22 \
  -device virtio-net-pci,netdev=net0 \
  -nographic \
  -serial mon:stdio
