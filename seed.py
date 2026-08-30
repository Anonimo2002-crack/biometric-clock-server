"""Matrícula de demostración para la laptop, cuando no hay reloj conectado."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from prisma import Prisma

TZ = ZoneInfo("America/Guatemala")

ALUMNOS: list[tuple[str, str]] = [
    ("Ana López", "1A"),
    ("Carlos Pérez", "1A"),
    ("María Elena García", "1A"),
    ("José Martínez", "1A"),
    ("Lucía Hernández", "2A"),
    ("Diego Morales", "2A"),
    ("Sofía Ramírez", "2A"),
    ("Andrés Castillo", "2A"),
    ("Valeria Soto", "3A"),
    ("Pablo Vásquez", "3A"),
    ("Camila Ortiz", "3A"),
    ("Luis Recinos", "3A"),
    ("Elena Chávez", "4A"),
    ("Mateo Díaz", "4A"),
    ("Isabel Fuentes", "4A"),
    ("Daniel Méndez", "4A"),
    ("Fernanda Ruiz", "5A"),
    ("Javier Aguilar", "5A"),
    ("Paula Navarro", "5A"),
    ("Sebastián López", "5A"),
    ("Rosa Cifuentes", "6A"),
    ("Héctor Salazar", "6A"),
    ("Mirna Paz", "6A"),
    ("Óscar López", "6A"),
]

CATEDRATICOS: list[tuple[str, str]] = [
    ("Rosa María Cifuentes", "Docente 1ro A"),
    ("Pedro Antonio Gómez", "Docente 2do A"),
    ("Claudia Patricia Mejía", "Docente 3ro A"),
    ("Héctor René Salazar", "Docente 4to A"),
    ("Mirna Elizabeth Paz", "Docente 5to A"),
    ("Óscar Armando López", "Docente 6to A"),
    ("Karla Judith Recinos", "Educación física"),
    ("Marvin Estuardo Díaz", "Apoyo administrativo"),
]


async def seed_demo_si_vacio(db: Prisma) -> None:
    existe = await db.alumno.find_first(where={"rol": "ALUMNO"})
    if existe is not None:
        return

    hoy = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)

    for index, (nombre, grado) in enumerate(ALUMNOS):
        persona = await db.alumno.create(
            data={
                "nombre": nombre,
                "codigo": f"ALU-{grado}-{index + 1:02d}",
                "employeeNo": f"DEMO-A-{index + 1:02d}",
                "grado": grado,
                "rol": "ALUMNO",
            }
        )
        # 1 de cada 6 ausente; 1 de cada 5 presente llega tarde.
        if index % 6 == 5:
            continue
        minutos = (13 * 60 + 22) if index % 5 == 0 else (12 * 60 + 54 + (index % 8))
        await db.asistencia.create(
            data={
                "alumnoId": persona.id,
                "fechaHora": hoy + timedelta(minutes=minutos),
                "tipo": "ENTRADA",
                "metodo": "ROSTRO",
                "serialEvento": f"DEMO-A-{index + 1}-{hoy.date().isoformat()}",
            }
        )

    for index, (nombre, cargo) in enumerate(CATEDRATICOS):
        persona = await db.alumno.create(
            data={
                "nombre": nombre,
                "codigo": f"DOC-{index + 1:02d}",
                "employeeNo": f"DEMO-M-{index + 1:02d}",
                "cargo": cargo,
                "rol": "CATEDRATICO",
            }
        )
        if index == 7:
            continue
        minutos = (13 * 60 + 8) if index == 5 else (12 * 60 + 38 + index)
        await db.asistencia.create(
            data={
                "alumnoId": persona.id,
                "fechaHora": hoy + timedelta(minutes=minutos),
                "tipo": "ENTRADA",
                "metodo": "ROSTRO",
                "serialEvento": f"DEMO-M-{index + 1}-{hoy.date().isoformat()}",
            }
        )
        if index < 6:
            await db.asistencia.create(
                data={
                    "alumnoId": persona.id,
                    "fechaHora": hoy + timedelta(hours=17, minutes=22 + index),
                    "tipo": "SALIDA",
                    "metodo": "ROSTRO",
                    "serialEvento": f"DEMO-M-{index + 1}-OUT-{hoy.date().isoformat()}",
                }
            )
