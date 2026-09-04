"""Arranca la API.

El archivo principal lleva guiones en el nombre, así que no se puede importar
de la forma normal. Se carga a mano y se registra en sys.modules, porque si no
Pydantic no logra resolver los tipos de los modelos.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import uvicorn

ARCHIVO = "biometric-clock-server.py"


def cargar_app():
    spec = importlib.util.spec_from_file_location("servidor", ARCHIVO)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"No se pudo cargar {ARCHIVO}")
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modulo
    spec.loader.exec_module(modulo)
    return modulo.app


if __name__ == "__main__":
    uvicorn.run(
        cargar_app(),
        host=os.getenv("API_HOST", "0.0.0.0"),
        port=int(os.getenv("API_PORT", "8000")),
        log_level="info",
    )
