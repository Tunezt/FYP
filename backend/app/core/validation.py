"""422s in words a cashier can act on (till-2).

FastAPI answers a request that fails its schema with a list of pydantic errors
in English (`[{"loc": ["body", "amount"], "msg": "Input should be greater
than 0"}]`). The till and the dashboard show `detail` as it comes, so that list
reached the screen as JSON. The forms now stop a bad submission before it is
sent, but the server keeps its own validation (never trust the client): when
it refuses, it says which fields, in Indonesian. The raw list stays under
`errors` for whoever is debugging.
"""
from __future__ import annotations

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

FIELD_LABELS = {
    "amount": "jumlah",
    "quantity": "jumlah",
    "reason": "alasan",
    "note": "catatan",
    "notes": "catatan",
    "name": "nama",
    "phone": "nomor HP",
    "pin": "PIN",
    "manager_pin": "PIN manajer",
    "category": "jenis",
    "occurred_on": "tanggal",
    "opening_float": "modal awal",
    "counted_cash": "uang dihitung",
    "supplier_id": "supplier",
    "table_label": "nomor meja",
    "lines": "daftar barang",
    "payments": "pembayaran",
    "value": "nilai",
    "code": "kode",
    "image_base64": "foto",
    "mime_type": "jenis foto",
    "order_type": "jenis pesanan",
    "tendered": "uang diterima",
    "unit": "satuan",
    "menu_category": "kategori menu",
    "receipt_footer": "catatan di bawah struk",
    "receipt_logo": "logo di struk",
    "sell_price": "harga jual",
    "cost_price": "harga modal",
    "current_stock": "stok",
    "reorder_threshold": "batas minimum",
    "staff_id": "kasir",
    "sold_at": "waktu",
    "delivery_address": "alamat pengantaran",
}


def _label(loc: tuple | list) -> str:
    # ("body", "lines", 0, "quantity") -> "jumlah"; the last named part wins.
    for part in reversed(list(loc)):
        if isinstance(part, str) and part not in ("body", "query", "path"):
            return FIELD_LABELS.get(part, part.replace("_", " "))
    return "isian"


def validation_message(errors: list[dict]) -> str:
    fields: list[str] = []
    for err in errors:
        label = _label(err.get("loc", ()))
        if err.get("type") == "value_error":
            # Our own validators raise their reason in Indonesian: say it.
            reason = str(err.get("msg", "")).removeprefix("Value error, ").strip()
            if reason:
                label = f"{label} ({reason})"
        if label not in fields:
            fields.append(label)
    listed = ", ".join(fields[:4]) + (" dan lainnya" if len(fields) > 4 else "")
    return f"Isian belum benar: {listed} — periksa lagi lalu coba simpan"


async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    return JSONResponse(
        status_code=422,
        content={"detail": validation_message(errors), "errors": jsonable_encoder(errors)},
    )
