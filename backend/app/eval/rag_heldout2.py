"""Second blind receipt-search holdout: 15 answerable and 15 unanswerable.

Authored from receipt fixtures and the RagQuestion type only. Keep this set
untouched for one final evaluation after search redesign; do not use for tuning.
Relevant keys identify fixture receipts, and an empty set means no receipt.
"""

from dataclasses import dataclass

from app.eval.questions import RagQuestion


@dataclass(frozen=True)
class HeldoutQuestion(RagQuestion):
    note: str


RAG_HELDOUT2_QUESTIONS: list[HeldoutQuestion] = [
    HeldoutQuestion(
        "yg beli gula putih 10 kilo itu nota mana ya?",
        "id", frozenset({"r1"}),
        "Informal abbreviation and gula putih synonym for gula pasir.",
    ),
    HeldoutQuestion(
        "Ada struk Kopi Nusantara yg arabika sama robustanya dibeli barengan?",
        "id", frozenset({"r2"}),
        "Supplier shorthand, Indonesian spelling, and two-item conjunction.",
    ),
    HeldoutQuestion(
        "Tolong cariin nota terigu 25kg bareng telor sepuluh kilo dong",
        "id", frozenset({"r3"}),
        "Item shorthand, telor variant, and quantities written differently.",
    ),
    HeldoutQuestion(
        "Sinar Jaya yg gula merah aren tgl 16 okt 2026, ada bonnya?",
        "id", frozenset({"r4"}),
        "Colloquial sugar name plus abbreviated date distinguishes supplier visits.",
    ),
    HeldoutQuestion(
        "Can you find the fresh milk and creamer receipt from 20 October 2026?",
        "en", frozenset({"r5"}),
        "English translations of both items and an explicit date.",
    ),
    HeldoutQuestion(
        "bon arabika yg totalnya Rp765.000 itu kirim lg dong",
        "id", frozenset({"r6"}),
        "Spelling variant and exact total distinguish two arabica purchases.",
    ),
    HeldoutQuestion(
        "Nota lettuce sama tomatoes dari pasar induk ada ga?",
        "mixed", frozenset({"r7"}),
        "English vegetable synonyms with plural tomatoes in Indonesian phrasing.",
    ),
    HeldoutQuestion(
        "maju makmur, butter 5 kilo sama telor, bon novembernya mana yah",
        "mixed", frozenset({"r8"}),
        "English butter synonym and month disambiguate repeat egg supplier.",
    ),
    HeldoutQuestion(
        "Need the receipt for 1,000 plastic cups and 2,000 drinking straws please",
        "en", frozenset({"r9"}),
        "English item translations and plural forms with quantity constraints.",
    ),
    HeldoutQuestion(
        "Dairy Fresh yg ada cheese dua kilo tanggal 12 nov 2026 itu nota mana?",
        "mixed", frozenset({"r10"}),
        "English cheese synonym and date select the second dairy receipt.",
    ),
    HeldoutQuestion(
        "bon Sinar Jaya tanggal 2 Oktober 2026, total 596rb kan? mau liat",
        "id", frozenset({"r1"}),
        "Supplier, date, and shorthand amount identify one receipt.",
    ),
    HeldoutQuestion(
        "struk belanja 9/10/2026 yg total lima ratus delapan puluh ribu ada?",
        "id", frozenset({"r3"}),
        "Indonesian day-first numeric date and total spelled out in words.",
    ),
    HeldoutQuestion(
        "Kopi Nusantara tgl 26 Oktober 2026 kita beli apa aja? cariin notanya",
        "id", frozenset({"r6"}),
        "Supplier and date lookup without naming the purchased item.",
    ),
    HeldoutQuestion(
        "kemasn prima yg 8 november 2026 total 470k, minta struknya pls",
        "mixed", frozenset({"r9"}),
        "Supplier typo, informal amount, and mixed-language request.",
    ),
    HeldoutQuestion(
        "Cari nota susu tanggal 12 November 2026, jumlah bayar 720 ribu",
        "id", frozenset({"r10"}),
        "Broad item category resolved by exact date and receipt total.",
    ),
    HeldoutQuestion(
        "pernah stok susu oat buat latte? kirim bonnya dong",
        "id", frozenset(),
        "Unbought milk alternative near the real dairy purchases.",
    ),
    HeldoutQuestion(
        "yoghurt Dairy Fresh yg kemaren itu ada struknya ga",
        "id", frozenset(),
        "Real dairy supplier paired with an absent dairy product.",
    ),
    HeldoutQuestion(
        "Any receipt for matcha powder for our drinks?",
        "en", frozenset(),
        "Plausible cafe beverage ingredient absent from all receipts.",
    ),
    HeldoutQuestion(
        "kopi liberika dari Nusantara pernah beli brp kilo ya? cari notanya",
        "id", frozenset(),
        "Absent coffee variety near two purchased coffee varieties.",
    ),
    HeldoutQuestion(
        "Sinar Jaya ada bon gula batu nggak?",
        "id", frozenset(),
        "Absent sugar type at a real sugar supplier.",
    ),
    HeldoutQuestion(
        "nota tepung beras 25 kilo dari Maju Makmur mana ya",
        "id", frozenset(),
        "Absent flour type with a real supplier and familiar quantity.",
    ),
    HeldoutQuestion(
        "minyak goreng buat dapur pernah belanja kan? bonnya ada?",
        "id", frozenset(),
        "Plausible kitchen staple absent despite a leading question.",
    ),
    HeldoutQuestion(
        "Kemasan Prima paper cups seribu pcs ada invoice-nya?",
        "mixed", frozenset(),
        "Paper cup near-miss against purchased plastic cups.",
    ),
    HeldoutQuestion(
        "cari bon gula pasir dari Toko Sinar Abadi ya, bukan toko lain",
        "id", frozenset(),
        "Absent supplier shares Sinar with a real supplier and names a real item.",
    ),
    HeldoutQuestion(
        "PT Dairy First fresh milk receipt ada gak?",
        "mixed", frozenset(),
        "Close but absent supplier name paired with a purchased item.",
    ),
    HeldoutQuestion(
        "susu segar 30 liter belinya di Sinar Jaya ada notanya?",
        "id", frozenset(),
        "Real item and quantity attributed to a supplier that only sold UHT milk.",
    ),
    HeldoutQuestion(
        "Nusantara pernah kirim gula pasir 10kg? minta bukti belinya",
        "id", frozenset(),
        "Real sugar purchase incorrectly attributed to the coffee supplier.",
    ),
    HeldoutQuestion(
        "bon keju dua kilo dari UD Maju Makmur dong",
        "id", frozenset(),
        "Purchased cheese and quantity paired with the wrong real supplier.",
    ),
    HeldoutQuestion(
        "Pasar Induk drinking straws 2000 biji ada receiptnya?",
        "mixed", frozenset(),
        "Translated purchased packaging item assigned to the produce supplier.",
    ),
    HeldoutQuestion(
        "ada struk chicken breast buat sandwich gak? lupa belinya kapan",
        "mixed", frozenset(),
        "Plausible unbought protein expressed in English within Indonesian chat.",
    ),
]
