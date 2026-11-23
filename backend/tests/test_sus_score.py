"""fin-eval (P1-1b): SUS scoring follows Brooke's rule."""
import importlib.util
import pathlib

import pytest

spec = importlib.util.spec_from_file_location(
    "sus_score", pathlib.Path(__file__).resolve().parents[2] / "scripts" / "sus_score.py")
sus_score = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sus_score)


def test_best_and_worst_and_neutral():
    assert sus_score.sus([5, 1, 5, 1, 5, 1, 5, 1, 5, 1]) == 100.0
    assert sus_score.sus([1, 5, 1, 5, 1, 5, 1, 5, 1, 5]) == 0.0
    assert sus_score.sus([3] * 10) == 50.0


def test_a_worked_example():
    # odd items 4,4,4,4,4 -> 3 each = 15; even items 2,2,2,2,2 -> 3 each = 15; 30 * 2.5
    assert sus_score.sus([4, 2, 4, 2, 4, 2, 4, 2, 4, 2]) == 75.0


def test_bad_input_is_refused():
    for bad in ([3] * 9, [3] * 9 + [6], [3] * 9 + [0]):
        with pytest.raises(ValueError):
            sus_score.sus(bad)


def test_csv_with_header(tmp_path):
    f = tmp_path / "a.csv"
    f.write_text("name,q1,q2,q3,q4,q5,q6,q7,q8,q9,q10\nIbu Diah,4,2,4,2,4,2,4,2,4,2\n", encoding="utf-8")
    assert sus_score.read(str(f)) == [("Ibu Diah", 75.0)]
