"""API de asistencia EORM Agua de la Mina — Jornada Vespertina."""

from __future__ import annotations

import asyncio
import os
import re
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from prisma.errors import UniqueViolationError
from pydantic import BaseModel, Field, field_serializer

load_dotenv()

from auth import (
    ROLES_CONSULTA,
    ROLES_DISPOSITIVO,
    ROLES_EXPORTAR,
    ROLES_MATRICULA_ESCRIBIR,
    ROLES_MATRICULA_VER,
    ROLES_SISTEMA,
    ROLES_SMTP,
    ROLES_SYNC,
    ROLES_USUARIOS,
    anotar_fallo,
    crear_token,
    hash_password,
    limpiar_intentos,
    require_roles,
    revisar_intentos,
    usuario_actual,
    verify_password,
)
from correo import CorreoError, enviar_ausencias, smtp_configurado
from database import db
from exportes import (
    armar_excel,
    armar_pdf,
    asistencia_filas,
    ausencias_filas,
    dashboard_filas,
    maestros_filas,
)
from hikvision import (
    MINOR_HUELLA_OK,
    MINOR_ROSTRO_OK,
    MINOR_TARJETA_OK,
    MINORES_ASISTENCIA,
    MINORES_FALLIDOS,
    HikvisionClient,
    HikvisionError,
)
from reportes import (
    armar_asistencia_grado,
    armar_ausencias,
    armar_dashboard,
    armar_maestros,
    catalogo_grados,
)
from seed import seed_catalogos, seed_demo_si_vacio
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Guatemala")
ROLES_RELOJ = {"ALUMNO", "CATEDRATICO", "ADMIN"}
HORAS_CORTE_VALIDAS = {"13:15", "14:00", "15:00", "16:00"}

DEVICE_IP = os.getenv("DEVICE_IP", "192.168.1.21")
DEVICE_IP_2 = os.getenv("DEVICE_IP_2", "").strip()
DEVICE_USER = os.getenv("DEVICE_USER", "admin")
DEVICE_PASS = os.getenv("DEVICE_PASS", "")
# Cada reloj puede traer su propio usuario y clave. Si no se ponen, usa los del
# primero. Ojo que las claves de Hikvision distinguen mayúsculas.
DEVICE_USER_2 = os.getenv("DEVICE_USER_2", "").strip() or DEVICE_USER
DEVICE_PASS_2 = os.getenv("DEVICE_PASS_2", "") or DEVICE_PASS
DEVICE_TIMEOUT = int(os.getenv("DEVICE_TIMEOUT", "10"))
# Cada cuántos minutos bajar los marcajes solo. En 0 queda apagado y hay que
# darle al botón del tablero.
SYNC_AUTO_MIN = int(os.getenv("SYNC_AUTO_MIN", "10"))
# Días que revisa la primera corrida, para recoger lo que quedó en los relojes
# mientras el servidor estuvo apagado.
SYNC_AUTO_DIAS = int(os.getenv("SYNC_AUTO_DIAS", "3"))
# La captura de huella espera a que la persona ponga el dedo en el lector.
CAPTURA_HUELLA_TIMEOUT = int(os.getenv("CAPTURA_HUELLA_TIMEOUT", "30"))
CORS_ORIGINS = [item.strip() for item in os.getenv("CORS_ORIGINS", "*").split(",") if item.strip()]


def device_ips() -> list[str]:
    ips = [DEVICE_IP]
    if DEVICE_IP_2 and DEVICE_IP_2 not in ips:
        ips.append(DEVICE_IP_2)
    return ips


def credenciales(ip: str) -> tuple[str, str]:
    if DEVICE_IP_2 and ip == DEVICE_IP_2:
        return DEVICE_USER_2, DEVICE_PASS_2
    return DEVICE_USER, DEVICE_PASS


def hikvision(ip: str | None = None) -> HikvisionClient:
    objetivo = ip or DEVICE_IP
    usuario, clave = credenciales(objetivo)
    if not clave:
        raise HTTPException(
            status_code=500, detail=f"Falta la clave del reloj {objetivo} en el archivo .env"
        )
    return HikvisionClient(objetivo, usuario, clave, DEVICE_TIMEOUT)


async def _seed_admin_reloj() -> None:
    existe = await db.persona.find_unique(where={"employeeNo": "1"})
    if existe is None:
        await db.persona.create(
            data={
                "nombre": "Jarod Fernando Fernandez Morales",
                "codigo": "ADM-001",
                "employeeNo": "1",
                "rol": "ADMIN",
            }
        )


# Claves que alguna vez fueron el valor de ejemplo. Si el administrador todavía
# tiene una de estas, el sistema se la cambia solo al arrancar.
CLAVES_DE_EJEMPLO = ("admin123", "admin", "123456", "cambiar")


async def _seed_usuario_admin() -> None:
    usuario = os.getenv("ADMIN_USUARIO", "admin").strip() or "admin"
    password = os.getenv("ADMIN_PASSWORD", "").strip()
    if len(password) < 12 or password in CLAVES_DE_EJEMPLO:
        raise RuntimeError(
            "ADMIN_PASSWORD falta o es muy fácil. Poné una de 12 caracteres o más en el .env."
        )

    existe = await db.usuario.find_unique(where={"usuario": usuario})
    if existe is None:
        await db.usuario.create(
            data={
                "nombre": "Administrador",
                "usuario": usuario,
                "passwordHash": hash_password(password),
                "rol": "ADMIN",
            }
        )
        return

    if any(verify_password(facil, existe.passwordHash) for facil in CLAVES_DE_EJEMPLO):
        await db.usuario.update(
            where={"id": existe.id}, data={"passwordHash": hash_password(password)}
        )
        print(
            "AVISO: el administrador tenía una clave de ejemplo. "
            "Se cambió por la de ADMIN_PASSWORD del .env."
        )


