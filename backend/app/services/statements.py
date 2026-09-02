"""Financial statements (roadmap M6-T5): profit and loss, then balance sheet.

Both read straight off the journal (M6-T2) through the chart's account types
(M6-T1). Revenue and expense accounts over a window make the P&L; asset,
liability and equity accounts up to an instant make the balance sheet, with
the P&L to date carried as *current earnings* because the ledger has no
closing entries (nothing is ever closed or deleted, roadmap §1.7).

Every figure is an account balance in its normal direction, so a contra
account (diskon, retur) shows negative under revenue rather than as an
expense, and inventory shows negative until opening stock is capitalised.

"Balance sheet balances" — assets = liabilities + equity + current earnings —
is a theorem of every entry balancing (the deferred constraint in 0015). It
is checked as an invariant in tests/test_invariants.py, not assumed here:
`BalanceSheet.balances` is computed, and the API surfaces it.

Windows are half-open [since, until) in UTC; the API turns local dates into
these bounds with the business timezone.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, JournalEntry, JournalLine
from app.services.accounts import DEBIT_NORMAL

ZERO = Decimal("0.00")
# Cost of goods sold lives in the 51xx block: 5100 HPP and any 51xx account the
# owner adds. Everything else under expense is operating expense.
COGS_PREFIX = "51"


@dataclass(frozen=True)
class StatementLine:
    code: str
    name: str
    type: str
    amount: Decimal  # normal-direction balance, 2 dp


def _total(lines: list[StatementLine]) -> Decimal:
    return sum((l.amount for l in lines), ZERO)


@dataclass
class ProfitAndLoss:
    since: datetime
    until: datetime
    revenue: list[StatementLine] = field(default_factory=list)
    cogs: list[StatementLine] = field(default_factory=list)
    expenses: list[StatementLine] = field(default_factory=list)

    @property
    def revenue_total(self) -> Decimal:
        return _total(self.revenue)

    @property
    def cogs_total(self) -> Decimal:
        return _total(self.cogs)

    @property
    def gross_profit(self) -> Decimal:
        return self.revenue_total - self.cogs_total

    @property
    def expenses_total(self) -> Decimal:
        return _total(self.expenses)

    @property
    def net_profit(self) -> Decimal:
        return self.gross_profit - self.expenses_total


@dataclass
class BalanceSheet:
    until: datetime
    assets: list[StatementLine] = field(default_factory=list)
    liabilities: list[StatementLine] = field(default_factory=list)
    equity: list[StatementLine] = field(default_factory=list)
    current_earnings: Decimal = ZERO  # revenue − expenses, all time up to `until`

    @property
    def assets_total(self) -> Decimal:
        return _total(self.assets)

    @property
    def liabilities_total(self) -> Decimal:
        return _total(self.liabilities)

    @property
    def equity_total(self) -> Decimal:
        return _total(self.equity)

    @property
    def liabilities_and_equity_total(self) -> Decimal:
        return self.liabilities_total + self.equity_total + self.current_earnings

    @property
    def balances(self) -> bool:
        return self.assets_total == self.liabilities_and_equity_total


async def statement_lines(
    session: AsyncSession, *, since: datetime | None = None, until: datetime | None = None,
) -> list[StatementLine]:
    """Every account with a non-zero normal-direction balance over [since, until),
    ordered by code. One grouped query."""
    stmt = (
        select(Account.code, Account.name, Account.type,
               func.coalesce(func.sum(JournalLine.debit), 0), func.coalesce(func.sum(JournalLine.credit), 0))
        .join(JournalLine, JournalLine.account_id == Account.id)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .group_by(Account.code, Account.name, Account.type)
        .order_by(Account.code)
    )
    if since is not None:
        stmt = stmt.where(JournalEntry.posted_at >= since)
    if until is not None:
        stmt = stmt.where(JournalEntry.posted_at < until)
    out: list[StatementLine] = []
    for code, name, type_, debit, credit in (await session.execute(stmt)).all():
        d, c = Decimal(debit), Decimal(credit)
        amount = (d - c) if type_ in DEBIT_NORMAL else (c - d)
        if amount != 0:
            out.append(StatementLine(code=code, name=name, type=type_, amount=amount.quantize(ZERO)))
    return out


async def profit_and_loss(session: AsyncSession, *, since: datetime, until: datetime) -> ProfitAndLoss:
    """Revenue, cost of goods sold and operating expenses posted in [since, until)."""
    report = ProfitAndLoss(since=since, until=until)
    for line in await statement_lines(session, since=since, until=until):
        if line.type == "revenue":
            report.revenue.append(line)
        elif line.type == "expense":
            (report.cogs if line.code.startswith(COGS_PREFIX) else report.expenses).append(line)
    return report


async def balance_sheet(session: AsyncSession, *, until: datetime | None = None) -> BalanceSheet:
    """Position from everything posted before `until` (None = everything)."""
    sheet = BalanceSheet(until=until)
    earnings = ZERO
    for line in await statement_lines(session, until=until):
        if line.type == "asset":
            sheet.assets.append(line)
        elif line.type == "liability":
            sheet.liabilities.append(line)
        elif line.type == "equity":
            sheet.equity.append(line)
        elif line.type == "revenue":
            earnings += line.amount
        elif line.type == "expense":
            earnings -= line.amount
    sheet.current_earnings = earnings
    return sheet
