"""SMTP para el resumen diario de ausencias."""

from __future__ import annotations

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from reportes import INSTITUCION, JORNADA


class CorreoError(RuntimeError):
    pass


def smtp_configurado() -> bool:
    return bool(os.getenv("SMTP_HOST", "").strip() and os.getenv("SMTP_TO", "").strip())


def enviar_ausencias(dto: dict[str, Any]) -> str:
    host = os.getenv("SMTP_HOST", "").strip()
    puerto = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    remitente = os.getenv("SMTP_FROM", "").strip() or user
    destino = os.getenv("SMTP_TO", "").strip()
    if not host or not destino or not remitente:
        raise CorreoError("Falta SMTP_HOST, SMTP_FROM o SMTP_TO en el archivo .env")

    fecha = dto.get("fecha") or ""
    corte = dto.get("horaCorte") or ""
    alumnos = dto.get("alumnos") or []
    filas = "".join(
        f"<li>{item['nombre']} — {item['grado']}</li>" for item in alumnos
    ) or "<li>Nadie figura ausente a esta hora.</li>"
    html = f"""
    <div style="font-family:Georgia,serif;color:#1c1914">
      <p style="color:#c46a2b;letter-spacing:.12em;text-transform:uppercase;font-size:12px">
        {INSTITUCION}
      </p>
      <h1 style="font-size:22px">{JORNADA} · ausencias</h1>
      <p>Fecha {fecha} · corte {corte} · {len(alumnos)} ausente(s).</p>
      <ul>{filas}</ul>
      <p style="color:#6b5f52;font-size:12px">Correo automático del reloj biométrico.</p>
    </div>
    """
    mensaje = MIMEMultipart("alternative")
    mensaje["Subject"] = f"Ausencias {fecha} {corte} · {INSTITUCION}"
    mensaje["From"] = remitente
    mensaje["To"] = destino
    mensaje.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP(host, puerto, timeout=20) as smtp:
            smtp.starttls()
            if user:
                smtp.login(user, password)
            smtp.sendmail(remitente, [destino], mensaje.as_string())
    except OSError as exc:
        raise CorreoError(f"No se pudo enviar el correo: {exc}") from exc
    return destino
