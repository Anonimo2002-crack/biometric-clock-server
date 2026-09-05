"""SMTP: avisos de ausencia a los padres, desde el Gmail de la escuela."""

from __future__ import annotations

import html
import os
import smtplib
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from reportes import INSTITUCION


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


def _cierre_escuela() -> str:
    return (
        "<p>Para cualquier aclaración, puede comunicarse con la Dirección "
        "del establecimiento.</p>"
        f"<p>Atentamente,<br/>"
        f"Dirección<br/>"
        f"<strong>{html.escape(INSTITUCION)}</strong><br/>"
        f"Jornada vespertina<br/>"
        f"Aldea Agua de la Mina, Amatitlán</p>"
    )


def _encabezado_padre() -> str:
    return (
        "<p>Estimado padre de familia / encargado:</p>"
        f"<p>Reciba un cordial saludo de la <strong>{html.escape(INSTITUCION)}</strong>, "
        f"jornada vespertina.</p>"
    )


def html_aviso_llegada(nombre: str, grado: str, fecha: str, hora: str, tarde: bool, limite: str) -> str:
    seccion = f", de {html.escape(grado)}," if grado else ""
    if tarde:
        cuerpo = (
            f"{_encabezado_padre()}"
            f"<p>Por este medio le informamos que el día "
            f"<strong>{html.escape(fecha)}</strong>, a las "
            f"<strong>{html.escape(hora)}</strong>, el/la estudiante "
            f"<strong>{html.escape(nombre)}</strong>{seccion} "
            f"registró su asistencia en el reloj biométrico de la institución. "
            f"Dicho registro se realizó fuera del horario establecido "
            f"(hora límite de ingreso: <strong>{html.escape(limite)}</strong>), "
            f"por lo que se consigna como <strong>llegada tarde</strong>.</p>"
            f"{_cierre_escuela()}"
        )
        titulo = "Comunicado de llegada tarde"
    else:
        cuerpo = (
            f"{_encabezado_padre()}"
            f"<p>Por este medio le informamos que el día "
            f"<strong>{html.escape(fecha)}</strong>, a las "
            f"<strong>{html.escape(hora)}</strong>, el/la estudiante "
            f"<strong>{html.escape(nombre)}</strong>{seccion} "
            f"registró su asistencia en el reloj biométrico de la institución.</p>"
            f"{_cierre_escuela()}"
        )
        titulo = "Comunicado de asistencia"
    return _html_base(titulo, cuerpo)


def html_aviso_padre(nombre: str, grado: str, fecha: str, corte: str) -> str:
    seccion = f", de {html.escape(grado)}," if grado else ""
    cuerpo = (
        f"{_encabezado_padre()}"
        f"<p>Por este medio le informamos que, al corte de las "
        f"<strong>{html.escape(corte)}</strong> del "
        f"<strong>{html.escape(fecha)}</strong>, el/la estudiante "
        f"<strong>{html.escape(nombre)}</strong>{seccion} "
        f"no registra asistencia en el reloj biométrico de la institución.</p>"
        f"<p>Si el estudiante ya se encuentra en el establecimiento "
        f"o existe un motivo justificado, le agradeceremos comunicarse "
        f"con la Dirección.</p>"
        f"{_cierre_escuela()}"
    )
    return _html_base("Comunicado de inasistencia", cuerpo)


def _html_base(titulo: str, cuerpo: str) -> str:
    return f"""
    <div style="font-family:Georgia,serif;color:#1c1914">
      <p style="color:#c46a2b;letter-spacing:.12em;text-transform:uppercase;font-size:12px">
        {html.escape(INSTITUCION)}
      </p>
      <h1 style="font-size:22px">{html.escape(titulo)}</h1>
      {cuerpo}
      <p style="color:#6b5f52;font-size:12px">
        Este es un comunicado automático de la {html.escape(INSTITUCION)}.
        Por favor no responda a este correo.
      </p>
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
                try:
                    _enviar(
                        smtp,
                        remitente,
                        destino,
                        f"Comunicado de inasistencia · {nombre} · {INSTITUCION}",
                        html_aviso_padre(
                            nombre,
                            str(item.get("grado") or ""),
                            fecha,
                            corte,
                        ),
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
                    f"Resumen de inasistencias {fecha} {corte} · {INSTITUCION}",
                    _html_base(
                        "Resumen de inasistencias",
                        f"<p>Fecha {html.escape(fecha)} · corte {html.escape(corte)} · "
                        f"{len(alumnos)} estudiante(s) sin registro. "
                        f"Se notificó a {enviados} padre(s) de familia / encargado(s).</p>{resumen}",
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


def enviar_prueba(destino: str | None = None) -> str:
    """Una sola carta de muestra, para verla en Gmail sin avisar a todos los padres."""
    host, puerto, user, password, remitente, copia = _credenciales()
    buzon = (destino or copia or user).strip()
    if "@" not in buzon:
        raise CorreoError("No hay un Gmail de destino para la prueba.")
    aviso = html_aviso_padre(
        "Geovany Emmanuel González Díaz",
        "Párvulos 1 A",
        _fecha_gt(date.today().isoformat()),
        "16:00",
    )
    nota = (
        "<p style='background:#fde8c0;padding:10px 12px;border-radius:8px'>"
        "<strong>Comunicado de prueba.</strong> Este es el formato que recibirán "
        "los padres de familia o encargados. No corresponde a una inasistencia real.</p>"
    )
    html_cuerpo = aviso.replace(
        "<p>Estimado padre de familia / encargado:</p>",
        nota + "<p>Estimado padre de familia / encargado:</p>",
        1,
    )
    try:
        with smtplib.SMTP(host, puerto, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            _enviar(
                smtp,
                remitente,
                buzon,
                f"PRUEBA · Comunicado de inasistencia · {INSTITUCION}",
                html_cuerpo,
            )
    except OSError as exc:
        raise CorreoError(f"No se pudo enviar la prueba: {exc}") from exc
    return buzon


def enviar_llegadas(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Avisos de llegada o tarde. Un correo por alumno, en una sola sesión SMTP."""
    if not items:
        return {"enviados": 0, "sinCorreo": 0, "omitidos": 0}
    host, puerto, user, password, remitente, _copia = _credenciales()
    enviados = 0
    sin_correo = 0
    omitidos = 0
    try:
        with smtplib.SMTP(host, puerto, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            for item in items:
                destino = str(item.get("correoPadres") or "").strip()
                nombre = str(item.get("nombre") or "el alumno")
                if not destino or "@" not in destino:
                    sin_correo += 1
                    continue
                tarde = bool(item.get("tarde"))
                asunto = (
                    f"Comunicado de llegada tarde · {nombre} · {INSTITUCION}"
                    if tarde
                    else f"Comunicado de asistencia · {nombre} · {INSTITUCION}"
                )
                try:
                    _enviar(
                        smtp,
                        remitente,
                        destino,
                        asunto,
                        html_aviso_llegada(
                            nombre,
                            str(item.get("grado") or ""),
                            _fecha_gt(str(item.get("fecha") or "")),
                            str(item.get("hora") or ""),
                            tarde,
                            str(item.get("limite") or ""),
                        ),
                    )
                    enviados += 1
                except OSError:
                    omitidos += 1
    except OSError as exc:
        raise CorreoError(f"No se pudo enviar el aviso de llegada: {exc}") from exc
    return {"enviados": enviados, "sinCorreo": sin_correo, "omitidos": omitidos}
