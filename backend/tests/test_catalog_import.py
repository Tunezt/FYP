"""M4-T6 — bulk catalogue import: validate before writing, report row-level
errors, all or nothing.

Done-when (roadmap): a 200-row template with deliberate errors in 5 rows imports
nothing and names all 5.

Needs the local Postgres (roadmap §2). No skip marker: unreachable DB fails.
"""
import io
import os
import uuid
from decimal import Decimal

import pandas as pd
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Business, Item, ItemVariant, Modifier, ModifierGroup, RecipeLine, StockMovement, Uom, UomConversion
from app.services.catalog_import import (
    CatalogImportInvalid,
    import_catalog,
    load_existing,
    parse_catalog_workbook,
    validate_catalog_workbook,
)
from app.services.stock_import import StockTemplateError
from app.services.units import ensure_standard_uoms

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")


def _workbook(sheets: dict[str, pd.DataFrame]) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for name, df in sheets.items():
            df.to_excel(xw, index=False, sheet_name=name)
    return buf.getvalue()


def _two_hundred_rows(broken: bool) -> tuple[bytes, list[tuple[str, int]]]:
    """120 items + 30 variants + 20 modifiers + 5 units + 5 conversions + 20 recipe lines = 200.
    With `broken`, five rows (one per kind of mistake) are wrong; returns their (sheet, row)."""
    items = pd.DataFrame({
        "Nama Barang": [f"Produk {i:03d}" for i in range(1, 119)] + ["Bahan A", "Bahan B"],
        "Jumlah": [10] * 118 + [5, 8],
        "Satuan": ["pcs"] * 118 + ["kg", "liter"],
        "Harga Modal": [5000] * 118 + [100000, 17000],
        "Harga Jual": [12000] * 118 + [0, 0],
        "Batas Minimum": [2] * 120,
    })
    variants = pd.DataFrame({
        "Barang": [f"Produk {i:03d}" for i in range(1, 31)],
        "Varian": ["Large"] * 30,
        "Harga Jual": [15000] * 30,
        "Harga Modal": [6000] * 30,
    })
    modifiers = pd.DataFrame({
        "Barang": [f"Produk {i:03d}" for i in range(1, 11) for _ in range(2)],
        "Kelompok": ["Gula"] * 20,
        "Jenis": ["single"] * 20,
        "Wajib": ["ya"] * 20,
        "Pilihan": ["Normal", "Sedikit"] * 10,
        "Tambahan Harga": [0] * 20,
        "Default": ["ya", ""] * 10,
    })
    uoms = pd.DataFrame({"Kode": [f"u{i}" for i in range(1, 6)], "Nama": [f"unit {i}" for i in range(1, 6)]})
    conversions = pd.DataFrame({"Dari": [f"u{i}" for i in range(1, 6)], "Ke": ["g"] * 5, "Faktor": [10, 20, 30, 40, 50]})
    recipes = pd.DataFrame({
        "Barang": [f"Produk {i:03d}" for i in range(1, 21)],
        "Varian": [""] * 20,
        "Bahan": ["Bahan A"] * 20,
        "Jumlah": [10] * 20,
        "Satuan": ["g"] * 20,
    })
    bad: list[tuple[str, int]] = []
    if broken:
        items.loc[49, "Jumlah"] = -3                        # Barang row 51: negative stock
        variants.loc[4, "Barang"] = "Tidak Ada"             # Varian row 6: unknown item
        modifiers.loc[7, "Jenis"] = "triple"                # Pilihan row 9: unknown selection kind
        conversions.loc[2, "Faktor"] = 0                    # Konversi row 4: zero factor
        recipes.loc[12, "Bahan"] = "Susu Kambing"           # Resep row 14: unknown component
        bad = [("Barang", 51), ("Varian", 6), ("Pilihan", 9), ("Konversi", 4), ("Resep", 14)]
    data = _workbook({"Barang": items, "Varian": variants, "Pilihan": modifiers, "Satuan": uoms,
                      "Konversi": conversions, "Resep": recipes})
    return data, bad


def test_parse_counts_two_hundred_rows():
    data, _ = _two_hundred_rows(broken=False)
    wb = parse_catalog_workbook(data)
    assert wb.row_count() == 200
    assert (len(wb.items), len(wb.variants), len(wb.modifiers), len(wb.uoms), len(wb.conversions), len(wb.recipes)) == (120, 30, 20, 5, 5, 20)


def test_single_sheet_legacy_template_still_parses():
    data = _workbook({"Stok": pd.DataFrame({"Nama Barang": ["Gula"], "Jumlah": [5], "Satuan": ["kg"]})})
    wb = parse_catalog_workbook(data)
    assert [r["name"] for r in wb.items] == ["Gula"] and wb.row_count() == 1


def test_unknown_sheets_only_is_a_structural_error():
    data = _workbook({"Apa": pd.DataFrame({"x": [1]}), "Ini": pd.DataFrame({"y": [2]})})
    with pytest.raises(StockTemplateError):
        parse_catalog_workbook(data)


@pytest.fixture
async def engine():
    if not DB_URL:
        pytest.fail("INTEGRATION_DATABASE_URL unset — local Postgres is not configured (roadmap §2)")
    engine = create_async_engine(DB_URL, connect_args={"statement_cache_size": 0})
    yield engine
    await engine.dispose()


