"""Bulk catalogue import from one Excel workbook (roadmap M4-T6).

Sheets (names matched loosely, Indonesian or English, any casing):

  Barang    items      — the existing stock template (nama, jumlah, satuan, harga modal, harga jual, batas)
  Varian    variants   — barang, varian, harga jual, harga modal, sku, utama (ya/tidak)
  Pilihan   modifiers  — barang, kelompok, jenis (single/multi), wajib (ya/tidak), pilihan, tambahan harga, default
  Satuan    uoms       — kode, nama
  Konversi  conversions— dari, ke, faktor
  Resep     recipes    — barang, varian (blank = Standar), bahan, jumlah, satuan

A workbook whose only sheet is the old single-sheet stock template still works.

Three phases, deliberately separate:
  parse     pure: bytes → rows per sheet, structural errors (unreadable file, missing columns)
  validate  rows + what already exists (items, variants, units) → every row-level error
            at once, with sheet and row number, in Indonesian
  apply     writes everything in the caller's transaction. It is only ever called
            with a clean validation, and any failure inside it propagates so the
            caller rolls back: ALL OR NOTHING.
"""
from __future__ import annotations

import io
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Business, Item, ItemVariant, Modifier, ModifierGroup, Uom
from app.services import catalog, units
from app.services.stock_import import HEADER_SYNONYMS, StockTemplateError, _dec, _map_headers, apply_stock_template

SHEETS = {
    "items": {"barang", "stok", "items", "item", "stock", "produk"},
    "variants": {"varian", "variants", "variant", "ukuran"},
    "modifiers": {"pilihan", "modifier", "modifiers", "tambahan"},
    "uoms": {"satuan", "uom", "uoms", "units", "unit"},
    "conversions": {"konversi", "conversions", "conversion"},
    "recipes": {"resep", "recipes", "recipe", "bahan"},
}

COLUMNS = {
    "variants": {
        "item": {"barang", "item", "nama barang", "produk", "product"},
        "name": {"varian", "nama varian", "variant", "ukuran", "size"},
        "sell_price": HEADER_SYNONYMS["sell_price"],
        "cost_price": HEADER_SYNONYMS["cost_price"],
        "sku": {"sku", "kode"},
        "is_default": {"utama", "default", "standar"},
    },
    "modifiers": {
        "item": {"barang", "item", "nama barang", "produk", "product"},
        "group": {"kelompok", "grup", "group", "pertanyaan"},
        "selection": {"jenis", "tipe", "selection", "type"},
        "is_required": {"wajib", "required"},
        "name": {"pilihan", "nama pilihan", "modifier", "option", "choice"},
        "price_delta": {"tambahan harga", "harga tambahan", "tambahan", "price", "harga", "delta"},
        "is_default": {"default", "bawaan", "utama"},
    },
    "uoms": {"code": {"kode", "code", "satuan", "unit", "uom"}, "name": {"nama", "name", "keterangan"}},
    "conversions": {
        "from": {"dari", "from", "dari satuan"},
        "to": {"ke", "to", "ke satuan"},
        "factor": {"faktor", "factor", "nilai", "isi"},
    },
    "recipes": {
        "item": {"barang", "item", "nama barang", "produk", "product"},
        "variant": {"varian", "variant", "ukuran"},
        "component": {"bahan", "komponen", "component", "ingredient", "bahan baku"},
        "quantity": {"jumlah", "qty", "quantity", "takaran", "banyak"},
        "uom": {"satuan", "unit", "uom"},
    },
}

YES = {"ya", "y", "yes", "true", "1", "1.0", "x", "✓", "utama", "default"}
SINGLE = {"single", "satu", "tunggal", "one", "1", "radio"}
MULTI = {"multi", "banyak", "multiple", "many", "checkbox", "lebih dari satu"}


