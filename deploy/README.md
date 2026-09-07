# Red de la escuela: FreeBSD (QEMU) + MySQL

En Debian, Docker no puede correr FreeBSD de verdad (el kernel es Linux).
Este compose baja la imagen oficial de FreeBSD y la emula con QEMU.

## Arranque

```bash
cd deploy
cp .env.ejemplo .env   # editar MYSQL_CLAVE y MYSQL_ROOT
sudo bash setup-red.sh
```

## SSH 4010 → 2224 → 22

| Tramo | Puerto | Quién |
| --- | --- | --- |
| Router / firewall de la escuela | **4010** | Redirige a la IP del Debian, puerto 2224 |
| Debian (contenedor QEMU) | **2224** | `hostfwd` de QEMU |
| FreeBSD | **22** | `sshd` |

Desde el celular o una laptop fuera de la aldea:

```bash
ssh -p 4010 root@IP-PUBLICA-O-ROUTER
```

En la LAN:

```bash
ssh -p 2224 root@IP-DEBIAN
```

Primera vez (consola de la VM):

```bash
docker attach asistencia-freebsd-qemu
# En FreeBSD:
passwd
echo 'sshd_enable="YES"' >> /etc/rc.conf
service sshd start
```

`Ctrl-P Ctrl-Q` suelta la consola sin apagar QEMU.

## MySQL en la red (sin abrir 3306 al celular)

MySQL queda en **127.0.0.1:3306**. La API de la escuela es quien lee y escribe.
El usuario `asistencia` solo tiene privilegios sobre `asistencia_db`.

El smartphone o la tablet **no** se conectan a MySQL. Entran al tablero con
una cuenta de rol **Consulta propia**, ligada a un alumno o a un maestro.
Esa cuenta solo ve sus propios marcajes.

`DATABASE_URL` de la API:

```
mysql://asistencia:CLAVE@127.0.0.1:3306/asistencia_db
```
