from app.ocr.base import OcrLine
from app.ocr.grouping import group_lines, line_direction, order_regions
from app.ocr.langdetect import aggregate_page_language, detect_language


def _line(x0, y0, x1, y1, text, conf=0.9):
    return OcrLine([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], text, conf)


def test_direction_estimation():
    assert line_direction(_line(0, 0, 20, 200, "縦書き")) == "vertical"
    assert line_direction(_line(0, 0, 200, 20, "横書き")) == "horizontal"
    assert line_direction(_line(0, 0, 20, 22, "字")) == "vertical"


def test_grouping_vertical_columns_into_one_bubble_right_to_left():
    cols = [
        _line(100, 10, 120, 200, "二"),
        _line(130, 10, 150, 200, "一"),
        _line(70, 12, 90, 190, "三"),
    ]
    regions = group_lines(cols)
    assert len(regions) == 1
    assert regions[0].direction == "vertical"
    assert regions[0].text == "一二三"


def test_grouping_keeps_distant_bubbles_apart_and_orders_rtl():
    a = [_line(300, 10, 320, 150, "右上")]
    b = [_line(20, 20, 40, 160, "左上")]
    c = [_line(100, 300, 300, 330, "下の横")]
    regions = order_regions(group_lines(a + b + c), "rtl")
    assert [r.text for r in regions] == ["右上", "左上", "下の横"]
    regions = order_regions(group_lines(a + b + c), "ltr")
    assert [r.text for r in regions] == ["左上", "右上", "下の横"]


def test_furigana_split():
    main = _line(100, 10, 130, 200, "漢字")
    furi = _line(131, 10, 140, 100, "かんじ")
    regions = group_lines([main, furi])
    assert len(regions) == 1
    assert regions[0].text == "漢字" and [f.text for f in regions[0].furigana] == ["かんじ"]


def test_language_detection():
    assert detect_language("こんにちは").lang == "ja"
    assert detect_language("这是简体中文").lang == "zh-Hans"
    assert detect_language("這是繁體中文").lang == "zh-Hant"
    g = detect_language("東京")  # kanji-only: could be Japanese; must stay uncertain
    assert g.lang in ("zh-Hant", "unknown") and g.uncertain
    assert detect_language("日本語").uncertain
    assert detect_language("hello").lang == "unknown"
    page = aggregate_page_language(
        [detect_language("こんにちは"), detect_language("元気です"), detect_language("东西")]
    )
    assert page.lang == "ja" and page.uncertain  # mixed page flagged
