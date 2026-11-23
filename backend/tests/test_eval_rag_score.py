"""M9-T7: the retrieval scorer, on hand-made results (no model)."""
from app.eval.questions import RAG_QUESTIONS, RAG_RECEIPTS, RagQuestion
from app.eval.rag import score


def test_the_set_is_consistent():
    keys = {r.key for r in RAG_RECEIPTS}
    assert all(q.relevant <= keys for q in RAG_QUESTIONS)
    assert sum(1 for q in RAG_QUESTIONS if not q.relevant) >= 10


def test_scores():
    qs = [RagQuestion("a", "id", frozenset({"r1", "r2"})), RagQuestion("b", "id", frozenset({"r3"})), RagQuestion("c", "id", frozenset())]
    s = score(qs, [["r1", "r9", "r2", "r8"], ["r5", "r3"], ["r7"]])
    assert s["precision"] == (2 / 4 + 1 / 2) / 2
    assert s["hit@1"] == 0.5 and s["recall"] == 1.0 and s["mrr"] == (1 + 0.5) / 2
    assert s["false_match"] == 1.0
