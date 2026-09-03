"""Cliente ISAPI para el terminal Hikvision DS-K1T344MBFWX-E1."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any
from xml.etree import ElementTree as ET

import requests
from requests.auth import HTTPDigestAuth

# minor 0 = todos los eventos de control de acceso. Pedimos todos porque el
# número exacto cambia según el modelo y el firmware del aparato; luego se
# filtra por employeeNo, que solo traen los marcajes de una persona reconocida.
MINOR_TODOS = 0
MINOR_ROSTRO_OK = 75
MINOR_HUELLA_OK = 113
MINOR_TARJETA_OK = 38

# Intentos rechazados: el aparato igual los guarda y no son asistencia.
MINORES_FALLIDOS = {5, 6, 7, 8, 39, 74, 76, 114}

# Solo estos cuentan como que la persona marcó. Es una lista cerrada a propósito:
# el reloj reporta muchos códigos (puerta abierta, revisión remota vencida, avisos
# del sistema) y si se aceptara todo lo que no está en MINORES_FALLIDOS, cualquier
# código nuevo entraría al tablero como si alguien hubiera llegado.
MINORES_ASISTENCIA = {MINOR_ROSTRO_OK, MINOR_HUELLA_OK, MINOR_TARJETA_OK}


class HikvisionError(RuntimeError):
    pass


class HikvisionClient:
    def __init__(self, ip: str, user: str, password: str, timeout: int = 10) -> None:
        self.ip = ip
        self.base = f"http://{ip}"
        self.auth = HTTPDigestAuth(user, password)
        self.timeout = timeout

    def _request(
        self,
        method: str,
        path: str,
        timeout: int | None = None,
        **kwargs: Any,
    ) -> requests.Response:
        url = f"{self.base}{path}"
        try:
            response = requests.request(
                method,
                url,
                auth=self.auth,
                timeout=timeout or self.timeout,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise HikvisionError(f"No se pudo conectar a {self.ip}: {exc}") from exc
        return response

    def _json_ok(self, response: requests.Response, accion: str) -> dict[str, Any]:
        """Valida la respuesta del aparato. Un HTTP 200 no siempre es éxito."""
        if response.status_code != 200:
            raise HikvisionError(f"{accion}: HTTP {response.status_code} {response.text[:300]}")
        try:
            cuerpo = response.json()
        except ValueError as exc:
            raise HikvisionError(f"{accion}: el reloj no devolvió JSON") from exc
        estado = cuerpo.get("statusCode")
        if estado is not None and int(estado) != 1:
            detalle = cuerpo.get("subStatusCode") or cuerpo.get("statusString") or estado
            raise HikvisionError(f"{accion}: el reloj respondió '{detalle}'")
        return cuerpo

    def get_device_info(self) -> dict[str, Any]:
        response = self._request("GET", "/ISAPI/System/deviceInfo")
        if response.status_code != 200:
            raise HikvisionError(f"deviceInfo HTTP {response.status_code}: {response.text[:300]}")
        return _parse_device_info(response.text)

    def get_time(self) -> dict[str, str]:
        response = self._request("GET", "/ISAPI/System/time")
        if response.status_code != 200:
            raise HikvisionError(f"time HTTP {response.status_code}: {response.text[:300]}")
        return _parse_time(response.text)

    def search_users(self, position: int = 0, max_results: int = 50) -> dict[str, Any]:
        body = {
            "UserInfoSearchCond": {
                "searchID": str(uuid.uuid4()),
                "searchResultPosition": position,
                "maxResults": max_results,
            }
        }
        response = self._request(
            "POST",
            "/ISAPI/AccessControl/UserInfo/Search?format=json",
            json=body,
        )
        if response.status_code != 200:
            raise HikvisionError(f"UserInfo HTTP {response.status_code}: {response.text[:300]}")
        return response.json()

    def save_user(self, employee_no: str, nombre: str, editar: bool = False) -> None:
        """Graba a la persona en el reloj. El employeeNo es la llave contra MySQL."""
        cuerpo = {
            "UserInfo": {
                "employeeNo": employee_no,
                "name": nombre,
                "userType": "normal",
                "Valid": {
                    "enable": True,
                    "beginTime": "2020-01-01T00:00:00",
                    "endTime": "2037-12-31T23:59:59",
                    "timeType": "local",
                },
                "doorRight": "1",
                "RightPlan": [{"doorNo": 1, "planTemplateNo": "1"}],
            }
        }
        if editar:
            respuesta = self._request(
                "PUT", "/ISAPI/AccessControl/UserInfo/Modify?format=json", json=cuerpo
            )
            self._json_ok(respuesta, f"editar a {nombre} en el reloj")
        else:
            respuesta = self._request(
                "POST", "/ISAPI/AccessControl/UserInfo/Record?format=json", json=cuerpo
            )
            self._json_ok(respuesta, f"grabar a {nombre} en el reloj")

    def delete_user(self, employee_no: str) -> None:
        cuerpo = {"UserInfoDelCond": {"EmployeeNoList": [{"employeeNo": employee_no}]}}
        respuesta = self._request(
            "PUT", "/ISAPI/AccessControl/UserInfo/Delete?format=json", json=cuerpo
        )
        self._json_ok(respuesta, f"borrar al {employee_no} del reloj")

    def capture_fingerprint(self, finger_no: int = 1, timeout: int = 30) -> dict[str, Any]:
        """Prende el lector y espera a que la persona ponga el dedo.

        Se queda esperando hasta `timeout` segundos, por eso no usa el timeout normal.
        """
        respuesta = self._request(
            "POST",
            "/ISAPI/AccessControl/CaptureFingerPrint?format=json",
            json={"CaptureFingerPrintCond": {"fingerNo": finger_no}},
            timeout=timeout,
        )
        cuerpo = self._json_ok(respuesta, "capturar la huella")
        captura = cuerpo.get("CaptureFingerPrint") or {}
        datos = str(captura.get("fingerData") or "")
        if not datos:
            raise HikvisionError("El lector no alcanzó a leer la huella. Intenta de nuevo.")
        return {
            "fingerData": datos,
            "fingerNo": int(captura.get("fingerNo") or finger_no),
            "calidad": int(captura.get("fingerPrintQuality") or 0),
        }

    def save_fingerprint(self, employee_no: str, finger_no: int, finger_data: str) -> None:
        cuerpo = {
            "FingerPrintCfg": {
                "employeeNo": employee_no,
                "fingerPrintID": finger_no,
                "fingerType": "normalFP",
                "enableCardReader": [1],
                "fingerData": finger_data,
            }
        }
        respuesta = self._request(
            "POST",
            "/ISAPI/AccessControl/FingerPrintDownload?format=json",
            json=cuerpo,
            timeout=max(self.timeout, 20),
        )
        self._json_ok(respuesta, f"guardar la huella del {employee_no}")

    def count_fingerprints(self, employee_no: str) -> int:
        respuesta = self._request(
            "GET",
            f"/ISAPI/AccessControl/FingerPrint/Count?format=json&employeeNo={employee_no}",
        )
        cuerpo = self._json_ok(respuesta, f"contar huellas del {employee_no}")
        bloque = cuerpo.get("FingerPrintCount") or cuerpo
        return int(bloque.get("fingerPrintNum") or bloque.get("num") or 0)

    def delete_fingerprints(self, employee_no: str) -> None:
        cuerpo = {
            "FingerPrintDelete": {
                "mode": "byEmployeeNo",
                "EmployeeNoDetail": [{"employeeNo": employee_no}],
            }
        }
        respuesta = self._request(
            "PUT", "/ISAPI/AccessControl/FingerPrint/Delete?format=json", json=cuerpo
        )
        self._json_ok(respuesta, f"borrar huellas del {employee_no}")

    def fetch_all_users(self, page_size: int = 30) -> list[dict[str, Any]]:
        usuarios: list[dict[str, Any]] = []
        position = 0
        while True:
            payload = self.search_users(position, page_size)
            bloque = payload.get("UserInfoSearch") or {}
            info = bloque.get("UserInfo") or []
            if isinstance(info, dict):
                info = [info]
            usuarios.extend(info)
            status = (bloque.get("responseStatusStrg") or "").upper()
            matched = int(bloque.get("numOfMatches") or 0)
            total = int(bloque.get("totalMatches") or 0)
            if status in {"NO MATCH", ""} or matched == 0:
                break
            position += matched
            if status != "MORE" and position >= total:
                break
        return usuarios

    def search_events(
        self,
        start: datetime,
        end: datetime,
        position: int = 0,
        max_results: int = 50,
        minor: int = MINOR_TODOS,
    ) -> dict[str, Any]:
        body = {
            "AcsEventCond": {
                "searchID": str(uuid.uuid4()),
                "searchResultPosition": position,
                "maxResults": max_results,
                "major": 5,
                "minor": minor,
                "startTime": start.strftime("%Y-%m-%dT%H:%M:%S"),
                "endTime": end.strftime("%Y-%m-%dT%H:%M:%S"),
            }
        }
        response = self._request(
            "POST",
            "/ISAPI/AccessControl/AcsEvent?format=json",
            json=body,
        )
        if response.status_code != 200:
            raise HikvisionError(f"AcsEvent HTTP {response.status_code}: {response.text[:300]}")
        return response.json()

    def fetch_all_events(
        self,
        start: datetime,
        end: datetime,
        minor: int = MINOR_TODOS,
        page_size: int = 50,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        position = 0
        while True:
            payload = self.search_events(start, end, position, page_size, minor)
            acs = payload.get("AcsEvent") or {}
            info = acs.get("InfoList") or []
            if isinstance(info, dict):
                info = [info]
            events.extend(info)
            status = (acs.get("responseStatusStrg") or "").upper()
            matched = int(acs.get("numOfMatches") or 0)
            total = int(acs.get("totalMatches") or 0)
            if status in {"NO MATCH", ""} or matched == 0:
                break
            position += matched
            if status != "MORE" and position >= total:
                break
        return events


def _local_name(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _parse_device_info(xml_text: str) -> dict[str, Any]:
    root = ET.fromstring(xml_text)
    data = {_local_name(child.tag): (child.text or "") for child in root}
    data["rawXml"] = xml_text
    return data


def _parse_time(xml_text: str) -> dict[str, str]:
    root = ET.fromstring(xml_text)
    data = {_local_name(child.tag): (child.text or "") for child in root}
    data["rawXml"] = xml_text
    return data
