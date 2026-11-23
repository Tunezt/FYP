"""Scores System Usability Scale (SUS) answers (CP1 §3.8, audit fin-eval / P1-1b).

Brooke's rule: each answer is 1 (sangat tidak setuju) to 5 (sangat setuju).
Odd-numbered items are positive: they score answer - 1. Even-numbered items are
negative: they score 5 - answer. The ten scores add up to 0-40; times 2.5 gives
0-100. CP1's target is 72 or more (the usual "good" mark is about 68 as the
average, 72+ is above average).

    python scripts/sus_score.py answers.csv

answers.csv: one row per participant, an optional header row, then
`name,q1,q2,...,q10`. The questionnaire text is docs/sus-questionnaire.md.
"""
from __future__ import annotations

import csv
import sys

TARGET = 72.0


def sus(answers: list[int]) -> float:
    if len(answers) != 10 or any(a not in (1, 2, 3, 4, 5) for a in answers):
        raise ValueError("SUS needs exactly 10 answers, each 1 to 5")
    return sum((a - 1) if i % 2 == 0 else (5 - a) for i, a in enumerate(answers)) * 2.5


def read(path: str) -> list[tuple[str, float]]:
    out = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.reader(fh):
            if not row or not row[0].strip():
                continue
            try:
                answers = [int(x) for x in row[1:11]]
            except ValueError:
                continue  # header row
            out.append((row[0].strip(), sus(answers)))
    return out


def main(path: str) -> None:
    scores = read(path)
    if not scores:
        sys.exit("no answers found")
    for name, s in scores:
        print(f"{name}: {s:.1f}")
    mean = sum(s for _n, s in scores) / len(scores)
    verdict = "meets" if mean >= TARGET else "is below"
    print(f"mean {mean:.1f} over {len(scores)} participant(s): {verdict} the CP1 target of {TARGET:g}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
