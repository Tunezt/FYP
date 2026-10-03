"""Public, unauthenticated pages' data (till-5b).

`GET /public/struk/{code}`: the receipt a customer opens by scanning the QR the
till showed (decision 4). The code is the whole credential: unguessable, made
only when a web or WhatsApp receipt was asked for, and it says which café it
belongs to (receipt_delivery.py). The view is the paper receipt's content and
nothing about the customer: no name, no address, no phone.
"""
from fastapi import APIRouter, HTTPException

from app.core.db import tenant_session
from app.schemas.pos import ReceiptOut

router = APIRouter(prefix="/public", tags=["public"])

NOT_FOUND = "Struk tidak ditemukan — pindai ulang kode QR dari kasir"


@router.get("/struk/{code}", response_model=ReceiptOut)
async def public_receipt(code: str):
    from app.api.pos import receipt_view
    from app.services.receipt_delivery import find_order_by_code

    found = await find_order_by_code(code)
    if found is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    business_id, order_id = found
    async with tenant_session(business_id) as session:
        view = await receipt_view(session, business_id, order_id)
    # A receipt is the shop's record of a sale, not of the person: strip them.
    return view.model_copy(update={"customer_name": None, "delivery_address": None, "points_earned": 0, "points_redeemed": 0})