async def _sync_automatico() -> None:
    """Baja los marcajes cada tanto, sin que nadie tenga que acordarse.

    El reloj guarda los eventos, pero si nadie los baja no salen en el tablero.
    La primera vuelta mira varios días atrás por si el servidor estuvo apagado.
    """
    primera = True
    while True:
        try:
            dias = SYNC_AUTO_DIAS if primera else 1
            hoy = datetime.now(TZ).date()
            for atras in range(dias):
                inicio, fin, dia = _inicio_fin_dia((hoy - timedelta(days=atras)).isoformat())
                for ip in device_ips():
                    resultado = await _sincronizar_ip(ip, inicio, fin, dia)
                    if resultado.nuevos:
                        print(f"Sync automático: {resultado.nuevos} marcaje(s) de {ip} el {dia}.")
            primera = False
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - no debe tumbar el servidor
            print(f"Sync automático falló: {exc}")
        await asyncio.sleep(SYNC_AUTO_MIN * 60)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if CORS_ORIGINS == ["*"]:
        print(
            "AVISO: CORS_ORIGINS está en *. Cualquier página puede llamar a esta API. "
            "Poné las direcciones reales del tablero en el .env."
        )
    await db.connect()
    await seed_catalogos(db, device_ips())
    await _seed_admin_reloj()
    await _seed_usuario_admin()
    if os.getenv("SEED_DEMO", "").strip().lower() in {"1", "true", "yes"}:
        await seed_demo_si_vacio(db)

    tarea = asyncio.create_task(_sync_automatico()) if SYNC_AUTO_MIN > 0 else None
    if tarea is None:
        print("AVISO: el sync automático está apagado (SYNC_AUTO_MIN=0).")
    try:
        yield
    finally:
        if tarea is not None:
            tarea.cancel()
            with suppress(asyncio.CancelledError):
                await tarea
        await db.disconnect()


app = FastAPI(
    title="API Reloj Biométrico",
    description="Asistencia EORM Agua de la Mina — Jornada Vespertina",
    version="0.4.0",
    lifespan=lifespan,
)


@app.get("/")
async def raiz() -> dict[str, str]:
    return {
        "proyecto": "EORM Agua de la Mina — Jornada Vespertina",
        "docs": "/docs",
        "health": "/api/health",
    }


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if CORS_ORIGINS == ["*"] else CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class LoginIn(BaseModel):
    usuario: str
    password: str


class LoginOut(BaseModel):
    token: str
    id: int
    nombre: str
    usuario: str
    rol: str


class UsuarioIn(BaseModel):
    nombre: str
    usuario: str
    password: str | None = None
    rol: str
    activo: bool = True


class UsuarioOut(BaseModel):
    id: int
    nombre: str
    usuario: str
    rol: str
    activo: bool

    class Config:
        from_attributes = True


class ClaveIn(BaseModel):
    actual: str
    nueva: str


class AlumnoIn(BaseModel):
    nombre: str
    cui: str | None = None
    grado: str | None = None
    cargo: str | None = None
    fechaNacimiento: str | None = Field(default=None, description="YYYY-MM-DD")
    telefonoPadres: str | None = None
    correoPadres: str | None = None
    telefono: str | None = None
    correo: str | None = None
    rol: str = "ALUMNO"
    activo: bool = True


class AlumnoOut(BaseModel):
    id: int
    nombre: str
    cui: str | None = None
    codigo: str
    employeeNo: str
    grado: str | None
    cargo: str | None = None
    fechaNacimiento: datetime | None = None
    telefonoPadres: str | None = None
    correoPadres: str | None = None
    telefono: str | None = None
    correo: str | None = None
    rol: str
    activo: bool

    @field_serializer("fechaNacimiento")
    def _solo_fecha(self, valor: datetime | None) -> str | None:
        return valor.date().isoformat() if valor else None

    class Config:
        from_attributes = True


class AsistenciaOut(BaseModel):
    id: int
    alumnoId: int
    nombre: str
    cui: str | None = None
    codigo: str
    grado: str | None
    rol: str
    fechaHora: datetime
    tipo: str
    metodo: str
    serialEvento: str | None
    dispositivoIp: str | None

    @field_serializer("fechaHora")
    def _hora_guatemala(self, value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(TZ).isoformat()


class RelojResultado(BaseModel):
    dispositivoIp: str
    ok: bool
    detalle: str | None = None


class EnrolarResult(BaseModel):
    employeeNo: str
    nombre: str
    dispositivos: list[RelojResultado]


class HuellaIn(BaseModel):
    dedo: int = 1
    ip: str | None = Field(default=None, description="Reloj donde la persona pone el dedo")


class HuellaResult(BaseModel):
    employeeNo: str
    dedo: int
    calidad: int
    capturadaEn: str
    dispositivos: list[RelojResultado]


class BiometriaReloj(BaseModel):
    dispositivoIp: str
    grabado: bool
    huellas: int
    detalle: str | None = None


class BiometriaOut(BaseModel):
    employeeNo: str
    nombre: str
    relojes: list[BiometriaReloj]


class SyncDispositivo(BaseModel):
    dispositivoIp: str
    consultados: int
    nuevos: int
    duplicados: int
    sinUsuario: int
    ignorados: int = Field(default=0, description="Eventos que no son un marcaje válido")
    error: str | None = None


class SyncResult(BaseModel):
    fecha: str
    dias: int = 1
    dispositivos: list[SyncDispositivo]
    consultados: int
    nuevos: int
    duplicados: int
    sinUsuario: int = Field(description="Eventos con employeeNo que no estaba en la BD")
    ignorados: int = Field(default=0, description="Eventos que no son un marcaje válido")


def _inicio_fin_dia(fecha: str | None) -> tuple[datetime, datetime, str]:
    if fecha:
        try:
            dia = datetime.strptime(fecha, "%Y-%m-%d").date()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Usa fecha YYYY-MM-DD") from exc
    else:
        dia = datetime.now(TZ).date()
    inicio = datetime(dia.year, dia.month, dia.day, tzinfo=TZ)
    fin = inicio + timedelta(days=1) - timedelta(seconds=1)
    return inicio, fin, dia.isoformat()


def _fecha_query(fecha: str | None) -> str:
    return fecha or datetime.now(TZ).date().isoformat()


def _nombre_minor(minor: int) -> str:
    conocidos = {
        MINOR_ROSTRO_OK: "rostro reconocido",
        MINOR_HUELLA_OK: "huella reconocida",
        MINOR_TARJETA_OK: "tarjeta aceptada",
        27: "botón de salida",
    }
    if minor in conocidos:
        return conocidos[minor]
    if minor in MINORES_FALLIDOS:
        return "intento rechazado"
    return "sin identificar"


def _metodo_evento(evento: dict[str, Any]) -> str:
    # El minor dice con qué se marcó de verdad. currentVerifyMode suele traer la
    # configuración de la puerta ("cardOrFaceOrFp"), por eso va de segundo.
    minor = int(evento.get("minor") or 0)
    if minor == MINOR_ROSTRO_OK:
        return "ROSTRO"
    if minor == MINOR_HUELLA_OK:
        return "HUELLA"
    if minor == MINOR_TARJETA_OK:
        return "TARJETA"

    modo = str(evento.get("currentVerifyMode") or "").lower()
    if "or" not in modo:
        if "face" in modo:
            return "ROSTRO"
        if "fp" in modo or "finger" in modo:
            return "HUELLA"
        if "card" in modo:
            return "TARJETA"
        if "pw" in modo or "password" in modo:
            return "CLAVE"
    return "DESCONOCIDO"


def _parse_evento_tiempo(valor: str | None) -> datetime | None:
    if not valor:
        return None
    texto = valor.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(texto)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TZ)
    return parsed.astimezone(TZ)


