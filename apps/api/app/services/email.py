"""Email transaccional via Brevo: API v3 (BREVO_API_KEY) o, si no hay API
key, el relay SMTP (NEXATEC_SMTP_*, STARTTLS obligatorio).

Mismo patron que Bancard y Cloudflare: sin BREVO_API_KEY no se envia nada,
se devuelve False y el flujo que lo llamo sigue (best-effort). Nunca se
loggea el contenido ni los enlaces (llevan tokens de un solo uso).

El remitente (NEXATEC_EMAIL_FROM) tiene que ser un remitente/dominio
verificado en la cuenta de Brevo, con SPF/DKIM publicados en el DNS del
dominio -- si no, Brevo rechaza o el correo cae en spam."""

import html
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

import httpx

from app.core.config import get_settings

logger = logging.getLogger("nexatec.email")

_BREVO_URL = "https://api.brevo.com/v3/smtp/email"
_TIMEOUT = 10.0


def _use_smtp() -> bool:
    s = get_settings()
    return not s.brevo_api_key and bool(s.smtp_host and s.smtp_user and s.smtp_password)


def is_configured() -> bool:
    s = get_settings()
    return bool(s.email_from_address and (s.brevo_api_key or _use_smtp()))


def _smtp() -> smtplib.SMTP:
    s = get_settings()
    return smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=_TIMEOUT)


def _send_smtp(to_email: str, subject: str, html_body: str, text_body: str, tag: str) -> bool:
    s = get_settings()
    msg = EmailMessage()
    msg["From"] = formataddr((s.email_from_name, s.email_from_address))
    msg["To"] = to_email
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=s.email_from_address.rsplit("@", 1)[-1])
    msg["X-Mailin-Tag"] = tag  # tag de Brevo para estadisticas
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    try:
        with _smtp() as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(s.smtp_user, s.smtp_password)
            smtp.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError) as exc:
        # Ni el mensaje ni la respuesta del servidor van al log: pueden
        # repetir el destinatario o partes del contenido.
        logger.warning("email_failed tag=%s transport=smtp error=%s", tag, type(exc).__name__)
        return False


def _client() -> httpx.Client:
    return httpx.Client(timeout=_TIMEOUT)


def send(to_email: str, subject: str, html_body: str, text_body: str, tag: str) -> bool:
    """Devuelve True si Brevo acepto el envio. Nunca lanza: un email que no
    sale no puede tumbar el alta de un usuario ni una demo."""
    s = get_settings()
    if not is_configured():
        logger.info("email_not_sent reason=not_configured tag=%s", tag)
        return False
    if _use_smtp():
        return _send_smtp(to_email, subject, html_body, text_body, tag)
    payload = {
        "sender": {"email": s.email_from_address, "name": s.email_from_name},
        "to": [{"email": to_email}],
        "subject": subject,
        "htmlContent": html_body,
        "textContent": text_body,
        "tags": [tag],
    }
    try:
        with _client() as client:
            resp = client.post(_BREVO_URL, json=payload, headers={"api-key": s.brevo_api_key, "accept": "application/json"})
        if resp.status_code >= 300:
            logger.warning("email_failed tag=%s status=%s", tag, resp.status_code)
            return False
        return True
    except httpx.HTTPError as exc:
        logger.warning("email_failed tag=%s error=%s", tag, type(exc).__name__)
        return False


def _layout(title: str, paragraphs: list[str], button: tuple[str, str] | None = None, footer: str | None = None) -> str:
    body = "".join(f'<p style="margin:0 0 14px;line-height:1.5">{p}</p>' for p in paragraphs)
    if button:
        label, url = button
        body += (f'<p style="margin:22px 0"><a href="{html.escape(url, quote=True)}" '
                 f'style="background:#27c8b5;color:#06352f;padding:12px 22px;border-radius:999px;'
                 f'font-weight:bold;text-decoration:none">{html.escape(label)}</a></p>')
    foot = f'<p style="color:#6b7f7a;font-size:12px;margin-top:26px">{footer}</p>' if footer else ""
    return (f'<div style="font-family:Arial,sans-serif;max-width:560px;margin:auto;color:#10302b">'
            f'<h2 style="color:#0b4b42">{html.escape(title)}</h2>{body}'
            f'<p style="color:#6b7f7a;font-size:12px;margin-top:26px">NEXATEC Soluciones Tecnologicas</p>{foot}</div>')


def _url(path: str) -> str:
    return get_settings().public_base_url.rstrip("/") + path


def send_password_reset(to_email: str, raw_token: str, ttl_minutes: int) -> bool:
    link = _url(f"/restablecer?token={raw_token}")
    return send(
        to_email, "Restablecer tu contrasena de NEXATEC",
        _layout("Restablecer contrasena", [
            "Recibimos un pedido para restablecer la contrasena de tu cuenta.",
            f"El enlace sirve una sola vez y vence en {ttl_minutes} minutos.",
        ], ("Elegir una contrasena nueva", link),
            "Si no lo pediste, ignora este correo: tu contrasena actual sigue funcionando."),
        f"Restablecer contrasena: {link}\nVence en {ttl_minutes} minutos. Si no lo pediste, ignora este correo.",
        "password_reset",
    )


def send_invitation(to_email: str, raw_token: str, ttl_hours: int, company: str | None = None) -> bool:
    link = _url(f"/restablecer?token={raw_token}&invitacion=1")
    where = f" de <strong>{html.escape(company)}</strong>" if company else ""
    return send(
        to_email, "Te invitaron a NEXATEC",
        _layout("Bienvenido a NEXATEC", [
            f"Te dieron acceso al sistema{where}.",
            f"Para entrar, elegi tu contrasena. El enlace sirve una sola vez y vence en {ttl_hours} horas.",
        ], ("Activar mi cuenta", link)),
        f"Activar tu cuenta: {link}\nVence en {ttl_hours} horas.",
        "invitation",
    )


def send_demo_expiring(to_email: str, company: str, days_left: int, whatsapp_number: str) -> bool:
    wa = f"https://wa.me/{whatsapp_number}?text=" + "Hola%20NEXATEC%2C%20quiero%20contratar%20el%20sistema%20que%20estoy%20probando."
    when = "manana" if days_left <= 1 else f"en {days_left} dias"
    return send(
        to_email, f"Tu demo de NEXATEC vence {when}",
        _layout("Tu demo esta por vencer", [
            f"La demo de <strong>{html.escape(company)}</strong> vence {when}.",
            "Si queres seguir usando el sistema con tus datos reales, escribinos y lo dejamos listo.",
        ], ("Hablar por WhatsApp", wa)),
        f"Tu demo vence {when}. Para contratar: {wa}",
        "demo_expiring",
    )


def send_demo_expired(to_email: str, company: str, whatsapp_number: str) -> bool:
    wa = f"https://wa.me/{whatsapp_number}?text=" + "Hola%20NEXATEC%2C%20mi%20demo%20vencio%20y%20quiero%20contratar."
    return send(
        to_email, "Tu demo de NEXATEC vencio",
        _layout("Tu demo vencio", [
            f"La demo de <strong>{html.escape(company)}</strong> llego a su fin y el acceso quedo bloqueado.",
            "Los datos de prueba no se borran de inmediato. Si queres contratar o extender la prueba, escribinos.",
        ], ("Hablar por WhatsApp", wa)),
        f"Tu demo vencio. Para contratar o extenderla: {wa}",
        "demo_expired",
    )
