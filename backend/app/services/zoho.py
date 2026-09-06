"""Zoho Mail SMTP adapter.

Plain RFC-compliant SMTP, so the same adapter works against any provider the
mailbox actually lives on; the configured host/port decide.

Two things matter beyond "send the mail":

* We mint the Message-ID ourselves and return it, so the outbound email can be
  persisted *before* the send and later matched against inbound replies.
* Failures are classified as permanent or transient. A permanent failure (bad
  credentials, rejected recipient) must not be retried in a loop.
"""

import asyncio
import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from app.core.config import Settings

logger = logging.getLogger(__name__)


class EmailSendError(RuntimeError):
    """SMTP send failed. `permanent` means retrying will not help."""

    def __init__(self, message: str, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent


@dataclass(frozen=True)
class SendResult:
    message_id: str
    accepted: bool
    detail: str = ""


class ZohoEmailService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def build_message_id(self) -> str:
        """Mint a Message-ID up front so it can be persisted before sending."""
        domain = self.settings.zoho_smtp_user.split("@")[-1] or "followops.local"
        return make_msgid(domain=domain).strip("<>")

    async def send(
        self,
        recipient: str,
        subject: str,
        body: str,
        message_id: str | None = None,
        in_reply_to: str | None = None,
        references: str | None = None,
    ) -> SendResult:
        if not recipient or "@" not in recipient:
            raise EmailSendError(f"Invalid recipient address: {recipient!r}", True)

        message_id = message_id or self.build_message_id()

        message = EmailMessage()
        message["From"] = self.settings.zoho_smtp_user
        message["To"] = recipient
        message["Subject"] = subject
        message["Message-ID"] = f"<{message_id}>"
        message["Date"] = formatdate(localtime=True)
        if in_reply_to:
            message["In-Reply-To"] = f"<{in_reply_to}>"
        if references:
            message["References"] = references
        message.set_content(body)

        await asyncio.to_thread(self._send_sync, message)
        logger.info(
            "smtp_send_ok message_id=%s recipient_domain=%s",
            message_id,
            recipient.split("@")[-1],
        )
        return SendResult(message_id=message_id, accepted=True)

    async def verify_connection(self) -> dict[str, object]:
        """Authenticate without sending. Used by the health/diagnostics route."""
        try:
            await asyncio.to_thread(self._login_only)
            return {"ok": True, "host": self.settings.zoho_smtp_host}
        except EmailSendError as exc:
            return {
                "ok": False,
                "host": self.settings.zoho_smtp_host,
                "error": str(exc),
                "permanent": exc.permanent,
            }

    # -- sync internals ----------------------------------------------------

    def _connect(self) -> smtplib.SMTP:
        settings = self.settings
        if settings.zoho_smtp_port == 465:
            smtp: smtplib.SMTP = smtplib.SMTP_SSL(
                settings.zoho_smtp_host,
                settings.zoho_smtp_port,
                timeout=30,
                context=ssl.create_default_context(),
            )
        else:
            smtp = smtplib.SMTP(
                settings.zoho_smtp_host, settings.zoho_smtp_port, timeout=30
            )
            if settings.zoho_use_tls:
                smtp.starttls(context=ssl.create_default_context())
        smtp.login(
            settings.zoho_smtp_user,
            settings.zoho_smtp_password.get_secret_value(),
        )
        return smtp

    def _login_only(self) -> None:
        try:
            with self._connect() as smtp:
                smtp.noop()
        except Exception as exc:
            raise _classify(exc) from exc

    def _send_sync(self, message: EmailMessage) -> None:
        try:
            with self._connect() as smtp:
                smtp.send_message(message)
        except Exception as exc:
            raise _classify(exc) from exc


def _classify(exc: Exception) -> EmailSendError:
    """Map an SMTP exception onto a retryable/permanent decision.

    The message deliberately carries provider text but never credentials: the
    password is only ever passed to smtplib, never formatted into a string.
    """
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return EmailSendError(
            "SMTP authentication failed. Check the mailbox user and "
            "app-specific password, and that IMAP/SMTP access is enabled for "
            "the account.",
            permanent=True,
        )
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return EmailSendError("Recipient address was refused by the server.", True)
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return EmailSendError("Sender address was refused by the server.", True)
    if isinstance(exc, smtplib.SMTPResponseException):
        # 4xx is a transient server condition, 5xx is a permanent rejection.
        permanent = exc.smtp_code >= 500
        return EmailSendError(f"SMTP error {exc.smtp_code}.", permanent)
    if isinstance(exc, TimeoutError | OSError | smtplib.SMTPServerDisconnected):
        return EmailSendError(f"SMTP connection problem: {type(exc).__name__}.", False)
    return EmailSendError(f"Unexpected SMTP failure: {type(exc).__name__}.", False)
