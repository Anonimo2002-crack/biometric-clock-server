"""Cliente ISAPI para el terminal Hikvision DS-K1T344MBFWX-E1."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any
from xml.etree import ElementTree as ET

import requests
from requests.auth import HTTPDigestAuth

# minor 0 = todos los eventos de control de acceso.
# minor 75 = autenticación por rostro correcta.
MINOR_TODOS = 0
MINOR_ROSTRO_OK = 75


class HikvisionError(RuntimeError):
    pass


class HikvisionClient:
    def __init__(self, ip: str, user: str, password: str, timeout: int = 10) -> None:
        self.ip = ip
        self.base = f"http://{ip}"
        self.auth = HTTPDigestAuth(user, password)
        self.timeout = timeout

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        url = f"{self.base}{path}"
        try:
            response = requests.request(
                method,
                url,
                auth=self.auth,
                timeout=self.timeout,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise HikvisionError(f"No se pudo conectar a {self.ip}: {exc}") from exc
        return response

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
        minor: int = MINOR_ROSTRO_OK,
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
