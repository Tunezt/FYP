"""Catalogue helpers — variants (roadmap M4-T1).

Every item has exactly one default variant (enforced by a partial unique index).
Its prices mirror `items.sell_price` / `items.cost_price` in both directions, so
the pre-variant code paths (kiosk tiles, WhatsApp tools, dashboard inventory)
keep reading the item while the order model prices by variant.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Item, ItemVariant

DEFAULT_VARIANT_NAME = "Standar"


class VariantInvalid(Exception):
    """`code` is one of: name, duplicate, default_inactive, item."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


async def default_variant(session: AsyncSession, item_id: uuid.UUID) -> ItemVariant | None:
    return (
        await session.execute(
            select(ItemVariant).where(ItemVariant.item_id == item_id, ItemVariant.is_default.is_(True))
        )
    ).scalar_one_or_none()


async def ensure_default_variant(session: AsyncSession, item: Item) -> ItemVariant:
    """Called wherever an item is created (dashboard, receipt photo, Excel
    import, seed) so no item is ever without its default variant."""
    existing = await default_variant(session, item.id)
    if existing is not None:
        return existing
    variant = ItemVariant(
        business_id=item.business_id,
        item_id=item.id,
        name=DEFAULT_VARIANT_NAME,
        sell_price=item.sell_price,
        cost_price=item.cost_price,
        is_default=True,
        is_active=True,
    )
    session.add(variant)
    await session.flush()
    return variant


async def _name_taken(session: AsyncSession, item_id: uuid.UUID, name: str, exclude: uuid.UUID | None = None) -> bool:
    stmt = select(func.count(ItemVariant.id)).where(
        ItemVariant.item_id == item_id, func.lower(ItemVariant.name) == name.lower()
    )
    if exclude is not None:
        stmt = stmt.where(ItemVariant.id != exclude)
    return (await session.execute(stmt)).scalar_one() > 0


async def _clear_default(session: AsyncSession, item_id: uuid.UUID) -> None:
    for v in (await session.execute(select(ItemVariant).where(ItemVariant.item_id == item_id, ItemVariant.is_default.is_(True)))).scalars():
        v.is_default = False
    await session.flush()


async def create_variant(
    session: AsyncSession,
    item: Item,
    *,
    name: str,
    sell_price: Decimal,
    cost_price: Decimal = Decimal(0),
    sku: str | None = None,
    is_default: bool = False,
) -> ItemVariant:
    name = (name or "").strip()
    if not name:
        raise VariantInvalid("name")
    if await _name_taken(session, item.id, name):
        raise VariantInvalid("duplicate")
    if is_default:
        await _clear_default(session, item.id)
    variant = ItemVariant(
        business_id=item.business_id,
        item_id=item.id,
        name=name,
        sku=(sku or None),
        sell_price=Decimal(sell_price),
        cost_price=Decimal(cost_price),
        is_default=is_default,
        is_active=True,
    )
    session.add(variant)
    await session.flush()
    if is_default:
        await sync_item_from_default(session, item, variant)
    return variant


async def update_variant(session: AsyncSession, variant: ItemVariant, item: Item, **changes) -> ItemVariant:
    if "name" in changes and changes["name"] is not None:
        new_name = changes["name"].strip()
        if not new_name:
            raise VariantInvalid("name")
        if await _name_taken(session, item.id, new_name, exclude=variant.id):
            raise VariantInvalid("duplicate")
        variant.name = new_name
    if changes.get("is_active") is False and variant.is_default:
        raise VariantInvalid("default_inactive")
    if changes.get("is_default") is True and not variant.is_default:
        await _clear_default(session, item.id)
        variant.is_default = True
    for field in ("sku", "sell_price", "cost_price", "is_active"):
        if field in changes and changes[field] is not None:
            setattr(variant, field, changes[field])
    variant.updated_at = datetime.now(timezone.utc)
    await session.flush()
    if variant.is_default:
        await sync_item_from_default(session, item, variant)
    return variant


async def sync_item_from_default(session: AsyncSession, item: Item, variant: ItemVariant) -> None:
    """The default variant's prices are the item's prices."""
    item.sell_price = variant.sell_price
    item.cost_price = variant.cost_price
    item.updated_at = datetime.now(timezone.utc)
    await session.flush()


async def sync_default_from_item(session: AsyncSession, item: Item) -> None:
    """The item's prices were edited directly (dashboard, Excel, receipt):
    carry them onto the default variant so the two never disagree."""
    variant = await default_variant(session, item.id)
    if variant is None:
        await ensure_default_variant(session, item)
        return
    variant.sell_price = item.sell_price
    variant.cost_price = item.cost_price
    variant.updated_at = datetime.now(timezone.utc)
    await session.flush()
