"""Posting rules (roadmap M6-T3): which accounts an event debits and credits.

Rules are DATA. Each row says: for `event_type`, the amount called `component`
is posted Dr `debit_code` / Cr `credit_code`. The posting engine (M6-T4) computes
an event's component amounts and looks up each rule; it contains no account
codes and no `if event == ...` branches. Owners may re-point a rule at another
account (a merchant with a separate "bank" for QRIS settlements, say) or
deactivate one; the standard set is seeded here and by migration 0016.

Components with a null debit/credit (`reversal`) tell the engine to reverse the
entry the source event posted, rather than post new lines.

Event types and components follow docs/BUILD-ROADMAP.md appendix A.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, PostingRule

# (event_type, component, debit_code, credit_code, description)
STANDARD_RULES: list[tuple[str, str, str | None, str | None, str]] = [
    # A sale: money in per payment method, revenue out; then the reclassifications.
    ("OrderCompleted", "payment:cash", "1100", "4100", "Penjualan dibayar tunai"),
    ("OrderCompleted", "payment:qris", "1120", "4100", "Penjualan dibayar QRIS"),
    ("OrderCompleted", "payment:transfer", "1110", "4100", "Penjualan dibayar transfer"),
    ("OrderCompleted", "payment:card", "1120", "4100", "Penjualan dibayar kartu"),
    ("OrderCompleted", "payment:ewallet", "1120", "4100", "Penjualan dibayar e-wallet"),
    ("OrderCompleted", "payment:points", "2300", "4100", "Penjualan ditukar poin"),
    ("OrderCompleted", "payment:other", "1200", "4100", "Penjualan, pembayaran lain / piutang"),
    ("OrderCompleted", "discount", "4200", "4100", "Diskon penjualan (kontra pendapatan)"),
    ("OrderCompleted", "promo", "4250", "4100", "Biaya promo (kontra pendapatan)"),   # M8-T3, migration 0023
    ("OrderCompleted", "tax", "4100", "2200", "Pajak yang dipungut dipisahkan dari pendapatan"),
    ("OrderCompleted", "service_charge", "4100", "4900", "Service charge diakui sebagai pendapatan lain"),
    ("OrderCompleted", "cogs", "5100", "1300", "Harga pokok penjualan"),
    # Rupiah rounding at the total (M7-T4b); backfilled by migration 0020.
    ("OrderCompleted", "rounding_up", "4100", "4900", "Pembulatan ke atas (pendapatan lain)"),
    ("OrderCompleted", "rounding_down", "4900", "4100", "Pembulatan ke bawah"),
    # Undoing a sale.
    ("OrderVoided", "reversal", None, None, "Batalkan seluruh jurnal penjualan"),
    ("OrderRefunded", "refund:cash", "4300", "1100", "Retur, uang kembali tunai"),
    ("OrderRefunded", "refund:qris", "4300", "1120", "Retur, uang kembali QRIS"),
    ("OrderRefunded", "refund:transfer", "4300", "1110", "Retur, uang kembali transfer"),
    ("OrderRefunded", "refund:card", "4300", "1120", "Retur, uang kembali kartu"),
    ("OrderRefunded", "refund:ewallet", "4300", "1120", "Retur, uang kembali e-wallet"),
    ("OrderRefunded", "refund:points", "4300", "2300", "Retur, poin dikembalikan"),
    ("OrderRefunded", "refund:other", "4300", "1200", "Retur, pembayaran lain"),
    ("OrderRefunded", "cogs_reversal", "1300", "5100", "Barang kembali ke persediaan"),
    # A refund also undoes what the sale reclassified (M7-T4b); migration 0020.
    ("OrderRefunded", "discount_reversal", "4100", "4200", "Retur: diskon penjualan dibatalkan"),
    ("OrderRefunded", "promo_reversal", "4100", "4250", "Retur: promo dibatalkan"),   # M8-T3
    ("OrderRefunded", "tax_reversal", "2200", "4100", "Retur: pajak yang dipungut dikembalikan"),
    ("OrderRefunded", "service_charge_reversal", "4900", "4100", "Retur: service charge dibatalkan"),
    ("OrderRefunded", "rounding_up_reversal", "4900", "4100", "Retur: pembulatan ke atas dibatalkan"),
    ("OrderRefunded", "rounding_down_reversal", "4100", "4900", "Retur: pembulatan ke bawah dibatalkan"),
    # Purchasing.
    ("GoodsReceived", "inventory", "1300", "2100", "Barang diterima, utang ke supplier"),
    ("SupplierPaid", "payment:cash", "2100", "1100", "Bayar supplier tunai"),
    ("SupplierPaid", "payment:transfer", "2100", "1110", "Bayar supplier transfer"),
    # Stock events.
    ("StockWasted", "waste", "5700", "1300", "Barang rusak / terbuang"),
    ("StockCounted", "variance_loss", "5700", "1300", "Selisih opname (kurang)"),
    ("StockCounted", "variance_gain", "1300", "5700", "Selisih opname (lebih)"),
    # Expenses by category (M6-T6); '*' is the fallback.
    ("ExpenseIncurred", "expense:bahan baku", "5200", "1100", "Belanja bahan baku"),
    ("ExpenseIncurred", "expense:operasional", "5500", "1100", "Biaya operasional"),
    ("ExpenseIncurred", "expense:gaji", "5300", "1100", "Gaji & upah"),
    ("ExpenseIncurred", "expense:sewa", "5400", "1100", "Sewa"),
    ("ExpenseIncurred", "expense:listrik", "5500", "1100", "Listrik, air & gas"),
    ("ExpenseIncurred", "expense:pemasaran", "5600", "1100", "Pemasaran & promo"),
    ("ExpenseIncurred", "expense:*", "5900", "1100", "Beban lain-lain"),
    # Till and loyalty.
    ("ShiftClosed", "variance_short", "5800", "1100", "Kas kurang saat tutup shift"),
    ("ShiftClosed", "variance_over", "1100", "5800", "Kas lebih saat tutup shift"),
    # Cash in and out (M7-T2); backfilled for existing businesses by migration 0018.
    ("CashIn", "owner", "1100", "3100", "Kas masuk dari pemilik"),
    ("CashIn", "bank", "1100", "1110", "Kas masuk dari bank"),
    ("BankDrop", "cash", "1110", "1100", "Setor kas ke bank"),
    ("SupplierPaid", "cash", "2100", "1100", "Bayar supplier tunai dari laci"),
    ("SupplierPaid", "transfer", "2100", "1110", "Bayar supplier lewat transfer"),
    ("PointsEarned", "points", "5600", "2300", "Poin diberikan ke pelanggan"),
    ("PointsRedeemed", "points", "2300", "4100", "Poin ditukar"),
    ("PointsReversed", "points", "2300", "5600", "Poin ditarik kembali (void / retur)"),   # M8-T2, migration 0022
]

EVENT_TYPES = sorted({e for e, *_ in STANDARD_RULES})


class PostingRuleInvalid(Exception):
    """`code`: account, same, reversal."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}:{detail}")


