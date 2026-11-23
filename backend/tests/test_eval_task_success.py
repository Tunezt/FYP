"""fin-eval (P1-1a): the task-success script counts an error reply and a repeat
within 2 minutes as failures, and nothing else."""
import importlib.util
import pathlib
from datetime import datetime, timedelta, timezone

spec = importlib.util.spec_from_file_location(
    "eval_task_success", pathlib.Path(__file__).resolve().parents[2] / "scripts" / "eval_task_success.py")
ts = importlib.util.module_from_spec(spec)
import sys
sys.modules["eval_task_success"] = ts
spec.loader.exec_module(ts)

T0 = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)


def row(minutes, text, status="ok", biz="a", path="text"):
    return ts.Row(biz, T0 + timedelta(seconds=minutes * 60), text, status, "whatsapp", path)


def test_clean_questions_are_all_successes():
    r = ts.evaluate([row(0, "penjualan hari ini brp"), row(10, "stok kopi berapa")])
    assert (r["total"], r["failed"], r["rate"]) == (2, 0, 1.0)


def test_an_error_reply_fails():
    r = ts.evaluate([row(0, "penjualan hari ini", "error"), row(10, "stok kopi")])
    assert (r["failed"], r["errors"], r["rate"]) == (1, 1, 0.5)


def test_a_rephrase_within_two_minutes_fails_the_first_ask_only():
    r = ts.evaluate([row(0, "penjualan hari ini brp?"), row(1, "penjualan hari ini berapa"), row(30, "stok gula")])
    assert (r["total"], r["failed"], r["repeats"]) == (3, 1, 1)


def test_a_repeat_after_two_minutes_or_a_follow_up_or_another_business_is_fine():
    r = ts.evaluate([
        row(0, "penjualan hari ini"), row(3, "penjualan hari ini"),      # too late
        row(10, "penjualan hari ini"), row(11, "dan kemarin?"),          # a follow-up, not a repeat
        row(20, "stok kopi", biz="a"), row(20.5, "stok kopi", biz="b"),  # different cafes
    ])
    assert r["failed"] == 0


def test_photos_and_no_text_are_not_questions():
    assert ts.evaluate([row(0, None), row(1, "x", path="image")])["total"] == 0
