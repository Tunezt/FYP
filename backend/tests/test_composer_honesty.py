"""fin-p1-6: the composer prompt keeps the replies factual (model mocked)."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.ai import composer


def _prompt_for(msg="promo apa yang jalan?"):
    b = SimpleNamespace(name="Kafe", business_type="cafe", language_preference="id")
    with patch("app.ai.composer.generate_text", new=AsyncMock(return_value="ok")) as g:
        asyncio.run(composer.compose_reply(b, msg, "get_promo_performance", {"promos": [{"is_active": True, "applications": 0}]}))
    return g.await_args.kwargs["system"]


def test_prompt_forbids_offering_actions_and_explaining_the_data():
    s = _prompt_for()
    assert "Facts only" in s and "Never offer, promise or hint at an action" in s


def test_prompt_separates_active_from_used_promos():
    assert "is_active` true means the promo IS running" in _prompt_for()


def test_prompt_keeps_names_as_the_facts_spell_them():
    """p2-14: the audit saw the recipe-cost reply call the ingredient "Biji Arabica"
    by the menu item's name, "Kopi Arabica"."""
    s = _prompt_for("modal kopi arabica berapa?")
    assert "exactly as the FACTS" in s and "never called by the name of the menu item" in s
