"""Cuentas del tablero para el personal docente (y dirección).

Qué hace
  Lee los maestros activos de `personas` (rol CATEDRATICO) y crea una cuenta
  en `usuarios` si todavía no existe. No toca alumnos, matrícula ni marcajes.

  - Docente: rol DOCENTE. Usuario = primer nombre + primer apellido.
  - Directora / director: rol ADMIN (mismo acceso que el administrador).
  - Contraseña nueva: Mina + número del reloj + 2026  (ejemplo: Mina1002026).
  - Si el usuario ya existe, no cambia la contraseña. Si es dirección y
    todavía tiene rol DIRECCION, lo pasa a ADMIN.

Qué no hace
  No borra datos. No vuelve a cargar estudiantes. No pisa claves ya creadas.

Cómo correrlo en la base del instituto
  1. En el .env, DATABASE_URL debe apuntar a esa MySQL (no a la local).
  2. Desde la carpeta del servidor, con el venv activo:

       python crear_cuentas_tablero.py

  3. Anotar la tabla que imprime (usuario y contraseña de las cuentas nuevas).
     Las filas "ya existía" no muestran clave porque no se regeneró.
"""

from __future__ import annotations

import asyncio
import re
import unicodedata

from dotenv import load_dotenv

load_dotenv()

import bcrypt
from prisma import Prisma

PARTICULAS = {"de", "del", "la", "las", "los", "y", "da", "do"}


def sin_acentos(texto: str) -> str:
    nfd = unicodedata.normalize("NFD", texto)
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn")


def usuario_de(nombre: str) -> str:
    limpio = re.sub(r"[^a-zA-ZñÑ\s]", " ", sin_acentos(nombre))
    partes = [p.lower() for p in limpio.split() if p.lower() not in PARTICULAS]
    if len(partes) >= 4:
        return f"{partes[0]}.{partes[2]}"
    if len(partes) >= 2:
        return f"{partes[0]}.{partes[1]}"
    return partes[0] if partes else "maestro"


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def es_direccion(cargo: str) -> bool:
    return "director" in cargo.lower()


async def main() -> None:
    db = Prisma()
    await db.connect()
    existentes = {fila.usuario.lower(): fila for fila in await db.usuario.find_many()}
    maestros = await db.persona.find_many(
        where={"rol": "CATEDRATICO", "activo": True},
        include={"detalleCatedratico": True},
        order={"employeeNo": "asc"},
    )
    if not maestros:
        print("No hay maestros activos en personas. Este script no carga matrícula.")
        await db.disconnect()
        return

    print("NOMBRE\tCARGO\tROL\tUSUARIO\tCONTRASEÑA\tESTADO")
    for persona in maestros:
        detalle = persona.detalleCatedratico
        cargo = (detalle.cargo if detalle else "") or "Docente"
        rol = "ADMIN" if es_direccion(cargo) else "DOCENTE"
        usuario = usuario_de(persona.nombre)
        clave = f"Mina{persona.employeeNo}2026"
        actual = existentes.get(usuario.lower())
        if actual is not None:
            extras = {}
            if actual.personaId != persona.id:
                extras["personaId"] = persona.id
            if es_direccion(cargo) and actual.rol != "ADMIN":
                extras["rol"] = "ADMIN"
            if extras:
                await db.usuario.update(where={"id": actual.id}, data=extras)
                print(f"{persona.nombre}\t{cargo}\t{extras.get('rol', actual.rol)}\t{usuario}\t(sin cambio)\tactualizado")
            else:
                print(f"{persona.nombre}\t{cargo}\t{actual.rol}\t{usuario}\t(sin cambio)\tya existía")
            continue
        creado = await db.usuario.create(
            data={
                "nombre": persona.nombre,
                "usuario": usuario,
                "passwordHash": hash_password(clave),
                "rol": rol,
                "activo": True,
                "personaId": persona.id,
            }
        )
        existentes[usuario.lower()] = creado
        print(f"{persona.nombre}\t{cargo}\t{rol}\t{usuario}\t{clave}\tcreado")
    await db.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
