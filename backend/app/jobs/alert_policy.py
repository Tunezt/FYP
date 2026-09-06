"""Alert quality, not quantity (roadmap M10-T2).

Silence when nothing is wrong is the feature; a muted assistant is worth
nothing. Three policies sit between a rule's finding and an `alerts` row:

  deduplicate     one alert per `rule_key` (rule + subject + local day), ever
  suppress        the same *subject* alerted within SUPPRESS_WINDOW_DAYS —
                  acknowledged or not — is the same alert as yesterday and is
                  not said again; when the window passes and the condition
                  still holds, it is said once more
  rate limit      at most MAX_NEW_PER_NIGHT new alerts per business per local
                  day (a re-run after a crash does not double it), highest
                  severity first; what does not fit is not lost — a condition
                  that still holds tomorrow is found again tomorrow

The subject is the rule key without its day: `stockout_risk:<item>:<day>` and
the till's own `stock:<item>:<day>` low-stock alert share the subject
`stock:<item>`, so an item running low produces one conversation, not two.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Alert, Business

SUPPRESS_WINDOW_DAYS = 7
MAX_NEW_PER_NIGHT = 5
_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


def subject_of(rule_key: str) -> str:
    """`rule:subject:YYYY-MM-DD` → `rule:subject`."""
    head, _, day = rule_key.rpartition(":")
    return head if head and len(day) == 10 and day[4] == "-" else rule_key


@dataclass
class PolicyResult:
    written: list[Alert] = field(default_factory=list)
    duplicates: int = 0
    suppressed: int = 0
    deferred: int = 0

    @property
    def dropped(self) -> int:
        return self.duplicates + self.suppressed + self.deferred


async def apply_policy(session: AsyncSession, business: Business, findings: list, now: datetime | None = None) -> PolicyResult:
    """Findings are `RuleAlert`-shaped (type, rule_key, severity, message, metric,
    related_item_id, details). Returns what was written and what was not, and why."""
    moment = now or datetime.now(timezone.utc)
    result = PolicyResult()
    if not findings:
        return result

    keys = [f.rule_key for f in findings]
    existing_keys = set((await session.execute(select(Alert.rule_key).where(Alert.rule_key.in_(keys)))).scalars().all())
    since = moment - timedelta(days=SUPPRESS_WINDOW_DAYS)
    recent = (await session.execute(
        select(Alert.rule_key).where(Alert.rule_key.is_not(None), Alert.created_at >= since, Alert.created_at <= moment)
    )).scalars().all()
    recent_subjects = {subject_of(k) for k in recent}

    candidates = []
    seen_now: set[str] = set()
    for f in findings:
        if f.rule_key in existing_keys or f.rule_key in seen_now:
            result.duplicates += 1
            continue
        if subject_of(f.rule_key) in recent_subjects:
            result.suppressed += 1
            continue
        seen_now.add(f.rule_key)
        candidates.append(f)

    # The cap is per business per local day, not per run: a job that is
    # re-run after a crash must not say ten things where one run would say five.
    from app.ai.periods import period_range

    day_start, _day_end, _ = period_range(
        "today", business.timezone, now=moment, day_start_hour=business.day_start_hour
    )
    already_today = int((await session.execute(
        select(func.count(Alert.id)).where(Alert.rule_key.is_not(None), Alert.created_at >= day_start, Alert.created_at <= moment)
    )).scalar_one())
    room = max(0, MAX_NEW_PER_NIGHT - already_today)
    candidates.sort(key=lambda f: (_SEVERITY_RANK.get(f.severity, 9), f.rule_key))
    kept, deferred = candidates[:room], candidates[room:]
    result.deferred = len(deferred)
    for f in kept:
        alert = Alert(
            business_id=business.id, type=f.type, metric=f.metric, severity=f.severity, message=f.message,
            related_item_id=f.related_item_id, rule_key=f.rule_key, details=f.details, created_at=moment,
        )
        session.add(alert)
        result.written.append(alert)
    await session.flush()
    return result
