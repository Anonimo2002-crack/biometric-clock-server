"""SMTP: avisos de ausencia a los padres, desde el Gmail de la escuela."""

from __future__ import annotations

import html
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from reportes import INSTITUCION, JORNADA


class CorreoError(RuntimeError):
    pass


def smtp_configurado() -> bool:
    return bool(
        os.getenv("SMTP_HOST", "").strip()
        and os.getenv("SMTP_USER", "").strip()
        and os.getenv("SMTP_PASSWORD", "").strip()
    )


def _credenciales() -> tuple[str, int, str, str, str, str]:
    host = os.getenv("SMTP_HOST", "").strip()
    puerto = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "").replace(" ", "")
    remitente = os.getenv("SMTP_FROM", "").strip() or user
    copia = os.getenv("SMTP_TO", "").strip()
    if not host or not user or not password:
        raise CorreoError("Falta SMTP_HOST, SMTP_USER o SMTP_PASSWORD en el .env")
    return host, puerto, user, password, remitente, copia


def _fecha_gt(iso: str) -> str:
    if len(iso) == 10 and iso[4] == "-" and iso[7] == "-":
        return f"{iso[8:10]}/{iso[5:7]}/{iso[0:4]}"
    return iso


def _html_base(titulo: str, cuerpo: str) -> str:
    return f"""
    <div style="font-family:Georgia,serif;color:#1c1914">
      <p style="color:#c46a2b;letter-spacing:.12em;text-transform:uppercase;font-size:12px">
        {html.escape(INSTITUCION)}
      </p>
      <h1 style="font-size:22px">{html.escape(titulo)}</h1>
      {cuerpo}
      <p style="color:#6b5f52;font-size:12px">Correo automático del reloj biométrico. No responder.</p>
    </div>
    """


def _enviar(smtp: smtplib.SMTP, remitente: str, destino: str, asunto: str, html_cuerpo: str) -> None:
    mensaje = MIMEMultipart("alternative")
    mensaje["Subject"] = asunto
    mensaje["From"] = remitente
    mensaje["To"] = destino
    mensaje.attach(MIMEText(html_cuerpo, "html", "utf-8"))
    smtp.sendmail(os.getenv("SMTP_USER", "").strip(), [destino], mensaje.as_string())


def enviar_ausencias(dto: dict[str, Any]) -> dict[str, Any]:
    host, puerto, user, password, remitente, copia = _credenciales()
    fecha = _fecha_gt(str(dto.get("fecha") or ""))
    corte = str(dto.get("horaCorte") or "")
    alumnos = list(dto.get("alumnos") or [])

    enviados = 0
    sin_correo = 0
    fallidos: list[str] = []

    try:
        with smtplib.SMTP(host, puerto, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            for item in alumnos:
                destino = str(item.get("correoPadres") or "").strip()
                nombre = str(item.get("nombre") or "el alumno")
                if not destino or "@" not in destino:
                    sin_correo += 1
                    continue
                grado = html.escape(str(item.get("grado") or ""))
                cuerpo = (
                    f"<p>Buenas tardes.</p>"
                    f"<p>Le informamos que <strong>{html.escape(nombre)}</strong>"
                    f"{f' ({grado})' if grado else ''} no registró marca "
                    f"en el reloj biométrico al corte de las {html.escape(corte)} "
                    f"del {html.escape(fecha)}.</p>"
                    f"<p>Jornada vespertina · {html.escape(INSTITUCION)}.</p>"
                )
                try:
                    _enviar(
                        smtp,
                        remitente,
                        destino,
                        f"Aviso de ausencia · {nombre} · {INSTITUCION}",
                        _html_base(f"{JORNADA} · ausencia", cuerpo),
                    )
                    enviados += 1
                except OSError:
                    fallidos.append(nombre)

            if copia:
                por_grado: dict[str, list[str]] = {}
                for item in alumnos:
                    por_grado.setdefault(item.get("grado") or "Sin grado", []).append(item["nombre"])
                bloques = []
                for grado, nombres in por_grado.items():
                    lis = "".join(f"<li>{html.escape(nombre)}</li>" for nombre in nombres)
                    bloques.append(
                        f"<h3 style='font-size:15px;margin:16px 0 6px'>"
                        f"{html.escape(str(grado))} ({len(nombres)})</h3><ul>{lis}</ul>"
                    )
                resumen = "".join(bloques) or "<p>Nadie figura ausente a esta hora.</p>"
                _enviar(
                    smtp,
                    remitente,
                    copia,
                    f"Ausencias {fecha} {corte} · {INSTITUCION}",
                    _html_base(
                        f"{JORNADA} · ausencias",
                        f"<p>Fecha {html.escape(fecha)} · corte {html.escape(corte)} · "
                        f"{len(alumnos)} ausente(s). Se avisó a {enviados} padre(s).</p>{resumen}",
                    ),
                )
    except OSError as exc:
        raise CorreoError(f"No se pudo enviar el correo: {exc}") from exc

    if enviados == 0 and alumnos and not copia:
        raise CorreoError(
            "Nadie de los ausentes tiene correo de padres. Cargalo en Matrícula."
        )

    partes = [f"{enviados} padre(s)"]
    if copia:
        partes.append(f"copia a {copia}")
    if sin_correo:
        partes.append(f"{sin_correo} sin correo")
    if fallidos:
        partes.append(f"{len(fallidos)} no se pudo enviar")
    return {
        "destinatario": " · ".join(partes),
        "enviados": enviados,
        "sinCorreo": sin_correo,
        "fallidos": fallidos,
    }
