import io
from decimal import Decimal

import pandas as pd
import pytest

from app.services.stock_import import StockTemplateError, parse_stock_template


def _xlsx(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    return buf.getvalue()


def test_indonesian_headers():
    data = _xlsx(
        pd.DataFrame(
            {
                "Nama Barang": ["Biji Arabica", "Gula Aren"],
                "Jumlah": [8, 5],
                "Satuan": ["kg", "kg"],
                "Harga Modal": [145000, 38000],
                "Harga Jual": [0, 0],
            }
        )
    )
    rows, warnings = parse_stock_template(data)
    assert len(rows) == 2
    assert rows[0]["name"] == "Biji Arabica"
    assert rows[0]["quantity"] == Decimal("8")
    assert rows[0]["unit"] == "kg"
    assert rows[0]["cost_price"] == Decimal("145000")
    assert warnings == []


def test_english_headers_and_defaults():
    data = _xlsx(pd.DataFrame({"Item": ["Cup 12oz"], "Qty": [200]}))
    rows, _ = parse_stock_template(data)
    assert rows[0]["unit"] == "pcs"  # default when no unit column
    assert rows[0]["sell_price"] == Decimal("0")


def test_blank_rows_skipped_and_negative_warned():
    data = _xlsx(
        pd.DataFrame(
            {"nama": ["Susu UHT", None, "Rusak"], "stok": [24, 10, -3], "satuan": ["liter", "x", "pcs"]}
        )
    )
    rows, warnings = parse_stock_template(data)
    assert [r["name"] for r in rows] == ["Susu UHT"]
    assert any("Rusak" in w for w in warnings)


def test_missing_required_columns_raises():
    data = _xlsx(pd.DataFrame({"kolom_aneh": ["a"], "lain": [1]}))
    with pytest.raises(StockTemplateError):
        parse_stock_template(data)


def test_not_an_excel_file_raises():
    with pytest.raises(StockTemplateError):
        parse_stock_template(b"definitely not a spreadsheet")
