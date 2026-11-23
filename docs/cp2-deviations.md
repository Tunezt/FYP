# CP2 report: Deviations from the CP1 design

Draft for the CP2 report (audit row P1-8). Each row is a place where the build differs from
the CP1 report (v13), with the reason. They are choices made on evidence while building, not
unfinished work. Code references are to this repository.

| # | CP1 said | The build does | Why |
|---|---|---|---|
| 1 | Table 3.1: **LangChain** as the orchestration layer and a **dedicated vector store**. | No LangChain orchestration. A fixed set of named tools is called through the Gemini function-calling API (`app/ai/tools.py`). Embeddings are stored in **pgvector inside the same PostgreSQL** (`receipts.embedding`, 768 dimensions, cosine search, `app/services/rag.py`). Only `langchain_core.documents.Document` remains, as the return type of the retrieval step. | LangChain's `SupabaseVectorStore` reads through a PostgREST call that uses the service-role key. That would bypass the per-request tenant setting (`app.current_business_id`) which row-level security relies on, and it expects a table layout that conflicts with the locked `receipts` schema. One database for transactions and vectors keeps tenant isolation enforced in one place. A separate vector store would have been a second system to isolate, back up and pay for. Function calling over a fixed tool set also gives the "fails loudly" property that the evaluation measures (`docs/evaluation.md`). |
| 2 | §3.4: messages through the **WhatsApp Cloud API** (Meta). | The Cloud API client is built and is still the default (`WHATSAPP_PROVIDER=cloud`), but the live bot runs on a **linked-device gateway** (WhatsApp Web through the open-source Baileys library, `gateway/`, `WHATSAPP_PROVIDER=gateway`). | Meta business verification was still pending when the café needed a working bot. The gateway is **unofficial and WhatsApp can ban a number that uses it**; it is the accepted risk until verification completes. Side effect: there are no approved templates on the gateway, so proactive messages (alerts, login codes, the daily summary) are sent as plain text. On the Cloud API they go through the approved Utility and Authentication templates. |
| 3 | §3.5: "the dashboard only ever reads". | The dashboard also **writes**: expenses, stock and catalogue edits, settings, voids and refunds, and backdated sales typed from paper. | Owners need to correct a mistake or enter a paper bill where they are looking at the numbers; making them do it by WhatsApp only would have blocked that. Every write goes through the same services as the till and the bot, so the ledger rules hold: nothing is deleted, voids and refunds write reversing rows, writes go through the owner's tenant-scoped session, and sensitive reversals need a manager PIN. |
| 4 | §3.6: reorder points from **lead time and safety stock**. | Stock risk is **days remaining** (current stock ÷ trailing 14-day average daily use, `app/services/velocity.py`) compared with the **next likely delivery** (the typical gap between past goods receipts from that supplier, `app/jobs/rules.py`). | A café does not record lead times or safety stock, and asking for them at onboarding would have been the first thing owners skip. Delivery gaps can be read from receipts that are already recorded. The result is the question the owner actually asks: "will this run out before the next delivery?". Limit: with few past deliveries the gap is a weak estimate. |
| 5 | §1.5: proactive **periodic business summaries**. | Built late: a daily summary every night, plus last week's on Monday (`app/jobs/summary.py`, called from the nightly job). A day with no sales sends none. | Alerts were built first because they are the core of Objective 2; the summary reuses the same registry numbers and the same nightly run. |
| 6 | Model: the CP1 text names one model family for classification and composition. | The model name is **configuration** (`GEMINI_FLASH_MODEL`), with a fallback when the free-tier quota is spent. Every evaluation number in `docs/evaluation.md` states which model produced it. | The free tier changes limits and available models without notice. Fixing a name in code would have broken the bot on the day a model was retired. |

## Not deviations, but worth stating beside them

- **Scope kept as in CP1:** no multi-outlet, no payroll, no real marketplace or payment
  gateway integration, no hardware drivers beyond the receipt printer, no native apps.
- **The evaluation (Objective 4) has parts that need real people:** the SUS questionnaire,
  the consent confirmation and the 2-4 week trial need the owner and Ibu Diah. The report
  must not present them as done until they are. Status is in `docs/evaluation.md`.

## Items to confirm before the report is final

- Row 6: the CP1 wording on the model is not in this repository; check it against CP1 v13.
- Row 4: the 14-day window is the value in `velocity.py` at the time of writing.