def _validar_rol_reloj(rol: str) -> str:
    limpio = rol.strip().upper()
    if limpio not in ROLES_RELOJ:
        raise HTTPException(status_code=400, detail="rol del reloj: ALUMNO, CATEDRATICO o ADMIN")
    return limpio


def _validar_rol_sistema(rol: str) -> str:
    limpio = rol.strip().upper()
    if limpio not in ROLES_SISTEMA:
        raise HTTPException(
            status_code=400,
            detail=f"rol del sistema: {', '.join(ROLES_SISTEMA)}",
        )
    return limpio


# Numeración del reloj. Va aparte por rol para que alumnos y personal no
# choquen, y no lleva el grado adentro porque el grado cambia cada año y el
# número tiene que seguir a la persona.
INICIO_NUMERO = {"ALUMNO": 1000, "CATEDRATICO": 100, "ADMIN": 1}
CUI_RE = re.compile(r"^\d{13}$")


async def _siguiente_employee_no(rol: str) -> str:
    desde = INICIO_NUMERO.get(rol, 1000)
    filas = await db.persona.find_many()
    usados = set()
    for fila in filas:
        if fila.employeeNo.isdigit():
            usados.add(int(fila.employeeNo))
    numero = desde
    while numero in usados:
        numero += 1
    return str(numero)


def _validar_cui(valor: str | None, *, obligatorio: bool) -> str | None:
    texto = "".join(ch for ch in (valor or "") if ch.isdigit())
    if not texto:
        if obligatorio:
            raise HTTPException(status_code=400, detail="El CUI / DPI es obligatorio (13 dígitos).")
        return None
    if not CUI_RE.fullmatch(texto):
        raise HTTPException(status_code=400, detail="El CUI / DPI debe tener exactamente 13 dígitos.")
    return texto


def _parse_fecha_nacimiento(valor: str | None) -> datetime | None:
    texto = (valor or "").strip()
    if not texto:
        return None
    try:
        dia = datetime.strptime(texto, "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="La fecha de nacimiento va como AAAA-MM-DD."
        ) from exc
    if dia > datetime.now(TZ).date():
        raise HTTPException(status_code=400, detail="La fecha de nacimiento no puede ser futura.")
    return datetime(dia.year, dia.month, dia.day)


# Persona siempre se lee con sus detalles, porque el tablero espera la ficha
# completa (grado, cargo, datos de los padres) en un solo objeto plano.
INCLUDE_PERSONA: dict[str, Any] = {
    "detalleAlumno": {"include": {"grado": True}},
    "detalleCatedratico": True,
}


def _alumno_out(fila: Any) -> AlumnoOut:
    """Aplana Persona + su detalle al formato que ya conoce el tablero."""
    alumno = getattr(fila, "detalleAlumno", None)
    catedratico = getattr(fila, "detalleCatedratico", None)
    return AlumnoOut(
        id=fila.id,
        nombre=fila.nombre,
        cui=fila.cui,
        codigo=fila.codigo,
        employeeNo=fila.employeeNo,
        grado=alumno.gradoId if alumno else None,
        cargo=catedratico.cargo if catedratico else None,
        fechaNacimiento=alumno.fechaNacimiento if alumno else None,
        telefonoPadres=alumno.telefonoPadres if alumno else None,
        correoPadres=alumno.correoPadres if alumno else None,
        telefono=catedratico.telefono if catedratico else None,
        correo=catedratico.correo if catedratico else None,
        rol=fila.rol,
        activo=fila.activo,
    )


