"""Fixture-only held-out receipt questions; do not use these to tune retrieval.

Written without reading the existing question list or search implementation.
RagQuestion's text/lang/relevant contract is preserved; note records the intent
of each case. An empty relevant set means that no fixture receipt answers it.
No model calls or evaluation runs are performed by this module.
"""

from dataclasses import dataclass

from app.eval.questions import RagQuestion


@dataclass(frozen=True)
class HeldoutRagQuestion(RagQuestion):
    note: str


RAG_HELDOUT_QUESTIONS: list[HeldoutRagQuestion] = [
    # Answerable: exactly one receipt satisfies every constraint in each question.
    HeldoutRagQuestion(
        "nota white sugar yg bareng susu kotak itu mana ya?",
        "mixed", frozenset({"r1"}),
        "English sugar synonym and informal milk packaging description identify r1.",
    ),
    HeldoutRagQuestion(
        "ada bon biji kopi dua jenis, arabika sama robusta, awal oktober 2026?",
        "id", frozenset({"r2"}),
        "Indonesian spelling variant and two-item conjunction distinguish the coffee receipts.",
    ),
    HeldoutRagQuestion(
        "Find the wheat flour and eggs purchase from 9 October 2026 please",
        "en", frozenset({"r3"}),
        "English item translations and plural eggs require semantic matching.",
    ),
    HeldoutRagQuestion(
        "yg beli palm sugar 5 kilo di Sinar Jaya, bonnya masih ada?",
        "mixed", frozenset({"r4"}),
        "English palm sugar synonym plus supplier selects the later Sinar Jaya receipt.",
    ),
    HeldoutRagQuestion(
        "cariin pembelian fresh milk sekalian creamer dong",
        "mixed", frozenset({"r5"}),
        "English translations and the creamer conjunction disambiguate fresh milk purchases.",
    ),
    HeldoutRagQuestion(
        "bon arabika tgl 26 okt 2026 yg 765rb itu",
        "id", frozenset({"r6"}),
        "Spelling variant, abbreviated date and shorthand amount identify one coffee purchase.",
    ),
    HeldoutRagQuestion(
        "Where's the receipt for lettuce and tomatoes from Pasar Induk?",
        "en", frozenset({"r7"}),
        "English vegetable translations and plural tomatoes identify the market receipt.",
    ),
    HeldoutRagQuestion(
        "mentega sm telor awal nov 2026 beli di mana ya, ada notanya?",
        "id", frozenset({"r8"}),
        "Colloquial egg spelling and abbreviated month select the butter-and-eggs receipt.",
    ),
    HeldoutRagQuestion(
        "need the bon for disposable cups + straws, seribu cup dua ribu straw",
        "mixed", frozenset({"r9"}),
        "English packaging paraphrase, plural forms and written quantities identify r9.",
    ),
    HeldoutRagQuestion(
        "bon cheese 2 kilo barengan susu dari Dairy Fresh ada gak?",
        "mixed", frozenset({"r10"}),
        "English cheese synonym and partial supplier name distinguish the dairy receipts.",
    ),
    HeldoutRagQuestion(
        "nota Toko Sinar Jaya tanggal 2 Oktober 2026 tolong cariin",
        "id", frozenset({"r1"}),
        "Supplier plus exact calendar date disambiguates two purchases from the same shop.",
    ),
    HeldoutRagQuestion(
        "Kopi Nusantara yg totalnya sejuta seratus lima puluh ribu, kirim bonnya dong",
        "id", frozenset({"r2"}),
        "Supplier plus a total written in words identifies 750000 + 400000.",
    ),
    HeldoutRagQuestion(
        "cari struk belanja 580rb di Maju Makmur bulan oktober 2026",
        "id", frozenset({"r3"}),
        "Informal total shorthand and month identify 300000 + 280000.",
    ),
    HeldoutRagQuestion(
        "the Dairy Fresh invoice on October 20, 2026, can you find it?",
        "en", frozenset({"r5"}),
        "English invoice wording and month-first date identify the earlier dairy receipt.",
    ),
    HeldoutRagQuestion(
        "yg abis 140 rebu buat sayur di pasar induk, itu nota mana?",
        "id", frozenset({"r7"}),
        "Vegetable category paraphrase and slang total identify 60000 + 80000.",
    ),
    # Unanswerable: related categories, near supplier names and wrong pairings.
    HeldoutRagQuestion(
        "pernah beli gula batu di Sinar Jaya? cariin notanya",
        "id", frozenset(),
        "Rock sugar is absent even though other sugars were bought from this supplier.",
    ),
    HeldoutRagQuestion(
        "ada bon oat milk dari Dairy Fresh gak?",
        "mixed", frozenset(),
        "Plant milk is absent despite two dairy milk purchases from the real supplier.",
    ),
    HeldoutRagQuestion(
        "invoice kopi liberika di Kopi Nusantara yg dulu itu mana",
        "mixed", frozenset(),
        "A different coffee variety must not match arabica or robusta receipts.",
    ),
    HeldoutRagQuestion(
        "tepung beras dari UD Maju Makmur ada struknya?",
        "id", frozenset(),
        "Rice flour is a same-category near miss for the purchased wheat flour.",
    ),
    HeldoutRagQuestion(
        "Find our mozzarella receipt from PT Dairy Fresh",
        "en", frozenset(),
        "Generic cheese in a receipt does not establish a specific mozzarella purchase.",
    ),
    HeldoutRagQuestion(
        "bon gelas kertas seribu pcs dr Kemasan Prima ada?",
        "id", frozenset(),
        "Paper cups must not match plastic cups with the same quantity and supplier.",
    ),
    HeldoutRagQuestion(
        "selada yg kita beli di Pasar Indah, tolong cari bonnya",
        "id", frozenset(),
        "Near supplier name Pasar Indah must not be treated as Pasar Induk.",
    ),
    HeldoutRagQuestion(
        "nota gula pasir dr Toko Sinar Baru ada ga",
        "id", frozenset(),
        "A plausible but absent Sinar supplier must not inherit Sinar Jaya purchases.",
    ),
    HeldoutRagQuestion(
        "did we buy robusta beans from CV Kopi Nusani? show the receipt",
        "en", frozenset(),
        "A similar but distinct supplier name must not match CV Kopi Nusantara.",
    ),
    HeldoutRagQuestion(
        "cariin bon gula aren dari UD Maju Makmur",
        "id", frozenset(),
        "Both item and supplier exist, but palm sugar was never bought from Maju Makmur.",
    ),
    HeldoutRagQuestion(
        "ada nota telur dari Toko Sinar Jaya?",
        "id", frozenset(),
        "A real item paired with the wrong real supplier must return no receipt.",
    ),
    HeldoutRagQuestion(
        "fresh milk dari CV Kopi Nusantara, invoice-nya mana ya?",
        "mixed", frozenset(),
        "A translated real item must not match receipts from an unrelated real supplier.",
    ),
    HeldoutRagQuestion(
        "bon sedotan dari Pasar Induk dong",
        "id", frozenset(),
        "A real packaging item was never purchased from the real vegetable supplier.",
    ),
    HeldoutRagQuestion(
        "pernah restok sirup vanila buat latte? cari nota belinya",
        "id", frozenset(),
        "A plausible cafe ingredient absent from every receipt requires abstention.",
    ),
    HeldoutRagQuestion(
        "got a receipt for dishwashing liquid from Toko Bersih Sentosa?",
        "en", frozenset(),
        "Both a plausible operating supply and its supplier are absent from the corpus.",
    ),
]
