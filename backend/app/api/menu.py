"""QR e-menu endpoints (roadmap M11-T1).

Public, keyed by the menu token baked into the QR code on the table — the same
shape as the POS pairing token: one business, long-lived, stateless, and it
grants exactly three things: read the menu, place a ticket, watch that ticket.
No stock figures, no costs, no other business's anything (the tenant session
pins the row-level policies to the token's business).

A placed ticket is an `open` row in `orders`; the till sees it in its queue
(`GET /pos/tickets`) and settles it there.
"""
import uuid

import jwt as pyjwt
from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.core.db import tenant_session
from app.core.security import decode_token
from app.models import Business, Item, ItemVariant, Order
from app.schemas.menu import MenuItemOut, MenuOut, TicketIn, TicketLineOut, TicketOut
from app.schemas.pos import PosModifierGroupOut, PosModifierOut, PosVariantOut
from app.services.orders import ItemNotFound, ModifierSelectionInvalid, OrderLineSpec, VariantNotFound
from app.services.tickets import QueueFull, TicketNotFound, TicketUnavailable, get_ticket, place_ticket, ticket_code

router = APIRouter(prefix="/menu", tags=["menu"])


def _menu_business_id(menu_token: str) -> uuid.UUID:
    try:
        claims = decode_token(menu_token)
    except pyjwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Tautan menu tidak berlaku — pindai ulang kode QR di meja ya")
    if claims.get("scope") != "menu":
        raise HTTPException(status_code=401, detail="Tautan menu tidak dikenali")
    return uuid.UUID(claims["business_id"])


def ticket_out(order: Order, model=TicketOut) -> TicketOut:
    cart = order.cart or {"lines": []}
    return model(
        id=order.id,
        code=ticket_code(order.id),
        status=order.status,
        order_type=order.order_type,
        table_label=order.table_label,
        guest_name=order.guest_name,
        guest_phone=order.guest_phone,
        note=cart.get("note"),
        placed_at=order.created_at,
        lines=[
            TicketLineOut(
                item_id=uuid.UUID(l["item_id"]),
                name=f"{l['item_name']} · {l['variant_name']}" if l.get("variant_name") else l["item_name"],
                modifiers=list(l.get("modifier_names", [])),
                quantity=l["quantity"], unit_price=l["unit_price"], line_total=l["line_total"], notes=l.get("notes"),
            )
            for l in cart["lines"]
        ],
        subtotal=order.subtotal, service_charge=order.service_charge, tax_total=order.tax_total,
        rounding=order.rounding, total=order.total,
        is_estimate=order.status == "open",
    )


@router.get("/{menu_token}", response_model=MenuOut)
async def menu(menu_token: str):
    """The menu as the guest sees it. `available` is the only thing stock decides."""
    business_id = _menu_business_id(menu_token)
    async with tenant_session(business_id) as session:
        business = await session.get(Business, business_id)
        if business is None:
            raise HTTPException(status_code=404, detail="Usaha tidak ditemukan")
        # The same rule as the till: an item without a selling price is an
        # ingredient (beans, sugar, milk), not something a guest orders.
        items = (await session.execute(select(Item).where(Item.sell_price > 0).order_by(Item.name))).scalars().all()
        variants = (await session.execute(
            select(ItemVariant).where(ItemVariant.is_active.is_(True))
            .order_by(ItemVariant.is_default.desc(), ItemVariant.sell_price, ItemVariant.name)
        )).scalars().all()
        by_item: dict[uuid.UUID, list[ItemVariant]] = {}
        for v in variants:
            by_item.setdefault(v.item_id, []).append(v)
        from app.services.catalog import made_to_order_item_ids, modifier_catalog

        groups_by_item = await modifier_catalog(session)
        made_to_order = await made_to_order_item_ids(session)
        return MenuOut(
            business_name=business.name,
            items=[
                MenuItemOut(
                    id=i.id, name=i.name, unit=i.unit, sell_price=i.sell_price,
                    available=i.id in made_to_order or i.current_stock > 0,
                    made_to_order=i.id in made_to_order,
                    variants=[PosVariantOut.model_validate(v) for v in by_item.get(i.id, [])],
                    modifier_groups=[
                        PosModifierGroupOut(
                            id=g.id, name=g.name, selection=g.selection, is_required=g.is_required,
                            min_select=g.min_select, max_select=g.max_select,
                            modifiers=[PosModifierOut.model_validate(m) for m in mods],
                        )
                        for g, mods in groups_by_item.get(i.id, [])
                    ],
                )
                for i in items
            ],
        )


@router.post("/{menu_token}/orders", response_model=TicketOut, status_code=201)
async def place(menu_token: str, payload: TicketIn):
    """The guest orders. Writes one open row in the till's own order table."""
    business_id = _menu_business_id(menu_token)
    async with tenant_session(business_id) as session:
        try:
            ticket = await place_ticket(
                session,
                business_id=business_id,
                lines=[
                    OrderLineSpec(item_id=l.item_id, variant_id=l.variant_id, modifier_ids=list(l.modifier_ids),
                                  quantity=l.quantity, notes=l.notes)
                    for l in payload.lines
                ],
                order_type=payload.order_type, table_label=payload.table_label,
                guest_name=payload.guest_name, guest_phone=payload.guest_phone, note=payload.note,
            )
        except QueueFull:
            raise HTTPException(status_code=429, detail="Antrean pesanan sedang penuh — silakan pesan langsung ke kasir ya")
        except ItemNotFound:
            raise HTTPException(status_code=404, detail="Menu tidak ditemukan — coba muat ulang halaman")
        except VariantNotFound:
            raise HTTPException(status_code=404, detail="Ukuran menu tidak ditemukan atau sudah tidak tersedia")
        except ModifierSelectionInvalid as exc:
            messages = {
                "unknown": "Pilihan tambahan tidak dikenali atau sudah tidak tersedia",
                "required": f"Pilihan '{exc.group_name}' wajib diisi",
                "single": f"Pilihan '{exc.group_name}' hanya boleh satu",
                "max": f"Pilihan '{exc.group_name}' melebihi batas maksimal",
            }
            raise HTTPException(status_code=422, detail=messages[exc.code])
        except TicketUnavailable as exc:
            raise HTTPException(status_code=409, detail=f"{exc.item_name} sedang tidak tersedia — pilih menu lain ya")
        return ticket_out(ticket)


@router.get("/{menu_token}/orders/{order_id}", response_model=TicketOut)
async def watch(menu_token: str, order_id: uuid.UUID):
    """The guest's own ticket: still waiting, paid, or cancelled."""
    business_id = _menu_business_id(menu_token)
    async with tenant_session(business_id) as session:
        try:
            ticket = await get_ticket(session, order_id)
        except TicketNotFound:
            raise HTTPException(status_code=404, detail="Pesanan tidak ditemukan")
        return ticket_out(ticket)
