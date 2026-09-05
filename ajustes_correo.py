"""Ajustes de avisos a padres. Los cambia dirección desde el tablero."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Guatemala")
ARCHIVO_AJUSTES = Path(__file__).resolve().parent / ".correo-ajustes.json"
ARCHIVO_ENVIADOS = Path(__file__).resolve().parent / ".correo-avisos-enviados.json"

HORA_TARDE_DEFECTO = "13:15"
HORA_AUSENCIA_DEFECTO = "16:00"


def normalizar_hora(valor: str, defecto: str) -> str:
    partes = (valor or "").strip().split(":")
    if len(partes) < 2:
        return defecto
    try:
        horas = int(partes[0])
        minutos = int(partes[1])
    except ValueError:
        return defecto
    if not (0 <= horas <= 23 and 0 <= minutos <= 59):
        return defecto
    return f"{horas:02d}:{minutos:02d}"


def minutos_de(valor: str) -> int:
    hora = normalizar_hora(valor, "00:00")
    horas, minutos = hora.split(":")
    return int(horas) * 60 + int(minutos)


def ajustes_por_defecto() -> dict[str, Any]:
    return {
        "avisoLlegada": True,
        "avisoTarde": True,
        "avisoAusencia": True,
        "horaTarde": normalizar_hora(os.getenv("SMTP_AUTO_TARDE", HORA_TARDE_DEFECTO), HORA_TARDE_DEFECTO),
        "horaAusencia": normalizar_hora(
            os.getenv("SMTP_AUTO_HORA", HORA_AUSENCIA_DEFECTO),
            HORA_AUSENCIA_DEFECTO,
        ),
    }


def leer_ajustes() -> dict[str, Any]:
    base = ajustes_por_defecto()
    if not ARCHIVO_AJUSTES.is_file():
        return base
    try:
        crudo = json.loads(ARCHIVO_AJUSTES.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return base
    if not isinstance(crudo, dict):
        return base
    return {
        "avisoLlegada": bool(crudo.get("avisoLlegada", base["avisoLlegada"])),
        "avisoTarde": bool(crudo.get("avisoTarde", base["avisoTarde"])),
        "avisoAusencia": bool(crudo.get("avisoAusencia", base["avisoAusencia"])),
        "horaTarde": normalizar_hora(str(crudo.get("horaTarde") or ""), base["horaTarde"]),
        "horaAusencia": normalizar_hora(str(crudo.get("horaAusencia") or ""), base["horaAusencia"]),
    }


def guardar_ajustes(datos: dict[str, Any]) -> dict[str, Any]:
    limpio = {
        "avisoLlegada": bool(datos.get("avisoLlegada")),
        "avisoTarde": bool(datos.get("avisoTarde")),
        "avisoAusencia": bool(datos.get("avisoAusencia")),
        "horaTarde": normalizar_hora(str(datos.get("horaTarde") or ""), HORA_TARDE_DEFECTO),
        "horaAusencia": normalizar_hora(str(datos.get("horaAusencia") or ""), HORA_AUSENCIA_DEFECTO),
    }
    ARCHIVO_AJUSTES.write_text(json.dumps(limpio, ensure_ascii=False, indent=2), encoding="utf-8")
    return limpio


def _leer_enviados() -> dict[str, Any]:
    if not ARCHIVO_ENVIADOS.is_file():
        return {}
    try:
        crudo = json.loads(ARCHIVO_ENVIADOS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return crudo if isinstance(crudo, dict) else {}


def _guardar_enviados(datos: dict[str, Any]) -> None:
    hoy = datetime.now(TZ).date().isoformat()
    vivos = {dia: valor for dia, valor in datos.items() if dia >= hoy}
    ARCHIVO_ENVIADOS.write_text(json.dumps(vivos, ensure_ascii=False), encoding="utf-8")


def ya_aviso_llegada(dia: str, persona_id: int) -> bool:
    bucket = _leer_enviados().get(dia) or {}
    ids = bucket.get("llegada") or []
    return int(persona_id) in {int(item) for item in ids}


def marcar_aviso_llegada(dia: str, persona_id: int) -> None:
    datos = _leer_enviados()
    bucket = datos.setdefault(dia, {})
    ids = [int(item) for item in (bucket.get("llegada") or [])]
    if int(persona_id) not in ids:
        ids.append(int(persona_id))
    bucket["llegada"] = ids
    datos[dia] = bucket
    _guardar_enviados(datos)


def es_tarde(hora_marca: str, hora_tarde: str) -> bool:
    return minutos_de(hora_marca) > minutos_de(hora_tarde)
