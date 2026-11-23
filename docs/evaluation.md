# Evaluation — fixed tool set vs naive text-to-SQL

Roadmap M9-T6. This file is the evidence for the project's central technical claim: that a
fixed tool set over a metric registry fails **loudly** where free-form text-to-SQL fails
**silently**. Runs are appended by `python -m app.eval` and are not edited by hand afterwards.

## What is measured

The question set lives in `backend/app/eval/questions.py`: owner messages in code-switched
Indonesian, Malay and English with the abbreviations and typos people type on a phone
("brp", "sy", "yg", "hw much", "kira2", "kat kedai sebelah"). Each question is either
**data** (answerable from the business data — the set names the tool and arguments a correct
system uses, and the ground truth is the metric registry computed with those arguments at run
time) or **out of scope** (a forecast, the weather, tax law, a competitor's price, staff
attendance — where the only correct behaviour is to say so).

Every answer gets one verdict:

| verdict | meaning |
|---|---|
| correct | a data question answered with the ground-truth figure (± Rp 0,50) |
| refused | no figure given: the system said it could not answer, asked what was meant, or its query failed loudly |
| **silent error** | a figure was given and it is wrong — the wrong number for a data question, or *any* number for an out-of-scope one |

Three rates follow: **answer accuracy** (correct ÷ data questions), **refusal rate**
(refused ÷ all), and the **silent-error rate** (silent errors ÷ all). The last is the one
that matters. An owner can see a refusal and ask again; a plausible wrong number is the
failure nobody sees until the money is gone.

## The two systems

**Tools** — what ships. One forced Gemini Flash function call chooses a tool from the fixed
set of fifteen (or `out_of_scope` / `clarify`) and extracts its arguments; the tool reads the
metric registry (`app/metrics`), the same implementation the dashboard reads. The harness
scores the tool's *facts* — the figure the composer would narrate — so the reply model is not
in the loop and cannot be blamed or credited.

**Baseline** — naive text-to-SQL, built only for this comparison and kept behind
`EVAL_TEXT_TO_SQL=1` (never reachable from WhatsApp). The model is handed the schema
(generated from the models so it cannot drift) and asked for one SELECT; the query runs in
the tenant session as a read-only transaction with a statement timeout, and the first
number in the first row is the answer — which is what a naive integration does. The guards
(one statement, SELECT only, no side-effect keywords) are deliberately strong: the point is
not that text-to-SQL can be made to write `DROP TABLE`, it is that a well-formed, plausible,
*wrong* query returns a number that looks right.

## Two classifiers for the tool path

The live model is on a free tier (20 requests a day per model at the time of writing), so
the harness also has a **keyword stand-in** classifier: a deterministic rule set used to
validate the harness and the scorer offline. It is *not* a model and *not* what ships; a run
that used it says so in its table. A test (`test_eval_harness`) requires the stand-in to
agree with the question set on intent, so its offline numbers measure the tools and the
registry, not the routing. Only a live run measures routing.

## How to run

```bash
cd backend && ./.venv/Scripts/python.exe -m app.eval --mode offline                       # no model calls
cd backend && ./.venv/Scripts/python.exe -m app.eval --mode live --limit 10               # 10 Flash calls
cd backend && EVAL_TEXT_TO_SQL=1 ./.venv/Scripts/python.exe -m app.eval --mode live --limit 10 --baseline   # + 10 more
```

The local Postgres must be up and seeded; the seeded café (owner 628120001111) is the
subject, so the set's item and customer names resolve.

## Runs

### Run “live, first 9 questions, tools + text-to-SQL baseline” — 2026-09-06 03:25 UTC

Business: Kopi Kenangan Senja. Questions: 30 (20 data, 10 out of scope).

| system | classifier | n | accuracy (data) | refusal rate | **silent-error rate** |
|---|---|---|---|---|---|
| tools | gemini | 6 | 100% | 0% | **0%** |
| baseline | gemini (text-to-sql) | 0 | — | — | — |

_tools: stopped early — model call failed at question 7: ClientError: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota, please check your plan and _

_baseline: stopped early — generation failed: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://ai.google.dev/gemini-api/docs/rate-limits. To monitor your current usage, head to: https://ai.dev/rate-limit. \n* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 5, model: gemini-2.5-flash\nPlease retry in 9.413189857s.', 'status': 'RESOURCE_EXHAUSTED', 'details': [{'@type': 'type.googleapis.com/google.rpc.Help', 'links': [{'description': 'Learn more about Gemini API quotas', 'url': 'https://ai.google.dev/gemini-api/docs/rate-limits'}]}, {'@type': 'type.googleapis.com/google.rpc.QuotaFailure', 'violations': [{'quotaMetric': 'generativelanguage.googleapis.com/generate_content_free_tier_requests', 'quotaId': 'GenerateRequestsPerMinutePerProjectPerModel-FreeTier', 'quotaDimensions': {'model': 'gemini-2.5-flash', 'location': 'global'}, 'quotaValue': '5'}]}, {'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '9s'}]}}_

_This first run had no pacing: the free tier allows 5 requests per minute per model, and the harness stopped at the 429. Six questions were classified before the cut, all six correctly. The paced run below covers the full set._

#### tools — per question

| # | question | expected | got | figure | truth | verdict |
|---|---|---|---|---|---|---|
| 1 | berapa penjualan hari ini? | get_sales_summary | get_sales_summary | 0 | 0 | correct |
| 2 | jualan hari ni brp bos | get_sales_summary | get_sales_summary | 0 | 0 | correct |
| 3 | omzet kemarin brp ya | get_sales_summary | get_sales_summary | 1.485e+06 | 1.485e+06 | correct |
| 4 | hw much did we sell this week | get_sales_summary | get_sales_summary | 4.244e+06 | 4.244e+06 | correct |
| 5 | penjualan minggu ini vs minggu lalu gmn | compare_periods | compare_periods | 4.244e+06 | 4.244e+06 | correct |
| 6 | bulan ni jualan berapa banyak? | get_sales_summary | get_sales_summary | 3.799e+06 | 3.799e+06 | correct |

#### baseline — per question

| # | question | expected | got | figure | truth | verdict |
|---|---|---|---|---|---|---|

### Run “live, full set, tools + text-to-SQL baseline, paced 13s” — 2026-09-06 03:36 UTC

Business: Kopi Kenangan Senja. Questions: 30 (20 data, 10 out of scope).

| system | classifier | n | accuracy (data) | refusal rate | **silent-error rate** |
|---|---|---|---|---|---|
| tools | gemini | 15 | 100% | 0% | **0%** |
| baseline | gemini (text-to-sql) | 0 | — | — | — |

_tools: stopped early — model call failed at question 16: ClientError: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota, please check your plan and _

_baseline: stopped early — generation failed: ClientError: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota, please check your plan and billing details. For more information on_

#### tools — per question

| # | question | expected | got | figure | truth | verdict |
|---|---|---|---|---|---|---|
| 1 | berapa penjualan hari ini? | get_sales_summary | get_sales_summary | 0 | 0 | correct |
| 2 | jualan hari ni brp bos | get_sales_summary | get_sales_summary | 0 | 0 | correct |
| 3 | omzet kemarin brp ya | get_sales_summary | get_sales_summary | 1.485e+06 | 1.485e+06 | correct |
| 4 | hw much did we sell this week | get_sales_summary | get_sales_summary | 4.244e+06 | 4.244e+06 | correct |
| 5 | penjualan minggu ini vs minggu lalu gmn | compare_periods | compare_periods | 4.244e+06 | 4.244e+06 | correct |
| 6 | bulan ni jualan berapa banyak? | get_sales_summary | get_sales_summary | 3.799e+06 | 3.799e+06 | correct |
| 7 | total sales last 30 days pls | get_sales_summary | get_sales_summary | 1.6082e+07 | 1.6082e+07 | correct |
| 8 | untung ga bulan ini? | get_profit | get_profit | 3.799e+06 | 3.799e+06 | correct |
| 9 | am i making money this month | get_profit | get_profit | 408000 | 408000 | correct |
| 10 | pengeluaran bulan ini total brp | get_profit | get_profit | 408000 | 408000 | correct |
| 11 | stok kopi arabica tinggal brp | get_stock | get_stock | 35 | 35 | correct |
| 12 | baki stok gula aren berapa | get_stock | get_stock | 5 | 5 | correct |
| 13 | how many croissant left? | get_stock | get_stock | 12 | 12 | correct |
| 14 | stok apa aja yg mau habis | get_low_stock | get_low_stock | — | — | correct · right tool, list answer |
| 15 | mana yg perlu restock | get_low_stock | get_low_stock | — | — | correct · right tool, list answer |

