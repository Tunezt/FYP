"""Audit P0-4: a made-to-order item has no stock of its own. The bot, the metric
and the alerts say servings left + limiting ingredient, and never alert on it.

Needs the local Postgres (roadmap §2)."""
from decimal import Decimal

from sqlalchemy import select

from app.ai.tools import get_low_stock, get_stock
from app.jobs.rules import rule_stockout_risk
from app.models import Alert, Business
from app.services.orders import OrderLineSpec, PaymentSpec, create_order
from app.services.velocity import check_low_stock_for_item

from tests.test_recipes import _set_tenant, cafe, engine, session_factory  # noqa: F401


async def test_made_to_order_says_servings_not_a_frozen_number(session_factory, cafe):
    c = cafe
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        await create_order(
            s, business_id=c["bid"], staff_id=c["staff"],
            lines=[OrderLineSpec(item_id=c["latte"], quantity=Decimal(1), variant_id=c["regular"])],
            payments=[PaymentSpec(method="cash", amount=Decimal(22000))],
        )
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        biz = await s.get(Business, c["bid"])
        got = await get_stock(s, biz, {"item_name": "latte"})
        row = got["items"][0]
        assert row["made_to_order"] is True and "current_stock" not in row
        # beans 4.982 kg / 18 g = 276; milk 9.880 l / 120 ml = 82  -> milk limits
        assert row["servings_left"] == 82 and row["limiting_ingredient"] == "Susu UHT"
        plain = (await get_stock(s, biz, {"item_name": "arabica"}))["items"][0]
        assert plain["current_stock"] > 0 and "made_to_order" not in plain

        # no alert, however the item has sold: nightly sweep, rule, post-sale check, bot list
        assert await check_low_stock_for_item(s, c["bid"], c["latte"]) is None
        assert [a for a in await rule_stockout_risk(s, biz) if a.related_item_id == c["latte"]] == []
        assert "Latte" not in [r["name"] for r in (await get_low_stock(s, biz, {}))["at_risk_items"]]
        assert (await s.execute(select(Alert).where(Alert.related_item_id == c["latte"]))).first() is None
