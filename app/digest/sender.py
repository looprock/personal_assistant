"""
Email sender via SMTP (aiosmtplib).

Required env vars:
    PA_SMTP_HOST      — e.g. smtp.gmail.com
    PA_SMTP_PORT      — e.g. 587
    PA_SMTP_USER      — sender email address
    PA_SMTP_PASSWORD  — SMTP password / app-specific password
"""

from __future__ import annotations

import logging
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib

log = logging.getLogger(__name__)


async def send_email(to: str, subject: str, html: str) -> None:
    from app.config import cfg
    host = cfg.smtp.host or os.environ["PA_SMTP_HOST"]
    port = cfg.smtp.port
    user = cfg.smtp.user or os.environ["PA_SMTP_USER"]
    password = os.environ["PA_SMTP_PASSWORD"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    msg["X-PA-Generated"] = "digest"
    msg.attach(MIMEText(html, "html"))

    await aiosmtplib.send(
        msg,
        hostname=host,
        port=port,
        username=user,
        password=password,
        start_tls=True,
    )
    log.info("Digest email sent to %s", to)
