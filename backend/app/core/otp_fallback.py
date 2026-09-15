"""Temporary OTP log fallback — owner login before the Meta app exists.

Owner login is a WhatsApp OTP, and until the Meta app and its Authentication
template exist the code goes nowhere: production has no way into the dashboard
at all, so the café cannot even be configured. This bridge writes the code to
the server log instead, where only whoever holds the Railway account can read it.

It is a hole on purpose, so it is fenced three ways:

  * off unless `OTP_LOG_FALLBACK=true`;
  * only while WhatsApp is still unconfigured — the moment a real token is set,
    codes go to WhatsApp and nowhere else;
  * **expires by date, not by memory.** `EXPIRES` is hardcoded. After it the flag
    is ignored whatever the environment says, and extending it takes a commit.

Every boot while it is enabled logs a loud warning with the days remaining.
Remove this module once Meta is live.
"""
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.core.config import Settings, get_settings, is_placeholder

logger = logging.getLogger("auth.otp_fallback")

# Committed 16 Sep 2026; 30 days. Extending this is a code change, deliberately.
EXPIRES = date(2026, 10, 16)
WIB = ZoneInfo("Asia/Jakarta")

OFF = "off"
ACTIVE = "active"
EXPIRED = "expired"
WHATSAPP_CONFIGURED = "whatsapp_configured"


def _today() -> date:
    return datetime.now(WIB).date()


def state(settings: Settings | None = None, today: date | None = None) -> str:
    settings = settings or get_settings()
    if not settings.otp_log_fallback:
        return OFF
    if not is_placeholder(settings.whatsapp_access_token):
        return WHATSAPP_CONFIGURED
    if (today or _today()) > EXPIRES:
        return EXPIRED
    return ACTIVE


def log_code(phone: str, code: str, settings: Settings | None = None, today: date | None = None) -> bool:
    """Writes the OTP to the log when, and only when, the fallback is active."""
    if state(settings, today) != ACTIVE:
        return False
    logger.warning("[OTP-FALLBACK] OTP for %s is %s", phone, code)
    return True


def announce(settings: Settings | None = None, today: date | None = None) -> str:
    """Startup banner. Returns the state so a test can assert on it."""
    current = state(settings, today)
    if current == ACTIVE:
        days_left = (EXPIRES - (today or _today())).days
        logger.warning(
            "!!! OTP_LOG_FALLBACK IS ON: owner login codes are written to this log. "
            "Anyone who can read these logs can log in as any owner. "
            "It stops working after %s (%d day(s) left). Turn it off once WhatsApp works. !!!",
            EXPIRES.isoformat(), days_left,
        )
    elif current == EXPIRED:
        logger.error(
            "OTP_LOG_FALLBACK=true is refused: it expired on %s. Owner OTP codes are NOT logged. "
            "Set up WhatsApp, or extend EXPIRES in app/core/otp_fallback.py in a commit.",
            EXPIRES.isoformat(),
        )
    elif current == WHATSAPP_CONFIGURED:
        logger.warning(
            "OTP_LOG_FALLBACK=true is ignored because WhatsApp is configured. Remove the variable."
        )
    return current
