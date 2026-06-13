"""Shared fixtures: env scrubbing + mock Anthropic."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test gets a clean env — no operator credentials leak into the run."""
    for name in (
        "BEACON_API_URL",
        "BEACON_JWT",
        "ANTHROPIC_API_KEY",
        "GITHUB_TOKEN",
        "DRIFT_MODEL",
        "DRIFT_PROJECTS_FILE",
        "DRIFT_STALE_DAYS",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def mock_anthropic_json(monkeypatch: pytest.MonkeyPatch):
    """Make Anthropic().messages.create() return a stubbed analyzer JSON.

    Returns a callable: set_response(dict_or_str) — call it before invoking
    the server tool to control the analyzer's response. Subsequent
    Anthropic() instantiations all return the same mock client.
    """
    state: dict[str, MagicMock | None] = {"resp": None}

    def _factory(*_args, **_kwargs) -> MagicMock:
        client = MagicMock()
        client.messages = MagicMock()
        client.messages.create = MagicMock(return_value=state["resp"])
        return client

    monkeypatch.setattr("anthropic.Anthropic", _factory)

    def set_response(payload: dict | str) -> None:
        body = json.dumps(payload) if isinstance(payload, dict) else payload
        msg = MagicMock()
        msg.text = body
        resp = MagicMock()
        resp.content = [msg]
        state["resp"] = resp

    return set_response