class CatalogImportInvalid(Exception):
    """Validation failed: `errors` names every bad row. Nothing was written."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


@dataclass
class Workbook:
    items: list[dict] = field(default_factory=list)
    variants: list[dict] = field(default_factory=list)
    modifiers: list[dict] = field(default_factory=list)
    uoms: list[dict] = field(default_factory=list)
    conversions: list[dict] = field(default_factory=list)
    recipes: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sheet_names: dict[str, str] = field(default_factory=dict)  # kind -> actual sheet name

    def row_count(self) -> int:
        return sum(len(getattr(self, k)) for k in SHEETS)


def _norm(v) -> str:
    return "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v).strip()


def _lower(v) -> str:
    return _norm(v).lower()


def _yes(v) -> bool:
    return _lower(v) in YES


def _num(v) -> Decimal | None:
    """Decimal or None when blank/unparseable (callers decide if that is an error)."""
    s = _norm(v).replace(",", ".")
    if not s:
        return None
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


def _sheet_kind(name: str) -> str | None:
    n = _lower(name)
    for kind, names in SHEETS.items():
        if n in names:
            return kind
    return None


def _map(columns, spec: dict[str, set[str]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for col in columns:
        n = _lower(col)
        for field_name, names in spec.items():
            if n in names and field_name not in mapping:
                mapping[field_name] = col
    return mapping


def _cell(record, mapping, field_name):
    return record[mapping[field_name]] if field_name in mapping else None


# ── parse ───────────────────────────────────────────────────────────────────

def parse_catalog_workbook(data: bytes) -> Workbook:
    try:
        book = pd.read_excel(io.BytesIO(data), sheet_name=None)
    except Exception as exc:
        raise StockTemplateError(f"unreadable_file: {exc}") from exc

    wb = Workbook()
    kinds = {name: _sheet_kind(name) for name in book}
    if not any(kinds.values()):
        if len(book) == 1:  # the old single-sheet stock template
            kinds = {next(iter(book)): "items"}
        else:
            raise StockTemplateError("missing_sheets: no recognizable sheet (Barang, Varian, Pilihan, Satuan, Konversi, Resep)")

    for name, kind in kinds.items():
        if kind is None:
            wb.warnings.append(f"Sheet '{name}' diabaikan (nama tidak dikenali)")
            continue
        wb.sheet_names[kind] = name
        df = book[name]
        if kind == "items":
            mapping = _map_headers(list(df.columns))
            if "name" not in mapping or "quantity" not in mapping:
                raise StockTemplateError(f"missing_columns: sheet '{name}' needs a name and quantity column")
            for idx, r in df.iterrows():
                item_name = _norm(r[mapping["name"]])
                if not item_name or item_name.lower() == "nan":
                    continue
                wb.items.append({
                    "row": idx + 2, "name": item_name,
                    "quantity": _num(r[mapping["quantity"]]),
                    "unit": _norm(_cell(r, mapping, "unit")) or "pcs",
                    "cost_price": _num(_cell(r, mapping, "cost_price")) if "cost_price" in mapping else Decimal(0),
                    "sell_price": _num(_cell(r, mapping, "sell_price")) if "sell_price" in mapping else Decimal(0),
                    "reorder_threshold": _num(_cell(r, mapping, "reorder_threshold")) if "reorder_threshold" in mapping else Decimal(0),
                })
            continue
        spec = COLUMNS[kind]
        mapping = _map(list(df.columns), spec)
        required = {
            "variants": ("item", "name"), "modifiers": ("item", "group", "name"), "uoms": ("code",),
            "conversions": ("from", "to", "factor"), "recipes": ("item", "component", "quantity"),
        }[kind]
        missing = [c for c in required if c not in mapping]
        if missing:
            raise StockTemplateError(f"missing_columns: sheet '{name}' needs {', '.join(missing)}")
        for idx, r in df.iterrows():
            row = {"row": idx + 2}
            for field_name in spec:
                row[field_name] = _cell(r, mapping, field_name)
            if all(_norm(v) == "" for k, v in row.items() if k != "row"):
                continue
            getattr(wb, kind).append(row)
    return wb


# ── validate ────────────────────────────────────────────────────────────────

@dataclass
class Existing:
    items: dict[str, Item]                       # lower name -> Item
    variants: set[tuple[str, str]]               # (lower item, lower variant)
    uoms: dict[str, Uom]                         # lower code -> Uom
    groups: set[tuple[str, str]]                 # (lower item, lower group)


async def load_existing(session: AsyncSession) -> Existing:
    items = {i.name.lower(): i for i in (await session.execute(select(Item))).scalars()}
    by_id = {i.id: i.name.lower() for i in items.values()}
    variants = {
        (by_id[v.item_id], v.name.lower())
        for v in (await session.execute(select(ItemVariant))).scalars() if v.item_id in by_id
    }
    uoms = {u.code.lower(): u for u in (await session.execute(select(Uom))).scalars()}
    groups = {
        (by_id[g.item_id], g.name.lower())
        for g in (await session.execute(select(ModifierGroup))).scalars() if g.item_id in by_id
    }
    return Existing(items=items, variants=variants, uoms=uoms, groups=groups)


def validate_catalog_workbook(wb: Workbook, existing: Existing) -> list[str]:
    errors: list[str] = []
    S = wb.sheet_names

    def err(kind: str, row: int, message: str) -> None:
        errors.append(f"Sheet '{S.get(kind, kind)}' baris {row}: {message}")

    sheet_items: dict[str, dict] = {}
    for r in wb.items:
        key = r["name"].lower()
        if key in sheet_items:
            err("items", r["row"], f"barang '{r['name']}' ditulis dua kali")
            continue
        sheet_items[key] = r
        if r["quantity"] is None or r["quantity"] < 0:
            err("items", r["row"], f"jumlah stok '{r['name']}' harus angka nol atau lebih")
        for f, label in (("cost_price", "harga modal"), ("sell_price", "harga jual"), ("reorder_threshold", "batas minimum")):
            if r[f] is None or r[f] < 0:
                err("items", r["row"], f"{label} '{r['name']}' harus angka nol atau lebih")

    def known_item(name: str) -> bool:
        return name.lower() in sheet_items or name.lower() in existing.items

    sheet_variants: set[tuple[str, str]] = set()
    for r in wb.variants:
        item, name = _norm(r["item"]), _norm(r["name"])
        if not item or not known_item(item):
            err("variants", r["row"], f"barang '{item or '?'}' tidak dikenal")
            continue
        if not name:
            err("variants", r["row"], f"nama varian untuk '{item}' kosong")
            continue
        key = (item.lower(), name.lower())
        if key in sheet_variants:
            err("variants", r["row"], f"varian '{name}' untuk '{item}' ditulis dua kali")
        sheet_variants.add(key)
        for f, label in (("sell_price", "harga jual"), ("cost_price", "harga modal")):
            v = _num(r[f])
            if _norm(r[f]) and (v is None or v < 0):
                err("variants", r["row"], f"{label} varian '{name}' harus angka nol atau lebih")

    def known_variant(item: str, variant: str) -> bool:
        key = (item.lower(), variant.lower())
        return key in sheet_variants or key in existing.variants or variant.lower() == catalog.DEFAULT_VARIANT_NAME.lower()

    seen_mod: set[tuple[str, str, str]] = set()
    for r in wb.modifiers:
        item, group, name = _norm(r["item"]), _norm(r["group"]), _norm(r["name"])
        if not item or not known_item(item):
            err("modifiers", r["row"], f"barang '{item or '?'}' tidak dikenal")
            continue
        if not group or not name:
            err("modifiers", r["row"], f"kelompok atau nama pilihan untuk '{item}' kosong")
            continue
        sel = _lower(r["selection"])
        if sel and sel not in SINGLE | MULTI:
            err("modifiers", r["row"], f"jenis '{_norm(r['selection'])}' tidak dikenal (pakai single atau multi)")
        delta = _num(r["price_delta"])
        if _norm(r["price_delta"]) and (delta is None or delta < 0):
            err("modifiers", r["row"], f"tambahan harga '{name}' harus angka nol atau lebih")
        key = (item.lower(), group.lower(), name.lower())
        if key in seen_mod:
            err("modifiers", r["row"], f"pilihan '{name}' di kelompok '{group}' ditulis dua kali")
        seen_mod.add(key)

    sheet_uoms: set[str] = set()
    for r in wb.uoms:
        code = _norm(r["code"])
        if not code:
            err("uoms", r["row"], "kode satuan kosong")
            continue
        if code.lower() in sheet_uoms:
            err("uoms", r["row"], f"satuan '{code}' ditulis dua kali")
        sheet_uoms.add(code.lower())

    def known_uom(code: str) -> bool:
        c = units._UNIT_ALIASES.get(code.lower(), code.lower())
        return c in sheet_uoms or c in existing.uoms or c in {u for u, _ in units.STANDARD_UOMS}

    for r in wb.conversions:
        f, t = _norm(r["from"]), _norm(r["to"])
        factor = _num(r["factor"])
        for code in (f, t):
            if not code or not known_uom(code):
                err("conversions", r["row"], f"satuan '{code or '?'}' tidak dikenal")
        if f and t and f.lower() == t.lower():
            err("conversions", r["row"], f"satuan asal dan tujuan sama ('{f}')")
        if factor is None or factor <= 0:
            err("conversions", r["row"], f"faktor konversi {f or '?'}→{t or '?'} harus lebih dari nol")

    seen_recipe: set[tuple[str, str, str]] = set()
    for r in wb.recipes:
        item, comp = _norm(r["item"]), _norm(r["component"])
        variant = _norm(r["variant"]) or catalog.DEFAULT_VARIANT_NAME
        qty, uom = _num(r["quantity"]), _norm(r["uom"])
        if not item or not known_item(item):
            err("recipes", r["row"], f"barang '{item or '?'}' tidak dikenal")
            continue
        if not known_variant(item, variant):
            err("recipes", r["row"], f"varian '{variant}' untuk '{item}' tidak dikenal")
        if not comp or not known_item(comp):
            err("recipes", r["row"], f"bahan '{comp or '?'}' tidak dikenal")
        elif comp.lower() == item.lower():
            err("recipes", r["row"], f"bahan '{comp}' tidak boleh barang itu sendiri")
        if qty is None or qty <= 0:
            err("recipes", r["row"], f"jumlah bahan '{comp or '?'}' harus lebih dari nol")
        if uom and not known_uom(uom):
            err("recipes", r["row"], f"satuan '{uom}' tidak dikenal")
        key = (item.lower(), variant.lower(), comp.lower())
        if key in seen_recipe:
            err("recipes", r["row"], f"bahan '{comp}' untuk '{item} / {variant}' ditulis dua kali")
        seen_recipe.add(key)

    return errors


# ── apply ───────────────────────────────────────────────────────────────────

async def apply_catalog_workbook(session: AsyncSession, business: Business, wb: Workbook) -> dict:
    """Write everything, in dependency order, in the caller's transaction."""
    counts = {"uoms": 0, "conversions": 0, "items_created": 0, "items_updated": 0,
              "variants": 0, "modifier_groups": 0, "modifiers": 0, "recipe_lines": 0}

    # 1. Units, then conversions.
    for r in wb.uoms:
        code = _norm(r["code"])
        if await units.uom_by_code(session, code) is None:
            await units.create_uom(session, business.id, code=code, name=_norm(r["name"]) or code)
            counts["uoms"] += 1
    for r in wb.conversions:
        f = await units.uom_by_code(session, _norm(r["from"]))
        t = await units.uom_by_code(session, _norm(r["to"]))
        try:
            await units.create_conversion(session, business.id, from_uom=f, to_uom=t, factor=_num(r["factor"]))
            counts["conversions"] += 1
        except units.UomInvalid as exc:
            if exc.code != "duplicate":
                raise
            wb.warnings.append(f"Konversi {f.code}→{t.code} sudah ada, dilewati")

    # 2. Items (the existing stock-template semantics: absolute counts, upsert by name).
    if wb.items:
        rows = [
            {"name": r["name"], "quantity": r["quantity"], "unit": r["unit"], "cost_price": r["cost_price"],
             "sell_price": r["sell_price"], "reorder_threshold": r["reorder_threshold"]}
            for r in wb.items
        ]
        result = await apply_stock_template(session, business, rows)
        counts["items_created"] = len(result.get("items_created", []))
        counts["items_updated"] = len(result.get("items_updated", []))
        # Link units to the items just written when the unit text is a known code.
        for r in wb.items:
            item = await _item_by_name(session, r["name"])
            if item is not None and item.uom_id is None:
                u = await units.uom_by_code(session, r["unit"])
                if u is not None:
                    item.uom_id = u.id
        await session.flush()

    # 3. Variants (create, or update prices of an existing one).
    for r in wb.variants:
        item = await _item_by_name(session, _norm(r["item"]))
        name = _norm(r["name"])
        existing = await _variant_by_name(session, item.id, name)
        sell, cost = _num(r["sell_price"]), _num(r["cost_price"])
        changes = {k: v for k, v in (("sell_price", sell), ("cost_price", cost)) if v is not None}
        if _norm(r.get("sku")):
            changes["sku"] = _norm(r["sku"])
        if _yes(r.get("is_default")):
            changes["is_default"] = True
        if existing is None:
            await catalog.create_variant(
                session, item, name=name, sell_price=sell if sell is not None else item.sell_price,
                cost_price=cost if cost is not None else Decimal(0), sku=changes.get("sku"),
                is_default=changes.get("is_default", False),
            )
        else:
            await catalog.update_variant(session, existing, item, **changes)
        counts["variants"] += 1

    # 4. Modifier groups and their choices.
    for r in wb.modifiers:
        item = await _item_by_name(session, _norm(r["item"]))
        gname, mname = _norm(r["group"]), _norm(r["name"])
        group = await _group_by_name(session, item.id, gname)
        if group is None:
            sel = "multi" if _lower(r["selection"]) in MULTI else "single"
            group = await catalog.create_modifier_group(
                session, item, name=gname, selection=sel, is_required=_yes(r["is_required"]),
            )
            counts["modifier_groups"] += 1
        mod = (
            await session.execute(
                select(Modifier).where(Modifier.group_id == group.id, func.lower(Modifier.name) == mname.lower())
            )
        ).scalar_one_or_none()
        delta = _num(r["price_delta"]) or Decimal(0)
        if mod is None:
            await catalog.create_modifier(session, group, name=mname, price_delta=delta, is_default=_yes(r["is_default"]))
        else:
            await catalog.update_modifier(session, mod, price_delta=delta, is_default=_yes(r["is_default"]) or None, is_active=True)
        counts["modifiers"] += 1

    # 5. Recipes, keyed on the variant.
    for r in wb.recipes:
        item = await _item_by_name(session, _norm(r["item"]))
        variant = await _variant_by_name(session, item.id, _norm(r["variant"]) or catalog.DEFAULT_VARIANT_NAME)
        component = await _item_by_name(session, _norm(r["component"]))
        uom_code = _norm(r["uom"])
        uom = await units.uom_by_code(session, uom_code) if uom_code else None
        await catalog.set_recipe_line(session, variant, component, quantity=_num(r["quantity"]), uom_id=uom.id if uom else None)
        counts["recipe_lines"] += 1

    await session.flush()
    return {"saved": True, **counts, "rows": wb.row_count(), "warnings": wb.warnings}


