"""Catálogos base y matrícula de demostración para la laptop."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from prisma import Prisma

TZ = ZoneInfo("America/Guatemala")

# Catálogo de la vespertina según los listados SIRE. Id corto para el API;
# nombre y sección se muestran en el tablero.
GRADOS: list[tuple[str, str, str, int]] = [
    ("P1A", "Párvulos 1", "A", 1),
    ("P2A", "Párvulos 2", "A", 2),
    ("P2B", "Párvulos 2", "B", 3),
    ("P3A", "Párvulos 3", "A", 4),
    ("P3B", "Párvulos 3", "B", 5),
    ("1A", "1ro Primaria", "A", 6),
    ("1B", "1ro Primaria", "B", 7),
    ("2A", "2do Primaria", "A", 8),
    ("3A", "3ro Primaria", "A", 9),
    ("4A", "4to Primaria", "A", 10),
    ("4B", "4to Primaria", "B", 11),
    ("5A", "5to Primaria", "A", 12),
    ("5B", "5to Primaria", "B", 13),
    ("6A", "6to Primaria", "A", 14),
]

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


async def seed_catalogos(db: Prisma, ips: list[str]) -> None:
    """Grados y relojes. Sin esto no se puede matricular a nadie."""
    for grado_id, nombre, seccion, orden in GRADOS:
        await db.grado.upsert(
            where={"id": grado_id},
            data={
                "create": {"id": grado_id, "nombre": nombre, "seccion": seccion, "orden": orden},
                "update": {"nombre": nombre, "seccion": seccion, "orden": orden},
            },
        )

    for numero, ip in enumerate(ips, start=1):
        await db.dispositivo.upsert(
            where={"ip": ip},
            data={
                "create": {"ip": ip, "nombre": f"Reloj {numero}"},
                "update": {},
            },
        )


async def seed_demo_si_vacio(db: Prisma) -> None:
    existe = await db.persona.find_first(where={"rol": "ALUMNO"})
    if existe is not None:
        return

    hoy = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    reloj = await db.dispositivo.find_first()

    for index, (nombre, grado) in enumerate(ALUMNOS):
        persona = await db.persona.create(
            data={
                "nombre": nombre,
                "cui": f"{3000000000000 + index}",
                "codigo": f"{3000000000000 + index}",
                "employeeNo": f"DEMO-A-{index + 1:02d}",
                "rol": "ALUMNO",
                "detalleAlumno": {
                    "create": {"gradoId": grado, "telefonoPadres": "0000-0000"},
                },
            }
        )
        # 1 de cada 6 ausente; 1 de cada 5 presente llega tarde.
        if index % 6 == 5:
            continue
        minutos = (13 * 60 + 22) if index % 5 == 0 else (12 * 60 + 54 + (index % 8))
        await db.asistencia.create(
            data={
                "personaId": persona.id,
                "dispositivoId": reloj.id if reloj else None,
                "fechaHora": hoy + timedelta(minutes=minutos),
                "tipo": "ENTRADA",
                "metodo": "ROSTRO",
                "serialEvento": f"DEMO-A-{index + 1}-{hoy.date().isoformat()}",
            }
        )

    for index, (nombre, cargo) in enumerate(CATEDRATICOS):
        persona = await db.persona.create(
            data={
                "nombre": nombre,
                "cui": f"{4000000000000 + index}",
                "codigo": f"{4000000000000 + index}",
                "employeeNo": f"DEMO-M-{index + 1:02d}",
                "rol": "CATEDRATICO",
                "detalleCatedratico": {
                    "create": {"cargo": cargo, "telefono": "0000-0000"},
                },
            }
        )
        if index == 7:
            continue
        minutos = (13 * 60 + 8) if index == 5 else (12 * 60 + 38 + index)
        await db.asistencia.create(
            data={
                "personaId": persona.id,
                "dispositivoId": reloj.id if reloj else None,
                "fechaHora": hoy + timedelta(minutes=minutos),
                "tipo": "ENTRADA",
                "metodo": "ROSTRO",
                "serialEvento": f"DEMO-M-{index + 1}-{hoy.date().isoformat()}",
            }
        )
        if index < 6:
            await db.asistencia.create(
                data={
                    "personaId": persona.id,
                    "dispositivoId": reloj.id if reloj else None,
                    "fechaHora": hoy + timedelta(hours=17, minutes=22 + index),
                    "tipo": "SALIDA",
                    "metodo": "ROSTRO",
                    "serialEvento": f"DEMO-M-{index + 1}-OUT-{hoy.date().isoformat()}",
                }
            )
