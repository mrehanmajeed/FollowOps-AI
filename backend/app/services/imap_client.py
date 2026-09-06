"""Zoho Mail IMAP reader.

Read-only by design: the mailbox is opened with `readonly=True` so FollowOps
never marks, moves or deletes a human's mail. Deduplication is therefore not
done with IMAP flags but with the `(direction, message_id)` unique index in
Postgres, which also makes reprocessing after a crash harmless.

Only headers plus a short body snippet are fetched. Full message bodies are not
read into the application and never logged.
"""

import email
import imaplib
import logging
import re
import ssl
from datetime import UTC, date, datetime, timedelta
from email.header import decode_header, make_header
from email.message import Message
from email.utils import getaddresses, parsedate_to_datetime

from app.core.config import Settings
from app.services.correlation import (
    InboundMessage,
    normalize_message_id,
    parse_references,
)

logger = logging.getLogger(__name__)

# Headers are enough to correlate; the body is not needed for reply detection.
_FETCH_PARTS = "(UID BODY.PEEK[HEADER])"


class ImapError(RuntimeError):
    """IMAP connection, authentication or protocol failure."""


class ZohoImapClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    # -- connection --------------------------------------------------------

    def _connect(self) -> imaplib.IMAP4:
        s = self.settings
        try:
            if s.zoho_imap_use_ssl:
                conn: imaplib.IMAP4 = imaplib.IMAP4_SSL(
                    s.zoho_imap_host,
                    s.zoho_imap_port,
                    ssl_context=ssl.create_default_context(),
                    timeout=30,
                )
            else:
                conn = imaplib.IMAP4(s.zoho_imap_host, s.zoho_imap_port, timeout=30)
                conn.starttls(ssl.create_default_context())
            conn.login(s.zoho_imap_user, s.zoho_imap_password.get_secret_value())
            return conn
        except imaplib.IMAP4.error as exc:
            # Zoho reports a failed login as a generic "[ALERT] Internal error".
            raise ImapError(
                f"IMAP login to {s.zoho_imap_host} failed. Verify the mailbox "
                f"user, the app-specific password, and that IMAP access is "
                f"enabled for the account. Provider said: {_safe(exc)}"
            ) from exc
        except (OSError, TimeoutError) as exc:
            raise ImapError(
                f"Cannot reach IMAP host {s.zoho_imap_host}:{s.zoho_imap_port} "
                f"({type(exc).__name__})."
            ) from exc

    def verify_connection(self) -> dict[str, object]:
        """Authenticate and list folders without reading any message."""
        try:
            conn = self._connect()
        except ImapError as exc:
            return {
                "ok": False,
                "host": self.settings.zoho_imap_host,
                "error": str(exc),
            }
        try:
            typ, boxes = conn.list()
            folders = [_folder_name(b) for b in (boxes or []) if b]
            typ, data = conn.select(self.settings.zoho_imap_mailbox, readonly=True)
            count = int(data[0]) if typ == "OK" and data and data[0] else 0
            return {
                "ok": True,
                "host": self.settings.zoho_imap_host,
                "mailbox": self.settings.zoho_imap_mailbox,
                "message_count": count,
                "folders": folders[:25],
            }
        except Exception as exc:
            return {
                "ok": False,
                "host": self.settings.zoho_imap_host,
                "error": _safe(exc),
            }
        finally:
            _close(conn)

    # -- reading -----------------------------------------------------------

    def fetch_recent(self, since: date | None = None) -> list[InboundMessage]:
        """Return recent inbound messages, newest last.

        A lookback window is used rather than IMAP flags because the mailbox is
        opened read-only and may also be read by a human mail client.
        """
        s = self.settings
        since = since or (
            datetime.now(UTC).date() - timedelta(days=s.zoho_imap_lookback_days)
        )
        conn = self._connect()
        try:
            typ, _ = conn.select(s.zoho_imap_mailbox, readonly=True)
            if typ != "OK":
                raise ImapError(f"Cannot select mailbox {s.zoho_imap_mailbox!r}.")

            criterion = since.strftime("%d-%b-%Y")
            typ, data = conn.search(None, "SINCE", criterion)
            if typ != "OK":
                raise ImapError("IMAP search failed.")

            ids = (data[0] or b"").split()
            ids = ids[-s.zoho_imap_max_messages_per_poll :]
            messages: list[InboundMessage] = []
            for raw_id in ids:
                parsed = self._fetch_one(conn, raw_id)
                if parsed:
                    messages.append(parsed)
            logger.info(
                "imap_poll mailbox=%s since=%s fetched=%d",
                s.zoho_imap_mailbox,
                criterion,
                len(messages),
            )
            return messages
        finally:
            _close(conn)

    def _fetch_one(self, conn: imaplib.IMAP4, raw_id: bytes) -> InboundMessage | None:
        try:
            typ, payload = conn.fetch(raw_id, _FETCH_PARTS)
            if typ != "OK" or not payload:
                return None
            header_bytes = next(
                (part[1] for part in payload if isinstance(part, tuple) and part[1]),
                None,
            )
            if not header_bytes:
                return None
            uid = _extract_uid(payload) or raw_id.decode(errors="replace")
            return parse_message(
                email.message_from_bytes(header_bytes),
                mailbox=self.settings.zoho_imap_mailbox,
                imap_uid=uid,
            )
        except Exception as exc:
            # One malformed message must not abort the whole poll.
            logger.warning(
                "imap_message_skipped uid=%s reason=%s",
                raw_id.decode(errors="replace"),
                type(exc).__name__,
            )
            return None