async def _item_by_name(session: AsyncSession, name: str) -> Item | None:
    return (await session.execute(select(Item).where(func.lower(Item.name) == name.lower()).limit(1))).scalar_one_or_none()


async def _variant_by_name(session: AsyncSession, item_id: uuid.UUID, name: str) -> ItemVariant | None:
    return (
        await session.execute(
            select(ItemVariant).where(ItemVariant.item_id == item_id, func.lower(ItemVariant.name) == name.lower())
        )
    ).scalar_one_or_none()


async def _group_by_name(session: AsyncSession, item_id: uuid.UUID, name: str) -> ModifierGroup | None:
    return (
        await session.execute(
            select(ModifierGroup).where(ModifierGroup.item_id == item_id, func.lower(ModifierGroup.name) == name.lower())
        )
    ).scalar_one_or_none()


async def import_catalog(session: AsyncSession, business: Business, data: bytes) -> dict:
    """parse → validate → apply. Raises StockTemplateError (file/structure) or
    CatalogImportInvalid (row errors) BEFORE anything is written."""
    wb = parse_catalog_workbook(data)
    errors = validate_catalog_workbook(wb, await load_existing(session))
    if errors:
        raise CatalogImportInvalid(errors)
    return await apply_catalog_workbook(session, business, wb)
