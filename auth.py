"""Login JWT y roles del sistema web (no confundir con ALUMNO/CATEDRATICO del reloj)."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from dotenv import load_dotenv

load_dotenv()

from database import db

ROLES_SISTEMA = ("ADMIN", "DIRECCION", "SECRETARIA", "DOCENTE")
ROLES_CONSULTA = ROLES_SISTEMA
ROLES_EXPORTAR = ("ADMIN", "DIRECCION", "SECRETARIA")
ROLES_MATRICULA_VER = ("ADMIN", "DIRECCION", "SECRETARIA")
ROLES_MATRICULA_ESCRIBIR = ("ADMIN", "SECRETARIA")
ROLES_USUARIOS = ("ADMIN",)
ROLES_SYNC = ("ADMIN", "SECRETARIA")
ROLES_MARCAR = ("ADMIN", "SECRETARIA", "DOCENTE", "DIRECCION")
ROLES_SMTP = ("ADMIN", "DIRECCION", "SECRETARIA")
ROLES_DISPOSITIVO = ("ADMIN",)

JWT_SECRET = os.getenv("JWT_SECRET", "").strip()
JWT_HOURS = int(os.getenv("JWT_HOURS", "12"))
ALGORITHM = "HS256"

# Quien conozca el secreto puede fabricarse un token de administrador sin saber
# ninguna clave. Por eso el sistema no arranca si quedó el de ejemplo o uno corto.
SECRETOS_PROHIBIDOS = {"cambia-este-secreto-en-el-servidor", "dev-seminario-agua-de-la-mina"}
if not JWT_SECRET or JWT_SECRET in SECRETOS_PROHIBIDOS or len(JWT_SECRET) < 32:
    raise RuntimeError(
        "JWT_SECRET falta o es débil. Poné uno largo en el .env; se genera con:\n"
        '  python -c "import secrets; print(secrets.token_urlsafe(48))"'
    )

# Freno contra la fuerza bruta en el login.
LOGIN_INTENTOS = int(os.getenv("LOGIN_INTENTOS", "5"))
LOGIN_BLOQUEO = timedelta(minutes=int(os.getenv("LOGIN_BLOQUEO_MIN", "15")))

_bearer = HTTPBearer(auto_error=True)

# Fallos recientes por origen. Vive en memoria porque es un solo servidor en la
# escuela; si algún día son varios, esto tendría que pasar a la base.
_fallos: dict[str, list[datetime]] = {}


def _vigentes(clave: str, ahora: datetime) -> list[datetime]:
    recientes = [dato for dato in _fallos.get(clave, []) if ahora - dato < LOGIN_BLOQUEO]
    if recientes:
        _fallos[clave] = recientes
    else:
        _fallos.pop(clave, None)
    return recientes


def revisar_intentos(clave: str) -> None:
    """Corta el login si ese origen ya falló demasiadas veces seguidas."""
    ahora = datetime.now(timezone.utc)
    recientes = _vigentes(clave, ahora)
    if len(recientes) >= LOGIN_INTENTOS:
        espera = LOGIN_BLOQUEO - (ahora - recientes[0])
        minutos = max(1, int(espera.total_seconds() // 60) + 1)
        raise HTTPException(
            status_code=429,
            detail=f"Demasiados intentos fallidos. Esperá {minutos} minutos y volvé a probar.",
        )


def anotar_fallo(clave: str) -> None:
    ahora = datetime.now(timezone.utc)
    _fallos.setdefault(clave, []).append(ahora)
    _vigentes(clave, ahora)


def limpiar_intentos(clave: str) -> None:
    _fallos.pop(clave, None)


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def crear_token(usuario: Any) -> str:
    ahora = datetime.now(timezone.utc)
    payload = {
        "sub": str(usuario.id),
        "usuario": usuario.usuario,
        "nombre": usuario.nombre,
        "rol": usuario.rol,
        "iat": int(ahora.timestamp()),
        "exp": int((ahora + timedelta(hours=JWT_HOURS)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=ALGORITHM)


def _payload_de(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="La sesión venció. Entra de nuevo.") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="Token inválido.") from exc


async def usuario_actual(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
) -> Any:
    payload = _payload_de(creds.credentials)
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Token inválido.") from exc
    fila = await db.usuario.find_unique(where={"id": user_id})
    if fila is None or not fila.activo:
        raise HTTPException(status_code=401, detail="Usuario inactivo o inexistente.")
    return fila


def require_roles(*roles: str):
    permitidos = set(roles)

    async def _dep(usuario: Any = Depends(usuario_actual)) -> Any:
        if usuario.rol not in permitidos:
            raise HTTPException(status_code=403, detail="No tienes permiso para esta acción.")
        return usuario

    return _dep