async def _partes_persona(
    payload: AlumnoIn,
    actual: Any | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    """Valida y reparte el formulario entre la tabla base y la de detalle."""
    rol = _validar_rol_reloj(payload.rol)
    grado = (payload.grado or "").strip() or None
    detalle_alumno: dict[str, Any] | None = None
    detalle_catedratico: dict[str, Any] | None = None

    if rol == "ALUMNO":
        if not grado:
            raise HTTPException(status_code=400, detail="El alumno necesita un grado del catálogo.")
        if await db.grado.find_unique(where={"id": grado}) is None:
            raise HTTPException(status_code=400, detail=f"El grado {grado} no está en el catálogo.")
        telefono = (payload.telefonoPadres or "").strip() or None
        if not telefono:
            raise HTTPException(
                status_code=400, detail="El teléfono de los padres es obligatorio para el alumno."
            )
        correo = (payload.correoPadres or "").strip() or None
        if correo and ("@" not in correo or "." not in correo.split("@")[-1]):
            raise HTTPException(status_code=400, detail="Ese correo de los padres no se ve válido.")
        detalle_alumno = {
            "gradoId": grado,
            "fechaNacimiento": _parse_fecha_nacimiento(payload.fechaNacimiento),
            "telefonoPadres": telefono,
            "correoPadres": correo,
        }
    elif rol == "CATEDRATICO":
        cargo = (payload.cargo or "").strip() or None
        if not cargo:
            raise HTTPException(status_code=400, detail="El maestro necesita un cargo.")
        telefono = (payload.telefono or "").strip() or None
        if not telefono:
            raise HTTPException(status_code=400, detail="El teléfono del maestro es obligatorio.")
        correo = (payload.correo or "").strip() or None
        if correo and ("@" not in correo or "." not in correo.split("@")[-1]):
            raise HTTPException(status_code=400, detail="Ese correo del maestro no se ve válido.")
        detalle_catedratico = {"cargo": cargo, "telefono": telefono, "correo": correo}

    cui = _validar_cui(payload.cui, obligatorio=rol in {"ALUMNO", "CATEDRATICO"})
    # El número del reloj no se pide: si ya existe se conserva, si es alta se asigna.
    employee_no = actual.employeeNo if actual else await _siguiente_employee_no(rol)
    # codigo queda igual al CUI para no romper reportes viejos; el admin del
    # reloj sigue con ADM-001 porque no tiene CUI.
    codigo = cui or (actual.codigo if actual else "ADM-001")
    base = {
        "nombre": payload.nombre.strip(),
        "cui": cui,
        "codigo": codigo,
        "employeeNo": employee_no,
        "rol": rol,
        "activo": payload.activo,
    }
    return base, detalle_alumno, detalle_catedratico


async def _guardar_detalles(
    persona_id: int,
    detalle_alumno: dict[str, Any] | None,
    detalle_catedratico: dict[str, Any] | None,
) -> None:
    """Deja solo el detalle que corresponde al rol y borra el del otro."""
    if detalle_alumno is not None:
        await db.detallealumno.upsert(
            where={"personaId": persona_id},
            data={
                "create": {"personaId": persona_id, **detalle_alumno},
                "update": detalle_alumno,
            },
        )
    else:
        if await db.detallealumno.find_unique(where={"personaId": persona_id}):
            await db.detallealumno.delete(where={"personaId": persona_id})

    if detalle_catedratico is not None:
        await db.detallecatedratico.upsert(
            where={"personaId": persona_id},
            data={
                "create": {"personaId": persona_id, **detalle_catedratico},
                "update": detalle_catedratico,
            },
        )
    else:
        if await db.detallecatedratico.find_unique(where={"personaId": persona_id}):
            await db.detallecatedratico.delete(where={"personaId": persona_id})


async def _dispositivo_id(ip: str) -> int:
    """Busca el reloj por IP y lo da de alta si es uno que aún no estaba."""
    fila = await db.dispositivo.upsert(
        where={"ip": ip},
        data={"create": {"ip": ip, "nombre": f"Reloj {ip}"}, "update": {}},
    )
    return fila.id


async def _siguiente_tipo(persona_id: int, cuando: datetime) -> str:
    inicio = datetime(cuando.year, cuando.month, cuando.day, tzinfo=cuando.tzinfo)
    fin = inicio + timedelta(days=1)
    ultimo = await db.asistencia.find_first(
        where={
            "personaId": persona_id,
            "fechaHora": {"gte": inicio, "lt": fin},
        },
        order={"fechaHora": "desc"},
    )
    if ultimo is None or ultimo.tipo == "SALIDA":
        return "ENTRADA"
    return "SALIDA"


async def _serializar_asistencia(row: Any) -> AsistenciaOut:
    persona = row.persona
    detalle = getattr(persona, "detalleAlumno", None) if persona else None
    dispositivo = getattr(row, "dispositivo", None)
    return AsistenciaOut(
        id=row.id,
        alumnoId=row.personaId,
        nombre=persona.nombre if persona else "",
        cui=persona.cui if persona else None,
        codigo=persona.codigo if persona else "",
        grado=detalle.gradoId if detalle else None,
        rol=persona.rol if persona else "",
        fechaHora=row.fechaHora,
        tipo=row.tipo,
        metodo=row.metodo,
        serialEvento=row.serialEvento,
        dispositivoIp=dispositivo.ip if dispositivo else None,
    )


async def _sincronizar_ip(ip: str, inicio: datetime, fin: datetime, dia: str) -> SyncDispositivo:
    inicio_busqueda = (inicio - timedelta(days=1)).replace(tzinfo=None)
    fin_busqueda = (fin + timedelta(days=1)).replace(tzinfo=None)
    try:
        eventos = hikvision(ip).fetch_all_events(inicio_busqueda, fin_busqueda)
    except HikvisionError as exc:
        return SyncDispositivo(
            dispositivoIp=ip,
            consultados=0,
            nuevos=0,
            duplicados=0,
            sinUsuario=0,
            error=str(exc),
        )

    nuevos = 0
    duplicados = 0
    creados_al_vuelo = 0
    ignorados = 0
    ordenados = sorted(eventos, key=lambda item: str(item.get("time") or ""))
    for evento in ordenados:
        employee_no = str(evento.get("employeeNoString") or evento.get("employeeNo") or "").strip()
        serial = evento.get("serialNo")
        if not employee_no or serial is None:
            continue
        if int(evento.get("minor") or 0) not in MINORES_ASISTENCIA:
            ignorados += 1
            continue
        serial_key = f"{ip}-{serial}"
        existente = await db.asistencia.find_unique(where={"serialEvento": serial_key})
        if existente:
            duplicados += 1
            continue
        cuando = _parse_evento_tiempo(evento.get("time")) or datetime.now(TZ)
        if cuando.astimezone(TZ).date() != inicio.date():
            continue
        persona = await db.persona.find_unique(where={"employeeNo": employee_no})
        if persona is None:
            nombre = str(evento.get("name") or f"Usuario {employee_no}").strip()
            persona = await db.persona.create(
                data={
                    "nombre": nombre,
                    "codigo": f"AUTO-{employee_no}",
                    "employeeNo": employee_no,
                    "rol": "ADMIN" if employee_no == "1" else "ALUMNO",
                }
            )
            creados_al_vuelo += 1
        tipo = await _siguiente_tipo(persona.id, cuando)
        await db.asistencia.create(
            data={
                "personaId": persona.id,
                "dispositivoId": await _dispositivo_id(ip),
                "fechaHora": cuando,
                "tipo": tipo,
                "metodo": _metodo_evento(evento),
                "serialEvento": serial_key,
            }
        )
        nuevos += 1

    return SyncDispositivo(
        dispositivoIp=ip,
        consultados=len(eventos),
        nuevos=nuevos,
        duplicados=duplicados,
        sinUsuario=creados_al_vuelo,
        ignorados=ignorados,
    )


def _archivo_reporte(
    formato: str,
    nombre: str,
    titulo: str,
    subtitulo: str,
    encabezados: list[str],
    filas: list[list[Any]],
) -> Response:
    if formato not in {"pdf", "xlsx"}:
        raise HTTPException(status_code=400, detail="formato: pdf o xlsx")
    if formato == "pdf":
        cuerpo = armar_pdf(titulo, subtitulo, encabezados, filas)
        media = "application/pdf"
        archivo = f"{nombre}.pdf"
    else:
        cuerpo = armar_excel(titulo, subtitulo, encabezados, filas)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        archivo = f"{nombre}.xlsx"
    return Response(
        content=cuerpo,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{archivo}"'},
    )


# --- público ---


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "deviceIps": device_ips(),
        "horaLocal": datetime.now(TZ).isoformat(),
        "zona": "America/Guatemala",
    }


@app.post("/api/auth/login", response_model=LoginOut)
async def login(payload: LoginIn, request: Request) -> LoginOut:
    nombre = payload.usuario.strip()
    # Detrás del túnel todas las visitas llegan desde la misma dirección, así que
    # el usuario también entra en la cuenta: si no, el freno no serviría de nada.
    origen = request.client.host if request.client else "desconocido"
    clave_intento = f"{origen}|{nombre.lower()}"
    revisar_intentos(clave_intento)

    fila = await db.usuario.find_unique(where={"usuario": nombre})
    if fila is None or not fila.activo or not verify_password(payload.password, fila.passwordHash):
        anotar_fallo(clave_intento)
        raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos.")

    limpiar_intentos(clave_intento)
    return LoginOut(
        token=crear_token(fila),
        id=fila.id,
        nombre=fila.nombre,
        usuario=fila.usuario,
        rol=fila.rol,
    )


