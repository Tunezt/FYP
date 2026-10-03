"""till-8 — a write is committed before the client is told it worked.

Found while auditing the till: FastAPI runs a `yield` dependency's code after
the yield (our commit) *after the response has been sent* unless the
dependency is declared `scope="function"`. So the till could save a held order,
re-read the list at once, and not see it; and a commit that fails — the
ledger's debit = credit check is a *deferred* constraint, checked at commit —
would already have been answered 201. The owner, till and printer-bridge
sessions now commit before the response starts. This test watches the order of
the two events on the wire.
"""
import os
import uuid

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import event

from app.core.security import create_token
from app.models import Business

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")


def _probe_app(dependency, order: list[str]):
    probe = FastAPI()

    @probe.get("/probe")
    async def probe_route(ctx: dependency):
        event.listen(ctx.session.sync_session, "after_commit", lambda _s: order.append("commit"))
        return {"ok": True}

    async def wire(scope, receive, send):
        async def watched(message):
            if message["type"] == "http.response.start":
                order.append("response")
            await send(message)

        await probe(scope, receive, watched)

    return wire


async def _run(dependency, token):
    order: list[str] = []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_probe_app(dependency, order)), base_url="http://test") as c:
        resp = await c.get("/probe", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    return order


async def test_the_owner_and_till_sessions_commit_before_the_response():
    if not DB_URL:
        pytest.fail("INTEGRATION_DATABASE_URL unset — local Postgres is not configured (roadmap §2)")
    from app.core.db import plain_session
    from app.core.deps import OwnerCtx, PosCtx

    async with plain_session() as s:
        biz = Business(name="Commit Probe", owner_phone=f"62991{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.flush()
        bid = biz.id
    try:
        owner = create_token(business_id=str(bid), scope="owner", staff_id=None)
        till = create_token(business_id=str(bid), scope="pos", staff_id=str(uuid.uuid4()))
        assert await _run(OwnerCtx, owner) == ["commit", "response"]
        assert await _run(PosCtx, till) == ["commit", "response"]
    finally:
        async with plain_session() as s:
            row = await s.get(Business, bid)
            if row:
                await s.delete(row)
        from app.core.db import engine

        await engine.dispose()


def test_the_printer_bridge_routes_commit_before_answering():
    """A claim must be on record before the bridge is told it holds the job."""
    from app.api.printing import agent_router, printer_ctx

    agent_routes = list(agent_router.routes)
    assert len(agent_routes) == 5
    for route in agent_routes:
        deps = [d for d in route.dependant.dependencies if d.call is printer_ctx]
        assert deps and all(d.scope == "function" for d in deps), route.path
