from app.ocr.base import OcrLine
from app.ocr.paddle import merge_new_lines


def _line(x0, y0, x1, y1, text="t"):
    return OcrLine([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], text, 0.9)


def test_merge_adds_only_non_overlapping_lines():
    primary = [_line(100, 100, 130, 300, "a"), _line(500, 500, 700, 540, "b")]
    extra = [
        _line(102, 98, 128, 305, "a-dup"),  # same box, slightly different -> dropped
        _line(200, 100, 230, 300, "new"),  # new column -> kept
        _line(205, 100, 235, 300, "new-dup"),  # overlaps the accepted one -> dropped
        _line(510, 505, 690, 535, "b-dup"),  # dropped
    ]
    merged = merge_new_lines(primary, extra)
    assert [ln.text for ln in merged] == ["new"]


def test_merge_with_empty_inputs():
    assert merge_new_lines([], []) == []
    assert [ln.text for ln in merge_new_lines([], [_line(0, 0, 10, 10, "x")])] == ["x"]