@app.get("/api/auth/me", response_model=UsuarioOut)
async def me(usuario: Any = Depends(usuario_actual)) -> UsuarioOut:
    return UsuarioOut.model_validate(usuario)


@app.post("/api/auth/cambiar-clave")
async def cambiar_clave(payload: ClaveIn, usuario: Any = Depends(usuario_actual)) -> dict[str, bool]:
    if not verify_password(payload.actual, usuario.passwordHash):
        raise HTTPException(status_code=400, detail="La contraseña actual no coincide.")
    if len(payload.nueva.strip()) < 6:
        raise HTTPException(status_code=400, detail="La nueva contraseña debe tener al menos 6 caracteres.")
    await db.usuario.update(
        where={"id": usuario.id},
        data={"passwordHash": hash_password(payload.nueva.strip())},
    )
    return {"ok": True}


# --- usuarios del sistema ---


@app.get("/api/usuarios", response_model=list[UsuarioOut])
async def listar_usuarios(_: Any = Depends(require_roles(*ROLES_USUARIOS))) -> list[UsuarioOut]:
    filas = await db.usuario.find_many(order={"nombre": "asc"})
    return [UsuarioOut.model_validate(fila) for fila in filas]


@app.post("/api/usuarios", response_model=UsuarioOut)
async def crear_usuario(payload: UsuarioIn, _: Any = Depends(require_roles(*ROLES_USUARIOS))) -> UsuarioOut:
    if not payload.password or len(payload.password.strip()) < 6:
        raise HTTPException(status_code=400, detail="La contraseña debe tener al menos 6 caracteres.")
    try:
        fila = await db.usuario.create(
            data={
                "nombre": payload.nombre.strip(),
                "usuario": payload.usuario.strip(),
                "passwordHash": hash_password(payload.password.strip()),
                "rol": _validar_rol_sistema(payload.rol),
                "activo": payload.activo,
            }
        )
    except UniqueViolationError as exc:
        raise HTTPException(status_code=409, detail="Ese usuario ya existe.") from exc
    return UsuarioOut.model_validate(fila)


@app.put("/api/usuarios/{usuario_id}", response_model=UsuarioOut)
async def editar_usuario(
    usuario_id: int,
    payload: UsuarioIn,
    _: Any = Depends(require_roles(*ROLES_USUARIOS)),
) -> UsuarioOut:
    actual = await db.usuario.find_unique(where={"id": usuario_id})
    if actual is None:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    data: dict[str, Any] = {
        "nombre": payload.nombre.strip(),
        "usuario": payload.usuario.strip(),
        "rol": _validar_rol_sistema(payload.rol),
        "activo": payload.activo,
    }
    if payload.password and payload.password.strip():
        if len(payload.password.strip()) < 6:
            raise HTTPException(status_code=400, detail="La contraseña debe tener al menos 6 caracteres.")
        data["passwordHash"] = hash_password(payload.password.strip())
    try:
        fila = await db.usuario.update(where={"id": usuario_id}, data=data)
    except UniqueViolationError as exc:
        raise HTTPException(status_code=409, detail="Ese usuario ya existe.") from exc
    return UsuarioOut.model_validate(fila)


@app.delete("/api/usuarios/{usuario_id}", response_model=UsuarioOut)
async def baja_usuario(
    usuario_id: int,
    sesion: Any = Depends(require_roles(*ROLES_USUARIOS)),
) -> UsuarioOut:
    if sesion.id == usuario_id:
        raise HTTPException(status_code=400, detail="No puedes darte de baja a ti mismo.")
    actual = await db.usuario.find_unique(where={"id": usuario_id})
    if actual is None:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    fila = await db.usuario.update(where={"id": usuario_id}, data={"activo": False})
    return UsuarioOut.model_validate(fila)


# --- dispositivo ---


@app.get("/api/device-info")
async def get_device_info(_: Any = Depends(require_roles(*ROLES_DISPOSITIVO))) -> dict[str, Any]:
    resultados = []
    for ip in device_ips():
        try:
            resultados.append(
                {
                    "status": "conectado",
                    "deviceIp": ip,
                    "dispositivo": hikvision(ip).get_device_info(),
                    "hora": hikvision(ip).get_time(),
                }
            )
        except HikvisionError as exc:
            resultados.append({"status": "error", "deviceIp": ip, "detalle": str(exc)})
    return {"dispositivos": resultados}


@app.get("/api/device/hora")
async def get_device_hora(_: Any = Depends(require_roles(*ROLES_DISPOSITIVO))) -> dict[str, Any]:
    try:
        hora = hikvision().get_time()
    except HikvisionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "deviceIps": device_ips(),
        "horaAparato": hora,
        "horaPcGuatemala": datetime.now(TZ).isoformat(),
        "aviso": "Si el aparato muestra Asia/Shanghai, cambia la zona a Guatemala en el menú.",
    }


@app.get("/api/device/eventos-crudos")
async def get_eventos_crudos(
    fecha: str | None = Query(default=None, description="YYYY-MM-DD. Si omites, usa hoy."),
    ip: str | None = Query(default=None, description="Reloj a revisar. Si omites, el primero."),
    _: Any = Depends(require_roles(*ROLES_DISPOSITIVO)),
) -> dict[str, Any]:
    """Marcajes tal como los manda el reloj, sin filtrar.

    Sirve para descubrir con qué número (minor) reporta este modelo la huella,
    porque cambia entre modelos y firmwares.
    """
    inicio, fin, dia = _inicio_fin_dia(fecha)
    objetivo = ip or device_ips()[0]
    try:
        eventos = hikvision(objetivo).fetch_all_events(
            (inicio - timedelta(days=1)).replace(tzinfo=None),
            (fin + timedelta(days=1)).replace(tzinfo=None),
        )
    except HikvisionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    conteo: dict[int, int] = {}
    for evento in eventos:
        minor = int(evento.get("minor") or 0)
        conteo[minor] = conteo.get(minor, 0) + 1
    return {
        "fecha": dia,
        "deviceIp": objetivo,
        "total": len(eventos),
        "porMinor": [
            {
                "minor": minor,
                "veces": veces,
                "significado": _nombre_minor(minor),
                "seGuarda": minor in MINORES_ASISTENCIA,
            }
            for minor, veces in sorted(conteo.items())
        ],
        "eventos": eventos,
    }


