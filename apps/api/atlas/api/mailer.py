"""Outgoing mail. With no SMTP host configured, messages are kept in an in-memory outbox."""

from __future__ import annotations

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

from atlas.api.settings import Settings


@dataclass
class Mail:
    to: str
    subject: str
    body: str


class Mailer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.outbox: list[Mail] = []

    def send(self, to: str, subject: str, body: str) -> None:
        if not self.settings.smtp_host:
            self.outbox.append(Mail(to, subject, body))
            return
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = self.settings.mail_from, to, subject
        message.set_content(body)
        with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=10) as smtp:
            smtp.send_message(message)
