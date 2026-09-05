"""Cliente ISAPI para el terminal Hikvision DS-K1T344MBFWX-E1."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from email.parser import BytesParser
from email.policy import default as email_default
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
MINOR_CLAVE_OK = 21

# Intentos rechazados: el aparato igual los guarda y no son asistencia.
MINORES_FALLIDOS = {5, 6, 7, 8, 39, 74, 76, 114}

# Solo estos cuentan como que la persona marcó. Es una lista cerrada a propósito:
# el reloj reporta muchos códigos (puerta abierta, revisión remota vencida, avisos
# del sistema) y si se aceptara todo lo que no está en MINORES_FALLIDOS, cualquier
# código nuevo entraría al tablero como si alguien hubiera llegado.
MINORES_ASISTENCIA = {MINOR_ROSTRO_OK, MINOR_HUELLA_OK, MINOR_TARJETA_OK, MINOR_CLAVE_OK}


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
        except requests.Timeout as exc:
            raise HikvisionError(f"El reloj {self.ip} no respondió a tiempo.") from exc
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
        """Graba a la persona en el reloj. El employeeNo es la llave contra MySQL.

        El PIN del teclado es el mismo número de reloj: en el aparato se escribe
        1000 y queda marcado, sin pasar por una pantalla web.
        """
        cuerpo = {
            "UserInfo": {
                "employeeNo": employee_no,
                "name": nombre,
                "userType": "normal",
                "password": employee_no,
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

    def asignar_pin(self, employee_no: str, nombre: str) -> None:
        """Deja el número de reloj como clave del teclado, sin tocar huella ni cara."""
        self.save_user(employee_no, nombre, editar=True)

    def delete_user(self, employee_no: str) -> None:
        cuerpo = {"UserInfoDelCond": {"EmployeeNoList": [{"employeeNo": employee_no}]}}
        respuesta = self._request(
            "PUT", "/ISAPI/AccessControl/UserInfo/Delete?format=json", json=cuerpo
        )
        self._json_ok(respuesta, f"borrar al {employee_no} del reloj")

    def capture_fingerprint(self, finger_no: int = 1, timeout: int = 30) -> dict[str, Any]:
        """Prende el lector y espera a que la persona ponga el dedo.

        Este modelo (DS-K1T344) ignora el JSON: hay que mandar XML o responde
        badXmlContent y el lector ni se enciende.
        """
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<CaptureFingerPrintCond version="2.0" '
            'xmlns="http://www.isapi.org/ver20/XMLSchema">'
            f"<fingerNo>{int(finger_no)}</fingerNo>"
            "</CaptureFingerPrintCond>"
        )
        try:
            respuesta = self._request(
                "POST",
                "/ISAPI/AccessControl/CaptureFingerPrint",
                data=xml.encode("utf-8"),
                headers={"Content-Type": "application/xml"},
                timeout=timeout,
            )
        except HikvisionError as exc:
            if "no respondió a tiempo" in str(exc):
                raise HikvisionError(
                    "El lector se encendió, pero nadie puso el dedo a tiempo. "
                    "Quedate frente al reloj, dale otra vez a Capturar y apoyá el dedo."
                ) from exc
            raise
        return self._parse_captura(respuesta, finger_no)

    def _parse_captura(self, respuesta: requests.Response, finger_no: int) -> dict[str, Any]:
        if respuesta.status_code != 200:
            raise HikvisionError(
                f"capturar la huella: HTTP {respuesta.status_code} {respuesta.text[:200]}"
            )
        texto = respuesta.text or ""
        tipo = (respuesta.headers.get("Content-Type") or "").lower()
        if "json" in tipo or texto.lstrip().startswith("{"):
            try:
                cuerpo = self._json_ok(respuesta, "capturar la huella")
            except HikvisionError:
                raise
            captura = cuerpo.get("CaptureFingerPrint") or cuerpo
            datos = str(captura.get("fingerData") or "")
            if datos:
                return {
                    "fingerData": datos,
                    "fingerNo": int(captura.get("fingerNo") or finger_no),
                    "calidad": int(captura.get("fingerPrintQuality") or 0),
                }
        parsed = _parse_captura_xml(texto)
        if parsed:
            if not parsed["fingerNo"]:
                parsed["fingerNo"] = finger_no
            return parsed
        raise HikvisionError("El lector no alcanzó a leer la huella. Intentá de nuevo.")

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
        lista = cuerpo.get("FingerPrintCountList")
        if isinstance(lista, list) and lista:
            primero = lista[0] or {}
            return int(primero.get("numberOfFP") or primero.get("fingerPrintNum") or 0)
        bloque = cuerpo.get("FingerPrintCount") or cuerpo
        return int(
            bloque.get("numberOfFP") or bloque.get("fingerPrintNum") or bloque.get("num") or 0
        )

    def delete_fingerprints(self, employee_no: str) -> None:
        intentos = [
            {"FingerPrintDelete": {"EmployeeNoDetail": {"employeeNo": employee_no}}},
            {
                "FingerPrintDelete": {
                    "mode": "byEmployeeNo",
                    "EmployeeNoList": [{"employeeNo": employee_no}],
                }
            },
            {
                "FingerPrintDelete": {
                    "mode": "byEmployeeNo",
                    "EmployeeNoDetail": [{"employeeNo": employee_no}],
                }
            },
        ]
        ultimo: HikvisionError | None = None
        for cuerpo in intentos:
            respuesta = self._request(
                "PUT", "/ISAPI/AccessControl/FingerPrint/Delete?format=json", json=cuerpo
            )
            try:
                self._json_ok(respuesta, f"borrar huellas del {employee_no}")
                return
            except HikvisionError as exc:
                ultimo = exc
        if ultimo:
            raise ultimo

    def buscar_usuario(self, employee_no: str) -> dict[str, Any] | None:
        cuerpo = {
            "UserInfoSearchCond": {
                "searchID": str(uuid.uuid4()),
                "searchResultPosition": 0,
                "maxResults": 1,
                "EmployeeNoList": [{"employeeNo": employee_no}],
            }
        }
        respuesta = self._request(
            "POST",
            "/ISAPI/AccessControl/UserInfo/Search?format=json",
            json=cuerpo,
        )
        if respuesta.status_code != 200:
            raise HikvisionError(f"buscar usuario HTTP {respuesta.status_code}: {respuesta.text[:200]}")
        bloque = (respuesta.json() or {}).get("UserInfoSearch") or {}
        info = bloque.get("UserInfo") or []
        if isinstance(info, dict):
            info = [info]
        return info[0] if info else None

    def conteo_biometria(self, employee_no: str) -> dict[str, int]:
        """Huellas y caras que el reloj ya tiene de esta persona."""
        fila = self.buscar_usuario(employee_no)
        if fila is None:
            raise HikvisionError(f"El {employee_no} no está grabado en {self.ip}.")
        return {
            "huellas": int(fila.get("numOfFP") or 0),
            "caras": int(fila.get("numOfFace") or 0),
        }

    def capture_face(self, timeout: int = 30) -> bytes:
        """Prende la cámara y espera a que la persona mire al reloj."""
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<CaptureFaceDataCond version="2.0" '
            'xmlns="http://www.isapi.org/ver20/XMLSchema">'
            "<captureInfrared>false</captureInfrared>"
            "<dataType>binary</dataType>"
            "</CaptureFaceDataCond>"
        )
        try:
            respuesta = self._request(
                "POST",
                "/ISAPI/AccessControl/CaptureFaceData",
                data=xml.encode("utf-8"),
                headers={"Content-Type": "application/xml"},
                timeout=timeout,
            )
        except HikvisionError as exc:
            if "no respondió a tiempo" in str(exc):
                raise HikvisionError(
                    "La cámara se encendió, pero no alcanzó a tomar la cara. "
                    "Quedate frente al reloj, dale otra vez a Capturar rostro y mirá la pantalla."
                ) from exc
            raise
        return self._parse_cara(respuesta)

    def save_face(self, employee_no: str, jpeg: bytes) -> None:
        if not jpeg:
            raise HikvisionError("No hay foto de la cara para guardar.")
        meta = json.dumps(
            {"faceLibType": "blackFD", "FDID": "1", "FPID": str(employee_no)},
            separators=(",", ":"),
        )
        boundary = "----hikface"
        crlf = b"\r\n"
        partes = [
            f"--{boundary}".encode(),
            b'Content-Disposition: form-data; name="FaceDataRecord";',
            b"Content-Type: application/json",
            f"Content-Length: {len(meta.encode())}".encode(),
            b"",
            meta.encode(),
            f"--{boundary}".encode(),
            b'Content-Disposition: form-data; name="FaceImage"; filename="face.jpg"',
            b"Content-Type: image/jpeg",
            f"Content-Length: {len(jpeg)}".encode(),
            b"",
            jpeg,
            f"--{boundary}--".encode(),
            b"",
        ]
        cuerpo = crlf.join(partes)
        respuesta = self._request(
            "POST",
            "/ISAPI/Intelligent/FDLib/FaceDataRecord?format=json",
            data=cuerpo,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            timeout=max(self.timeout, 25),
        )
        self._json_ok(respuesta, f"guardar el rostro del {employee_no}")

    def delete_face(self, employee_no: str) -> None:
        intentos = [
            {"FPID": [{"value": employee_no}]},
            {"FPID": [employee_no]},
        ]
        ultimo: HikvisionError | None = None
        for cuerpo in intentos:
            respuesta = self._request(
                "PUT",
                "/ISAPI/Intelligent/FDLib/FDSearch/Delete?format=json&FDID=1&faceLibType=blackFD",
                json=cuerpo,
            )
            try:
                self._json_ok(respuesta, f"borrar el rostro del {employee_no}")
                return
            except HikvisionError as exc:
                ultimo = exc
        if ultimo:
            raise ultimo

    def _parse_cara(self, respuesta: requests.Response) -> bytes:
        if respuesta.status_code != 200:
            raise HikvisionError(
                f"capturar el rostro: HTTP {respuesta.status_code} {respuesta.text[:200]}"
            )
        tipo = (respuesta.headers.get("Content-Type") or "").lower()
        crudo = respuesta.content or b""
        if "image/jpeg" in tipo or crudo[:2] == b"\xff\xd8":
            return crudo
        for parte in _partes_binarias(respuesta):
            if parte[:2] == b"\xff\xd8":
                return parte
        texto = respuesta.text or ""
        parsed = _parse_captura_xml(texto) if "<" in texto else None
        url = ""
        if texto:
            try:
                root = ET.fromstring(texto[texto.find("<") :] if "<" in texto else texto)
                url = _xml_texto(root, "faceDataUrl") or _xml_texto(root, "faceDataURL")
            except ET.ParseError:
                url = ""
        if url:
            extra = self._request("GET", url if url.startswith("/") else f"/{url.lstrip('/')}")
            if extra.content[:2] == b"\xff\xd8":
                return extra.content
        if parsed:
            raise HikvisionError("El reloj respondió sin foto de la cara. Intentá de nuevo.")
        raise HikvisionError("El reloj no alcanzó a tomar la cara. Intentá de nuevo.")

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


def _partes_binarias(respuesta: requests.Response) -> list[bytes]:
    tipo = respuesta.headers.get("Content-Type") or ""
    if "multipart" not in tipo.lower():
        return [respuesta.content] if respuesta.content else []
    crudo = b"Content-Type: " + tipo.encode("utf-8", "replace") + b"\r\n\r\n" + (respuesta.content or b"")
    mensaje = BytesParser(policy=email_default).parsebytes(crudo)
    if not mensaje.is_multipart():
        return [respuesta.content] if respuesta.content else []
    partes: list[bytes] = []
    for parte in mensaje.iter_parts():
        carga = parte.get_payload(decode=True)
        if isinstance(carga, bytes) and carga:
            partes.append(carga)
    return partes


def _xml_texto(root: ET.Element, nombre: str) -> str:
    for elem in root.iter():
        if _local_name(elem.tag) == nombre:
            return (elem.text or "").strip()
    return ""


def _parse_captura_xml(texto: str) -> dict[str, Any] | None:
    candidato = texto
    inicio = texto.find("<CaptureFingerPrint")
    if inicio >= 0:
        fin = texto.find("</CaptureFingerPrint>")
        if fin > inicio:
            candidato = texto[inicio : fin + len("</CaptureFingerPrint>")]
    try:
        root = ET.fromstring(candidato)
    except ET.ParseError:
        return None
    estado = _xml_texto(root, "statusCode")
    if estado and estado != "1":
        detalle = _xml_texto(root, "subStatusCode") or _xml_texto(root, "statusString") or estado
        raise HikvisionError(f"capturar la huella: el reloj respondió '{detalle}'")
    datos = _xml_texto(root, "fingerData")
    if not datos:
        return None
    calidad = _xml_texto(root, "fingerPrintQuality")
    dedo = _xml_texto(root, "fingerNo")
    return {
        "fingerData": datos,
        "fingerNo": int(dedo) if dedo.isdigit() else 0,
        "calidad": int(calidad) if calidad.isdigit() else 0,
    }


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
