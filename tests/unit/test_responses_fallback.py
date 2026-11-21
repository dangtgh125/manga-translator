"""Responses-compatible adapter: structured-output fallback and fence stripping."""

import json

import pytest
from app.translate.base import ProviderError, RegionText, TranslationContext
from app.translate.openai_responses import ResponsesProvider, _strip_fences

ITEMS = [RegionText("rg_a", "こんにちは")]
CTX = TranslationContext(target_lang="vi")
GOOD = json.dumps({"translations": [{"region_id": "rg_a", "translated_text": "Xin chào", "needs_context": False}]})


def _provider(monkeypatch, options=None):
    monkeypatch.setenv("MT_ENDPOINT_ALLOWLIST", "llm.example")
    from app.config import reset_settings_cache

    reset_settings_cache()
    return ResponsesProvider("responses_compatible", "m", "sk", "https://llm.example/v1", options or {})


def _reasoning_only_gateway(calls):
    def fake_request(method, path, body=None):
        calls.append(body)
        if "text" in body:  # gateway swallows the message when text.format is present
            return {"status": "completed", "output_text": "", "output": [{"type": "reasoning", "content": []}]}
        return {
            "status": "completed",
            "output_text": "",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "```json\n" + GOOD + "\n```"}]}],
        }

    return fake_request


def test_auto_falls_back_to_prompt_only_json_and_remembers(monkeypatch):
    p = _provider(monkeypatch)
    calls: list = []
    monkeypatch.setattr(p, "_request", _reasoning_only_gateway(calls))
    out = p.translate_batch(ITEMS, CTX)
    assert out[0].text == "Xin chào"
    assert "text" in calls[0] and "text" not in calls[1]
    p.translate_batch(ITEMS, CTX)
    assert len(calls) == 3 and "text" not in calls[2]  # format skipped on later batches


def test_json_schema_mode_never_falls_back(monkeypatch):
    p = _provider(monkeypatch, {"structured_output": "json_schema"})
    calls: list = []
    monkeypatch.setattr(p, "_request", _reasoning_only_gateway(calls))
    with pytest.raises(ProviderError, match="no text output"):
        p.translate_batch(ITEMS, CTX)
    assert len(calls) == 1


def test_none_mode_skips_format(monkeypatch):
    p = _provider(monkeypatch, {"structured_output": "none"})
    calls: list = []
    monkeypatch.setattr(p, "_request", _reasoning_only_gateway(calls))
    assert p.translate_batch(ITEMS, CTX)[0].text == "Xin chào"
    assert len(calls) == 1 and "text" not in calls[0]


def test_strip_fences():
    assert _strip_fences('```json\n{"a":1}\n```') == '{"a":1}'
    assert _strip_fences('{"a":1}') == '{"a":1}'
    assert _strip_fences("```\n{}\n```  ") == "{}"