#### baseline — per question

| # | question | expected | got | figure | truth | verdict |
|---|---|---|---|---|---|---|

### Run “live, text-to-SQL baseline only, first 15 questions (the ones the tools answered), paced 13s — model gemini-3.5-flash” — 2026-09-06 03:47 UTC

Business: Kopi Kenangan Senja. Questions: 30 (20 data, 10 out of scope).

| system | classifier | n | accuracy (data) | refusal rate | **silent-error rate** |
|---|---|---|---|---|---|
| baseline | gemini (text-to-sql) | 15 | 13% | 73% | **13%** |

#### baseline — per question

| # | question | expected | got | figure | truth | verdict |
|---|---|---|---|---|---|---|
| 1 | berapa penjualan hari ini? | get_sales_summary | — | — | 0 | refused · sql error: DBAPIError |
| 2 | jualan hari ni brp bos | get_sales_summary | — | — | 0 | refused · sql error: DBAPIError |
| 3 | omzet kemarin brp ya | get_sales_summary | — | — | 1.485e+06 | refused · sql error: DBAPIError |
| 4 | hw much did we sell this week | get_sales_summary | — | — | 4.244e+06 | refused · sql error: DBAPIError |
| 5 | penjualan minggu ini vs minggu lalu gmn | compare_periods | — | — | 4.244e+06 | refused · sql error: DBAPIError |
| 6 | bulan ni jualan berapa banyak? | get_sales_summary | — | — | 3.799e+06 | refused · sql error: DBAPIError |
| 7 | total sales last 30 days pls | get_sales_summary | — | 1.6351e+07 | 1.6082e+07 | silent_error · answered 1.6351e+07, truth 1.6082e+07 | SELECT COALESCE(SUM(total), 0) AS total_sales FROM orders WHERE sold_at >= NOW() - INTERVA |
| 8 | untung ga bulan ini? | get_profit | — | — | 3.799e+06 | refused · sql error: DBAPIError |
| 9 | am i making money this month | get_profit | — | — | 408000 | refused · sql error: DBAPIError |
| 10 | pengeluaran bulan ini total brp | get_profit | — | — | 408000 | refused · sql error: DBAPIError |
| 11 | stok kopi arabica tinggal brp | get_stock | — | 43 | 35 | silent_error · answered 43, truth 35 | SELECT SUM(current_stock) FROM items WHERE name ILIKE '%arabica%' OR name ILIKE '%arabika% |
| 12 | baki stok gula aren berapa | get_stock | — | 5 | 5 | correct ·  | SELECT current_stock FROM items WHERE name ILIKE '%gula aren%' LIMIT 1 |
| 13 | how many croissant left? | get_stock | — | 12 | 12 | correct ·  | SELECT SUM(current_stock) FROM items WHERE name ILIKE '%croissant%' |
| 14 | stok apa aja yg mau habis | get_low_stock | — | — | — | refused · no number |
| 15 | mana yg perlu restock | get_low_stock | — | — | — | refused · no number |


## Findings (as of 2026-09-06)

Written by hand after the runs above; the run blocks themselves are untouched.

### The silent-error gap, stated plainly

On the **same 15 questions** (the first 15 of the set: 15 data questions — sales, profit,
stock — no out-of-scope ones yet), on the same seeded café, on the same day:

| | tools (what ships) | text-to-SQL baseline |
|---|---|---|
| correct | **15 / 15** | 2 / 15 |
| refused / failed loudly | 0 | 11 |
| **silent errors** | **0 (0%)** | **2 (13%)** |

**Gap: 13 percentage points of silent error, in the baseline's disfavour, on a set where the
tools made no error of any kind.**

The two silent errors are exactly the failure the project is built to prevent, and neither
raised anything:

- *"stok kopi arabica tinggal brp"* → `SELECT SUM(current_stock) FROM items WHERE name ILIKE
  '%arabica%' …` → **43**. The café stocks *Biji Arabica* (8 kg of beans) and *Kopi Arabica*
  (35 cups). The query summed kilograms and cups and returned a confident number that means
  nothing. The tool path returned both items, each with its unit, and the owner reads the one
  they meant.
- *"total sales last 30 days pls"* → `SELECT COALESCE(SUM(total), 0) … FROM orders WHERE
  sold_at >= NOW() - INTERVAL …` → **16.351.000** against a truth of **16.082.000**. It summed
  `orders.total`, which includes the totals of voided and refunded orders — the ledger of
  reversing lines that makes a void net to zero is invisible to a query that never heard of it.
  Plausible to the rupiah, wrong by 269.000.

### What the numbers do and do not say

- **The tool path's 100% is 15 of 30.** The free tier's *daily* cap on `gemini-2.5-flash`
  (about 20 requests, on top of 5 per minute) cut the paced run at question 16, after the
  first unpaced attempt had spent 6 of the day's requests classifying questions 1–6 (all
  correctly; they are the same six the paced run then repeated). Questions 16–30 — including
  all ten out-of-scope questions — have **not yet been classified by the live model**. The
  offline stand-in run refuses all ten, but the stand-in is not a model and proves nothing
  about routing. Re-run with `--offset 15` on a fresh day's quota.
- **The baseline ran on a different model.** `gemini-2.5-flash` had no quota left, so the
  baseline's SQL was written by `gemini-3.5-flash` (the current-generation sibling; the older
  names this key was configured with have been retired). A stronger model for the baseline
  biases the comparison *against* the thesis, not for it.
- **Eleven of the baseline's fifteen queries failed loudly, and most of that is the local
  environment, not the model.** The bundled local Postgres (`scripts/local-pg.py`) ships
  without tzdata, so `AT TIME ZONE 'Asia/Jakarta'` — which the prompt invites and a competent
  query for "today" or "this week" needs — errors out here (`time zone "Asia/Jakarta" not
  recognized`). On a production Postgres those queries would run, and some would be correct;
  others would be plausible and wrong (every day-boundary decision is a chance to be off by
  seven hours). So the baseline's **accuracy is understated** by this environment and its
  **silent-error rate is, if anything, understated too**: a failed query cannot be silently
  wrong. The gap reported above is therefore a floor. Later runs record the SQL error text so
  this can be seen per question.
- The refusal rate is not a score. The tools' 0% here is because none of the first 15
  questions warranted one; the baseline's 73% is failures, not judgement.

### What is settled by this run

- The harness works end to end against the live model, scores what it claims to score, and
  paces itself under the free tier.
- A fixed tool set over one metric registry, asked fifteen real questions in three languages
  with typos, answered every one with the registry's own figure.
- Naive text-to-SQL, asked the same fifteen, produced two answers that were wrong in ways an
  owner could not detect — one by mixing units, one by not knowing what a void is.

### Still to do

- ~~Live-classify questions 16-30~~ **Done 2026-11-23 (16:53 UTC run, model gemini-3.5-flash-lite)**, see the
  two runs below and the summary at the end of this file. The earlier blockers were free-tier quota.
- Run the baseline on a Postgres with tzdata (or Supabase, roadmap M12) so its accuracy is
  measured fairly and its silent-error rate is a ceiling rather than a floor.
- Objective 4 with real people: see "Objective 4: what still needs real participants" at the end.

### Run “live, questions 16-30, tools only, paced 13s — model gemini-3.5-flash-lite” — 2026-11-23 16:53 UTC

Business: Poernama. Questions: 30 (20 data, 10 out of scope).

| system | classifier | n | accuracy (data) | refusal rate | **silent-error rate** |
|---|---|---|---|---|---|
| tools | gemini | 6 | 100% | 17% | **0%** |

