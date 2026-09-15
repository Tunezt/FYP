"""Temporary OTP log fallback (until Meta exists).

The fallback writes owner login codes to the server log, which is a hole on
purpose. These tests pin the fence around it: off by default, silent once
WhatsApp is configured, dead after a hardcoded date whatever the environment
says, loud on every boot while it is on — and never a way to switch on the
development-only `000000` bypass in production.
"""
import logging
from datetime import timedelta

from app.api.auth import DEV_BYPASS_CODE, is_dev_bypass_code
from app.core import otp_fallback
from app.core.config import Settings

BEFORE = otp_fallback.EXPIRES - timedelta(days=10)
LAST_DAY = otp_fallback.EXPIRES
AFTER = otp_fallback.EXPIRES + timedelta(days=1)


def _settings(**overrides) -> Settings:
    base = dict(
        environment="production",
        otp_log_fallback=True,
        whatsapp_access_token="CHANGE_ME",
    )
    base.update(overrides)
    return Settings(_env_file=None, **base)


def _fallback_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "auth.otp_fallback"]


def test_off_by_default_logs_nothing(caplog):
    caplog.set_level(logging.WARNING)
    settings = Settings(_env_file=None, environment="production", whatsapp_access_token="CHANGE_ME")
    assert settings.otp_log_fallback is False
    assert otp_fallback.log_code("6281200011112", "123456", settings, today=BEFORE) is False
    assert otp_fallback.announce(settings, today=BEFORE) == otp_fallback.OFF
    assert _fallback_lines(caplog) == []


def test_on_unexpired_and_whatsapp_unconfigured_logs_the_code(caplog):
    caplog.set_level(logging.WARNING)
    assert otp_fallback.log_code("6281200011112", "482913", _settings(), today=BEFORE) is True
    assert any("[OTP-FALLBACK]" in m and "482913" in m for m in _fallback_lines(caplog))


def test_still_works_on_the_expiry_day_itself(caplog):
    caplog.set_level(logging.WARNING)
    assert otp_fallback.log_code("6281200011112", "482913", _settings(), today=LAST_DAY) is True


def test_whatsapp_configured_means_codes_never_reach_the_log(caplog):
    caplog.set_level(logging.WARNING)
    settings = _settings(whatsapp_access_token="EAAG-real-looking-token")
    assert otp_fallback.log_code("6281200011112", "482913", settings, today=BEFORE) is False
    assert not any("482913" in m for m in _fallback_lines(caplog))


def test_expired_ignores_the_flag_whatever_the_environment_says(caplog):
    caplog.set_level(logging.WARNING)
    assert otp_fallback.log_code("6281200011112", "482913", _settings(), today=AFTER) is False
    assert not any("482913" in m for m in _fallback_lines(caplog))


def test_expired_flag_is_refused_loudly_at_startup(caplog):
    caplog.set_level(logging.WARNING)
    assert otp_fallback.announce(_settings(), today=AFTER) == otp_fallback.EXPIRED
    errors = [r for r in caplog.records if r.name == "auth.otp_fallback" and r.levelno == logging.ERROR]
    assert errors and "refused" in errors[0].getMessage()


def test_every_boot_warns_with_days_left_while_enabled(caplog):
    caplog.set_level(logging.WARNING)
    assert otp_fallback.announce(_settings(), today=BEFORE) == otp_fallback.ACTIVE
    warnings = [r for r in caplog.records if r.name == "auth.otp_fallback" and r.levelno == logging.WARNING]
    assert warnings
    assert "10 day(s) left" in warnings[0].getMessage()


def test_fallback_never_enables_the_dev_bypass_in_production():
    assert is_dev_bypass_code(DEV_BYPASS_CODE, _settings()) is False
    assert is_dev_bypass_code(DEV_BYPASS_CODE, _settings(environment="development")) is True
