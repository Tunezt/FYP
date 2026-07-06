"""Text-message orchestration: one forced Flash tool call classifies the intent
and extracts arguments simultaneously, then the matching executor runs against
the tenant-scoped session, then the composer writes the reply.

The tool set is fixed (app/ai/tools.py) — the model never writes SQL.
"""
import logging
from dataclasses import dataclass

from google.genai import types
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import tools as tools_module
from app.ai.composer import compose_reply
from app.ai.gemini import force_tool_call
from app.models import Business

logger = logging.getLogger("ai.router")

CLARIFY = types.FunctionDeclaration(
    name="clarify",
    description=(
        "Use ONLY when the message cannot be handled by any other function — "
        "greetings, unclear requests, or things outside business data. "
        "Provide a short helpful clarification/greeting in the owner's language."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "reply": types.Schema(
                type=types.Type.STRING,
                description="The clarification or greeting to send, in the owner's language.",
            )
        },
        required=["reply"],
    ),
)

_CLASSIFIER_SYSTEM = """You route WhatsApp messages from the owner of {business_name}
(a small {business_type}) to exactly one function. The owner writes in Bahasa
Indonesia, Bahasa Malaysia, or English — often mixed in one message. Item names
may be abbreviated or misspelled; pass them through as written.

Prefer a data function whenever the message plausibly asks about the business.
Choose clarify only as a last resort."""


@dataclass
class RoutedReply:
    intent: str
    reply: str


def _declarations() -> list[types.FunctionDeclaration]:
    return [*tools_module.TOOL_DECLARATIONS, CLARIFY]


async def handle_text(session: AsyncSession, business: Business, text: str) -> RoutedReply:
    system = _CLASSIFIER_SYSTEM.format(
        business_name=business.name, business_type=business.business_type
    )
    call = await force_tool_call(system=system, message=text, tools=_declarations())

    if call is None or call.name == "clarify":
        reply = (call.args or {}).get("reply") if call else None
        if not reply:
            reply = await compose_reply(
                business, text, "clarify", {"note": "Message was unclear; ask what they need."}
            )
        return RoutedReply(intent="clarify", reply=reply)

    executor = tools_module.TOOL_EXECUTORS.get(call.name)
    if executor is None:
        logger.warning("Model chose unknown tool %r", call.name)
        reply = await compose_reply(
            business, text, "clarify", {"note": "Could not handle that request."}
        )
        return RoutedReply(intent="clarify", reply=reply)

    facts = await executor(session, business, dict(call.args or {}))
    reply = await compose_reply(business, text, call.name, facts)
    # request_logs vocabulary: the RAG path is logged as 'rag' (per Section 6),
    # data tools keep their specific names (strictly more evaluable than the
    # generic 'function_call').
    intent = "rag" if call.name == "search_history" else call.name
    return RoutedReply(intent=intent, reply=reply)
