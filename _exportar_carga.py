"""Arma un SQL listo para pegar en el MySQL del Debian."""

from pathlib import Path

import pymysql

DESTINO = Path(__file__).resolve().parent / "carga-parvulos3a-maestros.sql"


def esc(valor: object) -> str:
    if valor is None:
        return "NULL"
    texto = str(valor).replace("\\", "\\\\").replace("'", "''")
    return f"'{texto}'"


def main() -> None:
    cn = pymysql.connect(
        host="127.0.0.1",
        user="root",
        password="admin98",
        database="asistencia_db",
        charset="utf8mb4",
    )
    cur = cn.cursor()
    lineas = [
        "-- Carga: teléfonos Párvulos 3 A + maestros.",
        "-- Correr en el servidor: mysql -u asistencia -p asistencia_db < carga-parvulos3a-maestros.sql",
        "SET NAMES utf8mb4;",
        "",
        "-- Teléfonos de Párvulos 3 A (actualiza por CUI, no borra a nadie)",
    ]

    cur.execute(
        """
        SELECT p.cui, d.telefonoPadres
        FROM personas p
        JOIN alumnos_detalle d ON d.personaId = p.id
        WHERE d.gradoId IN ('P3A', 'P3B') AND CHAR_LENGTH(d.telefonoPadres) = 8
          AND d.telefonoPadres NOT IN ('00000000', '')
        """
    )
    for cui, tel in cur.fetchall():
        lineas.append(
            "UPDATE alumnos_detalle d JOIN personas p ON p.id = d.personaId "
            f"SET d.telefonoPadres = {esc(tel)} WHERE p.cui = {esc(cui)};"
        )

    lineas += ["", "-- Maestros (si el CUI ya existe, solo actualiza cargo/teléfono/correo)"]
    cur.execute(
        """
        SELECT p.nombre, p.cui, p.codigo, p.employeeNo, p.rol, p.activo,
               c.cargo, c.telefono, c.correo
        FROM personas p
        JOIN catedraticos_detalle c ON c.personaId = p.id
        WHERE p.rol = 'CATEDRATICO'
        ORDER BY CAST(p.employeeNo AS UNSIGNED)
        """
    )
    for nombre, cui, codigo, emp, rol, activo, cargo, tel, correo in cur.fetchall():
        lineas.append(
            "INSERT INTO personas (nombre, cui, codigo, employeeNo, rol, activo, creadoEn, actualizadoEn) "
            f"VALUES ({esc(nombre)}, {esc(cui)}, {esc(codigo)}, {esc(emp)}, {esc(rol)}, {int(activo)}, NOW(3), NOW(3)) "
            "ON DUPLICATE KEY UPDATE nombre = VALUES(nombre), employeeNo = VALUES(employeeNo), "
            "rol = 'CATEDRATICO', activo = 1, actualizadoEn = NOW(3);"
        )
        lineas.append(
            "INSERT INTO catedraticos_detalle (personaId, cargo, telefono, correo) "
            f"SELECT id, {esc(cargo)}, {esc(tel)}, {esc(correo)} FROM personas WHERE cui = {esc(cui)} "
            "ON DUPLICATE KEY UPDATE cargo = VALUES(cargo), telefono = VALUES(telefono), correo = VALUES(correo);"
        )

    DESTINO.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    print(DESTINO)
    cur.close()
    cn.close()


if __name__ == "__main__":
    main()
