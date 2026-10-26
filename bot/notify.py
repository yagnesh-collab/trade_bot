"""Send reports by email over TLS. Credentials come only from environment variables."""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from .config import env


def send_email(subject: str, text: str, html_body: str) -> bool:
    host, user, password, to = (env("SMTP_HOST", "smtp.gmail.com"), env("SMTP_USER"),
                                env("SMTP_PASSWORD"), env("REPORT_TO"))
    if not (user and password and to):
        print("Email not configured (SMTP_USER / SMTP_PASSWORD / REPORT_TO missing) — skipping.")
        return False
    port = int(env("SMTP_PORT", "465") or 465)

    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.set_content(text)
    msg.add_alternative(html_body, subtype="html")

    ctx = ssl.create_default_context()
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as s:
                s.login(user, password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=30) as s:
                s.starttls(context=ctx)
                s.login(user, password)
                s.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        # Print only the error type: SMTP errors can echo parts of the login exchange.
        raise RuntimeError(f"Sending email failed ({type(exc).__name__})") from None
    print(f"Email sent: {subject}")
    return True
