"""Seed the demo café: `python -m app.seed`

Creates the **Poernama** demo business (Jakarta) so that every page, till screen
and WhatsApp tool has something real to show (docs/showcase-map.md says where):

- owner + 3 cashiers (one a manager) + 1 deactivated cashier (PINs below)
- 19 items: drinks and food (several made to order from a recipe, with sizes and
  options) plus raw materials in kg/liter/pcs
- 5 suppliers, 12 goods receipts (a supplier price rise yesterday), 6 purchase orders
  in every status, 4 photographed receipts (nota) with expenses and stock in
- 8 customers with visit history and points, 3 promos and a voucher batch, used
- 56 days of deterministic pseudo-random sales (weekend bumps, a growth trend, one
  planted spike yesterday), today's sales up to the current hour, cashier shifts
  with cash counts (one short, one over), voids by one cashier, one refund
- the nightly pass run once, so all five alert rules have fired; plus older alerts
  the owner already read

Idempotent: re-running wipes and refills the demo business (cascade), so its
dates move up to today. Re-run it before each presentation. The business keeps
its id, and the owner and staff keep theirs (p2-reseed): a dashboard or till
session open before the re-seed still works after it, and the WhatsApp cafe
pick, extra owner numbers, till pairing and bound tablet carry over.

A demo on a real number (the FYP presentation, 26 Nov 2026):
  python -m app.seed --phone 0811... --name "Poernama Demo" --owner "Pemilik" --yes
It refuses when the number owns any other cafe, so it can only ever replace
its own demo business. Being an extra assistant number elsewhere is fine.

Never calls WhatsApp. Receipt embeddings come from
app/seed_receipt_embeddings.json (made once by scripts/demo_receipt_embeddings.py);
a receipt whose text changed has none, and the seed says so. Run from the command
line it also saves docs/demo-receipt-toko-supply.jpg as if the owner had sent it
and replied YA (stored photo, receipt, goods receipt, expense): that one receipt
costs one Gemini embedding call, and if storage or the embedding fails the seed
still finishes and says what to do.

**Development only.** It refuses to run with ENVIRONMENT=production, and asks
before touching any database that is not on this machine, because deleting and
recreating a business is not something to do to a cafe that is trading. The
production entrypoint is `python -m app.bootstrap` (roadmap M15-T3).

Demo credentials:
  owner phone  +62 812-000-1111  (OTP arrives via WhatsApp; in dev the code is logged)
  owner PIN    1234   | Sari 2345 | Budi 3456 | Dewi (manager) 4567
"""
import argparse
import asyncio
import hashlib
import json
import random
import uuid
from collections import defaultdict
from datetime import date, datetime, time as dtime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select, text

from app.core.config import DEMO_OWNER_PHONE, ProductionRefusal, get_settings, refuse_in_production
from app.core.db import plain_session, tenant_session
from app.core.security import hash_pin
from app.models import (
    Alert, Approval, Business, Customer, Item, ItemVariant, KitchenEvent, Order, OrderLine, Payment,
    PointsMovement, PromoApplication, PurchaseOrder, Receipt, ServiceNumberCounter, Staff,
    StockMovement,
)
from app.seed_data import (
    CUSTOMERS, DEARER_FACTOR, PRIOR_MONTH_EXPENSES, DEARER_THIS_WEEK, EXTRA_EXPENSES, EXTRA_ITEMS, EXTRA_MENU_CATEGORIES,
    EXTRA_MODIFIERS, EXTRA_PREP_STATIONS, EXTRA_RECIPES, EXTRA_VARIANTS, HISTORY_DAYS, NOTAS, OLD_ALERTS,
    PURCHASE_ORDERS, RECEIPTS, STAFF_WEIGHTS, STAGED_RECEIPT, SUPPLIERS, VOID_NOTES, VOUCHER_USES,
)
from app.services.catalog import (
    create_modifier, create_modifier_group, create_variant, ensure_default_variant, set_recipe_line,
    sync_default_from_item,
)
from app.services.accounts import ensure_standard_chart
from app.services.cash import record_cash_movement
from app.services.posting_rules import ensure_standard_rules
from app.services.points import ensure_loyalty_settings
from app.services.business_profile import apply_tax_placeholder, wordmark_logo
from app.services.pricing import ensure_pricing_settings
from app.services.customers import create_customer
from app.services.promos import create_promo
from app.services.purchasing import PoLineSpec, create_purchase_order, lines_of, mark_ordered, cancel as cancel_po
from app.services.receiving import GrLineSpec, receive_goods
from app.services.shifts import close_shift, open_shift
from app.services.stock import add_stock
from app.services.suppliers import create_supplier, link_receipt
from app.services.tickets import place_ticket
from app.services.orders import OrderLineSpec
from app.services.vouchers import create_vouchers, redeem
from app.services.owner_numbers import OwnerNumberInvalid, clean_owner_number
from app.services.units import ensure_standard_uoms

SUPPLIER_ROWS = SUPPLIERS

# Recipes (M4-T4): (item, variant name, [(component item, quantity, uom code)])
# A large latte uses more beans and milk than a regular one.
RECIPES = [
    ("Es Kopi Susu", "Standar", [("Biji Arabica", 18, "g"), ("Susu UHT", 120, "ml"), ("Gula Aren", 15, "g")]),
    ("Es Kopi Susu", "Large", [("Biji Arabica", 24, "g"), ("Susu UHT", 180, "ml"), ("Gula Aren", 20, "g")]),
    ("Americano", "Standar", [("Biji Arabica", 18, "g")]),
    ("Americano", "Large", [("Biji Arabica", 24, "g")]),
] + EXTRA_RECIPES

# Modifier groups (M4-T2): (item, group name, selection, required, [(modifier, price_delta, default)])
MODIFIERS = [
    ("Es Kopi Susu", "Gula", "single", True,
     [("Normal", 0, True), ("Sedikit gula", 0, False), ("Tanpa gula", 0, False)]),
    ("Es Kopi Susu", "Tambahan", "multi", False,
     [("Extra shot", 5000, False), ("Susu oat", 6000, False)]),
    ("Matcha Latte", "Tambahan", "multi", False,
     [("Extra matcha", 4000, False), ("Susu oat", 6000, False)]),
    ("Americano", "Suhu", "single", True,
     [("Panas", 0, True), ("Dingin", 0, False)]),
] + EXTRA_MODIFIERS

# Sizes for the drinks that have them (M4-T1): (item name, variant name, sell, cost)
VARIANTS = [
    ("Es Kopi Susu", "Large", 27000, 10000),
    ("Matcha Latte", "Large", 33000, 13000),
    ("Americano", "Large", 22000, 7500),
] + EXTRA_VARIANTS