_tools: stopped early — model call failed at question 7: ServerError: 503 UNAVAILABLE. {'error': {'code': 503, 'message': 'This model is currently experiencing high demand. Spikes in demand _

#### tools — per question

| # | question | expected | got | figure | truth | verdict |
|---|---|---|---|---|---|---|
| 1 | harga beli terakhir susu uht brp | get_supplier_prices | get_supplier_prices | — | — | correct · right tool, list answer |
| 2 | modal es kopi susu per gelas berapa | get_recipe_cost | get_recipe_cost | 5220 | 5220 | correct |
| 3 | kas kemarin ada selisih ga | get_shift_summary | get_shift_summary | -5000 | -5000 | correct |
| 4 | andi udah belanja brp bulan ini | get_customer_summary | get_customer_summary | 1.449e+06 | 1.449e+06 | correct |
| 5 | promo bogo laku ga bulan ini | get_promo_performance | get_promo_performance | — | — | correct · right tool, list answer |
| 6 | penjualan bulan depan kira2 brp? | refuse | refuse | — | — | refused · out of scope, refused |

### Run “live, questions 22-30, tools only, paced 13s — model gemini-3.5-flash-lite” — 2026-11-23 16:56 UTC

Business: Poernama. Questions: 30 (20 data, 10 out of scope).

| system | classifier | n | accuracy (data) | refusal rate | **silent-error rate** |
|---|---|---|---|---|---|
| tools | gemini | 9 | — | 100% | **0%** |

#### tools — per question

| # | question | expected | got | figure | truth | verdict |
|---|---|---|---|---|---|---|
| 1 | besok hujan ga? mau stok es | refuse | refuse | — | — | refused · out of scope, refused |
| 2 | pajak umkm sy tahun ini brp yg hrs dibayar | refuse | refuse | — | — | refused · out of scope, refused |
| 3 | harga kopi di warung sebelah brp | refuse | refuse | — | — | refused · out of scope, refused |
| 4 | budi absen brp kali bulan ini | refuse | refuse | — | — | refused · out of scope, refused |
| 5 | berapa banyak jualan bulan depan agaknya | refuse | refuse | — | — | refused · out of scope, refused |
| 6 | what will my profit be next year | refuse | refuse | — | — | refused · out of scope, refused |
| 7 | should i raise my prices? | refuse | refuse | — | — | refused · out of scope, refused |
| 8 | berapa gaji kasir yg wajar di jakarta | refuse | refuse | — | — | refused · out of scope, refused |
| 9 | kurs dollar hari ini brp | refuse | refuse | — | — | refused · out of scope, refused |

### M9-T7 RAG run - 2026-11-23 17:04 UTC

Embedding model: gemini-embedding-001; classifier model: gemini-3.5-flash-lite. 10 receipts, 10 answerable + 2 unanswerable questions, top-5, distance cut-off 0.65.

| metric | value |
|---|---|
| precision of returned results | 28% |
| hit@1 | 90% |
| recall | 100% |
| MRR | 0.93 |
| false match on unanswerable | 100% |

| question | relevant | returned (best first) | routed to |
|---|---|---|---|
| kapan terakhir beli susu uht | r1,r4 | r10,r5,r4,r1,r8 | search_history |
| pernah beli gula aren? | r4 | r4,r1,r6,r10,r8 | search_history |
| biasanya beli arabica di mana | r2,r6 | r6,r2,r9,r7,r10 | search_history |
| beli telur dari supplier mana aja | r3,r8 | r8,r3,r10,r5,r7 | search_history |
| have I bought cheese before | r10 | r10,r7,r5,r8,r4 | search_history |
| nota dari Dairy Fresh ada ga | r10,r5 | r10,r5,r8,r4,r1 | search_history |
| kapan terakhir beli gelas plastik | r9 | r9,r1,r4,r10,r5 | search_history |
| pernah beli tepung terigu? | r3 | r3,r8,r1,r10,r4 | search_history |
| harga beli mentega terakhir brp | r8 | r8,r5,r10,r1,r3 | get_supplier_prices |
| beli sayur di mana ya | r7 | r7,r4,r10,r8,r1 | search_history |
| pernah beli daging sapi? | none | r10,r4,r8,r5,r1 | search_history |
| did i ever buy caviar | none | r8,r3,r7,r10,r4 | search_history |


## Summary of the 2026-11-23 runs (what the numbers say)

Model for every number below: **gemini-3.5-flash-lite** (classifier) and **gemini-embedding-001**
(embeddings); the live bot's own model and the 15 questions of the first run
(`gemini-2.5-flash`) are above. Business: the local seeded "Poernama". About 60 model calls in total.

| measurement | result | model | label |
|---|---|---|---|
| Questions 16-30, tool path: 5 data questions | 5 of 5 routed to the right tool with the registry's own figure | flash-lite | measured |
| Questions 16-30: 10 out-of-scope questions | 10 of 10 refused, no figure given | flash-lite | measured |
| Tool path, all 30 questions (1-15 on gemini-2.5-flash, 16-30 on flash-lite) | silent-error rate 0% | mixed, stated per block | measured |
| M9-T7 RAG: 10 answerable questions over 10 receipts | hit@1 90%, recall 100%, MRR 0.93, precision of what is returned 28% | gemini-embedding-001 | measured |
| M9-T7: 2 unanswerable questions (daging sapi, caviar) | **100% false match**: the search returned 5 unrelated receipts both times | gemini-embedding-001 | measured |
| Routing of the 12 RAG questions by the classifier | 11 to `search_history`, 1 ("harga beli mentega terakhir") to `get_supplier_prices` (defensible) | flash-lite | measured |

What this does and does not say:

- The out-of-scope result is the one that supports the thesis: ten questions with no answer in the data
  produced ten refusals and no invented number. The run had a transient 503 ("high demand") from the model
  after question 21, so questions 16-21 and 22-30 are two blocks; the harness now retries on 503.
- **RAG finds the right receipt first but cannot say "nothing found".** The distance cut-off (0.65) never
  removes anything with only ten receipts, so every query returns five receipts and precision is capped at
  about 20-40%. Both unanswerable questions got five confident-looking results. The tool's own wording
  ("No matching receipts") is therefore never reached on this data. Guess (not measured): a tighter cut-off
  of roughly 0.35-0.45 would fix it; test by printing each result's `distance` for relevant and irrelevant
  hits (it is in the result metadata) and choosing the gap. `services/rag.py` was not changed in this run.
- The set is small (10 + 2 questions, 10 hand-written receipts): the figures are indications, not
  statistics. Receipts are synthetic, not Ibu Diah's.
- Quota: embeddings and flash-lite are on the shared free key; no run was made on Thu 26 Nov.

## Objective 4: what still needs real participants

| CP1 §3.7-3.8 item | Built | Still needed |
|---|---|---|
| Task-success rate | `scripts/eval_task_success.py` (error reply or a repeat within 2 minutes = failure), tested | Real WhatsApp use by the owner and Ibu Diah for 2-4 weeks, then run it with `--phone`. The local database has no real traffic, so **no rate is reported**. |
| SUS questionnaire (target 72) | `docs/sus-questionnaire.md` (Indonesian + English), `scripts/sus_score.py`, tested | Ibu Diah (and any cashier) fills it in; ask her to read the Indonesian first. Report n. |
| Informed consent | `docs/consent-text.md`, confirmed by SETUJU on WhatsApp or on paper | Fill in the researcher's name; send before the trial starts; keep the reply. |
| Trial (2-4 weeks) | nothing to build | The trial itself; **not possible to finish before 26 Nov**, so the report must say the trial is ongoing or short. |
| Real receipts for the RAG set | synthetic set | Optional: re-run `python -m app.eval.rag` after Ibu Diah's receipts are in the live database. |

## RAG cut-off sweep and decision (fin-rag-cutoff) - 2026-11-23

Why: the first run returned five receipts for questions with no answer (100% false match). The bot can then
cite a receipt that does not exist, the silent error the thesis argues against. Model: **gemini-embedding-001**
(no classifier call). 10 synthetic receipts, 10 answerable + 12 unanswerable questions (things never bought,
suppliers never used), top-5. 32 embedding calls, paced 7 s. Distances are cosine distance (0 = identical).

Cut-off sweep over the same retrieved distances (22 questions, 10 receipts, 32 embedding calls):

| cut-off | hit@1 (answerable) | recall | precision | unanswerable rejected |
|---|---|---|---|---|
| 0.30 | 30% | 30% | 27% | 83% |
| 0.35 | 80% | 85% | 71% | 75% |
| 0.40 | 80% | 90% | 49% | 25% |
| 0.45 | 90% | 100% | 28% | 0% |
| 0.50 | 90% | 100% | 28% | 0% |
| 0.55 | 90% | 100% | 28% | 0% |
| 0.60 | 90% | 100% | 28% | 0% |
| 0.65 | 90% | 100% | 28% | 0% |

Distances (best first), A = answerable, U = unanswerable:

- A kapan terakhir beli susu uht: r10=0.31, r5=0.32, r4=0.33, r1=0.33, r8=0.38
- A pernah beli gula aren?: r4=0.30, r1=0.36, r6=0.40, r10=0.41, r8=0.41
- A biasanya beli arabica di mana: r6=0.31, r2=0.31, r9=0.41, r7=0.42, r10=0.42
- A beli telur dari supplier mana aja: r8=0.32, r3=0.36, r10=0.38, r5=0.39, r7=0.40
- A have I bought cheese before: r10=0.40, r7=0.43, r5=0.43, r8=0.44, r4=0.44
- A nota dari Dairy Fresh ada ga: r10=0.19, r5=0.20, r8=0.29, r4=0.31, r1=0.31
- A kapan terakhir beli gelas plastik: r9=0.30, r1=0.40, r4=0.40, r10=0.41, r5=0.41
- A pernah beli tepung terigu?: r3=0.30, r8=0.38, r1=0.38, r10=0.39, r4=0.40
- A harga beli mentega terakhir brp: r8=0.28, r5=0.33, r10=0.33, r1=0.34, r3=0.34
- A beli sayur di mana ya: r7=0.34, r4=0.41, r10=0.42, r8=0.42, r1=0.42
- U pernah beli daging sapi?: r10=0.38, r4=0.40, r8=0.41, r5=0.41, r1=0.41
- U did i ever buy caviar: r8=0.44, r3=0.45, r7=0.46, r10=0.46, r4=0.46
- U pernah beli ikan salmon?: r10=0.39, r7=0.40, r8=0.40, r5=0.41, r3=0.42
- U nota dari Toko Bangunan Jaya ada ga: r9=0.27, r1=0.28, r4=0.28, r8=0.28, r3=0.28
- U did we ever buy printer paper: r9=0.42, r6=0.43, r2=0.44, r4=0.44, r8=0.44
- U kapan terakhir beli beras: r4=0.37, r3=0.37, r8=0.37, r1=0.37, r10=0.38
- U beli minyak goreng di mana: r8=0.40, r4=0.41, r7=0.41, r10=0.42, r1=0.42
- U pernah order dari PT Samudra Frozen?: r10=0.39, r5=0.40, r3=0.41, r8=0.42, r1=0.42
- U have I bought vanilla syrup before: r4=0.42, r6=0.43, r1=0.43, r2=0.44, r8=0.45
- U harga beli ayam terakhir brp: r8=0.32, r10=0.35, r3=0.35, r7=0.36, r5=0.36
- U nota dari CV Sumber Rejeki ada ga: r10=0.29, r6=0.29, r8=0.30, r5=0.30, r2=0.31
- U pernah beli teh celup?: r6=0.39, r9=0.39, r2=0.40, r3=0.40, r7=0.41


**Decision: `MAX_DISTANCE` 0.65 -> 0.35** in `backend/app/services/rag.py` (pinned by `tests/test_rag_cutoff.py`).

- 0.30 loses 70% of the right answers; 0.40 and above reject at most 25% of the unanswerable questions;
  0.35 is the knee: **75% of unanswerable questions now return nothing** (false match 100% -> 25%) while
  hit@1 on answerable questions is 80% (was 90%: "have I bought cheese before", distance 0.40, is now missed)
  and recall 85%. Precision of what is returned rises from 28% to 71%. Measured on this set.
- **A distance cut-off alone cannot separate the two groups.** Answerable top hits sit at 0.19-0.40,
  unanswerable ones at 0.27-0.46: they overlap. The three unanswerable questions still matched at 0.35 are the
  supplier-name ones ("nota dari Toko Bangunan Jaya", "CV Sumber Rejeki") and "harga beli ayam": the embedding
  rates a made-up supplier close to any receipt that has a supplier line. Not fixed here; **guess (not measured):**
  a lexical check that the asked item or supplier word occurs in the returned receipt text would remove them.
  Test by adding that check to `search_receipts` and re-running `python -m app.eval.rag --sweep`.
- The set is small and synthetic; the right value on Ibu Diah's real receipts (more of them, real photos' text)
  may differ. Re-run the sweep once her receipts are in the live database.
- The earlier result table above ("cut-off 0.65") stays as the before-picture.

## RAG lexical guard and final cut-off (fin-rag-lexical) - 2026-11-23

Why: a distance cut-off alone still left 25% of the unanswerable questions matched at the best value (0.35)
and cost 10 points of hit@1: the embedding rates a made-up supplier close to any receipt that has a supplier
line. The guard: a receipt is accepted only if **every key word of the question** (item or supplier; question
words like "pernah beli", "kapan terakhir", "nota dari" dropped) occurs in that receipt's text, with case and
accents ignored, one-letter spelling slips and plural forms tolerated (`sayur`/`sayuran`, `arabika`/`arabica`),
and a small English/category synonym list (`cheese` -> `keju`, `sayur` -> `selada`, `tomat`, ...). A question
that names nothing is not checked. Code: `key_words`, `mentions` in `backend/app/services/rag.py`; tests
`tests/test_rag_lexical.py`, `tests/test_rag_cutoff.py`. Model: **gemini-embedding-001**; same 10 receipts, 10
answerable + 12 unanswerable questions, top-5, 32 embedding calls (7 s pace). "(x)" marks a result the guard
removes.

Cut-off sweep over the same retrieved distances (22 questions, 10 receipts, 32 embedding calls):

| cut-off | alone: hit@1 | recall | false match | + lexical: hit@1 | recall | precision | false match |
|---|---|---|---|---|---|---|---|
| 0.30 | 30% | 30% | 17% | 30% | 30% | 30% | 0% |
| 0.35 | 80% | 85% | 25% | 90% | 85% | 90% | 0% |
| 0.40 | 80% | 90% | 75% | 90% | 90% | 90% | 0% |
| 0.45 | 90% | 100% | 100% | 100% | 100% | 100% | 0% |
| 0.50 | 90% | 100% | 100% | 100% | 100% | 100% | 0% |
| 0.55 | 90% | 100% | 100% | 100% | 100% | 100% | 0% |
| 0.60 | 90% | 100% | 100% | 100% | 100% | 100% | 0% |
| 0.65 | 90% | 100% | 100% | 100% | 100% | 100% | 0% |

Distances (best first), A = answerable, U = unanswerable, (x) = key word missing from that receipt:

- A kapan terakhir beli susu uht: r10=0.31(x), r5=0.32(x), r4=0.33, r1=0.33, r8=0.38(x)
- A pernah beli gula aren?: r4=0.30, r1=0.36(x), r6=0.40(x), r10=0.41(x), r8=0.41(x)
- A biasanya beli arabica di mana: r6=0.31, r2=0.31, r9=0.41(x), r7=0.42(x), r10=0.42(x)
- A beli telur dari supplier mana aja: r8=0.32, r3=0.36, r10=0.38(x), r5=0.39(x), r7=0.40(x)
- A have I bought cheese before: r10=0.40, r7=0.43(x), r5=0.43(x), r8=0.44(x), r4=0.44(x)
- A nota dari Dairy Fresh ada ga: r10=0.19, r5=0.20, r8=0.29(x), r4=0.31(x), r1=0.31(x)
- A kapan terakhir beli gelas plastik: r9=0.30, r1=0.40(x), r4=0.40(x), r10=0.41(x), r5=0.41(x)
- A pernah beli tepung terigu?: r3=0.30, r8=0.38(x), r1=0.38(x), r10=0.39(x), r4=0.40(x)
- A harga beli mentega terakhir brp: r8=0.28, r5=0.33(x), r10=0.33(x), r1=0.34(x), r3=0.34(x)
- A beli sayur di mana ya: r7=0.34, r4=0.41(x), r10=0.42(x), r8=0.42(x), r1=0.42(x)
- U pernah beli daging sapi?: r10=0.38(x), r4=0.40(x), r8=0.41(x), r5=0.41(x), r1=0.41(x)
- U did i ever buy caviar: r8=0.44(x), r3=0.45(x), r7=0.46(x), r10=0.46(x), r4=0.46(x)
- U pernah beli ikan salmon?: r10=0.39(x), r7=0.40(x), r8=0.40(x), r5=0.41(x), r3=0.42(x)
- U nota dari Toko Bangunan Jaya ada ga: r9=0.27(x), r1=0.28(x), r4=0.28(x), r8=0.28(x), r3=0.28(x)
- U did we ever buy printer paper: r9=0.42(x), r6=0.43(x), r2=0.44(x), r4=0.44(x), r8=0.44(x)
- U kapan terakhir beli beras: r4=0.37(x), r3=0.37(x), r8=0.37(x), r1=0.37(x), r10=0.38(x)
- U beli minyak goreng di mana: r8=0.40(x), r4=0.41(x), r7=0.41(x), r10=0.42(x), r1=0.42(x)
- U pernah order dari PT Samudra Frozen?: r10=0.39(x), r5=0.40(x), r3=0.41(x), r8=0.42(x), r1=0.42(x)
- U have I bought vanilla syrup before: r4=0.42(x), r6=0.43(x), r1=0.43(x), r2=0.44(x), r8=0.45(x)
- U harga beli ayam terakhir brp: r8=0.32(x), r10=0.35(x), r3=0.35(x), r7=0.36(x), r5=0.36(x)
- U nota dari CV Sumber Rejeki ada ga: r10=0.29(x), r6=0.29(x), r8=0.30(x), r5=0.30(x), r2=0.31(x)
- U pernah beli teh celup?: r6=0.39(x), r9=0.39(x), r2=0.40(x), r3=0.40(x), r7=0.41(x)


**Decision: `MAX_DISTANCE` 0.35 -> 0.45, lexical guard on.** From 0.45 up the guarded result is identical: hit@1,
recall and precision 100%, false match on unanswerable **0%** (cut-off alone at 0.45: 100% false match; at its
best, 0.35: hit@1 80%, recall 85%, 25% false match). The guard does the rejecting; the cut-off only bounds
semantic noise, and 0.45 recovers "have I bought cheese before" (distance 0.40).

Caveats (labelled): **measured** on this set only; the set is 10 synthetic receipts and 22 questions.
**Guess:** the stop-word and synonym lists were written with these question styles in mind (the `cheese` and
`sayur` synonyms were needed for two answerable questions), so 100% is partly fitted; real owner questions with
other words ("susu kental manis" for a receipt that says "SKM") will be missed (a wrongly refused question, the
safe direction), and the guard checks words, not meaning ("pernah beli susu" will cite any receipt with "susu").
Test by re-running the sweep on Ibu Diah's real receipts and questions once they exist, and widen `_SYNONYMS`
from the misses.

## Held-out result (fin-rag-heldout) - 2026-11-24

Why: the 100% above was measured on the same 22 questions the cut-off and the lexical guard's word lists were
tuned on. A second agent wrote 30 new questions blind (`backend/app/eval/rag_heldout.py`: 15 answerable, 15
unanswerable, same 10 receipts), without reading the first set or `rag.py`. Run once with the **current settings,
not retuned**: cut-off 0.45 + lexical guard. Command: `python -m app.eval.rag --heldout --sweep`.
Model: **gemini-embedding-001**, 40 embedding calls (10 receipts + 30 questions, 7 s pace), no generate calls.

| set | settings | hit@1 | recall | precision | false match (unanswerable) |
|---|---|---|---|---|---|
| tuned set (22 q, above) | 0.45 + guard | 100% | 100% | 100% | 0% |
| **held-out set (30 q)** | **0.45 + guard** | **0%** | **0%** | **0%** | **0%** |
| held-out set | 0.45, guard off | 100% | 100% | (not separating) | 100% |
| held-out set | 0.30, guard off (best cut-off alone) | 93% | 93% | - | 73% |

What it says (measured on this set):

- **The guard rejects every receipt on realistic phrasing.** It needs *every* key word of the question in the
  receipt text, and the stop-word and synonym lists were written for the tuning questions. Blind questions
  carry words no receipt contains: filler (`bareng`, `tolong`, `cariin`, `please`, `and`), dates (`oktober`,
  `2026`, `tgl 26 okt`), amounts (`765rb`, `sejuta seratus lima puluh ribu`) and untranslated English
  (`white sugar`, `palm sugar`, `lettuce`, `wheat flour`). Example: "nota Toko Sinar Jaya tanggal 2 Oktober
  2026 tolong cariin" keeps `sinar jaya tanggal oktober 2026 cariin`; the right receipt (r1) fails on
  `tanggal`, `oktober`, `cariin`. So the bot answers "no receipt found" to all 30: safe (it never cites a
  wrong receipt) but useless for the answerable half.
- **The embedding ranking itself is good:** with the guard off, the right receipt is first for 15 of 15
  answerable questions. But it also returns receipts for every unanswerable one (100% false match at 0.45;
  73% even at 0.30), because near-misses ("gula batu di Sinar Jaya", "Toko Sinar Baru", "CV Kopi Nusani") sit
  as close as true answers (best distance 0.21-0.35 for unanswerable, 0.17-0.30 for answerable). Distance alone cannot separate them, here too.
- **So the tuned-set 100% was overfitted.** The honest numbers for the report are the held-out row: the
  current RAG never cites a wrong receipt, and finds the right one only when the question uses the receipt's
  own words (as in the demo: "pernah beli gula aren dari toko manis?").
- Not changed here, on purpose: retuning on this set would make it a tuning set too. **Guess (not measured):**
  a softer rule (at least one item-or-supplier word must match, dates and amounts parsed as filters, filler
  words ignored) would recover most answerable questions; it needs a NEW blind set to be measured honestly.

<details><summary>Held-out run output (per question, sweep, distances)</summary>

### M9-T7 RAG run, blind held-out set - 2026-11-23 19:59 UTC

Embedding model: gemini-embedding-001; classifier model: not called (retrieval only). 10 receipts, 15 answerable + 15 unanswerable questions, top-5, distance cut-off 0.45 + lexical guard.

| metric | value |
|---|---|
| precision of returned results | 0% |
| hit@1 | 0% |
| recall | 0% |
| MRR | 0.00 |
| false match on unanswerable | 0% |

| question | relevant | returned (best first) | routed to |
|---|---|---|---|
| nota white sugar yg bareng susu kotak itu mana ya? | r1 | none | - |
| ada bon biji kopi dua jenis, arabika sama robusta, awal oktober 2026? | r2 | none | - |
| Find the wheat flour and eggs purchase from 9 October 2026 please | r3 | none | - |
| yg beli palm sugar 5 kilo di Sinar Jaya, bonnya masih ada? | r4 | none | - |
| cariin pembelian fresh milk sekalian creamer dong | r5 | none | - |
| bon arabika tgl 26 okt 2026 yg 765rb itu | r6 | none | - |
| Where's the receipt for lettuce and tomatoes from Pasar Induk? | r7 | none | - |
| mentega sm telor awal nov 2026 beli di mana ya, ada notanya? | r8 | none | - |
| need the bon for disposable cups + straws, seribu cup dua ribu straw | r9 | none | - |
| bon cheese 2 kilo barengan susu dari Dairy Fresh ada gak? | r10 | none | - |
| nota Toko Sinar Jaya tanggal 2 Oktober 2026 tolong cariin | r1 | none | - |
| Kopi Nusantara yg totalnya sejuta seratus lima puluh ribu, kirim bonnya dong | r2 | none | - |
| cari struk belanja 580rb di Maju Makmur bulan oktober 2026 | r3 | none | - |
| the Dairy Fresh invoice on October 20, 2026, can you find it? | r5 | none | - |
| yg abis 140 rebu buat sayur di pasar induk, itu nota mana? | r7 | none | - |
| pernah beli gula batu di Sinar Jaya? cariin notanya | none | none | - |
| ada bon oat milk dari Dairy Fresh gak? | none | none | - |
| invoice kopi liberika di Kopi Nusantara yg dulu itu mana | none | none | - |
| tepung beras dari UD Maju Makmur ada struknya? | none | none | - |
| Find our mozzarella receipt from PT Dairy Fresh | none | none | - |
| bon gelas kertas seribu pcs dr Kemasan Prima ada? | none | none | - |
| selada yg kita beli di Pasar Indah, tolong cari bonnya | none | none | - |
| nota gula pasir dr Toko Sinar Baru ada ga | none | none | - |
| did we buy robusta beans from CV Kopi Nusani? show the receipt | none | none | - |
| cariin bon gula aren dari UD Maju Makmur | none | none | - |
| ada nota telur dari Toko Sinar Jaya? | none | none | - |
| fresh milk dari CV Kopi Nusantara, invoice-nya mana ya? | none | none | - |
| bon sedotan dari Pasar Induk dong | none | none | - |
| pernah restok sirup vanila buat latte? cari nota belinya | none | none | - |
| got a receipt for dishwashing liquid from Toko Bersih Sentosa? | none | none | - |

Cut-off sweep over the same retrieved distances (30 questions, 10 receipts, 40 embedding calls):

| cut-off | alone: hit@1 | recall | false match | + lexical: hit@1 | recall | precision | false match |
|---|---|---|---|---|---|---|---|
| 0.30 | 93% | 93% | 73% | 0% | 0% | 0% | 0% |
| 0.35 | 100% | 100% | 100% | 0% | 0% | 0% | 0% |
| 0.40 | 100% | 100% | 100% | 0% | 0% | 0% | 0% |
| 0.45 | 100% | 100% | 100% | 0% | 0% | 0% | 0% |
| 0.50 | 100% | 100% | 100% | 0% | 0% | 0% | 0% |
| 0.55 | 100% | 100% | 100% | 0% | 0% | 0% | 0% |
| 0.60 | 100% | 100% | 100% | 0% | 0% | 0% | 0% |
| 0.65 | 100% | 100% | 100% | 0% | 0% | 0% | 0% |

Distances (best first), A = answerable, U = unanswerable, (x) = key word missing from that receipt:

- A nota white sugar yg bareng susu kotak itu mana ya?: r1=0.30(x), r4=0.32(x), r5=0.32(x), r10=0.32(x), r8=0.36(x)
- A ada bon biji kopi dua jenis, arabika sama robusta, awal oktober 2026?: r2=0.21(x), r6=0.24(x), r9=0.34(x), r7=0.34(x), r5=0.35(x)
- A Find the wheat flour and eggs purchase from 9 October 2026 please: r3=0.28(x), r8=0.31(x), r1=0.32(x), r4=0.33(x), r5=0.33(x)
- A yg beli palm sugar 5 kilo di Sinar Jaya, bonnya masih ada?: r4=0.25(x), r1=0.27(x), r8=0.35(x), r6=0.35(x), r3=0.36(x)
- A cariin pembelian fresh milk sekalian creamer dong: r5=0.25(x), r10=0.27(x), r1=0.32(x), r4=0.32(x), r8=0.33(x)
- A bon arabika tgl 26 okt 2026 yg 765rb itu: r6=0.19(x), r2=0.24(x), r5=0.31(x), r9=0.32(x), r7=0.32(x)
- A Where's the receipt for lettuce and tomatoes from Pasar Induk?: r7=0.22(x), r10=0.32(x), r5=0.33(x), r8=0.33(x), r1=0.34(x)
- A mentega sm telor awal nov 2026 beli di mana ya, ada notanya?: r8=0.20(x), r3=0.26(x), r10=0.27(x), r5=0.27(x), r7=0.29(x)
- A need the bon for disposable cups + straws, seribu cup dua ribu straw: r9=0.25(x), r2=0.31(x), r5=0.32(x), r4=0.33(x), r1=0.33(x)
- A bon cheese 2 kilo barengan susu dari Dairy Fresh ada gak?: r10=0.28(x), r5=0.30(x), r4=0.37(x), r1=0.37(x), r8=0.39(x)
- A nota Toko Sinar Jaya tanggal 2 Oktober 2026 tolong cariin: r1=0.17(x), r4=0.18(x), r5=0.22(x), r8=0.23(x), r6=0.23(x)
- A Kopi Nusantara yg totalnya sejuta seratus lima puluh ribu, kirim bonnya dong: r2=0.21(x), r6=0.23(x), r9=0.27(x), r5=0.29(x), r10=0.29(x)
- A cari struk belanja 580rb di Maju Makmur bulan oktober 2026: r3=0.22(x), r8=0.25(x), r4=0.26(x), r1=0.27(x), r5=0.29(x)
- A the Dairy Fresh invoice on October 20, 2026, can you find it?: r5=0.25(x), r10=0.27(x), r4=0.32(x), r1=0.32(x), r7=0.33(x)
- A yg abis 140 rebu buat sayur di pasar induk, itu nota mana?: r7=0.21(x), r10=0.29(x), r8=0.30(x), r1=0.30(x), r5=0.30(x)
- U pernah beli gula batu di Sinar Jaya? cariin notanya: r1=0.23(x), r4=0.23(x), r8=0.30(x), r3=0.31(x), r6=0.33(x)
- U ada bon oat milk dari Dairy Fresh gak?: r5=0.29(x), r10=0.30(x), r4=0.37(x), r1=0.38(x), r7=0.40(x)
- U invoice kopi liberika di Kopi Nusantara yg dulu itu mana: r2=0.24(x), r6=0.25(x), r10=0.34(x), r9=0.34(x), r4=0.34(x)
- U tepung beras dari UD Maju Makmur ada struknya?: r3=0.33(x), r8=0.37(x), r1=0.42(x), r4=0.42(x), r10=0.43(x)
- U Find our mozzarella receipt from PT Dairy Fresh: r10=0.29(x), r5=0.31(x), r8=0.38(x), r3=0.39(x), r1=0.40(x)
- U bon gelas kertas seribu pcs dr Kemasan Prima ada?: r9=0.22(x), r2=0.33(x), r6=0.33(x), r5=0.34(x), r1=0.35(x)
- U selada yg kita beli di Pasar Indah, tolong cari bonnya: r7=0.24(x), r10=0.34(x), r8=0.34(x), r6=0.35(x), r5=0.35(x)
- U nota gula pasir dr Toko Sinar Baru ada ga: r1=0.21(x), r4=0.22(x), r8=0.27(x), r3=0.28(x), r6=0.29(x)
- U did we buy robusta beans from CV Kopi Nusani? show the receipt: r2=0.23(x), r6=0.24(x), r9=0.33(x), r7=0.34(x), r10=0.34(x)
- U cariin bon gula aren dari UD Maju Makmur: r4=0.32(x), r3=0.33(x), r8=0.34(x), r1=0.37(x), r10=0.42(x)
- U ada nota telur dari Toko Sinar Jaya?: r1=0.22(x), r8=0.23(x), r4=0.23(x), r3=0.26(x), r10=0.29(x)
- U fresh milk dari CV Kopi Nusantara, invoice-nya mana ya?: r10=0.25(x), r5=0.25(x), r2=0.26(x), r6=0.26(x), r4=0.30(x)
- U bon sedotan dari Pasar Induk dong: r7=0.35(x), r9=0.36(x), r4=0.39(x), r10=0.39(x), r5=0.40(x)
- U pernah restok sirup vanila buat latte? cari nota belinya: r2=0.30(x), r6=0.30(x), r1=0.30(x), r4=0.31(x), r5=0.31(x)
- U got a receipt for dishwashing liquid from Toko Bersih Sentosa?: r10=0.32(x), r1=0.32(x), r5=0.32(x), r9=0.33(x), r4=0.33(x)

</details>

## Redesign: word match as a fast accept, a model check for the rest (fin-rag-judge) - 2026-11-24

The story so far: tuned set 100% -> blind held-out set 0% recall. The every-word guard was **overfitted** to
the questions it was written for. Design rule for the redesign (set before looking at any new numbers): the
check must not depend on how the owner words a question, so no new word lists.

How a match is now verified (`verify` / `judge` in `backend/app/services/rag.py`):

1. The vector search returns the top 5 receipts; anything beyond cosine distance 0.45 is dropped (unchanged).
2. **Fast accept:** a receipt that contains every key word of the question is cited without a model call
   (the old guard, now only a shortcut that can say yes, never no).
3. **Judge:** all other candidates go to **one** model call (`gemini-3.5-flash-lite` first, then the usual
   fallback chain) that answers true/false per receipt: is this a purchase the question asks about, with every
   stated detail (supplier, date, amount, variety) matching? If the judge fails (503 past the whole chain,
   network, malformed answer) nothing is cited AND the owner is told plainly that receipts cannot be checked
   right now ("Maaf, catatan nota belum bisa kucek sekarang ... coba tanya lagi"), never a silent
   "no receipt found"; an exhausted quota gets the existing quota message.

All candidates within the cut-off are judged in the same single call (not only the top 1-3), because a
question like "beli telur dari supplier mana aja" has two right receipts; it costs no extra call.

| set | how it was used | hit@1 | recall | precision | false match | judge calls / question |
|---|---|---|---|---|---|---|
| tuned (22 q) | tuned on (cut-off, old word lists) | 100% | 100% | 100% | 0% | 1.00 |
| held-out 1 (30 q) | **seen once during the redesign** (it showed the 0%) | 100% | 100% | 100% | 7% (1 of 15) | 1.00 |
| **held-out 2 (30 q)** | **untouched: written by another agent, opened and scored once after the design was committed, nothing changed after** | **73%** | **73%** | **73%** | **0%** | 1.00 |

Measured on gemini-embedding-001 + the judge, 2026-11-24, `python -m app.eval.rag [--heldout]`.

- **Recall on unseen phrasing is back**: every answerable held-out question (dates, amounts, English,
  slang) gets its one right receipt, first.
- **The one false match** is "selada yg kita beli di Pasar Indah" -> the Pasar Induk receipt: the judge
  accepted a supplier name that only looks alike, although its instructions name that case. Not fixed: a fix
  aimed at this one question would be tuning on this set.
- **Cost:** in practice one extra model call per receipt question (1.00 per question on both sets: among 5
  neighbours there is almost always one without the words). On the free tier that is one more request
  against the 15/min of gemini-3.5-flash-lite; the owner's question already costs two.
- **Caveat (measured):** 2 of the 30 held-out judge calls failed (gemini-3.5-flash-lite and
  gemini-3.1-flash-lite 503 "high demand", the rest of the chain at its daily or per-minute limit) and cited
  nothing (in that run the bot would have stayed silent about it; since then it says it cannot check). Both fell on unanswerable questions (a failure on an answerable one would have lost its receipt:
  none of them is word-matched, and recall stayed 100%), so 2 of the 14 correct rejections are the safe
  failure path, not the judge's verdict.
- The demo question ("pernah beli gula aren dari toko manis?") is word-matched; in the demo cafe both
  receipts are Toko Manis ones, so it needs no judge call.

<details><summary>Redesign runs, per question (tuned set, held-out 1)</summary>

### M9-T7 RAG run - 2026-11-23 20:34 UTC

Embedding model: gemini-embedding-001; classifier model: not called (retrieval only). 10 receipts, 10 answerable + 12 unanswerable questions, top-5, distance cut-off 0.45, verified by word match else one judge call (gemini-3.5-flash-lite first): 22 judge calls, 1.00 per question.

| metric | value |
|---|---|
| precision of returned results | 100% |
| hit@1 | 100% |
| recall | 100% |
| MRR | 1.00 |
| false match on unanswerable | 0% |

| question | relevant | returned (best first) | routed to | judge calls |
|---|---|---|---|---|
| kapan terakhir beli susu uht | r1,r4 | r4,r1 | - | 1 |
| pernah beli gula aren? | r4 | r4 | - | 1 |
| biasanya beli arabica di mana | r2,r6 | r6,r2 | - | 1 |
| beli telur dari supplier mana aja | r3,r8 | r8,r3 | - | 1 |
| have I bought cheese before | r10 | r10 | - | 1 |
| nota dari Dairy Fresh ada ga | r10,r5 | r10,r5 | - | 1 |
| kapan terakhir beli gelas plastik | r9 | r9 | - | 1 |
| pernah beli tepung terigu? | r3 | r3 | - | 1 |
| harga beli mentega terakhir brp | r8 | r8 | - | 1 |
| beli sayur di mana ya | r7 | r7 | - | 1 |
| pernah beli daging sapi? | none | none | - | 1 |
| did i ever buy caviar | none | none | - | 1 |
| pernah beli ikan salmon? | none | none | - | 1 |
| nota dari Toko Bangunan Jaya ada ga | none | none | - | 1 |
| did we ever buy printer paper | none | none | - | 1 |
| kapan terakhir beli beras | none | none | - | 1 |
| beli minyak goreng di mana | none | none | - | 1 |
| pernah order dari PT Samudra Frozen? | none | none | - | 1 |
| have I bought vanilla syrup before | none | none | - | 1 |
| harga beli ayam terakhir brp | none | none | - | 1 |
| nota dari CV Sumber Rejeki ada ga | none | none | - | 1 |
| pernah beli teh celup? | none | none | - | 1 |
### M9-T7 RAG run, blind held-out set - 2026-11-23 20:38 UTC

Embedding model: gemini-embedding-001; classifier model: not called (retrieval only). 10 receipts, 15 answerable + 15 unanswerable questions, top-5, distance cut-off 0.45, verified by word match else one judge call (gemini-3.5-flash-lite first): 30 judge calls, 1.00 per question.

| metric | value |
|---|---|
| precision of returned results | 100% |
| hit@1 | 100% |
| recall | 100% |
| MRR | 1.00 |
| false match on unanswerable | 7% |

| question | relevant | returned (best first) | routed to | judge calls |
|---|---|---|---|---|
| nota white sugar yg bareng susu kotak itu mana ya? | r1 | r1 | - | 1 |
| ada bon biji kopi dua jenis, arabika sama robusta, awal oktober 2026? | r2 | r2 | - | 1 |
| Find the wheat flour and eggs purchase from 9 October 2026 please | r3 | r3 | - | 1 |
| yg beli palm sugar 5 kilo di Sinar Jaya, bonnya masih ada? | r4 | r4 | - | 1 |
| cariin pembelian fresh milk sekalian creamer dong | r5 | r5 | - | 1 |
| bon arabika tgl 26 okt 2026 yg 765rb itu | r6 | r6 | - | 1 |
| Where's the receipt for lettuce and tomatoes from Pasar Induk? | r7 | r7 | - | 1 |
| mentega sm telor awal nov 2026 beli di mana ya, ada notanya? | r8 | r8 | - | 1 |
| need the bon for disposable cups + straws, seribu cup dua ribu straw | r9 | r9 | - | 1 |
| bon cheese 2 kilo barengan susu dari Dairy Fresh ada gak? | r10 | r10 | - | 1 |
| nota Toko Sinar Jaya tanggal 2 Oktober 2026 tolong cariin | r1 | r1 | - | 1 |
| Kopi Nusantara yg totalnya sejuta seratus lima puluh ribu, kirim bonnya dong | r2 | r2 | - | 1 |
| cari struk belanja 580rb di Maju Makmur bulan oktober 2026 | r3 | r3 | - | 1 |
| the Dairy Fresh invoice on October 20, 2026, can you find it? | r5 | r5 | - | 1 |
| yg abis 140 rebu buat sayur di pasar induk, itu nota mana? | r7 | r7 | - | 1 |
| pernah beli gula batu di Sinar Jaya? cariin notanya | none | none | - | 1 |
| ada bon oat milk dari Dairy Fresh gak? | none | none | - | 1 |
| invoice kopi liberika di Kopi Nusantara yg dulu itu mana | none | none | - | 1 |
| tepung beras dari UD Maju Makmur ada struknya? | none | none | - | 1 |
| Find our mozzarella receipt from PT Dairy Fresh | none | none | - | 1 |
| bon gelas kertas seribu pcs dr Kemasan Prima ada? | none | none | - | 1 |
| selada yg kita beli di Pasar Indah, tolong cari bonnya | none | r7 | - | 1 |
| nota gula pasir dr Toko Sinar Baru ada ga | none | none | - | 1 |
| did we buy robusta beans from CV Kopi Nusani? show the receipt | none | none | - | 1 |
| cariin bon gula aren dari UD Maju Makmur | none | none | - | 1 |
| ada nota telur dari Toko Sinar Jaya? | none | none | - | 1 |
| fresh milk dari CV Kopi Nusantara, invoice-nya mana ya? | none | none | - | 1 |
| bon sedotan dari Pasar Induk dong | none | none | - | 1 |
| pernah restok sirup vanila buat latte? cari nota belinya | none | none | - | 1 |
| got a receipt for dishwashing liquid from Toko Bersih Sentosa? | none | none | - | 1 |

</details>

## Held-out 2: the final, untouched number (fin-rag-heldout2) - 2026-11-24

A second blind set (`backend/app/eval/rag_heldout2.py`, 15 answerable + 15 unanswerable, written by another agent
and kept closed until `[fin-rag-judge]` was committed) was scored **exactly once** with
`python -m app.eval.rag --questions app.eval.rag_heldout2 --no-append`, 2026-11-24 04:31 WIB. Nothing was
changed after it. **This is the number for the report.**

| metric | held-out 2 |
|---|---|
| hit@1 | **73%** (11 of 15) |
| recall | **73%** |
| precision of returned results | 73% (every returned receipt was right; the 4 misses returned nothing) |
| false match on unanswerable | **0%** (0 of 15) |
| extra model calls | 1.00 judge call per question |

What the 4 misses were (read from the run below and its log):

- **3 of the 4 were the judge failing, not judging wrongly.** The free-tier models were overloaded at that hour
  (12 × 503, 2 × 504, 3 × daily 429 across the chain); 8 of the 30 judge calls failed, 3 of them on answerable
  questions (r1 "gula putih 10 kilo", r6 "arabika Rp765.000", r8 "maju makmur butter 5 kilo"). On the live bot
  these get "Maaf, catatan nota belum bisa kucek sekarang … coba tanya lagi", not "no receipt".
- **1 of the 4 is a real miss:** "struk belanja 9/10/2026 yg total lima ratus delapan puluh ribu" (r3) names
  no item or supplier, only a date and an amount written in words; the judge said no.
- Where the judge did answer: recall 12 of 13 answerable (92%), false match 0 of 10 unanswerable.

Honest reading: on wording it has never seen, the design cites no wrong receipt (0 false matches on both blind
sets), and finds the right one whenever the free model answers, except for a date-plus-amount-only question.
Its weak point is the **free tier's availability**: at a busy hour a quarter of the judge calls failed. The bot
says so plainly instead of guessing; paid Gemini (or a retry later) removes that failure mode.

<details><summary>Raw run (held-out 2, scored once)</summary>

### M9-T7 RAG run, app.eval.rag_heldout2 - 2026-11-23 21:31 UTC

Embedding model: gemini-embedding-001; classifier model: not called (retrieval only). 10 receipts, 15 answerable + 15 unanswerable questions, top-5, distance cut-off 0.45, verified by word match else one judge call (gemini-3.5-flash-lite first): 30 judge calls, 1.00 per question; 8 judge call(s) failed (the bot would say it cannot check receipts right now): yg beli gula putih 10 kilo itu nota mana ya?; bon arabika yg totalnya Rp765.000 itu kirim lg dong; maju makmur, butter 5 kilo sama telor, bon novembernya mana yah; nota tepung beras 25 kilo dari Maju Makmur mana ya; susu segar 30 liter belinya di Sinar Jaya ada notanya?; bon keju dua kilo dari UD Maju Makmur dong; Pasar Induk drinking straws 2000 biji ada receiptnya?; ada struk chicken breast buat sandwich gak? lupa belinya kapan.

| metric | value |
|---|---|
| precision of returned results | 73% |
| hit@1 | 73% |
| recall | 73% |
| MRR | 0.73 |
| false match on unanswerable | 0% |

| question | relevant | returned (best first) | routed to | judge calls |
|---|---|---|---|---|
| yg beli gula putih 10 kilo itu nota mana ya? | r1 | none | - | 1 |
| Ada struk Kopi Nusantara yg arabika sama robustanya dibeli barengan? | r2 | r2 | - | 1 |
| Tolong cariin nota terigu 25kg bareng telor sepuluh kilo dong | r3 | r3 | - | 1 |
| Sinar Jaya yg gula merah aren tgl 16 okt 2026, ada bonnya? | r4 | r4 | - | 1 |
| Can you find the fresh milk and creamer receipt from 20 October 2026? | r5 | r5 | - | 1 |
| bon arabika yg totalnya Rp765.000 itu kirim lg dong | r6 | none | - | 1 |
| Nota lettuce sama tomatoes dari pasar induk ada ga? | r7 | r7 | - | 1 |
| maju makmur, butter 5 kilo sama telor, bon novembernya mana yah | r8 | none | - | 1 |
| Need the receipt for 1,000 plastic cups and 2,000 drinking straws please | r9 | r9 | - | 1 |
| Dairy Fresh yg ada cheese dua kilo tanggal 12 nov 2026 itu nota mana? | r10 | r10 | - | 1 |
| bon Sinar Jaya tanggal 2 Oktober 2026, total 596rb kan? mau liat | r1 | r1 | - | 1 |
| struk belanja 9/10/2026 yg total lima ratus delapan puluh ribu ada? | r3 | none | - | 1 |
| Kopi Nusantara tgl 26 Oktober 2026 kita beli apa aja? cariin notanya | r6 | r6 | - | 1 |
| kemasn prima yg 8 november 2026 total 470k, minta struknya pls | r9 | r9 | - | 1 |
| Cari nota susu tanggal 12 November 2026, jumlah bayar 720 ribu | r10 | r10 | - | 1 |
| pernah stok susu oat buat latte? kirim bonnya dong | none | none | - | 1 |
| yoghurt Dairy Fresh yg kemaren itu ada struknya ga | none | none | - | 1 |
| Any receipt for matcha powder for our drinks? | none | none | - | 1 |
| kopi liberika dari Nusantara pernah beli brp kilo ya? cari notanya | none | none | - | 1 |
| Sinar Jaya ada bon gula batu nggak? | none | none | - | 1 |
| nota tepung beras 25 kilo dari Maju Makmur mana ya | none | none | - | 1 |
| minyak goreng buat dapur pernah belanja kan? bonnya ada? | none | none | - | 1 |
| Kemasan Prima paper cups seribu pcs ada invoice-nya? | none | none | - | 1 |
| cari bon gula pasir dari Toko Sinar Abadi ya, bukan toko lain | none | none | - | 1 |
| PT Dairy First fresh milk receipt ada gak? | none | none | - | 1 |
| susu segar 30 liter belinya di Sinar Jaya ada notanya? | none | none | - | 1 |
| Nusantara pernah kirim gula pasir 10kg? minta bukti belinya | none | none | - | 1 |
| bon keju dua kilo dari UD Maju Makmur dong | none | none | - | 1 |
| Pasar Induk drinking straws 2000 biji ada receiptnya? | none | none | - | 1 |
| ada struk chicken breast buat sandwich gak? lupa belinya kapan | none | none | - | 1 |

</details>