@pytest.fixture
def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _set_tenant(session, business_id):
    await session.execute(
        text("select set_config('app.current_business_id', :bid, true)"), {"bid": str(business_id)}
    )


@pytest.fixture
async def empty_shop(session_factory):
    async with session_factory() as s:
        biz = Business(name="Import Test", owner_phone=f"62988{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await ensure_standard_uoms(s, bid)
        await s.commit()
    yield bid
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


async def _counts(s) -> tuple[int, ...]:
    out = []
    for model in (Item, ItemVariant, ModifierGroup, Modifier, Uom, UomConversion, RecipeLine, StockMovement):
        out.append((await s.execute(select(func.count(model.id)))).scalar_one())
    return tuple(out)


async def test_five_bad_rows_import_nothing_and_are_all_named(session_factory, empty_shop):
    bid = empty_shop
    data, bad = _two_hundred_rows(broken=True)
    async with session_factory() as s:
        await _set_tenant(s, bid)
        before = await _counts(s)
        assert before[:4] == (0, 0, 0, 0)  # nothing but the 16 standard units and 4 conversions
        biz = await s.get(Business, bid)
        with pytest.raises(CatalogImportInvalid) as exc:
            await import_catalog(s, biz, data)
        await s.rollback()
    errors = exc.value.errors
    assert len(errors) == 5
    for sheet, row in bad:
        assert any(f"Sheet '{sheet}' baris {row}:" in e for e in errors), (sheet, row, errors)
    assert any("Produk 050" in e and "nol atau lebih" in e for e in errors)
    assert any("Tidak Ada" in e and "tidak dikenal" in e for e in errors)
    assert any("triple" in e for e in errors)
    assert any("faktor" in e.lower() and "u3" in e for e in errors)
    assert any("Susu Kambing" in e for e in errors)
    async with session_factory() as s:
        await _set_tenant(s, bid)
        assert await _counts(s) == before  # nothing written, not even the 195 good rows


async def test_clean_two_hundred_rows_import_everything(session_factory, empty_shop):
    bid = empty_shop
    data, _ = _two_hundred_rows(broken=False)
    async with session_factory() as s:
        await _set_tenant(s, bid)
        biz = await s.get(Business, bid)
        result = await import_catalog(s, biz, data)
        await s.commit()
    assert result["saved"] and result["rows"] == 200
    assert (result["items_created"], result["variants"], result["modifier_groups"], result["modifiers"],
            result["uoms"], result["conversions"], result["recipe_lines"]) == (120, 30, 10, 20, 5, 5, 20)
    async with session_factory() as s:
        await _set_tenant(s, bid)
        items, variants, groups, mods, uoms, convs, recipes, moves = await _counts(s)
        assert items == 120 and moves == 120                       # every item got its opening ledger row
        assert variants == 120 + 30                                # a default for each + 30 Large
        assert (groups, mods) == (10, 20)
        assert uoms == 16 + 5 and convs == 4 + 5 * 2               # custom conversions get their reverse
        assert recipes == 20
        p1 = (await s.execute(select(Item).where(Item.name == "Produk 001"))).scalar_one()
        assert p1.uom_id is not None                               # 'pcs' linked to the standard unit
        large = (await s.execute(select(ItemVariant).where(ItemVariant.item_id == p1.id, ItemVariant.name == "Large"))).scalar_one()
        assert large.sell_price == Decimal("15000.00") and large.is_default is False
        gula = (await s.execute(select(ModifierGroup).where(ModifierGroup.item_id == p1.id))).scalar_one()
        assert gula.selection == "single" and gula.is_required and gula.min_select == 1
        default_mod = (await s.execute(select(Modifier).where(Modifier.group_id == gula.id, Modifier.is_default.is_(True)))).scalar_one()
        assert default_mod.name == "Normal"
        line = (await s.execute(select(RecipeLine).join(ItemVariant, ItemVariant.id == RecipeLine.variant_id).where(ItemVariant.item_id == p1.id))).scalar_one()
        assert line.quantity == Decimal("10.000") and line.uom_id is not None

    # Re-importing the same workbook is an update, not a duplicate.
    async with session_factory() as s:
        await _set_tenant(s, bid)
        biz = await s.get(Business, bid)
        result = await import_catalog(s, biz, data)
        await s.commit()
    assert result["items_created"] == 0 and result["items_updated"] == 120
    async with session_factory() as s:
        await _set_tenant(s, bid)
        items, variants, groups, mods, uoms, convs, recipes, _ = await _counts(s)
        assert (items, variants, groups, mods, uoms, convs, recipes) == (120, 150, 10, 20, 21, 14, 20)


async def test_validation_sees_what_already_exists(session_factory, empty_shop):
    """A variant sheet may reference an item that exists only in the database."""
    bid = empty_shop
    async with session_factory() as s:
        await _set_tenant(s, bid)
        s.add(Item(business_id=bid, name="Kopi Lama", unit="cup", sell_price=Decimal(20000)))
        await s.commit()
    data = _workbook({"Varian": pd.DataFrame({"Barang": ["Kopi Lama", "Kopi Baru"], "Varian": ["Large", "Large"], "Harga Jual": [25000, 1]})})
    async with session_factory() as s:
        await _set_tenant(s, bid)
        wb = parse_catalog_workbook(data)
        errors = validate_catalog_workbook(wb, await load_existing(s))
    assert len(errors) == 1 and "Kopi Baru" in errors[0] and "baris 3" in errors[0]
