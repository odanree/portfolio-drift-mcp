"""Structured error envelope shared by every tool.

Same shape as oci-mcp / infra-mcp / beacon-mcp: the calling LLM can branch
deterministically on `ok` and `error_kind` rather than parsing tracebacks.
"""

from __future__ import annotations

from typing import Any


def ok(payload: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, **payload}


def err(error_kind: str, message: str, **extra: Any) -> dict[str, Any]:
    """Return a failure envelope.

    error_kind taxonomy:
        config        — missing env var, bad path, malformed config
        network       — GitHub/Anthropic/Beacon HTTP failure
        not_found     — project_id not in the portfolio
        upstream      — drift_agent itself raised an unexpected error
    """
    return {"ok": False, "error_kind": error_kind, "error": message, **extra}
