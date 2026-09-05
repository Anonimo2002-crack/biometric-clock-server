"""Carga teléfonos de Párvulos 3 A y el listado de maestros en MySQL local."""

from __future__ import annotations

import re
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pymysql
import openpyxl

DOCX = Path(
    r"c:\Users\geova\OneDrive\Desktop\10 semestre\Seminario de tecnologías de información\LISTADO PARVULOS 3 A, TELEFONOS.docx"
)
XLSX = Path(
    r"c:\Users\geova\OneDrive\Desktop\10 semestre\Seminario de tecnologías de información\Maestros 1.1 (2)-3.xlsx"
)
NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


def norm(texto: str) -> str:
    texto = unicodedata.normalize("NFD", texto or "")
    texto = "".join(ch for ch in texto if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", texto).strip().upper()


def tokens(texto: str) -> set[str]:
    return {p for p in re.split(r"[^A-Z]+", norm(texto)) if len(p) > 1}


def solo_digitos(valor: object, tope: int) -> str:
    return re.sub(r"\D", "", str(valor or ""))[:tope]


def leer_parvulos() -> list[tuple[str, str]]:
    root = ET.fromstring(zipfile.ZipFile(DOCX).read("word/document.xml"))
    filas: list[tuple[str, str]] = []
    for tr in root.findall(".//w:tbl/w:tr", NS)[1:]:
        celdas = []
        for tc in tr.findall("w:tc", NS):
            celdas.append("".join((t.text or "") for t in tc.findall(".//w:t", NS)).strip())
        if len(celdas) < 4 or not celdas[1]:
            continue
        nombre = f"{celdas[2]} {celdas[1]}".strip()
        filas.append((nombre, solo_digitos(celdas[3], 8)))
    return filas


def leer_maestros() -> list[dict[str, str]]:
    wb = openpyxl.load_workbook(XLSX, data_only=True)
    ws = wb.active
    filas: list[dict[str, str]] = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        dpi = solo_digitos(row[1], 13)
        nombre = str(row[2] or "").strip()
        cargo = str(row[3] or "").strip()
        telefono = solo_digitos(row[4], 8)
        correo = re.sub(r"\s+", "", str(row[5] or "").strip()) or None
        if not nombre or not dpi:
            continue
        filas.append(
            {
                "cui": dpi,
                "nombre": " ".join(nombre.split()),
                "cargo": cargo or "Docente",
                "telefono": telefono,
                "correo": correo,
            }
        )
    return filas


def mejor_match(nombre: str, candidatos: list[tuple[int, str]]) -> tuple[int, str] | None:
    buscado = tokens(nombre)
    ganador = None
    mejor = 0.0
    for pid, actual in candidatos:
        comunes = len(buscado & tokens(actual))
        if comunes < 3:
            continue
        score = comunes / max(len(buscado | tokens(actual)), 1)
        if score > mejor:
            mejor = score
            ganador = (pid, actual)
    return ganador if mejor >= 0.45 else None


INICIO_NUMERO = {"ALUMNO": 1000, "CATEDRATICO": 100, "ADMIN": 1}
TOPE_NUMERO = {"CATEDRATICO": 999, "ADMIN": 99}


def siguiente_employee(cur, rol: str) -> str:
    desde = INICIO_NUMERO.get(rol, 1000)
    tope = TOPE_NUMERO.get(rol)
    cur.execute("SELECT employeeNo FROM personas WHERE employeeNo REGEXP '^[0-9]+$'")
    usados = {int(fila[0]) for fila in cur.fetchall()}
    numero = desde
    while numero in usados:
        numero += 1
        if tope is not None and numero > tope:
            raise RuntimeError(f"Sin números de reloj libres para {rol}.")
    return str(numero)


def main() -> None:
    cn = pymysql.connect(
        host="127.0.0.1",
        user="root",
        password="admin98",
        database="asistencia_db",
        charset="utf8mb4",
    )
    cur = cn.cursor()

    cur.execute(
        """
        SELECT p.id, p.nombre
        FROM personas p
        JOIN alumnos_detalle d ON d.personaId = p.id
        WHERE d.gradoId IN ('P3A', 'P3B')
        """
    )
    candidatos = list(cur.fetchall())

    print("=== Párvulos 3 A: teléfonos ===")
    actualizados = 0
    sin_match = []
    for nombre, telefono in leer_parvulos():
        hit = mejor_match(nombre, candidatos)
        if not hit or len(telefono) != 8:
            sin_match.append((nombre, telefono))
            print(f"  SIN MATCH  {nombre}  {telefono}")
            continue
        pid, actual = hit
        cur.execute(
            "UPDATE alumnos_detalle SET telefonoPadres=%s WHERE personaId=%s",
            (telefono, pid),
        )
        actualizados += 1
        print(f"  OK  {actual}  <-  {telefono}")

    print("=== Maestros ===")
    insertados = 0
    for item in leer_maestros():
        cur.execute("SELECT id, rol FROM personas WHERE cui=%s OR codigo=%s", (item["cui"], item["cui"]))
        existe = cur.fetchone()
        if existe:
            pid = existe[0]
            cur.execute(
                """
                UPDATE personas SET nombre=%s, rol='CATEDRATICO', activo=1, actualizadoEn=NOW(3)
                WHERE id=%s
                """,
                (item["nombre"], pid),
            )
            cur.execute("SELECT personaId FROM catedraticos_detalle WHERE personaId=%s", (pid,))
            if cur.fetchone():
                cur.execute(
                    "UPDATE catedraticos_detalle SET cargo=%s, telefono=%s, correo=%s WHERE personaId=%s",
                    (item["cargo"], item["telefono"] or "00000000", item["correo"], pid),
                )
            else:
                cur.execute(
                    "INSERT INTO catedraticos_detalle (personaId, cargo, telefono, correo) VALUES (%s,%s,%s,%s)",
                    (pid, item["cargo"], item["telefono"] or "00000000", item["correo"]),
                )
            print(f"  UPDATE  {item['nombre']}")
            continue
        emp = siguiente_employee(cur, "CATEDRATICO")
        cur.execute(
            """
            INSERT INTO personas (nombre, cui, codigo, employeeNo, rol, activo, creadoEn, actualizadoEn)
            VALUES (%s,%s,%s,%s,'CATEDRATICO',1,NOW(3),NOW(3))
            """,
            (item["nombre"], item["cui"], item["cui"], emp),
        )
        pid = cur.lastrowid
        cur.execute(
            "INSERT INTO catedraticos_detalle (personaId, cargo, telefono, correo) VALUES (%s,%s,%s,%s)",
            (pid, item["cargo"], item["telefono"] or "00000000", item["correo"]),
        )
        insertados += 1
        print(f"  INSERT  {item['nombre']}  reloj {emp}  CUI {item['cui']}")

    cn.commit()
    print(f"\nTeléfonos P3A: {actualizados}  sin match: {len(sin_match)}")
    print(f"Maestros nuevos: {insertados}")
    cur.close()
    cn.close()


if __name__ == "__main__":
    main()
