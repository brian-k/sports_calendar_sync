from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Any


def send_email(cfg: dict[str, Any], subject: str, body: str) -> None:
    """Send a plain-text email using the `notify` section of config.yaml.

    Defaults to an unauthenticated SMTP server on localhost:25, which is what
    most shared hosts provide. Raises on failure; callers decide how to handle it.
    """
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.get("email_from") or cfg["email_to"]
    msg["To"] = cfg["email_to"]
    msg.set_content(body)

    host = cfg.get("smtp_host", "localhost")
    port = int(cfg.get("smtp_port", 25))
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        if cfg.get("smtp_starttls"):
            smtp.starttls()
        if cfg.get("smtp_username"):
            smtp.login(cfg["smtp_username"], cfg.get("smtp_password", ""))
        smtp.send_message(msg)
