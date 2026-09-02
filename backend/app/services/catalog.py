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

from app.models import Item, ItemVariant, Modifier, ModifierGroup

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


# ── Modifiers (roadmap M4-T2) ───────────────────────────────────────────────


class ModifierInvalid(Exception):
    """`code` is one of: name, duplicate, bounds."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _normalise_group_rules(selection: str, is_required: bool, min_select: int, max_select: int | None):
    if selection == "single":
        max_select = 1
    if is_required and min_select < 1:
        min_select = 1
    if not is_required and min_select > 0:
        is_required = True
    if max_select is not None and min_select > max_select:
        raise ModifierInvalid("bounds")
    return is_required, min_select, max_select


async def _group_name_taken(session, item_id, name, exclude=None) -> bool:
    stmt = select(func.count(ModifierGroup.id)).where(
        ModifierGroup.item_id == item_id, func.lower(ModifierGroup.name) == name.lower()
    )
    if exclude is not None:
        stmt = stmt.where(ModifierGroup.id != exclude)
    return (await session.execute(stmt)).scalar_one() > 0


async def _modifier_name_taken(session, group_id, name, exclude=None) -> bool:
    stmt = select(func.count(Modifier.id)).where(
        Modifier.group_id == group_id, func.lower(Modifier.name) == name.lower()
    )
    if exclude is not None:
        stmt = stmt.where(Modifier.id != exclude)
    return (await session.execute(stmt)).scalar_one() > 0


async def create_modifier_group(
    session: AsyncSession, item: Item, *, name: str, selection: str = "single",
    is_required: bool = False, min_select: int = 0, max_select: int | None = None, sort_order: int = 0,
) -> ModifierGroup:
    name = (name or "").strip()
    if not name:
        raise ModifierInvalid("name")
    if await _group_name_taken(session, item.id, name):
        raise ModifierInvalid("duplicate")
    is_required, min_select, max_select = _normalise_group_rules(selection, is_required, min_select, max_select)
    group = ModifierGroup(
        business_id=item.business_id, item_id=item.id, name=name, selection=selection,
        is_required=is_required, min_select=min_select, max_select=max_select, sort_order=sort_order,
    )
    session.add(group)
    await session.flush()
    return group


async def update_modifier_group(session: AsyncSession, group: ModifierGroup, **changes) -> ModifierGroup:
    if changes.get("name") is not None:
        new_name = changes["name"].strip()
        if not new_name:
            raise ModifierInvalid("name")
        if await _group_name_taken(session, group.item_id, new_name, exclude=group.id):
            raise ModifierInvalid("duplicate")
        group.name = new_name
    selection = changes.get("selection") or group.selection
    is_required = changes["is_required"] if changes.get("is_required") is not None else group.is_required
    min_select = changes["min_select"] if changes.get("min_select") is not None else group.min_select
    max_select = changes["max_select"] if "max_select" in changes and changes["max_select"] is not None else group.max_select
    group.is_required, group.min_select, group.max_select = _normalise_group_rules(selection, is_required, min_select, max_select)
    group.selection = selection
    for field in ("sort_order", "is_active"):
        if changes.get(field) is not None:
            setattr(group, field, changes[field])
    group.updated_at = datetime.now(timezone.utc)
    await session.flush()
    return group


async def create_modifier(
    session: AsyncSession, group: ModifierGroup, *, name: str, price_delta: Decimal = Decimal(0),
    is_default: bool = False, sort_order: int = 0,
) -> Modifier:
    name = (name or "").strip()
    if not name:
        raise ModifierInvalid("name")
    if await _modifier_name_taken(session, group.id, name):
        raise ModifierInvalid("duplicate")
    modifier = Modifier(
        business_id=group.business_id, group_id=group.id, name=name, price_delta=Decimal(price_delta),
        is_default=is_default, sort_order=sort_order,
    )
    session.add(modifier)
    await session.flush()
    return modifier


async def update_modifier(session: AsyncSession, modifier: Modifier, **changes) -> Modifier:
    if changes.get("name") is not None:
        new_name = changes["name"].strip()
        if not new_name:
            raise ModifierInvalid("name")
        if await _modifier_name_taken(session, modifier.group_id, new_name, exclude=modifier.id):
            raise ModifierInvalid("duplicate")
        modifier.name = new_name
    for field in ("price_delta", "is_default", "sort_order", "is_active"):
        if changes.get(field) is not None:
            setattr(modifier, field, changes[field])
    modifier.updated_at = datetime.now(timezone.utc)
    await session.flush()
    return modifier


async def modifier_catalog(session: AsyncSession, item_ids: list[uuid.UUID] | None = None):
    """{item_id: [(group, [modifiers])]} — active groups and modifiers only,
    in sort order. What the kiosk shows."""
    stmt = select(ModifierGroup).where(ModifierGroup.is_active.is_(True)).order_by(ModifierGroup.sort_order, ModifierGroup.name)
    if item_ids is not None:
        stmt = stmt.where(ModifierGroup.item_id.in_(item_ids))
    groups = (await session.execute(stmt)).scalars().all()
    if not groups:
        return {}
    mods = (
        await session.execute(
            select(Modifier).where(Modifier.group_id.in_([g.id for g in groups]), Modifier.is_active.is_(True))
            .order_by(Modifier.sort_order, Modifier.name)
        )
    ).scalars().all()
    by_group: dict[uuid.UUID, list[Modifier]] = {}
    for m in mods:
        by_group.setdefault(m.group_id, []).append(m)
    out: dict[uuid.UUID, list[tuple[ModifierGroup, list[Modifier]]]] = {}
    for g in groups:
        out.setdefault(g.item_id, []).append((g, by_group.get(g.id, [])))
    return out