def parse_message(
    message: Message, mailbox: str = "INBOX", imap_uid: str = ""
) -> InboundMessage | None:
    """Convert a parsed email into the correlator's input shape.

    Returns None when the message carries no Message-ID, since without one it
    cannot be deduplicated and must not be processed.
    """
    message_id = normalize_message_id(_header(message, "Message-ID"))
    if not message_id:
        return None

    from_pairs = getaddresses([_header(message, "From")])
    from_address = next((addr for _, addr in from_pairs if addr), "")

    to_addresses = tuple(
        addr
        for _, addr in getaddresses(
            [_header(message, "To"), _header(message, "Cc")]
        )
        if addr
    )

    auto_submitted = bool(
        (_header(message, "Auto-Submitted") or "no").lower().strip() != "no"
        or _header(message, "X-Autoreply")
        or _header(message, "X-Autorespond")
        or _header(message, "Precedence").lower() in {"bulk", "auto_reply", "junk"}
        or _header(message, "X-Failed-Recipients")
    )

    return InboundMessage(
        message_id=message_id,
        from_address=from_address.lower(),
        subject=_header(message, "Subject"),
        in_reply_to=normalize_message_id(_header(message, "In-Reply-To")),
        references=parse_references(_header(message, "References")),
        to_addresses=to_addresses,
        received_at=_parse_date(_header(message, "Date")),
        auto_submitted=auto_submitted,
        mailbox=mailbox,
        imap_uid=imap_uid,
    )


# -- helpers ---------------------------------------------------------------


def _header(message: Message, name: str) -> str:
    raw = message.get(name)
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw))).strip()
    except Exception:
        return str(raw).strip()


def _parse_date(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed and parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _extract_uid(payload: list) -> str:
    for part in payload:
        head = part[0] if isinstance(part, tuple) else part
        if isinstance(head, bytes):
            match = re.search(rb"UID (\d+)", head)
            if match:
                return match.group(1).decode()
    return ""


def _folder_name(raw: bytes) -> str:
    text = raw.decode(errors="replace")
    return text.split(' "/" ')[-1].split(" . ")[-1].strip('"')


def _safe(exc: Exception) -> str:
    """Provider text can echo the login command; keep only the exception type."""
    text = str(exc)
    return text[:200] if "LOGIN" not in text.upper() else type(exc).__name__


def _close(conn: imaplib.IMAP4) -> None:
    try:
        conn.logout()
    except Exception:
        pass