@app.get("/api/device/personas")
async def get_personas_dispositivo(
    ip: str | None = Query(default=None, description="Reloj a revisar. Si omites, el primero."),
    _: Any = Depends(require_roles(*ROLES_DISPOSITIVO)),
) -> dict[str, Any]:
    """Compara quién está grabado en el reloj contra la matrícula de MySQL."""
    objetivo = ip or device_ips()[0]
    try:
        personas = hikvision(objetivo).fetch_all_users()
    except HikvisionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    en_reloj = {
        str(item.get("employeeNo") or "").strip(): str(item.get("name") or "").strip()
        for item in personas
        if str(item.get("employeeNo") or "").strip()
    }
    matricula = await db.persona.find_many(where={"activo": True})
    en_bd = {fila.employeeNo: fila.nombre for fila in matricula}

    return {
        "deviceIp": objetivo,
        "enReloj": len(en_reloj),
        "enMatricula": len(en_bd),
        "sinMatricula": [
            {"employeeNo": numero, "nombre": nombre}
            for numero, nombre in sorted(en_reloj.items())
            if numero not in en_bd
        ],
        "sinHuellaEnReloj": [
            {"employeeNo": numero, "nombre": nombre}
            for numero, nombre in sorted(en_bd.items())
            if numero not in en_reloj
        ],
    }


# --- matrícula ---


@app.get("/api/alumnos", response_model=list[AlumnoOut])
async def listar_alumnos(
    rol: str | None = None,
    incluirInactivos: bool = False,
    _: Any = Depends(require_roles(*ROLES_MATRICULA_VER)),
) -> list[AlumnoOut]:
    where: dict[str, Any] = {}
    if not incluirInactivos:
        where["activo"] = True
    if rol:
        where["rol"] = rol.upper()
    filas = await db.persona.find_many(where=where, include=INCLUDE_PERSONA, order={"nombre": "asc"})
    return [_alumno_out(fila) for fila in filas]


@app.get("/api/alumnos/siguiente-codigo")
async def siguiente_codigo(
    rol: str = Query(default="ALUMNO"),
    _: Any = Depends(require_roles(*ROLES_MATRICULA_VER)),
) -> dict[str, str]:
    """Lo que le tocaría al próximo registro, para enseñarlo en el formulario."""
    limpio = _validar_rol_reloj(rol)
    return {
        "rol": limpio,
        "employeeNo": await _siguiente_employee_no(limpio),
    }


@app.post("/api/alumnos", response_model=AlumnoOut)
async def crear_alumno(
    payload: AlumnoIn,
    _: Any = Depends(require_roles(*ROLES_MATRICULA_ESCRIBIR)),
) -> AlumnoOut:
    base, detalle_alumno, detalle_catedratico = await _partes_persona(payload)
    if detalle_alumno is not None:
        base["detalleAlumno"] = {"create": detalle_alumno}
    if detalle_catedratico is not None:
        base["detalleCatedratico"] = {"create": detalle_catedratico}
    try:
        fila = await db.persona.create(data=base, include=INCLUDE_PERSONA)
    except UniqueViolationError as exc:
        raise HTTPException(status_code=409, detail="Ese CUI ya está registrado.") from exc
    return _alumno_out(fila)


@app.put("/api/alumnos/{alumno_id}", response_model=AlumnoOut)
async def editar_alumno(
    alumno_id: int,
    payload: AlumnoIn,
    _: Any = Depends(require_roles(*ROLES_MATRICULA_ESCRIBIR)),
) -> AlumnoOut:
    actual = await db.persona.find_unique(where={"id": alumno_id})
    if actual is None:
        raise HTTPException(status_code=404, detail="No está en la matrícula.")
    base, detalle_alumno, detalle_catedratico = await _partes_persona(payload, actual)
    try:
        await db.persona.update(where={"id": alumno_id}, data=base)
    except UniqueViolationError as exc:
        raise HTTPException(status_code=409, detail="Ese CUI ya está registrado.") from exc
    await _guardar_detalles(alumno_id, detalle_alumno, detalle_catedratico)
    fila = await db.persona.find_unique(where={"id": alumno_id}, include=INCLUDE_PERSONA)
    return _alumno_out(fila)


@app.delete("/api/alumnos/{alumno_id}", response_model=AlumnoOut)
async def baja_alumno(
    alumno_id: int,
    _: Any = Depends(require_roles(*ROLES_MATRICULA_ESCRIBIR)),
) -> AlumnoOut:
    actual = await db.persona.find_unique(where={"id": alumno_id})
    if actual is None:
        raise HTTPException(status_code=404, detail="No está en la matrícula.")
    fila = await db.persona.update(
        where={"id": alumno_id}, data={"activo": False}, include=INCLUDE_PERSONA
    )

    # Quien ya no está en la matrícula tampoco debe poder marcar en el reloj.
    # Si el reloj no contesta, la baja en la BD igual queda hecha.
    def quitar(ip: str) -> None:
        hikvision(ip).delete_user(actual.employeeNo)

    await _en_cada_reloj(quitar)
    return _alumno_out(fila)


# --- enrolamiento en los relojes ---


async def _persona_matricula(alumno_id: int) -> Any:
    fila = await db.persona.find_unique(where={"id": alumno_id})
    if fila is None:
        raise HTTPException(status_code=404, detail="No está en la matrícula.")
    return fila


async def _en_cada_reloj(accion: Any) -> list[RelojResultado]:
    """Corre la acción en todos los relojes sin frenar el resto del sistema.

    Las llamadas al aparato son lentas y bloqueantes, por eso van en otro hilo.
    """
    resultados: list[RelojResultado] = []
    for ip in device_ips():
        try:
            await asyncio.to_thread(accion, ip)
            resultados.append(RelojResultado(dispositivoIp=ip, ok=True))
        except HikvisionError as exc:
            resultados.append(RelojResultado(dispositivoIp=ip, ok=False, detalle=str(exc)))
    return resultados


@app.post("/api/alumnos/{alumno_id}/enrolar", response_model=EnrolarResult)
async def enrolar_en_relojes(
    alumno_id: int,
    _: Any = Depends(require_roles(*ROLES_MATRICULA_ESCRIBIR)),
) -> EnrolarResult:
    """Graba a la persona en los relojes para que después le tomen la huella."""
    persona = await _persona_matricula(alumno_id)

    def grabar(ip: str) -> None:
        cliente = hikvision(ip)
        try:
            cliente.save_user(persona.employeeNo, persona.nombre)
        except HikvisionError:
            # Si ya existía, el alta falla; se actualiza el nombre y listo.
            cliente.save_user(persona.employeeNo, persona.nombre, editar=True)

    dispositivos = await _en_cada_reloj(grabar)
    if not any(item.ok for item in dispositivos):
        detalle = dispositivos[0].detalle if dispositivos else "No hay relojes configurados."
        raise HTTPException(status_code=502, detail=detalle)
    return EnrolarResult(
        employeeNo=persona.employeeNo,
        nombre=persona.nombre,
        dispositivos=dispositivos,
    )