# Where each sellable item is prepared (prt-2). Set by hand, never inferred from
# a name: the croissant is on the display at the front, so it is the bar's.
PREP_STATIONS = {
    "Es Kopi Susu": "bar", "Kopi Arabica (cup)": "bar", "Americano": "bar", "Matcha Latte": "bar",
    "Teh Tarik": "bar", "Croissant": "bar", "Roti Bakar Coklat": "kitchen", "Nasi Goreng Spesial": "kitchen",
    **EXTRA_PREP_STATIONS,
}

# till-10: the section each product is listed under on the till and the QR menu.
MENU_CATEGORIES = {
    "Es Kopi Susu": "Kopi", "Kopi Arabica (cup)": "Kopi", "Americano": "Kopi",
    "Matcha Latte": "Non-kopi", "Teh Tarik": "Non-kopi",
    "Nasi Goreng Spesial": "Makanan", "Croissant": "Camilan", "Roti Bakar Coklat": "Dessert",
    **EXTRA_MENU_CATEGORIES,
}

OWNER_PHONE = DEMO_OWNER_PHONE

ITEMS = [
    # (name, unit, stock, cost, sell, reorder_threshold, popularity weight)
    ("Es Kopi Susu", "cup", 48, 8000, 22000, 20, 10),
    ("Kopi Arabica (cup)", "cup", 35, 7000, 20000, 15, 7),
    ("Americano", "cup", 40, 6000, 18000, 15, 5),
    ("Matcha Latte", "cup", 25, 11000, 28000, 10, 4),
    ("Teh Tarik", "cup", 30, 4000, 15000, 10, 4),
    ("Roti Bakar Coklat", "pcs", 18, 9000, 24000, 8, 3),
    ("Croissant", "pcs", 12, 12000, 28000, 6, 6),
    ("Nasi Goreng Spesial", "porsi", 15, 15000, 35000, 6, 2),
    # Raw materials — restocked via receipts, queried by kg on WhatsApp
    ("Biji Arabica", "kg", 8, 145000, 0, 3, 0),
    ("Gula Aren", "kg", 5, 38000, 0, 2, 0),
    ("Susu UHT", "liter", 24, 17000, 0, 10, 0),
] + EXTRA_ITEMS

# This month's costs: (category, description, amount, day of the month). Each one, and each nota in
# seed_data.NOTAS, has a twin on the same day of last month that is at least as large
# (PRIOR_MONTH_EXPENSES), so "Untung bulan ini" is up on whatever day the demo is seeded.
EXPENSES = [
    ("operasional", "Galon air minum + es batu", 45000, 1),
    ("bahan baku", "Susu UHT 2 dus", 408000, 3),
    ("bahan baku", "Belanja biji kopi + gula mingguan", 750000, 6),
    ("operasional", "Gas 3kg x4", 88000, 9),
    ("operasional", "Listrik & air bulan ini", 850000, 10),
    ("lainnya", "Service mesin espresso", 350000, 18),
] + EXTRA_EXPENSES

EMBEDDINGS_FILE = Path(__file__).with_name("seed_receipt_embeddings.json")
STAGED_PHOTO = Path(__file__).resolve().parents[2] / "docs" / "demo-receipt-toko-supply.jpg"
# What a re-seed keeps of the business row (p2-reseed): the id every owner and till token carries,
# the till pairing they were issued under, the WhatsApp cafe pick and extra numbers, the bound tablet.
KEPT_ON_RESEED = ("id", "pairing_generation", "extra_owner_numbers", "active_owner_numbers",
                  "till_device_lock", "till_device_hash", "till_device_bound_at")
JAK = ZoneInfo("Asia/Jakarta")
TWO = Decimal("0.01")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def guard_demo_data(confirm: bool = False) -> None:
    """Two gates in front of a destructive demo. The first is absolute; the
    second exists because ENVIRONMENT is a self-declared flag and a real cafe's
    database is remote long before anyone remembers to set it."""
    refuse_in_production("app.seed")
    host = urlsplit(get_settings().database_url).hostname
    if host not in LOCAL_HOSTS and not confirm:
        raise ProductionRefusal(
            f"app.seed deletes and recreates the demo business, and {host} is not this "
            "machine. Re-run with --yes if that database really is a throwaway."
        )


async def demo_business_to_replace(session, phone: str, name: str) -> Business | None:
    """The demo business this run replaces, or None. Refuses when `phone` owns a
    business of another name: a mistyped number must never delete a real cafe.
    An extra assistant number elsewhere is allowed (the bot asks which cafe)."""
    found = (await session.execute(select(Business).where(Business.owner_phone == phone))).scalar_one_or_none()
    if found is not None and found.name != name:
        raise ProductionRefusal(
            f"{phone} already owns '{found.name}', and the seed only replaces its own demo. "
            "Use the --name of the demo it seeded before, or another number."
        )
    return found


def at_local(now: datetime, days_ago: int, hour: int, minute: int = 0) -> datetime:
    """`hour:minute` WIB, `days_ago` days back from today, as an aware UTC time."""
    local = now.astimezone(JAK) - timedelta(days=days_ago)
    return local.replace(hour=hour, minute=minute, second=0, microsecond=0).astimezone(timezone.utc)


async def default_modifiers(session, item_id) -> list:
    """The ticked option of every required choice (what a guest gets by not touching it)."""
    from app.models import Modifier, ModifierGroup

    return list((await session.execute(
        select(Modifier.id).join(ModifierGroup, ModifierGroup.id == Modifier.group_id)
        .where(ModifierGroup.item_id == item_id, ModifierGroup.is_required.is_(True), Modifier.is_default.is_(True))
    )).scalars().all())


def nota_parsed(nota: tuple) -> tuple[dict, datetime, Decimal]:
    """What the vision step would have read from a nota's photo, its time, and its total."""
    _fname, sname, nota_date, hour, nlines, _style = nota
    when = datetime.combine(date.fromisoformat(nota_date), dtime(hour, 20), tzinfo=JAK).astimezone(timezone.utc)
    total = sum((Decimal(q) * Decimal(p) for _n, q, _u, p in nlines), Decimal(0))
    parsed = {
        "document_type": "receipt", "supplier": sname, "date": when.astimezone(JAK).date().isoformat(),
        "items": [{"name": n, "quantity": q, "unit": u, "unit_price": p, "line_total": q * p} for n, q, u, p in nlines],
        "total_amount": int(total), "confidence": "high", "ambiguities": [],
    }
    return parsed, when, total


def embedding_for(text_: str) -> list[float] | None:
    """The stored vector for a receipt's exact text, or None (never a live call)."""
    if not EMBEDDINGS_FILE.exists():
        return None
    return json.loads(EMBEDDINGS_FILE.read_text(encoding="utf-8")).get(hashlib.sha256(text_.encode()).hexdigest())


