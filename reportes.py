"""Reportes JSON que consume el tablero Angular de Geovany."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

from prisma import Prisma

TZ = ZoneInfo("America/Guatemala")
INSTITUCION = "EORM Agua de la Mina"
JORNADA = "Jornada Vespertina"
HORA_LIMITE_TARDE_ALUMNOS = "13:15"
HORA_LIMITE_TARDE_MAESTROS = "12:55"

Estado = Literal["presente", "tarde", "ausente"]

# Los grados viven en la tabla `grados`. Se piden una sola vez por reporte y se
# van pasando, para no golpear la base dentro de cada vuelta del ciclo.
async def catalogo_grados(db: Prisma) -> list[dict[str, str]]:
    filas = await db.grado.find_many(where={"activo": True}, order={"orden": "asc"})
    return [
        {"id": fila.id, "nombre": fila.nombre, "seccion": fila.seccion, "etiqueta": _etiqueta(fila.nombre, fila.seccion)}
        for fila in filas
    ]


def _etiqueta(nombre: str, seccion: str) -> str:
    return f"{nombre.replace(' Primaria', '')} {seccion}"


def _minutos(hora: str) -> int:
    horas, minutos = hora.split(":")
    return int(horas) * 60 + int(minutos)


def _hora_hhmm(valor: datetime) -> str:
    if valor.tzinfo is None:
        valor = valor.replace(tzinfo=timezone.utc)
    return valor.astimezone(TZ).strftime("%H:%M")


def estado_por_hora(
    hora_marca: str | None,
    limite_tarde: str,
    hora_corte: str | None = None,
) -> Estado:
    """Si marcó, es presente o tarde. Ausente solo cuando no hay marca."""
    if not hora_marca:
        return "ausente"
    if _minutos(hora_marca) > _minutos(limite_tarde):
        return "tarde"
    return "presente"


def totales_de(items: list[dict[str, Any]]) -> dict[str, Any]:
    matriculados = len(items)
    presentes = sum(1 for item in items if item["estado"] == "presente")
    tardes = sum(1 for item in items if item["estado"] == "tarde")
    ausentes = sum(1 for item in items if item["estado"] == "ausente")
    porcentaje = 0.0 if matriculados == 0 else round(((presentes + tardes) / matriculados) * 1000) / 10
    return {
        "matriculados": matriculados,
        "presentes": presentes,
        "tardes": tardes,
        "ausentes": ausentes,
        "porcentaje": porcentaje,
    }


def _en_el_dia(valor: datetime, inicio: datetime, fin: datetime) -> bool:
    if valor.tzinfo is None:
        valor = valor.replace(tzinfo=timezone.utc)
    local = valor.astimezone(TZ)
    return inicio <= local < fin


def _primera_entrada(marcajes: list[Any], inicio: datetime, fin: datetime) -> datetime | None:
    entradas = [
        row.fechaHora
        for row in marcajes
        if row.tipo == "ENTRADA" and _en_el_dia(row.fechaHora, inicio, fin)
    ]
    return min(entradas) if entradas else None


def _ultima_salida(marcajes: list[Any], inicio: datetime, fin: datetime) -> datetime | None:
    salidas = [
        row.fechaHora
        for row in marcajes
        if row.tipo == "SALIDA" and _en_el_dia(row.fechaHora, inicio, fin)
    ]
    return max(salidas) if salidas else None


def _rango_dia(fecha: str) -> tuple[datetime, datetime]:
    inicio = datetime.strptime(fecha, "%Y-%m-%d").replace(tzinfo=TZ)
    return inicio, inicio + timedelta(days=1)


class FechaInvalida(ValueError):
    pass


MAX_DIAS_RANGO = 62


def _fecha_iso(valor: str) -> str:
    try:
        return datetime.strptime(valor, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise FechaInvalida("Usá fecha AAAA-MM-DD.") from exc


def normalizar_rango(fecha: str | None, desde: str | None, hasta: str | None) -> tuple[str, str]:
    """Un día (fecha) o un intervalo inclusive (desde/hasta). Máximo 62 días."""
    if (desde or "").strip() or (hasta or "").strip():
        a = _fecha_iso((desde or hasta or "").strip())
        b = _fecha_iso((hasta or desde or "").strip())
        if b < a:
            a, b = b, a
        span = (datetime.strptime(b, "%Y-%m-%d").date() - datetime.strptime(a, "%Y-%m-%d").date()).days
        if span > MAX_DIAS_RANGO:
            raise FechaInvalida("El rango no puede pasar de 62 días.")
        return a, b
    if fecha:
        dia = _fecha_iso(fecha.strip())
        return dia, dia
    hoy = datetime.now(TZ).date().isoformat()
    return hoy, hoy


def dias_habiles(desde: str, hasta: str) -> list[str]:
    """Lunes a viernes. Si piden un solo día, ese día aunque sea fin de semana."""
    cur = datetime.strptime(desde, "%Y-%m-%d").date()
    end = datetime.strptime(hasta, "%Y-%m-%d").date()
    todos: list[str] = []
    habiles: list[str] = []
    while cur <= end:
        iso = cur.isoformat()
        todos.append(iso)
        if cur.weekday() < 5:
            habiles.append(iso)
        cur += timedelta(days=1)
    if desde == hasta:
        return todos
    return habiles or todos


def _conteo_periodo(por_dia: dict[str, dict[str, Any]], dias: list[str]) -> dict[str, Any]:
    estados = [(por_dia.get(dia) or {}).get("estado") for dia in dias]
    return {
        "presentes": estados.count("presente"),
        "tardes": estados.count("tarde"),
        "ausentes": estados.count("ausente"),
        "fechasAusente": [dia for dia in dias if (por_dia.get(dia) or {}).get("estado") == "ausente"],
    }


def _alumno_periodo(persona: Any, dias: list[str], hora_corte: str | None = None) -> dict[str, Any]:
    por_dia: dict[str, dict[str, Any]] = {}
    ficha: dict[str, Any] | None = None
    for dia in dias:
        inicio, fin = _rango_dia(dia)
        item = _alumno_asistencia(persona, inicio, fin, hora_corte)
        if ficha is None:
            ficha = item
        por_dia[dia] = {"horaMarca": item["horaMarca"], "estado": item["estado"]}
    assert ficha is not None
    return {
        "id": ficha["id"],
        "nombre": ficha["nombre"],
        "cui": ficha["cui"],
        "employeeNo": ficha["employeeNo"],
        "gradoId": ficha["gradoId"],
        "grado": ficha["grado"],
        "encargado": ficha["encargado"],
        "telefonoEncargado": ficha["telefonoEncargado"],
        "porDia": por_dia,
        **_conteo_periodo(por_dia, dias),
    }


def _maestro_periodo(persona: Any, dias: list[str]) -> dict[str, Any]:
    por_dia: dict[str, dict[str, Any]] = {}
    ficha: dict[str, Any] | None = None
    for dia in dias:
        inicio, fin = _rango_dia(dia)
        item = _maestro_asistencia(persona, inicio, fin)
        if ficha is None:
            ficha = item
        por_dia[dia] = {
            "horaEntrada": item["horaEntrada"],
            "horaSalida": item["horaSalida"],
            "estado": item["estado"],
        }
    assert ficha is not None
    return {
        "id": ficha["id"],
        "nombre": ficha["nombre"],
        "cargo": ficha["cargo"],
        "porDia": por_dia,
        "sinSalida": sum(
            1
            for dia in dias
            if por_dia[dia]["estado"] != "ausente" and not por_dia[dia]["horaSalida"]
        ),
        **_conteo_periodo(por_dia, dias),
    }


async def _personas_del_dia(db: Prisma, rol: str, inicio: datetime, fin: datetime) -> list[Any]:
    return await db.persona.find_many(
        where={"activo": True, "rol": rol},
        include={
            "marcajes": {
                "where": {"fechaHora": {"gte": inicio, "lt": fin}},
            },
            "detalleAlumno": {"include": {"grado": True}},
            "detalleCatedratico": True,
        },
        order={"nombre": "asc"},
    )


def _grado_de(persona: Any) -> tuple[str, str]:
    detalle = getattr(persona, "detalleAlumno", None)
    if detalle is None:
        return "", "Sin grado"
    grado = getattr(detalle, "grado", None)
    if grado is None:
        return detalle.gradoId, detalle.gradoId
    return grado.id, _etiqueta(grado.nombre, grado.seccion)


def _telefono_util(valor: str | None) -> str:
    digitos = "".join(ch for ch in (valor or "") if ch.isdigit())
    if len(digitos) != 8 or set(digitos) == {"0"}:
        return ""
    return digitos


def _encargado_de(detalle: Any) -> tuple[str | None, str | None]:
    if detalle is None:
        return None, None
    nombre = (getattr(detalle, "contactoEmergenciaNombre", None) or "").strip() or None
    telefono = _telefono_util(getattr(detalle, "telefonoPadres", None)) or _telefono_util(
        getattr(detalle, "contactoEmergenciaTelefono", None)
    )
    return nombre, telefono or None


def _alumno_asistencia(
    persona: Any,
    inicio: datetime,
    fin: datetime,
    hora_corte: str | None = None,
) -> dict[str, Any]:
    grado_id, grado_texto = _grado_de(persona)
    entrada = _primera_entrada(persona.marcajes or [], inicio, fin)
    hora_marca = _hora_hhmm(entrada) if entrada else None
    detalle = getattr(persona, "detalleAlumno", None)
    encargado, telefono = _encargado_de(detalle)
    return {
        "id": str(persona.id),
        "nombre": persona.nombre,
        "cui": persona.cui,
        "employeeNo": persona.employeeNo,
        "gradoId": grado_id,
        "grado": grado_texto,
        "correoPadres": getattr(detalle, "correoPadres", None) if detalle else None,
        "encargado": encargado,
        "telefonoEncargado": telefono,
        "horaMarca": hora_marca,
        "estado": estado_por_hora(hora_marca, HORA_LIMITE_TARDE_ALUMNOS, hora_corte),
    }


def _maestro_asistencia(persona: Any, inicio: datetime, fin: datetime) -> dict[str, Any]:
    marcajes = persona.marcajes or []
    entrada = _primera_entrada(marcajes, inicio, fin)
    salida = _ultima_salida(marcajes, inicio, fin)
    hora_entrada = _hora_hhmm(entrada) if entrada else None
    detalle = getattr(persona, "detalleCatedratico", None)
    return {
        "id": str(persona.id),
        "nombre": persona.nombre,
        "cargo": (detalle.cargo if detalle else None) or "Docente",
        "horaEntrada": hora_entrada,
        "horaSalida": _hora_hhmm(salida) if salida else None,
        "estado": estado_por_hora(hora_entrada, HORA_LIMITE_TARDE_MAESTROS),
    }


def resumen_por_grado(
    alumnos: list[dict[str, Any]],
    grados: list[dict[str, str]],
) -> list[dict[str, Any]]:
    filas = []
    for grado in grados:
        del_grado = [item for item in alumnos if item["gradoId"] == grado["id"]]
        filas.append(
            {
                "gradoId": grado["id"],
                "grado": grado["etiqueta"],
                **totales_de(del_grado),
            }
        )
    return filas


async def armar_dashboard(db: Prisma, fecha: str) -> dict[str, Any]:
    inicio, fin = _rango_dia(fecha)
    grados = await catalogo_grados(db)
    alumnos = [_alumno_asistencia(item, inicio, fin) for item in await _personas_del_dia(db, "ALUMNO", inicio, fin)]
    maestros = [
        _maestro_asistencia(item, inicio, fin) for item in await _personas_del_dia(db, "CATEDRATICO", inicio, fin)
    ]
    ultimas = [
        *[
            {
                "hora": item["horaMarca"],
                "nombre": item["nombre"],
                "rol": "alumno",
                "detalle": item["grado"],
                "estado": item["estado"],
            }
            for item in alumnos
            if item["horaMarca"]
        ],
        *[
            {
                "hora": item["horaEntrada"],
                "nombre": item["nombre"],
                "rol": "maestro",
                "detalle": item["cargo"],
                "estado": item["estado"],
            }
            for item in maestros
            if item["horaEntrada"]
        ],
    ]
    ultimas.sort(key=lambda item: item["hora"], reverse=True)
    return {
        "fecha": fecha,
        "jornada": JORNADA,
        "institucion": INSTITUCION,
        "alumnos": totales_de(alumnos),
        "maestros": {
            "total": len(maestros),
            "presentes": sum(1 for item in maestros if item["estado"] == "presente"),
            "tardes": sum(1 for item in maestros if item["estado"] == "tarde"),
            "ausentes": sum(1 for item in maestros if item["estado"] == "ausente"),
        },
        "porGrado": resumen_por_grado(alumnos, grados),
        "ultimasMarcas": ultimas[:8],
        "sinMatricula": len(alumnos) == 0,
    }


async def armar_asistencia_grado(db: Prisma, fecha: str, grado_id: str) -> dict[str, Any]:
    inicio, fin = _rango_dia(fecha)
    grados = await catalogo_grados(db)
    grado = next((item for item in grados if item["id"] == grado_id), grados[0])
    alumnos = [
        item
        for item in [
            _alumno_asistencia(row, inicio, fin) for row in await _personas_del_dia(db, "ALUMNO", inicio, fin)
        ]
        if item["gradoId"] == grado["id"]
    ]
    return {
        "fecha": fecha,
        "gradoId": grado["id"],
        "grado": grado["etiqueta"],
        "totales": totales_de(alumnos),
        "alumnos": alumnos,
    }


async def armar_asistencia_secciones(db: Prisma, fecha: str) -> dict[str, Any]:
    """Todas las secciones del día, para el reporte conjunto."""
    inicio, fin = _rango_dia(fecha)
    grados = await catalogo_grados(db)
    alumnos = [
        _alumno_asistencia(row, inicio, fin) for row in await _personas_del_dia(db, "ALUMNO", inicio, fin)
    ]
    secciones = []
    for grado in grados:
        del_grado = [item for item in alumnos if item["gradoId"] == grado["id"]]
        secciones.append(
            {
                "gradoId": grado["id"],
                "grado": grado["etiqueta"],
                "totales": totales_de(del_grado),
                "alumnos": del_grado,
            }
        )
    return {
        "fecha": fecha,
        "totales": totales_de(alumnos),
        "secciones": secciones,
        "alumnos": alumnos,
    }


async def armar_ausencias(db: Prisma, fecha: str, hora_corte: str) -> dict[str, Any]:
    inicio, fin = _rango_dia(fecha)
    grados = await catalogo_grados(db)
    del_dia = [
        _alumno_asistencia(row, inicio, fin, hora_corte)
        for row in await _personas_del_dia(db, "ALUMNO", inicio, fin)
    ]
    ausentes = [item for item in del_dia if item["estado"] == "ausente"]
    return {
        "fecha": fecha,
        "horaCorte": hora_corte,
        "totalAusentes": len(ausentes),
        "porGrado": resumen_por_grado(del_dia, grados),
        "alumnos": ausentes,
    }


async def armar_propia(db: Prisma, fecha: str, persona_id: int) -> dict[str, Any] | None:
    """Un solo alumno o maestro: lo que puede ver el celular, nadie más."""
    inicio, fin = _rango_dia(fecha)
    persona = await db.persona.find_unique(
        where={"id": persona_id},
        include={
            "marcajes": {"where": {"fechaHora": {"gte": inicio, "lt": fin}}},
            "detalleAlumno": {"include": {"grado": True}},
            "detalleCatedratico": True,
        },
    )
    if persona is None:
        return None

    if persona.rol == "CATEDRATICO":
        hoy = _maestro_asistencia(persona, inicio, fin)
        detalle = hoy.get("cargo") or "Docente"
        hora = hoy.get("horaEntrada")
        hora_salida = hoy.get("horaSalida")
    else:
        hoy = _alumno_asistencia(persona, inicio, fin)
        detalle = hoy.get("grado") or ""
        hora = hoy.get("horaMarca")
        hora_salida = None

    desde = inicio - timedelta(days=13)
    recientes = await db.asistencia.find_many(
        where={"personaId": persona_id, "fechaHora": {"gte": desde, "lt": fin}},
        order={"fechaHora": "desc"},
        take=40,
    )
    return {
        "fecha": fecha,
        "jornada": JORNADA,
        "institucion": INSTITUCION,
        "persona": {
            "id": persona.id,
            "nombre": persona.nombre,
            "rol": persona.rol,
            "detalle": detalle,
        },
        "hoy": {
            "horaMarca": hora,
            "horaSalida": hora_salida,
            "estado": hoy["estado"],
        },
        "marcajes": [
            {
                "id": row.id,
                "fechaHora": _iso_gt(row.fechaHora),
                "tipo": row.tipo,
                "metodo": row.metodo,
            }
            for row in recientes
        ],
    }


def _iso_gt(valor: datetime) -> str:
    if valor.tzinfo is None:
        valor = valor.replace(tzinfo=timezone.utc)
    return valor.astimezone(TZ).isoformat()


async def armar_maestros(db: Prisma, fecha: str) -> dict[str, Any]:
    inicio, fin = _rango_dia(fecha)
    maestros = [
        _maestro_asistencia(item, inicio, fin)
        for item in await _personas_del_dia(db, "CATEDRATICO", inicio, fin)
    ]
    return {
        "fecha": fecha,
        "totales": {
            "total": len(maestros),
            "presentes": sum(1 for item in maestros if item["estado"] == "presente"),
            "tardes": sum(1 for item in maestros if item["estado"] == "tarde"),
            "ausentes": sum(1 for item in maestros if item["estado"] == "ausente"),
        },
        "maestros": maestros,
    }


async def armar_dashboard_periodo(db: Prisma, desde: str, hasta: str) -> dict[str, Any]:
    dias = dias_habiles(desde, hasta)
    inicio, _ = _rango_dia(desde)
    _, fin = _rango_dia(hasta)
    alumnos_p = await _personas_del_dia(db, "ALUMNO", inicio, fin)
    maestros_p = await _personas_del_dia(db, "CATEDRATICO", inicio, fin)
    filas = []
    for dia in dias:
        ini, fin_d = _rango_dia(dia)
        alumnos = [_alumno_asistencia(item, ini, fin_d) for item in alumnos_p]
        maestros = [_maestro_asistencia(item, ini, fin_d) for item in maestros_p]
        filas.append(
            {
                "fecha": dia,
                "alumnos": totales_de(alumnos),
                "maestros": {
                    "total": len(maestros),
                    "presentes": sum(1 for item in maestros if item["estado"] == "presente"),
                    "tardes": sum(1 for item in maestros if item["estado"] == "tarde"),
                    "ausentes": sum(1 for item in maestros if item["estado"] == "ausente"),
                },
            }
        )
    return {"desde": desde, "hasta": hasta, "dias": dias, "filas": filas}


async def armar_asistencia_periodo(
    db: Prisma, desde: str, hasta: str, grado_id: str | None = None
) -> dict[str, Any]:
    dias = dias_habiles(desde, hasta)
    inicio, _ = _rango_dia(desde)
    _, fin = _rango_dia(hasta)
    grados = await catalogo_grados(db)
    alumnos = [_alumno_periodo(item, dias) for item in await _personas_del_dia(db, "ALUMNO", inicio, fin)]
    etiqueta = "Todas las secciones"
    if grado_id:
        alumnos = [item for item in alumnos if item["gradoId"] == grado_id]
        grado = next((item for item in grados if item["id"] == grado_id), None)
        etiqueta = grado["etiqueta"] if grado else grado_id
    return {
        "desde": desde,
        "hasta": hasta,
        "dias": dias,
        "gradoId": grado_id,
        "grado": etiqueta,
        "alumnos": alumnos,
    }


async def armar_ausencias_periodo(db: Prisma, desde: str, hasta: str, hora_corte: str) -> dict[str, Any]:
    dias = dias_habiles(desde, hasta)
    inicio, _ = _rango_dia(desde)
    _, fin = _rango_dia(hasta)
    filas: list[dict[str, Any]] = []
    for persona in await _personas_del_dia(db, "ALUMNO", inicio, fin):
        for dia in dias:
            ini, fin_d = _rango_dia(dia)
            item = _alumno_asistencia(persona, ini, fin_d, hora_corte)
            if item["estado"] == "ausente":
                filas.append({**item, "fecha": dia})
    filas.sort(key=lambda item: (item["fecha"], item["grado"], item["nombre"]))
    return {
        "desde": desde,
        "hasta": hasta,
        "dias": dias,
        "horaCorte": hora_corte,
        "alumnos": filas,
        "totalAusentes": len(filas),
    }


async def armar_maestros_periodo(db: Prisma, desde: str, hasta: str) -> dict[str, Any]:
    dias = dias_habiles(desde, hasta)
    inicio, _ = _rango_dia(desde)
    _, fin = _rango_dia(hasta)
    maestros = [
        _maestro_periodo(item, dias) for item in await _personas_del_dia(db, "CATEDRATICO", inicio, fin)
    ]
    return {"desde": desde, "hasta": hasta, "dias": dias, "maestros": maestros}