@app.post("/api/alumnos/{alumno_id}/huella", response_model=HuellaResult)
async def capturar_huella(
    alumno_id: int,
    payload: HuellaIn,
    _: Any = Depends(require_roles(*ROLES_MATRICULA_ESCRIBIR)),
) -> HuellaResult:
    """Prende el lector, espera el dedo y copia la huella a todos los relojes."""
    if not 1 <= payload.dedo <= 10:
        raise HTTPException(status_code=400, detail="El dedo va del 1 al 10.")
    persona = await _persona_matricula(alumno_id)
    lector = payload.ip or device_ips()[0]
    if lector not in device_ips():
        raise HTTPException(status_code=400, detail=f"El reloj {lector} no está configurado.")

    try:
        captura = await asyncio.to_thread(
            hikvision(lector).capture_fingerprint, payload.dedo, CAPTURA_HUELLA_TIMEOUT
        )
    except HikvisionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    def guardar(ip: str) -> None:
        hikvision(ip).save_fingerprint(persona.employeeNo, payload.dedo, captura["fingerData"])

    dispositivos = await _en_cada_reloj(guardar)
    if not any(item.ok for item in dispositivos):
        detalle = dispositivos[0].detalle if dispositivos else "No hay relojes configurados."
        raise HTTPException(status_code=502, detail=f"Se leyó la huella pero no se guardó: {detalle}")
    return HuellaResult(
        employeeNo=persona.employeeNo,
        dedo=payload.dedo,
        calidad=captura["calidad"],
        capturadaEn=lector,
        dispositivos=dispositivos,
    )


@app.delete("/api/alumnos/{alumno_id}/huella", response_model=EnrolarResult)
async def borrar_huellas(
    alumno_id: int,
    _: Any = Depends(require_roles(*ROLES_MATRICULA_ESCRIBIR)),
) -> EnrolarResult:
    persona = await _persona_matricula(alumno_id)

    def borrar(ip: str) -> None:
        hikvision(ip).delete_fingerprints(persona.employeeNo)

    return EnrolarResult(
        employeeNo=persona.employeeNo,
        nombre=persona.nombre,
        dispositivos=await _en_cada_reloj(borrar),
    )


@app.get("/api/alumnos/{alumno_id}/biometria", response_model=BiometriaOut)
async def estado_biometria(
    alumno_id: int,
    _: Any = Depends(require_roles(*ROLES_MATRICULA_VER)),
) -> BiometriaOut:
    """Dice si la persona ya quedó grabada en cada reloj y cuántas huellas tiene."""
    persona = await _persona_matricula(alumno_id)
    relojes: list[BiometriaReloj] = []
    for ip in device_ips():
        try:
            huellas = await asyncio.to_thread(hikvision(ip).count_fingerprints, persona.employeeNo)
            relojes.append(BiometriaReloj(dispositivoIp=ip, grabado=True, huellas=huellas))
        except HikvisionError as exc:
            relojes.append(
                BiometriaReloj(dispositivoIp=ip, grabado=False, huellas=0, detalle=str(exc))
            )
    return BiometriaOut(employeeNo=persona.employeeNo, nombre=persona.nombre, relojes=relojes)


# --- sync y asistencia ---


@app.post("/api/asistencia/sincronizar", response_model=SyncResult)
async def sincronizar_asistencia(
    fecha: str | None = Query(default=None, description="YYYY-MM-DD. Si omites, usa hoy en Guatemala."),
    dias: int = Query(
        default=1,
        ge=1,
        le=30,
        description="Cuántos días hacia atrás recoger, contando el de la fecha.",
    ),
    _: Any = Depends(require_roles(*ROLES_SYNC)),
) -> SyncResult:
    """Baja los marcajes de los relojes.

    Se puede pedir varios días para recuperar los que nadie sincronizó a tiempo:
    el reloj guarda los eventos, pero si no se bajan no aparecen en el tablero.
    """
    _inicio, _fin, dia_final = _inicio_fin_dia(fecha)
    ultimo = datetime.strptime(dia_final, "%Y-%m-%d").date()

    # Un acumulado por reloj, para que el resumen no se llene de una fila por día.
    acumulado: dict[str, SyncDispositivo] = {}
    for atras in range(dias):
        objetivo = (ultimo - timedelta(days=atras)).isoformat()
        inicio, fin, dia = _inicio_fin_dia(objetivo)
        for ip in device_ips():
            parcial = await _sincronizar_ip(ip, inicio, fin, dia)
            previo = acumulado.get(ip)
            if previo is None:
                acumulado[ip] = parcial
                continue
            previo.consultados += parcial.consultados
            previo.nuevos += parcial.nuevos
            previo.duplicados += parcial.duplicados
            previo.sinUsuario += parcial.sinUsuario
            previo.ignorados += parcial.ignorados
            previo.error = previo.error or parcial.error

    dispositivos = [acumulado[ip] for ip in device_ips() if ip in acumulado]
    return SyncResult(
        fecha=dia_final,
        dias=dias,
        dispositivos=dispositivos,
        consultados=sum(item.consultados for item in dispositivos),
        nuevos=sum(item.nuevos for item in dispositivos),
        duplicados=sum(item.duplicados for item in dispositivos),
        sinUsuario=sum(item.sinUsuario for item in dispositivos),
        ignorados=sum(item.ignorados for item in dispositivos),
    )


async def _asistencias_del_dia(fecha: str | None, grado: str | None) -> list[AsistenciaOut]:
    inicio, fin, _dia = _inicio_fin_dia(fecha)
    where: dict[str, Any] = {"fechaHora": {"gte": inicio, "lte": fin}}
    if grado:
        where["persona"] = {"is": {"detalleAlumno": {"is": {"gradoId": grado}}}}
    filas = await db.asistencia.find_many(
        where=where,
        include={"persona": {"include": {"detalleAlumno": True}}, "dispositivo": True},
        order={"fechaHora": "asc"},
    )
    return [await _serializar_asistencia(fila) for fila in filas]