async def ensure_standard_rules(session: AsyncSession, business_id: uuid.UUID) -> dict[tuple[str, str], PostingRule]:
    existing = {(r.event_type, r.component): r for r in (await session.execute(select(PostingRule))).scalars()}
    for event_type, component, debit, credit, description in STANDARD_RULES:
        if (event_type, component) not in existing:
            r = PostingRule(business_id=business_id, event_type=event_type, component=component,
                            debit_code=debit, credit_code=credit, description=description, is_system=True)
            session.add(r)
            existing[(event_type, component)] = r
    await session.flush()
    return existing


async def rules_for(session: AsyncSession, event_type: str) -> dict[str, PostingRule]:
    """{component: rule} for the active rules of one event type."""
    rows = (
        await session.execute(
            select(PostingRule).where(PostingRule.event_type == event_type, PostingRule.is_active.is_(True))
        )
    ).scalars().all()
    return {r.component: r for r in rows}


def pick_rule(rules: dict[str, PostingRule], component: str) -> PostingRule | None:
    """Exact component, else the `prefix:*` fallback (expense:gaji → expense:*)."""
    if component in rules:
        return rules[component]
    if ":" in component:
        return rules.get(component.split(":", 1)[0] + ":*")
    return None


async def update_rule(session: AsyncSession, rule: PostingRule, **changes) -> PostingRule:
    if rule.component == "reversal" and (changes.get("debit_code") or changes.get("credit_code")):
        raise PostingRuleInvalid("reversal")
    # Validate the whole new pair before touching the row: the table's CHECK
    # would otherwise fire on autoflush with a less useful message.
    new_codes = {"debit_code": rule.debit_code, "credit_code": rule.credit_code}
    for field in ("debit_code", "credit_code"):
        code = changes.get(field)
        if code is None:
            continue
        acc = (await session.execute(select(Account).where(Account.code == code, Account.is_active.is_(True)))).scalar_one_or_none()
        if acc is None:
            raise PostingRuleInvalid("account", code)
        new_codes[field] = code
    if new_codes["debit_code"] is not None and new_codes["debit_code"] == new_codes["credit_code"]:
        raise PostingRuleInvalid("same")
    rule.debit_code, rule.credit_code = new_codes["debit_code"], new_codes["credit_code"]
    if changes.get("description") is not None:
        rule.description = changes["description"]
    if changes.get("is_active") is not None:
        rule.is_active = changes["is_active"]
    rule.updated_at = datetime.now(timezone.utc)
    await session.flush()
    return rule
