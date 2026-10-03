"""till-14 — the table's nota is laid out like the receipt.

The owner's feedback of 2 October 2026: "design the nota meja nicely, just
like the final receipt — the clean separation makes it much more organised".
So the nota now has the receipt's top, the table and number between double
rules, the details as label and value, each item named with "qty x @price",
the item count, what this send adds, TOTAL SEMENTARA between double rules,
then BELUM DIBAYAR. Checked through the bridge's ESC/POS renderer at 48
columns.
"""
from tests.test_open_bills import (  # noqa: F401 (fixtures)
    _bill, _line, _order_jobs, _stations, cafe, client, engine, session_factory,
)
from tests.test_receipt_content import pb


def _paper(document, columns=48):
    lines = []
    w = pb.Writer(columns)
    original = w.row

    def row(text_, **kw):
        lines.append(pb.to_printer_text(text_))
        original(text_, **kw)

    w.row = row
    for block in document["blocks"]:
        pb.render_block(w, block, pb.Profile(columns=columns))
    return lines


async def test_the_nota_reads_like_the_receipt(client, session_factory, cafe):
    c = cafe
    await _stations(client, c)
    bill = await _bill(client, c, _line(c, item="roti"))
    nota = next(j for j in await _order_jobs(session_factory, c, bill["id"]) if j.kind == "nota")
    paper = _paper(nota.document)
    stripped = [l.strip() for l in paper]
    assert max(len(l) for l in paper) <= 48

    # The table and number sit between double rules, under the café's top.
    meja = stripped.index("MEJA 7")
    assert paper[meja - 1] == "=" * 48 and paper[meja + 2] == "=" * 48 and stripped[meja + 1] == "Pesanan 001"
    # Details as label and value.
    labels = [l.split()[0] for l in paper if l and l[0].isalpha() and "  " in l]
    assert labels.index("Tanggal") < labels.index("Jenis") < labels.index("Nota")
    assert any(l.startswith("Jenis") and l.endswith("Makan di sini") for l in paper)
    # The item on its own line, then quantity at unit price and the amount.
    roti = stripped.index("Roti")
    assert paper[roti + 1].startswith("  1 x @15.000") and paper[roti + 1].endswith("15.000")
    assert any(l.startswith("Total item") and l.endswith("1") for l in paper)
    assert any(l.startswith("Pesanan ini") and l.endswith("Rp 15.000") for l in paper)
    # TOTAL SEMENTARA is set apart between double rules, then BELUM DIBAYAR.
    total = next(i for i, l in enumerate(paper) if l.startswith("TOTAL SEMENTARA"))
    assert paper[total - 1] == "=" * 48 and paper[total + 1] == "=" * 48
    assert "BELUM DIBAYAR" in "\n".join(paper[total:])
    assert stripped.index("Bayar di kasir sebelum pulang.") < stripped.index("Terima kasih!")
