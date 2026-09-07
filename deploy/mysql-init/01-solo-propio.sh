#!/bin/bash
# Primera vez que arranca el volumen: el usuario de la API solo toca asistencia_db.
# El celular no usa este usuario. Entra al tablero con JWT y ve lo suyo.
set -euo pipefail
mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" <<'SQL'
REVOKE ALL PRIVILEGES, GRANT OPTION FROM 'asistencia'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON asistencia_db.* TO 'asistencia'@'%';
FLUSH PRIVILEGES;
SQL
