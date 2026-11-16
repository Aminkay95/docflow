"""Worker loop defaults: run_forever must claim the same kinds as run_once.

A real worker starts through run_forever, so if it hard-defaulted to the
legacy kinds the classify_segment tasks enqueued by LLM classification would
never be claimed and the pipeline would stall.
"""

from __future__ import annotations

from trade_extract.config import Settings
from trade_extract.workers import runner


def _kinds_seen_by_run_once(monkeypatch, settings: Settings) -> list[str]:
    captured: dict[str, object] = {}

    def fake_run_once(**kwargs: object) -> None:
        captured.update(kwargs)
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "run_once", fake_run_once)
    runner.run_forever(settings=settings, session_factory_=None)  # type: ignore[arg-type]
    kinds = captured.get("kinds")
    assert isinstance(kinds, list)
    return kinds


def test_run_forever_claims_classify_tasks_by_default(monkeypatch) -> None:
    monkeypatch.delenv("LLM_CLASSIFICATION_ENABLED", raising=False)
    settings = Settings(_env_file=None)
    assert _kinds_seen_by_run_once(monkeypatch, settings) == runner.PIPELINE_KINDS


def test_run_forever_claims_default_kinds_when_llm_classification_disabled(monkeypatch) -> None:
    settings = Settings(llm_classification_enabled=False)
    assert _kinds_seen_by_run_once(monkeypatch, settings) == runner.DEFAULT_KINDS