async def stage_demo_receipt(business_id: uuid.UUID, now: datetime) -> list[str]:
    """Saves docs/demo-receipt-toko-supply.jpg the way the owner's photo + YA on WhatsApp saves it:
    the photo uploaded to storage, then the same draft and confirm as the YA (receipt row, goods
    receipt with the stock in, the expense, the search vector). Returns the warnings, if any."""
    from app.ai.vision import normalize_parse
    from app.services.invoice_draft import build_draft, confirm_draft
    from app.services.receipts import image_hash
    from app.whatsapp import storage

    parsed = normalize_parse(json.loads(json.dumps(STAGED_RECEIPT)))   # a fresh copy: the confirm stores it
    if date.fromisoformat(parsed["date"]) > now.astimezone(JAK).date():
        return []   # the photo's printed date has not come yet on the day being seeded
    try:
        photo = STAGED_PHOTO.read_bytes()
    except OSError as exc:
        return [f"staged receipt NOT saved: {exc}; send the photo from the demo WhatsApp number and reply YA"]
    parsed["image_sha256"] = image_hash(photo)
    # The dashboard also ships this photo (frontend/public/demo-receipts), so the receipt keeps its
    # thumbnail when the bucket is unreachable or this machine has no storage credentials.
    bundled = f"{storage.DEMO_PREFIX}{STAGED_PHOTO.name}"
    warnings = []
    if storage._dry_run():
        image_path = bundled
    else:
        try:
            image_path = await storage.upload_receipt_image(business_id, photo, "image/jpeg")
        except Exception as exc:
            image_path = bundled
            warnings.append(f"the staged receipt's photo upload failed ({type(exc).__name__}: {exc}); "
                            "the receipt is saved with the dashboard's bundled copy of the photo")
    async with tenant_session(business_id) as session:
        business = await session.get(Business, business_id)
        draft = await build_draft(session, business, parsed)
        facts = await confirm_draft(session, business, draft, image_path, parsed)
        receipt = await session.get(Receipt, uuid.UUID(facts["receipt_id"]))
        embedded = receipt.embedding is not None
    total = f"{int(parsed['total_amount']):,}".replace(",", ".")
    print(f"Staged receipt saved: Toko Supply Rp {total}, {len(draft['matched'])} line(s) into stock")
    if draft["questions"]:
        warnings.append(f"staged receipt saved, but {len(draft['questions'])} line(s) matched no item: {draft['questions']}")
    if not embedded:
        warnings.append("staged receipt saved WITHOUT its search vector (the Gemini embedding call failed, see the log "
                        "above): the receipt question will not find it. Re-run the seed when Gemini answers.")
    return warnings


