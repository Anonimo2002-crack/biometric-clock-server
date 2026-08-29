"""API de asistencia EORM Agua de la Mina — Jornada Vespertina."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from prisma import Prisma
from prisma.errors import UniqueViolationError
from pydantic import BaseModel, Field, field_serializer

from hikvision import HikvisionClient, HikvisionError
from reportes import (
    armar_asistencia_grado,
    armar_ausencias,
    armar_dashboard,
    armar_maestros,
    catalogo_grados,
)

load_dotenv()

TZ = ZoneInfo("America/Guatemala")
db = Prisma()

DEVICE_IP = os.getenv("DEVICE_IP", "192.168.1.21")
DEVICE_USER = os.getenv("DEVICE_USER", "admin")
DEVICE_PASS = os.getenv("DEVICE_PASS", "")
DEVICE_TIMEOUT = int(os.getenv("DEVICE_TIMEOUT", "10"))
CORS_ORIGINS = [item.strip() for item in os.getenv("CORS_ORIGINS", "*").split(",") if item.strip()]


def hikvision() -> HikvisionClient:
    if not DEVICE_PASS:
        raise HTTPException(status_code=500, detail="Falta DEVICE_PASS en el archivo .env")
    return HikvisionClient(DEVICE_IP, DEVICE_USER, DEVICE_PASS, DEVICE_TIMEOUT)


async def _seed_admin() -> None:
    existe = await db.alumno.find_unique(where={"employeeNo": "1"})
    if existe is None:
        await db.alumno.create(
            data={
                "nombre": "Jarod Fernando Fernandez Morales",
                "codigo": "ADM-001",
                "employeeNo": "1",
                "rol": "ADMIN",
            }
        )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await db.connect()
    await _seed_admin()
    yield
    await db.disconnect()


app = FastAPI(
    title="API Reloj Biométrico",
    description="Asistencia EORM Agua de la Mina — Jornada Vespertina",
    version="0.3.0",
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


class AlumnoIn(BaseModel):
    nombre: str
    codigo: str
    employeeNo: str
    grado: str | None = None
    cargo: str | None = None
    rol: str = "ALUMNO"


class AlumnoOut(BaseModel):
    id: int
    nombre: str
    codigo: str
    employeeNo: str
    grado: str | None
    cargo: str | None = None
    rol: str
    activo: bool

    class Config:
        from_attributes = True


class AsistenciaOut(BaseModel):
    id: int
    alumnoId: int
    nombre: str
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


class SyncResult(BaseModel):
    dispositivoIp: str
    consultados: int
    nuevos: int
    duplicados: int
    sinUsuario: int = Field(description="Eventos con employeeNo que no estaba en la BD y se crearon al vuelo")
    fecha: str


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


def _metodo_evento(evento: dict[str, Any]) -> str:
    modo = str(evento.get("currentVerifyMode") or "").lower()
    if "face" in modo:
        return "ROSTRO"
    if "fp" in modo or "finger" in modo:
        return "HUELLA"
    if "card" in modo:
        return "TARJETA"
    if "pw" in modo or "password" in modo:
        return "CLAVE"
    minor = int(evento.get("minor") or 0)
    if minor == 75:
        return "ROSTRO"
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


async def _siguiente_tipo(alumno_id: int, cuando: datetime) -> str:
    inicio = datetime(cuando.year, cuando.month, cuando.day, tzinfo=cuando.tzinfo)
    fin = inicio + timedelta(days=1)
    ultimo = await db.asistencia.find_first(
        where={
            "alumnoId": alumno_id,
            "fechaHora": {"gte": inicio, "lt": fin},
        },
        order={"fechaHora": "desc"},
    )
    if ultimo is None or ultimo.tipo == "SALIDA":
        return "ENTRADA"
    return "SALIDA"


async def _serializar_asistencia(row: Any) -> AsistenciaOut:
    alumno = row.alumno
    return AsistenciaOut(
        id=row.id,
        alumnoId=row.alumnoId,
        nombre=alumno.nombre if alumno else "",
        codigo=alumno.codigo if alumno else "",
        grado=alumno.grado if alumno else None,
        rol=alumno.rol if alumno else "",
        fechaHora=row.fechaHora,
        tipo=row.tipo,
        metodo=row.metodo,
        serialEvento=row.serialEvento,
        dispositivoIp=row.dispositivoIp,
    )


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "deviceIp": DEVICE_IP,
        "horaLocal": datetime.now(TZ).isoformat(),
        "zona": "America/Guatemala",
    }


@app.get("/api/device-info")
async def get_device_info() -> dict[str, Any]:
    try:
        info = hikvision().get_device_info()
        hora = hikvision().get_time()
    except HikvisionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"status": "conectado", "deviceIp": DEVICE_IP, "dispositivo": info, "hora": hora}


@app.get("/api/device/hora")
async def get_device_hora() -> dict[str, Any]:
    try:
        hora = hikvision().get_time()
    except HikvisionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "deviceIp": DEVICE_IP,
        "horaAparato": hora,
        "horaPcGuatemala": datetime.now(TZ).isoformat(),
        "aviso": "Si el aparato muestra Asia/Shanghai, cambia la zona a Guatemala en el menú (icono de 4 cuadrados).",
    }


@app.get("/api/alumnos", response_model=list[AlumnoOut])
async def listar_alumnos(rol: str | None = None) -> list[AlumnoOut]:
    where: dict[str, Any] = {"activo": True}
    if rol:
        where["rol"] = rol.upper()
    filas = await db.alumno.find_many(where=where, order={"nombre": "asc"})
    return [AlumnoOut.model_validate(fila) for fila in filas]


@app.post("/api/alumnos", response_model=AlumnoOut)
async def crear_alumno(payload: AlumnoIn) -> AlumnoOut:
    try:
        fila = await db.alumno.create(
            data={
                "nombre": payload.nombre.strip(),
                "codigo": payload.codigo.strip(),
                "employeeNo": payload.employeeNo.strip(),
                "grado": payload.grado,
                "cargo": payload.cargo,
                "rol": payload.rol.upper(),
            }
        )
    except UniqueViolationError as exc:
        raise HTTPException(status_code=409, detail="codigo o employeeNo ya existe") from exc
    return AlumnoOut.model_validate(fila)


@app.post("/api/asistencia/sincronizar", response_model=SyncResult)
async def sincronizar_asistencia(
    fecha: str | None = Query(default=None, description="YYYY-MM-DD. Si omites, usa hoy en Guatemala."),
) -> SyncResult:
    inicio, fin, dia = _inicio_fin_dia(fecha)
    # El aparato puede estar en otra zona (p. ej. China). Pedimos ±1 día
    # y al guardar convertimos cada evento a hora de Guatemala.
    inicio_busqueda = (inicio - timedelta(days=1)).replace(tzinfo=None)
    fin_busqueda = (fin + timedelta(days=1)).replace(tzinfo=None)
    try:
        eventos = hikvision().fetch_all_events(inicio_busqueda, fin_busqueda)
    except HikvisionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    nuevos = 0
    duplicados = 0
    creados_al_vuelo = 0

    ordenados = sorted(eventos, key=lambda item: str(item.get("time") or ""))
    for evento in ordenados:
        employee_no = str(evento.get("employeeNoString") or evento.get("employeeNo") or "").strip()
        serial = evento.get("serialNo")
        if not employee_no or serial is None:
            continue
        serial_key = f"{DEVICE_IP}-{serial}"
        existente = await db.asistencia.find_unique(where={"serialEvento": serial_key})
        if existente:
            duplicados += 1
            continue

        cuando = _parse_evento_tiempo(evento.get("time")) or datetime.now(TZ)
        if cuando.astimezone(TZ).date() != inicio.date():
            continue
        alumno = await db.alumno.find_unique(where={"employeeNo": employee_no})
        if alumno is None:
            nombre = str(evento.get("name") or f"Usuario {employee_no}").strip()
            alumno = await db.alumno.create(
                data={
                    "nombre": nombre,
                    "codigo": f"AUTO-{employee_no}",
                    "employeeNo": employee_no,
                    "rol": "ADMIN" if employee_no == "1" else "ALUMNO",
                }
            )
            creados_al_vuelo += 1

        tipo = await _siguiente_tipo(alumno.id, cuando)
        await db.asistencia.create(
            data={
                "alumnoId": alumno.id,
                "fechaHora": cuando,
                "tipo": tipo,
                "metodo": _metodo_evento(evento),
                "serialEvento": serial_key,
                "dispositivoIp": DEVICE_IP,
            }
        )
        nuevos += 1

    return SyncResult(
        dispositivoIp=DEVICE_IP,
        consultados=len(eventos),
        nuevos=nuevos,
        duplicados=duplicados,
        sinUsuario=creados_al_vuelo,
        fecha=dia,
    )


@app.get("/api/asistencia/hoy", response_model=list[AsistenciaOut])
async def asistencia_hoy() -> list[AsistenciaOut]:
    return await asistencia_por_fecha(None, None)


@app.get("/api/asistencia", response_model=list[AsistenciaOut])
async def asistencia_por_fecha(
    fecha: str | None = Query(default=None, description="YYYY-MM-DD"),
    grado: str | None = None,
) -> list[AsistenciaOut]:
    inicio, fin, _dia = _inicio_fin_dia(fecha)
    where: dict[str, Any] = {"fechaHora": {"gte": inicio, "lte": fin}}
    if grado:
        where["alumno"] = {"is": {"grado": grado}}
    filas = await db.asistencia.find_many(
        where=where,
        include={"alumno": True},
        order={"fechaHora": "asc"},
    )
    return [await _serializar_asistencia(fila) for fila in filas]


def _fecha_query(fecha: str | None) -> str:
    return fecha or datetime.now(TZ).date().isoformat()


@app.get("/api/catalogos/grados")
async def get_catalogo_grados() -> list[dict[str, str]]:
    return catalogo_grados()


@app.get("/api/dashboard")
async def get_dashboard(
    fecha: str | None = Query(default=None, description="YYYY-MM-DD"),
) -> dict[str, Any]:
    _inicio_fin_dia(fecha)
    return await armar_dashboard(db, _fecha_query(fecha))


@app.get("/api/reportes/asistencia-grados")
async def get_asistencia_grados(
    fecha: str | None = Query(default=None, description="YYYY-MM-DD"),
    gradoId: str = Query(..., description="Ejemplo: 1A"),
) -> dict[str, Any]:
    _inicio_fin_dia(fecha)
    ids = {item["id"] for item in catalogo_grados()}
    if gradoId not in ids:
        raise HTTPException(status_code=400, detail=f"gradoId inválido. Usa: {sorted(ids)}")
    return await armar_asistencia_grado(db, _fecha_query(fecha), gradoId)


@app.get("/api/reportes/ausencias")
async def get_ausencias(
    fecha: str | None = Query(default=None, description="YYYY-MM-DD"),
    horaCorte: str = Query("13:15", description="13:15, 14:00, 15:00 o 16:00"),
) -> dict[str, Any]:
    _inicio_fin_dia(fecha)
    return await armar_ausencias(db, _fecha_query(fecha), horaCorte)


@app.get("/api/reportes/maestros")
async def get_maestros(
    fecha: str | None = Query(default=None, description="YYYY-MM-DD"),
) -> dict[str, Any]:
    _inicio_fin_dia(fecha)
    return await armar_maestros(db, _fecha_query(fecha))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("biometric-clock-server:app", host="0.0.0.0", port=8000, reload=False)

