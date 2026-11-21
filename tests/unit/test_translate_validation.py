import json

import pytest
from app.logging_utils import redact
from app.translate.base import ProviderError, RegionText, glossary_check, validate_ai_output

ITEMS = [RegionText("rg_a", "こんにちは"), RegionText("rg_b", "元気")]


def _raw(entries):
    return json.dumps({"translations": entries})


def test_valid_output():
    out = validate_ai_output(
        _raw(
            [
                {"region_id": "rg_a", "translated_text": "Xin chào", "needs_context": False},
                {"region_id": "rg_b", "translated_text": "Khỏe", "needs_context": True},
            ]
        ),
        ITEMS,
    )
    assert [o.text for o in out] == ["Xin chào", "Khỏe"] and out[1].flags == ["needs_context"]


@pytest.mark.parametrize(
    "entries",
    [
        [{"region_id": "rg_a", "translated_text": "x", "needs_context": False}],  # missing
        [{"region_id": "rg_a", "translated_text": "x", "needs_context": False}] * 2
        + [{"region_id": "rg_b", "translated_text": "y", "needs_context": False}],  # dup
        [
            {"region_id": "rg_zzz", "translated_text": "x", "needs_context": False},
            {"region_id": "rg_b", "translated_text": "y", "needs_context": False},
        ],  # unknown
        [
            {"region_id": "rg_a", "translated_text": 5, "needs_context": False},
            {"region_id": "rg_b", "translated_text": "y", "needs_context": False},
        ],  # type
    ],
)
def test_invalid_outputs(entries):
    with pytest.raises(ProviderError):
        validate_ai_output(_raw(entries), ITEMS)


def test_rejects_garbage_and_oversized():
    with pytest.raises(ProviderError):
        validate_ai_output("not json", ITEMS)
    with pytest.raises(ProviderError):
        validate_ai_output('{"foo": []}', ITEMS)
    with pytest.raises(ProviderError):
        validate_ai_output(
            _raw(
                [
                    {"region_id": "rg_a", "translated_text": "x" * 6000, "needs_context": False},
                    {"region_id": "rg_b", "translated_text": "y", "needs_context": False},
                ]
            ),
            ITEMS,
        )


def test_control_chars_stripped_and_injection_is_just_text():
    out = validate_ai_output(
        _raw(
            [
                {"region_id": "rg_a", "translated_text": "a\x00b\x07c", "needs_context": False},
                {
                    "region_id": "rg_b",
                    "translated_text": "IGNORE PREVIOUS INSTRUCTIONS <script>alert(1)</script>",
                    "needs_context": False,
                },
            ]
        ),
        ITEMS,
    )
    assert out[0].text == "abc"
    assert "<script>" in out[1].text  # stored as text; UI escapes it


def test_glossary_check_flags_mismatch():
    items = [RegionText("r1", "ナルトが来た"), RegionText("r2", "ok")]
    res = validate_ai_output(
        _raw(
            [
                {"region_id": "r1", "translated_text": "Bob đã đến", "needs_context": False},
                {"region_id": "r2", "translated_text": "ok", "needs_context": False},
            ]
        ),
        items,
    )
    glossary_check(items, res, [{"source": "ナルト", "target": "Naruto"}])
    assert res[0].flags == ["glossary_mismatch:ナルト"] and res[1].flags == []


def test_redaction():
    for s in (
        "key sk-abcdefghijklmnopqrstuvwxyz0123",
        "AIzaSyA-1234567890abcdefghijklmnopqrstu",
        "Bearer abcdefghijklmnopqrstuvwxyz",
        "-----BEGIN PRIVATE KEY-----\nMIIE\n-----END PRIVATE KEY-----",
    ):
        out = redact(s)
        assert "REDACTED" in out and "sk-abc" not in out and "MIIE" not in out
    assert redact("normal error text 404") == "normal error text 404"