@app.get("/api/asistencia/hoy", response_model=list[AsistenciaOut])
async def asistencia_hoy(_: Any = Depends(require_roles(*ROLES_CONSULTA))) -> list[AsistenciaOut]:
    return await _asistencias_del_dia(None, None)


@app.get("/api/asistencia", response_model=list[AsistenciaOut])
async def asistencia_por_fecha(
    fecha: str | None = Query(default=None, description="YYYY-MM-DD"),
    grado: str | None = None,
    _: Any = Depends(require_roles(*ROLES_CONSULTA)),
) -> list[AsistenciaOut]:
    return await _asistencias_del_dia(fecha, grado)


@app.get("/api/catalogos/grados")
async def get_catalogo_grados(_: Any = Depends(require_roles(*ROLES_CONSULTA))) -> list[dict[str, str]]:
    return await catalogo_grados(db)


@app.get("/api/dashboard")
async def get_dashboard(
    fecha: str | None = Query(default=None, description="YYYY-MM-DD"),
    _: Any = Depends(require_roles(*ROLES_CONSULTA)),
) -> dict[str, Any]:
    _inicio_fin_dia(fecha)
    return await armar_dashboard(db, _fecha_query(fecha))


@app.get("/api/reportes/asistencia-grados")
async def get_asistencia_grados(
    fecha: str | None = Query(default=None, description="YYYY-MM-DD"),
    gradoId: str = Query(..., description="Ejemplo: 1A"),
    _: Any = Depends(require_roles(*ROLES_CONSULTA)),
) -> dict[str, Any]:
    _inicio_fin_dia(fecha)
    ids = {item["id"] for item in await catalogo_grados(db)}
    if gradoId not in ids:
        raise HTTPException(status_code=400, detail=f"gradoId inválido. Usa: {sorted(ids)}")
    return await armar_asistencia_grado(db, _fecha_query(fecha), gradoId)


@app.get("/api/reportes/ausencias")
async def get_ausencias(
    fecha: str | None = Query(default=None, description="YYYY-MM-DD"),
    horaCorte: str = Query("13:15", description="13:15, 14:00, 15:00 o 16:00"),
    _: Any = Depends(require_roles(*ROLES_CONSULTA)),
) -> dict[str, Any]:
    _inicio_fin_dia(fecha)
    if horaCorte not in HORAS_CORTE_VALIDAS:
        raise HTTPException(
            status_code=400,
            detail=f"horaCorte inválida. Usa: {sorted(HORAS_CORTE_VALIDAS)}",
        )
    return await armar_ausencias(db, _fecha_query(fecha), horaCorte)


@app.get("/api/reportes/maestros")
async def get_maestros(
    fecha: str | None = Query(default=None, description="YYYY-MM-DD"),
    _: Any = Depends(require_roles(*ROLES_CONSULTA)),
) -> dict[str, Any]:
    _inicio_fin_dia(fecha)
    return await armar_maestros(db, _fecha_query(fecha))


# --- PDF / Excel / SMTP ---


@app.get("/api/exportar/dashboard")
async def exportar_dashboard(
    fecha: str | None = Query(default=None),
    formato: Literal["pdf", "xlsx"] = "pdf",
    _: Any = Depends(require_roles(*ROLES_EXPORTAR)),
) -> Response:
    dia = _fecha_query(fecha)
    dto = await armar_dashboard(db, dia)
    encabezados, filas = dashboard_filas(dto)
    return _archivo_reporte(
        formato,
        f"tablero-{dia}",
        "Tablero de asistencia",
        dia,
        encabezados,
        filas,
    )


@app.get("/api/exportar/asistencia-grados")
async def exportar_asistencia_grados(
    gradoId: str = Query(...),
    fecha: str | None = Query(default=None),
    formato: Literal["pdf", "xlsx"] = "pdf",
    _: Any = Depends(require_roles(*ROLES_EXPORTAR)),
) -> Response:
    dia = _fecha_query(fecha)
    ids = {item["id"] for item in await catalogo_grados(db)}
    if gradoId not in ids:
        raise HTTPException(status_code=400, detail=f"gradoId inválido. Usa: {sorted(ids)}")
    dto = await armar_asistencia_grado(db, dia, gradoId)
    encabezados, filas = asistencia_filas(dto)
    return _archivo_reporte(
        formato,
        f"asistencia-{gradoId}-{dia}",
        f"Asistencia {dto['grado']}",
        dia,
        encabezados,
        filas,
    )


@app.get("/api/exportar/ausencias")
async def exportar_ausencias(
    fecha: str | None = Query(default=None),
    horaCorte: str = Query("13:15"),
    formato: Literal["pdf", "xlsx"] = "pdf",
    _: Any = Depends(require_roles(*ROLES_EXPORTAR)),
) -> Response:
    if horaCorte not in HORAS_CORTE_VALIDAS:
        raise HTTPException(status_code=400, detail="horaCorte inválida")
    dia = _fecha_query(fecha)
    dto = await armar_ausencias(db, dia, horaCorte)
    encabezados, filas = ausencias_filas(dto)
    return _archivo_reporte(
        formato,
        f"ausencias-{horaCorte.replace(':', '')}-{dia}",
        f"Ausencias a las {horaCorte}",
        dia,
        encabezados,
        filas,
    )


@app.get("/api/exportar/maestros")
async def exportar_maestros(
    fecha: str | None = Query(default=None),
    formato: Literal["pdf", "xlsx"] = "pdf",
    _: Any = Depends(require_roles(*ROLES_EXPORTAR)),
) -> Response:
    dia = _fecha_query(fecha)
    dto = await armar_maestros(db, dia)
    encabezados, filas = maestros_filas(dto)
    return _archivo_reporte(
        formato,
        f"maestros-{dia}",
        "Asistencia de maestros",
        dia,
        encabezados,
        filas,
    )


@app.post("/api/reportes/ausencias/enviar-correo")
async def correo_ausencias(
    fecha: str | None = Query(default=None),
    horaCorte: str = Query("13:15"),
    _: Any = Depends(require_roles(*ROLES_SMTP)),
) -> dict[str, Any]:
    if not smtp_configurado():
        raise HTTPException(
            status_code=503,
            detail="SMTP no está configurado. Completa SMTP_HOST y SMTP_TO en el .env",
        )
    if horaCorte not in HORAS_CORTE_VALIDAS:
        raise HTTPException(status_code=400, detail="horaCorte inválida")
    dto = await armar_ausencias(db, _fecha_query(fecha), horaCorte)
    try:
        destino = enviar_ausencias(dto)
    except CorreoError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True, "destinatario": destino, "totalAusentes": dto["totalAusentes"]}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("biometric-clock-server:app", host="0.0.0.0", port=8000, reload=False)