async def seed(confirm: bool = False, phone: str = OWNER_PHONE, business_name: str = "Poernama",
               owner_name: str = "Ibu Diah", today: datetime | None = None, stage_receipt: bool = False) -> list[str]:
    """Returns the alert types the nightly pass wrote. `today` is for tests: the demo as it would
    look on another day (the weekday pattern changes, the alert rules must still all fire).
    `stage_receipt` also saves the staged Toko Supply receipt (one Gemini embedding call); the
    command line turns it on."""
    guard_demo_data(confirm)
    try:
        phone = clean_owner_number(phone)
    except OwnerNumberInvalid:
        raise ProductionRefusal(f"{phone!r} is not a mobile number")
    rng = random.Random(42)
    now = today or datetime.now(timezone.utc)
    local_now = now.astimezone(JAK)

    async with plain_session() as session:
        existing = await demo_business_to_replace(session, phone, business_name)
        kept = {col: getattr(existing, col) for col in KEPT_ON_RESEED} if existing else {}
    kept_staff: dict = {}   # "owner" or a cashier's name -> the id their till/dashboard session carries
    if kept:
        async with tenant_session(kept["id"]) as session:
            for sid, sname, role in (await session.execute(select(Staff.id, Staff.name, Staff.role))).all():
                kept_staff["owner" if role == "owner" else sname] = sid

    def same_id(key: str) -> dict:
        return {"id": kept_staff[key]} if key in kept_staff else {}

    async with plain_session() as session:
        if kept:
            # Cascades to all business data; the row comes back below under the same id.
            await session.execute(delete(Business).where(Business.id == kept["id"]))
        business = Business(
            **kept,
            name=business_name,
            business_type="cafe",
            owner_phone=phone,
            language_preference="id",
            timezone="Asia/Jakarta",
            onboarding_completed_at=now - timedelta(days=HISTORY_DAYS + 5),
            require_shift=True,   # till-4
            receipt_mode="ask",   # till-5b: the customer's receipt only when asked
        )
        # The header of a demo receipt reads like a café's, not like a blank form.
        business.address = "Jl. Kenanga No. 21, Kebayoran Baru, Jakarta Selatan"
        business.contact_phone = "021-5550-0123"
        business.instagram = "@poernama.demo"
        business.receipt_logo = wordmark_logo()   # till-12: the signage lettering on the receipt
        business.receipt_footer = "WiFi: Poernama-Tamu - kata sandi ada di kasir"   # till-12: demo closing line
        # The demo café has two months of invented history, so its row has to be at
        # least that old or the story does not hold together: M15-T10 refuses a
        # paper sale dated before the business existed, and a business created
        # "now" would make every one of its own seeded sales unenterable.
        business.created_at = now - timedelta(days=HISTORY_DAYS + 5)
        session.add(business)
        await session.flush()
        business_id = business.id

    async with tenant_session(business_id) as session:
        owner = Staff(**same_id("owner"), business_id=business_id, name=owner_name, role="owner", phone=phone, pin_hash=hash_pin("1234"))
        sari = Staff(**same_id("Sari"), business_id=business_id, name="Sari", pin_hash=hash_pin("2345"))
        budi = Staff(**same_id("Budi"), business_id=business_id, name="Budi", pin_hash=hash_pin("3456"))
        dewi = Staff(**same_id("Dewi"), business_id=business_id, name="Dewi", role="manager", pin_hash=hash_pin("4567"))
        rudi = Staff(**same_id("Rudi"), business_id=business_id, name="Rudi", pin_hash=hash_pin("5678"), is_active=False)  # left in October
        session.add_all([owner, sari, budi, dewi, rudi])
        await session.flush()
        by_staff = {"owner": owner, "Sari": sari, "Budi": budi, "Dewi": dewi}
        staff_pool = [by_staff[k] for k in STAFF_WEIGHTS]
        staff_weights = list(STAFF_WEIGHTS.values())

        # Units of measure (M4-T3): the standard set, and each item linked to its unit.
        uoms = await ensure_standard_uoms(session, business_id)
        await ensure_standard_chart(session, business_id)  # M6-T1
        await ensure_standard_rules(session, business_id)  # M6-T3
        apply_tax_placeholder(await ensure_pricing_settings(session, business_id))  # M7-T4; till-5a/11 PB1 10% on top of the price
        loyalty = await ensure_loyalty_settings(session, business_id)  # M8-T2: on, 1 poin / Rp 1.000, poin = Rp 100
        loyalty.is_active, loyalty.rupiah_per_point, loyalty.point_value, loyalty.min_redeem_points = True, Decimal(1000), Decimal(100), 10
        await session.flush()

        # Customers (M8-T1): regulars, a lapsed one, a brand-new one, a birthday this week.
        customers = []   # (customer, first_day, last_day, weight)
        for cname, cphone, cnotes, first, last, weight, birthday_in in CUSTOMERS:
            birthday = None
            if birthday_in is not None:
                b = (local_now + timedelta(days=birthday_in)).date()
                birthday = date(1998, b.month, b.day)
            c = await create_customer(session, business_id, name=cname, phone=cphone, notes=cnotes, birthday=birthday)
            customers.append((c, first, last, weight))
        andi = customers[0][0]
        suppliers = {}
        for sname, sphone, saddress, snotes in SUPPLIER_ROWS:  # M5-T1
            suppliers[sname] = await create_supplier(session, business_id, name=sname, phone=sphone, address=saddress, notes=snotes)

        items = []
        target_stock: dict = {}
        for name, unit, stock, cost, sell, reorder, weight in ITEMS:
            item = Item(
                business_id=business_id, name=name, unit=unit,
                current_stock=Decimal(str(stock)), cost_price=Decimal(cost),
                sell_price=Decimal(sell), reorder_threshold=Decimal(str(reorder)),
                uom_id=uoms[unit].id if unit in uoms else None,
                prep_station=PREP_STATIONS.get(name),
                menu_category=MENU_CATEGORIES.get(name),
            )
            session.add(item)
            items.append((item, weight))
        await session.flush()
        by_name = {item.name: item for item, _w in items}
        target_stock = {item.id: Decimal(item.current_stock) for item, _w in items}

        # Every item gets its default "Standar" variant; a few drinks get a Large.
        defaults = {}
        for item, _w in items:
            defaults[item.id] = (await ensure_default_variant(session, item)).id
        larges = {}
        for item_name, vname, sell, cost in VARIANTS:
            v = await create_variant(session, by_name[item_name], name=vname,
                                     sell_price=Decimal(sell), cost_price=Decimal(cost))
            larges[by_name[item_name].id] = v
        for item_name, gname, selection, required, choices in MODIFIERS:
            group = await create_modifier_group(session, by_name[item_name], name=gname,
                                                selection=selection, is_required=required)
            for order, (mname, delta, is_default) in enumerate(choices):
                await create_modifier(session, group, name=mname, price_delta=Decimal(delta),
                                      is_default=is_default, sort_order=order)
        # Recipes: the drinks are made to order from the raw materials.
        variant_by = {(item.name, "Standar"): defaults[item.id] for item, _w in items}
        for item_id, v in larges.items():
            variant_by[(next(i.name for i, _w in items if i.id == item_id), v.name)] = v.id
        recipe_of: dict[tuple[str, str], list[tuple[Item, Decimal]]] = {}   # (item, variant) -> [(component, qty in the component's own unit)]
        to_item_unit = {"g": Decimal("0.001"), "ml": Decimal("0.001")}
        for item_name, vname, components in RECIPES:
            variant = await session.get(ItemVariant, variant_by[(item_name, vname)])
            for comp_name, qty, code in components:
                await set_recipe_line(session, variant, by_name[comp_name], quantity=Decimal(qty), uom_id=uoms[code].id)
                recipe_of.setdefault((item_name, vname), []).append((by_name[comp_name], Decimal(qty) * to_item_unit[code]))

        # ── buying: purchase orders, goods receipts, photographed receipts ───────
        purchased: dict = defaultdict(Decimal)   # item id -> quantity bought in the history
        po_rows: dict[str, PurchaseOrder] = {}
        for key, (sname, status, ordered_ago, expected_in, note, plines) in PURCHASE_ORDERS.items():
            po = await create_purchase_order(
                session, business_id, supplier_id=suppliers[sname].id, notes=note, created_by=owner.id,
                expected_at=(local_now + timedelta(days=expected_in)).date() if expected_in is not None else None,
                lines=[PoLineSpec(item_id=by_name[n].id, quantity=Decimal(q), unit_cost=Decimal(p)) for n, q, p in plines],
            )
            if status != "draft":
                await mark_ordered(session, po)
                po.ordered_at = at_local(now, ordered_ago, 9)
            if status == "cancelled":
                await cancel_po(session, po)
                po.cancelled_at = at_local(now, ordered_ago - 1, 10)
            po.created_at = po.updated_at = at_local(now, ordered_ago, 9)
            po_rows[key] = po
        await session.flush()
        for sname, days_ago, hour, po_key, rlines in sorted(RECEIPTS, key=lambda r: -r[1]):
            po = po_rows.get(po_key) if po_key else None
            plmap = {l.item_id: l for l in await lines_of(session, po.id)} if po is not None else {}
            when = at_local(now, days_ago, hour)
            got = await receive_goods(
                session, business_id, supplier_id=suppliers[sname].id, po_id=po.id if po else None, received_by=owner.id,
                received_at=when,
                lines=[GrLineSpec(item_id=by_name[n].id, quantity=Decimal(q), unit_cost=Decimal(p),
                                  po_line_id=plmap[by_name[n].id].id if by_name[n].id in plmap else None)
                       for n, q, p in rlines],
            )
            for n, q, _p in rlines:
                purchased[by_name[n].id] += Decimal(q)
            if po is not None:
                po.updated_at = when
            _ = got
        # The receipts the owner photographed: the receipt row, the expense, the stock in.
        from app.services.expenses import record_expense
        from app.services.posting import post_event
        from app.services.rag import receipt_to_document_text

        missing_embeddings = 0
        nota_receipts = []
        for nota in NOTAS:
            fname, sname, _d, _h, nlines, _style = nota
            parsed, when, total = nota_parsed(nota)
            if when > now:   # a photo dated after the day being seeded does not exist yet
                continue
            receipt = Receipt(
                business_id=business_id, image_url=f"demo/{fname}.jpg", parsed_data=parsed, supplier=sname,
                total_amount=total, occurred_at=when, created_at=when,
            )
            session.add(receipt)
            await session.flush()
            await link_receipt(session, receipt)
            vec = embedding_for(receipt_to_document_text(receipt))
            if vec is None:
                missing_embeddings += 1
            else:
                receipt.embedding = vec
            for n, q, _u, p in nlines:
                item = by_name[n]
                await add_stock(session, item, Decimal(q), reason="purchase", source_type="receipt", source_id=receipt.id,
                                unit_cost=Decimal(p), now=when)
                await sync_default_from_item(session, item)
                purchased[item.id] += Decimal(q)
            await post_event(session, business_id, "GoodsReceived", {"inventory": total.quantize(TWO)}, source_type="receipt",
                             source_id=receipt.id, memo=f"nota {sname}", posted_at=when)
            await record_expense(session, business_id, amount=total, category="bahan baku", description=f"Nota {sname}",
                                 source="receipt", receipt_id=receipt.id, occurred_at=when, ledger_amount=Decimal(0))
            nota_receipts.append(receipt)
        # The purchases moved the figures; the shelf must end where the story says it ends today.
        for item, _w in items:
            item.current_stock = target_stock[item.id]
        await session.flush()

        # Promos (M8-T3): a weekday afternoon BOGO on the Kopi Arabica cup, a Friday
        # 10% off Nasi Goreng, a weekday happy hour on matcha, and one that ended.
        arabica = by_name["Kopi Arabica (cup)"]
        nasgor = by_name["Nasi Goreng Spesial"]
        matcha = by_name["Matcha Latte"]
        promo_bogo = await create_promo(
            session, business_id, name="Beli 1 gratis 1 Kopi Arabica (sore)", kind="bonus_item", item_id=arabica.id,
            max_per_order=2,
            conditions=[
                {"kind": "day_of_week", "days_of_week": [0, 1, 2, 3, 4]},
                {"kind": "time_window", "time_start": dtime(14, 0), "time_end": dtime(17, 0)},
            ],
        )
        promo_fri = await create_promo(
            session, business_id, name="Jumat Nasi Goreng 10%", kind="percent_off", value=Decimal("0.10"), item_id=nasgor.id,
            conditions=[{"kind": "day_of_week", "days_of_week": [4]}],
        )
        promo_matcha = await create_promo(
            session, business_id, name="Happy Hour Matcha 15%", kind="percent_off", value=Decimal("0.15"), item_id=matcha.id,
            conditions=[
                {"kind": "day_of_week", "days_of_week": [0, 1, 2, 3, 4]},
                {"kind": "time_window", "time_start": dtime(15, 0), "time_end": dtime(17, 0)},
            ],
        )
        await create_promo(
            session, business_id, name="Promo Hari Pahlawan (sudah berakhir)", kind="amount_off", value=Decimal(5000),
            item_id=by_name["Croissant"].id, is_active=False,
            conditions=[{"kind": "date_range", "starts_at": now - timedelta(days=20), "ends_at": now - timedelta(days=13)}],
        )

        # Vouchers (M8-T4): one named welcome code and a batch of ten single-use 20% codes.
        welcome = (await create_vouchers(session, business_id, kind="amount_off", value=Decimal(5000), code="SELAMAT-DATANG",
                                         min_spend=Decimal(25000), max_uses=100, expires_at=now + timedelta(days=60)))[0]
        batch = await create_vouchers(session, business_id, kind="percent_off", value=Decimal("0.20"), count=10, prefix="PRNM",
                                      batch_name="Flyer September", max_discount=Decimal(15000), expires_at=now + timedelta(days=30))
        voucher_plan = {day: (welcome if code == "SELAMAT-DATANG" else batch[code[1]]) for day, code in VOUCHER_USES}

        # ── the sales history ────────────────────────────────────────────────────
        sellable = [(i, w) for i, w in items if w > 0]
        sold_per_item: dict = defaultdict(Decimal)       # finished goods without a recipe
        consumed_per_item: dict = defaultdict(Decimal)   # raw materials the recipes used
        day_books: dict = {}                              # local date -> (posted_at, {component: amount}) — journalled per day after the loop
        points_cost_by_day: dict = {}                     # local date -> (posted_at, rupiah value of the points given)
        orders_by_day: dict = defaultdict(list)           # local date -> [(sold_at, order)]
        shift_work: dict = defaultdict(list)              # (staff id, local date) -> [(order, [payments])]
        points_balance: dict = defaultdict(int)           # customer id -> points
        flags = {"andi_redeemed": False, "refund_done": False}
        voids = {"n": 0}
        order_rows: list = []

        def pick_customer(day_offset: int):
            pool = [(c, w) for c, first, last, w in customers if first <= day_offset <= last]
            if not pool or rng.random() >= 0.27:
                return None
            return rng.choices([c for c, _w in pool], weights=[w for _c, w in pool])[0]

        try:   # the same date last month (none on the 31st after a 30-day month)
            month_ago_date = (local_now.date().replace(day=1) - timedelta(days=1)).replace(day=local_now.day)
        except ValueError:
            month_ago_date = None
        today_takings = month_ago_takings = Decimal(0)
        for day_offset in range(HISTORY_DAYS, -1, -1):   # 0 = today, up to the current hour (audit P1-5)
            day = now - timedelta(days=day_offset)
            local_date = day.astimezone(JAK).date()
            weekend = local_date.weekday() >= 5
            base = (26 if weekend else 18) * (0.85 + 0.15 * (HISTORY_DAYS - day_offset) / HISTORY_DAYS)
            if day_offset == 1:
                base = 55  # planted anomaly: yesterday was unusually busy
            n_sales = round(base) + rng.randint(-4, 4)
            if day_offset == 0:
                open_hours = min(max(local_now.hour - 6, 0), 14)   # the café trades 07:00-20:59
                n_sales = round(n_sales * open_hours / 14)
            sold = 0
            # Today also sells at least what the same date last month had sold by this hour, so on the 1st
            # (one part-day against one part-day, a Sunday against a Tuesday) the month still reads as growing.
            while sold < n_sales or (day_offset == 0 and today_takings < month_ago_takings):
                sold += 1
                item, _w = rng.choices(sellable, weights=[w for _, w in sellable])[0]
                qty = Decimal(rng.choices([1, 1, 1, 2], weights=[6, 6, 6, 2])[0])
                # Business hours in the café's own timezone (07:00–20:59 WIB),
                # stored as UTC — not UTC-hour times that read as 3 AM sales.
                last_hour = 20 if day_offset else min(local_now.hour, 20)
                sold_at = day.astimezone(JAK).replace(
                    hour=rng.randint(7, last_hour), minute=rng.randint(0, 59), second=0, microsecond=0
                ).astimezone(timezone.utc)
                if sold_at > now:   # the current hour: never a sale from the future
                    sold_at = now - timedelta(minutes=rng.randint(1, 30))
                local_sold = sold_at.astimezone(JAK)
                staff = rng.choices(staff_pool, weights=staff_weights)[0]
                customer = pick_customer(day_offset)

                # One in four drinks with a Large size sells as Large (M4-T1).
                large = larges.get(item.id)
                variant_name, variant_id, unit_price, unit_cost = "Standar", defaults[item.id], Decimal(item.sell_price), Decimal(item.cost_price)
                if large is not None and rng.random() < 0.25:
                    variant_name, variant_id, unit_price, unit_cost = large.name, large.id, Decimal(large.sell_price), Decimal(large.cost_price)
                if day_offset <= 6 and item.name in DEARER_THIS_WEEK:
                    unit_cost = (unit_cost * Decimal(DEARER_FACTOR)).quantize(TWO)   # the beans and the milk went up, the menu did not

                # What discount applies: a promo that is open at this hour, or a planned voucher.
                promo = bonus_qty = None
                if item is arabica and local_sold.weekday() < 5 and 14 <= local_sold.hour < 17 and rng.random() < 0.6:
                    promo, qty, bonus_qty = promo_bogo, Decimal(1), Decimal(1)
                elif item is nasgor and local_sold.weekday() == 4 and rng.random() < 0.7:
                    promo = promo_fri
                elif item is matcha and local_sold.weekday() < 5 and 15 <= local_sold.hour < 17 and rng.random() < 0.7:
                    promo = promo_matcha
                voucher = None
                v = voucher_plan.get(day_offset)
                if v is not None and promo is None and unit_price * qty >= Decimal(v.min_spend):
                    voucher = voucher_plan.pop(day_offset)
                    customer = customer or rng.choice(customers)[0]

                status = "completed"
                refund_case = (day_offset == 9 and staff is sari and not flags["refund_done"] and voucher is None and promo is None)
                void_case = (staff is budi and 3 <= day_offset <= 28 and voucher is None and promo is None and rng.random() < 0.18)
                if void_case:
                    status, customer = "voided", None
                    voids["n"] += 1
                elif refund_case:
                    status, customer = "refunded", None
                    flags["refund_done"] = True

                line_total = unit_price * qty
                subtotal = line_total + (unit_price * bonus_qty if bonus_qty else 0)
                promo_total = voucher_total = Decimal(0)
                if promo is promo_bogo:
                    promo_total = unit_price * bonus_qty
                elif promo is not None:
                    promo_total = (line_total * Decimal(promo.value)).quantize(TWO)
                if voucher is not None:
                    base_amount = subtotal
                    voucher_total = Decimal(voucher.value) if voucher.kind == "amount_off" else min(
                        (base_amount * Decimal(voucher.value)).quantize(TWO), Decimal(voucher.max_discount or base_amount))
                total = subtotal - promo_total - voucher_total
                if status == "completed":
                    if day_offset == 0:
                        today_takings += total
                    elif local_date == month_ago_date and local_sold.time() < local_now.time():
                        month_ago_takings += total
                order_type = rng.choices(["dine_in", "takeaway", "delivery"], weights=[5, 4, 1])[0]
                method = rng.choices(["cash", "qris"], weights=[7, 3])[0]
                order = Order(
                    id=uuid.uuid4(), business_id=business_id, staff_id=staff.id, order_type=order_type,
                    status=status, subtotal=subtotal, promo_total=promo_total, voucher_total=voucher_total, total=total,
                    sold_at=sold_at, created_at=sold_at, customer_id=customer.id if customer is not None else None,
                    table_label=f"Meja {rng.randint(1, 8)}" if order_type == "dine_in" else None,
                    delivery_address="Jl. Kenanga 12" if order_type == "delivery" else None,
                    guest_phone="0812-5550-0199" if order_type == "delivery" else None,
                )
                session.add(order)
                line = OrderLine(
                    id=uuid.uuid4(), business_id=business_id, order_id=order.id, item_id=item.id, variant_id=variant_id,
                    quantity=qty, unit_price=unit_price, line_total=line_total,
                    unit_cost_at_sale=unit_cost, created_at=sold_at, prep_station=item.prep_station,
                )
                session.add(line)
                lines = [line]
                bonus_line = None
                if bonus_qty:
                    bonus_line = OrderLine(
                        id=uuid.uuid4(), business_id=business_id, order_id=order.id, item_id=item.id, variant_id=variant_id,
                        quantity=bonus_qty, unit_price=unit_price, line_total=unit_price * bonus_qty,
                        unit_cost_at_sale=unit_cost, created_at=sold_at, prep_station=item.prep_station,
                        notes=f"promo: {promo.name}",
                    )
                    session.add(bonus_line)
                    lines.append(bonus_line)

                # Points: one customer spends some on a sale once, everyone else just earns.
                points_paid = Decimal(0)
                if (customer is andi and not flags["andi_redeemed"] and day_offset <= 12 and points_balance[andi.id] >= 60
                        and status == "completed"):
                    points_paid, flags["andi_redeemed"] = Decimal(5000), True
                payments = []
                if points_paid:
                    payments.append(Payment(id=uuid.uuid4(), business_id=business_id, order_id=order.id, method="points",
                                            amount=points_paid, created_at=sold_at))
                payments.append(Payment(id=uuid.uuid4(), business_id=business_id, order_id=order.id, method=method,
                                        amount=total - points_paid, created_at=sold_at))
                for p in payments:
                    session.add(p)
                order_rows.append(order)
                orders_by_day[local_date].append((sold_at, order))
                shift_entry = None
                mirrors: list = []
                if staff.name in ("Sari", "Budi", "Dewi") and 1 <= day_offset <= 14:
                    shift_entry = (order, list(payments))
                    shift_work[(staff.id, local_date)].append(shift_entry)

                if status != "completed":
                    # A reversal is a row of its own (nothing is deleted): the sale, then its mirror a few minutes later,
                    # and the manager who authorised it. The books never saw this sale, so they never see its undoing.
                    kind = "void" if status == "voided" else "refund"
                    approver = dewi if rng.random() < 0.6 else owner
                    undone = sold_at + timedelta(minutes=rng.randint(3, 12))
                    session.add(OrderLine(
                        id=uuid.uuid4(), business_id=business_id, order_id=order.id, item_id=item.id, variant_id=variant_id,
                        quantity=-qty, unit_price=unit_price, line_total=-line_total, unit_cost_at_sale=unit_cost,
                        notes=f"{kind} oleh {approver.name}", created_at=undone,
                    ))
                    for p in payments:
                        mirror = Payment(id=uuid.uuid4(), business_id=business_id, order_id=order.id, method=p.method, amount=-p.amount,
                                         reference=f"{kind}:{p.id}", created_at=undone)
                        session.add(mirror)
                        mirrors.append(mirror)
                    if shift_entry is not None:
                        shift_entry[1].extend(mirrors)
                    await session.flush()
                    session.add(Approval(
                        business_id=business_id, order_id=order.id, action=kind,
                        approved_by=approver.id, approver_role=approver.role, requested_by=staff.id, amount=total,
                        note=("kopi tumpah, diganti baru" if status == "refunded" else rng.choice(VOID_NOTES)), created_at=undone,
                    ))
                    continue

                # Stock: a made-to-order drink takes its recipe's ingredients off the shelf, anything else is itself.
                for ln in lines:
                    recipe = recipe_of.get((item.name, variant_name))
                    if recipe:
                        for comp, per_cup in recipe:
                            used = (per_cup * ln.quantity).quantize(Decimal("0.001"))
                            consumed_per_item[comp.id] += used
                            session.add(StockMovement(business_id=business_id, item_id=comp.id, qty_delta=-used, reason="sale",
                                                      source_type="sale", source_id=ln.id, unit_cost=comp.cost_price,
                                                      staff_id=staff.id, created_at=sold_at))
                    else:
                        sold_per_item[item.id] += ln.quantity
                        session.add(StockMovement(business_id=business_id, item_id=item.id, qty_delta=-ln.quantity, reason="sale",
                                                  source_type="sale", source_id=ln.id, unit_cost=unit_cost,
                                                  staff_id=staff.id, created_at=sold_at))

                close_of_day = min(local_sold.replace(hour=21, minute=0).astimezone(timezone.utc), now)
                _, components = day_books.setdefault(local_date, (close_of_day, {}))
                for p in payments:
                    components[f"payment:{p.method}"] = components.get(f"payment:{p.method}", Decimal(0)) + p.amount
                if promo_total:
                    components["promo"] = components.get("promo", Decimal(0)) + promo_total
                if voucher_total:
                    components["voucher"] = components.get("voucher", Decimal(0)) + voucher_total
                components["cogs"] = components.get("cogs", Decimal(0)) + sum((l.unit_cost_at_sale * l.quantity for l in lines), Decimal(0))

                if promo is not None:
                    session.add(PromoApplication(
                        business_id=business_id, order_id=order.id, promo_id=promo.id,
                        order_line_id=(bonus_line or line).id, amount=promo_total,
                        bonus_quantity=bonus_qty or Decimal(0), created_at=sold_at,
                    ))
                if customer is not None:
                    earned = int((total - points_paid) // 1000)   # 1 poin per Rp 1.000 paid in money
                    if earned > 0:
                        cost = Decimal(earned) * 100
                        session.add(PointsMovement(business_id=business_id, customer_id=customer.id, points_delta=earned,
                                                   reason="earn", source_type="order", source_id=order.id, amount=cost,
                                                   staff_id=staff.id, created_at=sold_at))
                        points_balance[customer.id] += earned
                        when_close, so_far = points_cost_by_day.get(local_date, (close_of_day, Decimal(0)))
                        points_cost_by_day[local_date] = (when_close, so_far + cost)
                    if points_paid:
                        session.add(PointsMovement(business_id=business_id, customer_id=customer.id, points_delta=-int(points_paid // 100),
                                                   reason="redeem", source_type="order", source_id=order.id, amount=points_paid,
                                                   staff_id=staff.id, created_at=sold_at))
                        points_balance[customer.id] -= int(points_paid // 100)
                if voucher is not None:
                    await session.flush()
                    await redeem(session, business_id, voucher, order_id=order.id, amount=voucher_total, at=sold_at,
                                 customer_id=customer.id if customer is not None else None)
            await session.flush()

        # A birthday gift the owner added by hand, and a correction: the ledger says who and why.
        maya = customers[3][0]
        for cust, delta, why in ((maya, 30, "bonus ulang tahun"), (customers[5][0], -10, "koreksi: poin dobel dari nota yang sama")):
            session.add(PointsMovement(business_id=business_id, customer_id=cust.id, points_delta=delta, reason="adjust",
                                       source_type="manual", amount=Decimal(0), staff_id=owner.id, notes=why,
                                       created_at=now - timedelta(days=2)))
            points_balance[cust.id] += delta
        for cust, _f, _l, _w in customers:
            cust.points_balance = points_balance[cust.id]
        await session.flush()

        # Service numbers: "Pesanan 007", counted per day in the order they were rung up.
        for local_date, rows in orders_by_day.items():
            for n, (_sold, o) in enumerate(sorted(rows, key=lambda r: r[0]), start=1):
                o.service_date, o.service_number = local_date, n
            session.add(ServiceNumberCounter(business_id=business_id, service_date=local_date, last_number=len(rows)))
        await session.flush()

        # Stock ledger (M2-T2/M2-T3): the café opened with enough of everything to
        # cover the history, then bought, sold and used it, so SUM(qty_delta) per
        # item equals today's current_stock.
        opened_at = now - timedelta(days=HISTORY_DAYS + 1)
        opening_value = Decimal(0)
        short = []
        for item, _w in items:
            opening = target_stock[item.id] + sold_per_item.get(item.id, Decimal(0)) + consumed_per_item.get(item.id, Decimal(0)) - purchased.get(item.id, Decimal(0))
            if opening < 0:
                short.append(f"{item.name}: open {opening} (target {target_stock[item.id]}, sold {sold_per_item.get(item.id, 0)}, used {consumed_per_item.get(item.id, 0)}, bought {purchased.get(item.id, 0)})")
                continue
            opening_value += opening * Decimal(item.cost_price)
            session.add(StockMovement(business_id=business_id, item_id=item.id, qty_delta=opening, reason="opname",
                                      source_type="seed", unit_cost=item.cost_price, created_at=opened_at))
        if short:
            raise RuntimeError('seed data: bought more than could have been used, lower purchases: ' + '; '.join(short))
        await session.flush()
        # The purchases were ledgered while they were written; give them the date they happened on.
        await session.execute(text(
            "update stock_movements m set created_at = r.occurred_at from receipts r "
            "where m.source_type = 'receipt' and m.source_id = r.id"
        ))
        await session.execute(text(
            "update stock_movements m set created_at = g.received_at from goods_receipt_lines l "
            "join goods_receipts g on g.id = l.receipt_id where m.source_type = 'goods_receipt' and m.source_id = l.id"
        ))

        # Shifts (M7-T1): each cashier's day, counted at close. Sari was 5.000 short yesterday,
        # Budi 20.000 short two days ago, Sari 2.000 over a week ago; money also left and entered the drawer.
        owner_gives = {(sari.id, 8): ("cash_in", Decimal(100000), "Tambah uang receh dari pemilik", None, None)}
        drawer_events = {
            (sari.id, 5): ("supplier_payment", Decimal(150000), "Bayar sisa nota Toko Supply", None, suppliers["Toko Supply"].id),
            (dewi.id, 3): ("petty_cash", Decimal(65000), "Es batu dan plastik kemasan", "operasional", None),
            (dewi.id, 10): ("bank_drop", Decimal(100000), "Setor kas ke bank", None, None),
            **owner_gives,
        }
        variances = {(sari.id, 1): (Decimal(-5000), "kurang 5rb, mungkin kembalian"),
                     (budi.id, 2): (Decimal(-20000), "kurang 20rb, kembalian salah, dicek ulang besok"),
                     (sari.id, 8): (Decimal(2000), "lebih 2rb, ada tips")}
        today_local = local_now.date()
        for (staff_id, local_date), work in sorted(shift_work.items(), key=lambda kv: kv[0][1]):
            ago = (today_local - local_date).days
            opened = datetime.combine(local_date, dtime(7, 0), tzinfo=JAK).astimezone(timezone.utc)
            closed = datetime.combine(local_date, dtime(21, 5), tzinfo=JAK).astimezone(timezone.utc)
            shift = await open_shift(session, business_id, staff_id=staff_id, opening_float=Decimal(200000), opened_at=opened)
            for o, ps in work:
                o.shift_id = shift.id
                for p in ps:
                    p.shift_id = shift.id
            await session.flush()
            ev = drawer_events.get((staff_id, ago))
            if ev is not None:
                kind, amount, why, category, supplier_id = ev
                await record_cash_movement(session, business_id, kind=kind, amount=amount, reason=why, staff_id=staff_id,
                                           category=category, supplier_id=supplier_id, occurred_at=opened + timedelta(hours=5))
            from app.services.shifts import cash_summary

            expected = (await cash_summary(session, shift)).expected_cash
            variance, note = variances.get((staff_id, ago), (Decimal(0), None))
            await close_shift(session, shift, counted_cash=max(expected + variance, Decimal(0)), closed_by=owner.id, closed_at=closed, notes=note)
            shift.created_at = opened

        # Books (M6-T4/M6-T5): the opening stock is capitalised as owner's
        # capital and the history's sales post through the engine, one summary
        # entry per day, so the statements show a real two months.
        from app.services.ledger import LineSpec, post_entry

        await post_entry(
            session, business_id, lines=[LineSpec("1300", debit=opening_value.quantize(TWO)), LineSpec("3100", credit=opening_value.quantize(TWO))],
            memo="Modal awal: persediaan pembukaan", source_type="seed", event_type="OpeningBalance", posted_at=opened_at,
        )
        for _day, (posted_at, components) in sorted(day_books.items()):
            await post_event(session, business_id, "OrderCompleted", components, source_type="seed",
                             memo=f"penjualan {_day.isoformat()}", posted_at=posted_at)
        for _day, (posted_at, cost) in sorted(points_cost_by_day.items()):
            await post_event(session, business_id, "PointsEarned", {"points": cost}, source_type="seed",
                             memo=f"poin pelanggan {_day.isoformat()}", posted_at=posted_at)

        # Expenses go through the one writer (M6-T6), so they are on the P&L.
        # Dated by the calendar, only once the day has come (a bill is not on the books before
        # its day), so "this month vs the same days of last month" compares like with like on
        # any day the demo is seeded.
        local_today = now.astimezone(JAK).date()
        for category, description, amount, day in EXPENSES:
            if day <= local_today.day:
                await record_expense(
                    session, business_id, amount=Decimal(amount), category=category,
                    description=description, source="manual",
                    occurred_at=at_local(now, local_today.day - day, 0),   # 00:00, inside the month from its first minute
                )

        # Last month's costs, by the same calendar.
        last_month_first = (local_today.replace(day=1) - timedelta(days=1)).replace(day=1)
        for category, description, amount, day in PRIOR_MONTH_EXPENSES:
            days_ago = (local_today - last_month_first.replace(day=day)).days
            await record_expense(   # every one, even past the sales history: the twins hold on the 31st too
                session, business_id, amount=Decimal(amount), category=category,
                description=description, source="manual", occurred_at=at_local(now, days_ago, 0),
            )

        # Kitchen: everything from the last day is handed over except the three newest tickets.
        recent = sorted((o for o in order_rows if o.status == "completed" and o.sold_at >= now - timedelta(hours=24)),
                        key=lambda o: o.sold_at)
        for o in recent[:-3]:
            session.add(KitchenEvent(business_id=business_id, order_id=o.id, state="done", staff_id=o.staff_id,
                                     created_at=o.sold_at + timedelta(minutes=8)))
        for o, state in zip(recent[-3:], ("ready", "preparing")):
            session.add(KitchenEvent(business_id=business_id, order_id=o.id, state=state, staff_id=o.staff_id,
                                     created_at=min(o.sold_at + timedelta(minutes=2), now)))

        # Two guests ordered from the table QR and wait for the till (a ticket nobody has paid yet).
        for guest, table, picks in (("Bu Ratna", "Meja 4", [("Es Kopi Susu", 2), ("Croissant", 1)]),
                                    ("Mas Eko", "Meja 7", [("Nasi Goreng Spesial", 1), ("Teh Tarik", 1)])):
            await place_ticket(
                session, business_id=business_id, table_label=table, guest_name=guest, order_type="dine_in",
                lines=[OrderLineSpec(item_id=by_name[n].id, quantity=Decimal(q), variant_id=defaults[by_name[n].id], modifier_ids=await default_modifiers(session, by_name[n].id))
                       for n, q in picks],
                placed_at=now - timedelta(minutes=4 if guest == "Bu Ratna" else 1),
            )


    # The nightly pass (audit P0-5): baselines, the stock sweep and the alert rules,
    # so Peringatan, the Ringkasan card and the anomaly exist right after seeding.
    # Rules only, never delivery: nothing is sent to WhatsApp from a seed, and
    # nothing here calls Gemini.
    from app.jobs.nightly import nightly_pass

    async with tenant_session(business_id) as session:
        business = await session.get(Business, business_id)
        # As last night's run saw it (23:30 WIB yesterday): the planted spike is then
        # "that day", which is what the cron would have flagged by the morning.
        last_night = (local_now - timedelta(days=1)).replace(hour=23, minute=30, second=0, microsecond=0)
        written = await nightly_pass(session, business, last_night.astimezone(timezone.utc))
        fired = sorted({a.type for a in written})
        # Earlier nights' alerts: the owner already read and acknowledged them (and the nightly job
        # already sent them, so tonight's run does not send them again).
        for kind, severity, ago, metric, message, _tag in OLD_ALERTS:
            when = at_local(now, ago, 23, 30)
            session.add(Alert(business_id=business_id, type=kind, severity=severity, metric=metric, message=message,
                              is_sent=True, is_acknowledged=True, created_at=when,
                              rule_key=f"{kind}:{_tag}:{when.astimezone(JAK).date().isoformat()}"))
        # One of tonight's alerts she has read already; the rest wait on the page.
        read_first = next((a for a in written if a.type == "anomaly"), None)
        if read_first is not None:
            read_first.is_acknowledged = True
    print(f"Nightly pass: {len(written)} alert(s) ready: {fired}")
    print(f"Voids by Budi: {voids['n']} | embeddings missing: {missing_embeddings} of {len(nota_receipts)} receipts")
    if stage_receipt:   # after the seed, as the owner's photo came after it
        for warning in await stage_demo_receipt(business_id, now):
            print(f"WARNING: {warning}")

    print(f"Seeded '{business_name}' (business_id={business_id})")
    print(f"Owner phone {phone} | owner PIN 1234 | Sari 2345 | Budi 3456 | Dewi (manager) 4567")
    return fired


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed (or refresh) a demo cafe with two months of invented sales.")
    parser.add_argument("--yes", action="store_true", help="allow a database that is not on this machine")
    parser.add_argument("--phone", default=OWNER_PHONE, help="the demo owner's WhatsApp number")
    parser.add_argument("--name", default="Poernama", help="the demo business's name")
    parser.add_argument("--owner", default="Ibu Diah", help="the demo owner's name")
    args = parser.parse_args()
    try:
        asyncio.run(seed(confirm=args.yes, phone=args.phone, business_name=args.name, owner_name=args.owner,
                         stage_receipt=True))
    except ProductionRefusal as exc:
        raise SystemExit(f"Refused: {exc}")
