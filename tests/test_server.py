"""Server tool tests — config gating, error envelopes, and the happy path
through a fully-mocked Beacon + GitHub + Anthropic stack."""

from __future__ import annotations

import base64

import httpx
import respx

from drift_mcp.server import (
    apply_drift_patches,
    audit_portfolio_drift,
    audit_single_project,
)

# ── Fixture data ──────────────────────────────────────────────────────────────

PROJECT_ID = "11111111-2222-3333-4444-555555555555"
SAMPLE_PROJECT = {
    "id": PROJECT_ID,
    "name": "Test Project",
    "description": "A demo project",
    "outcome": "It works",
    "tech_stack": ["Python"],
    "url": "https://github.com/octo/test-repo",
    "start_date": None,
    "end_date": None,
}

ANALYZER_NO_DRIFT = {
    "description_suggestion": None,
    "outcome_suggestion": None,
    "tech_stack_additions": [],
    "tech_stack_removals": [],
    "canonical_url": None,
    "notes": "no drift detected",
}

ANALYZER_WITH_DRIFT = {
    "description_suggestion": None,
    "outcome_suggestion": None,
    "tech_stack_additions": ["Rust"],
    "tech_stack_removals": [],
    "canonical_url": None,
    "notes": "rust added in recent commit",
}


def _mock_beacon_and_github(respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://beacon.danhle.net/api/profile/projects").mock(
        return_value=httpx.Response(200, json=[SAMPLE_PROJECT])
    )
    respx_mock.get("https://api.github.com/repos/octo/test-repo").mock(
        return_value=httpx.Response(
            200,
            json={
                "html_url": "https://github.com/octo/test-repo",
                "description": "A demo repo",
                "topics": ["demo"],
                "pushed_at": "2026-06-01T00:00:00Z",
                "stargazers_count": 0,
            },
        )
    )
    respx_mock.get("https://api.github.com/repos/octo/test-repo/languages").mock(
        return_value=httpx.Response(200, json={"Python": 1000})
    )
    respx_mock.get("https://api.github.com/repos/octo/test-repo/readme").mock(
        return_value=httpx.Response(
            200,
            json={"content": base64.b64encode(b"# Test\nA test repo.").decode()},
        )
    )
    respx_mock.get("https://api.github.com/repos/octo/test-repo/commits?per_page=15").mock(
        return_value=httpx.Response(200, json=[{"commit": {"message": "feat: add rust extension"}}])
    )


# ── audit_portfolio_drift ─────────────────────────────────────────────────────


def test_audit_portfolio_drift_missing_anthropic_key_returns_config_err() -> None:
    out = audit_portfolio_drift()
    assert out["ok"] is False
    assert out["error_kind"] == "config"
    assert "ANTHROPIC_API_KEY" in out["error"]


def test_audit_portfolio_drift_missing_beacon_jwt_returns_config_err(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    out = audit_portfolio_drift(adapter="beacon")
    assert out["ok"] is False
    assert out["error_kind"] == "config"
    assert "BEACON_JWT" in out["error"]


def test_audit_portfolio_drift_unknown_adapter(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    out = audit_portfolio_drift(adapter="notion")
    assert out["ok"] is False
    assert out["error_kind"] == "config"
    assert "unknown adapter" in out["error"]


def test_audit_portfolio_drift_happy_path(monkeypatch, mock_anthropic_json) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    monkeypatch.setenv("BEACON_JWT", "fake")
    mock_anthropic_json(ANALYZER_NO_DRIFT)

    with respx.mock(assert_all_called=False) as respx_mock:
        _mock_beacon_and_github(respx_mock)
        out = audit_portfolio_drift(limit=1)

    assert out["ok"] is True
    assert out["adapter"] == "beacon"
    assert out["analyzed"] == 1
    assert out["drift"] == 0
    assert len(out["reports"]) == 1
    assert out["reports"][0]["project_name"] == "Test Project"
    assert out["reports"][0]["has_changes"] is False


def test_audit_portfolio_drift_counts_drift_when_analyzer_proposes_changes(
    monkeypatch, mock_anthropic_json
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    monkeypatch.setenv("BEACON_JWT", "fake")
    mock_anthropic_json(ANALYZER_WITH_DRIFT)

    with respx.mock(assert_all_called=False) as respx_mock:
        _mock_beacon_and_github(respx_mock)
        out = audit_portfolio_drift(limit=1)

    assert out["drift"] == 1
    assert out["reports"][0]["tech_stack_additions"] == ["Rust"]
    assert out["reports"][0]["has_changes"] is True


# ── audit_single_project ──────────────────────────────────────────────────────


def test_audit_single_project_not_found(monkeypatch, mock_anthropic_json) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    monkeypatch.setenv("BEACON_JWT", "fake")

    with respx.mock(assert_all_called=False) as respx_mock:
        _mock_beacon_and_github(respx_mock)
        out = audit_single_project(project_id="missing")

    assert out["ok"] is False
    assert out["error_kind"] == "not_found"


def test_audit_single_project_happy_path(monkeypatch, mock_anthropic_json) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    monkeypatch.setenv("BEACON_JWT", "fake")
    mock_anthropic_json(ANALYZER_NO_DRIFT)

    with respx.mock(assert_all_called=False) as respx_mock:
        _mock_beacon_and_github(respx_mock)
        out = audit_single_project(project_id=PROJECT_ID)

    assert out["ok"] is True
    assert out["report"]["project_name"] == "Test Project"
    assert out["report"]["has_changes"] is False


# ── apply_drift_patches ───────────────────────────────────────────────────────


def test_apply_drift_patches_dry_run_returns_intent() -> None:
    patches = [
        {
            "project_id": "abc",
            "name_hint": "A test",
            "tech_additions": ["Rust"],
        }
    ]
    out = apply_drift_patches(patches, dry_run=True)
    assert out["ok"] is True
    assert out["mode"] == "dry_run"
    assert out["attempted"] == 1
    assert out["intent"][0]["tech_additions"] == ["Rust"]


def test_apply_drift_patches_dry_run_does_not_require_jwt() -> None:
    out = apply_drift_patches(
        [{"project_id": "x", "name_hint": "x"}],
        dry_run=True,
    )
    assert out["ok"] is True


def test_apply_drift_patches_apply_without_jwt_returns_config_err() -> None:
    out = apply_drift_patches(
        [{"project_id": "x", "name_hint": "x"}],
        dry_run=False,
    )
    assert out["ok"] is False
    assert out["error_kind"] == "config"
    assert "BEACON_JWT" in out["error"]


def test_apply_drift_patches_apply_marks_not_found(monkeypatch) -> None:
    monkeypatch.setenv("BEACON_JWT", "fake")
    patches = [
        {"project_id": "ghost", "name_hint": "Ghost project"},
    ]

    with respx.mock(assert_all_called=False) as respx_mock:
        respx_mock.get("https://beacon.danhle.net/api/profile/projects").mock(
            return_value=httpx.Response(200, json=[])
        )
        out = apply_drift_patches(patches, dry_run=False)

    assert out["ok"] is True
    assert out["succeeded"] == 0
    assert out["applied"][0]["reason"] == "not_found"


def test_apply_drift_patches_apply_happy_path(monkeypatch) -> None:
    monkeypatch.setenv("BEACON_JWT", "fake")
    patches = [
        {
            "project_id": PROJECT_ID,
            "name_hint": "Test Project",
            "tech_additions": ["Rust"],
        }
    ]

    with respx.mock(assert_all_called=False) as respx_mock:
        respx_mock.get("https://beacon.danhle.net/api/profile/projects").mock(
            return_value=httpx.Response(200, json=[SAMPLE_PROJECT])
        )
        respx_mock.delete(f"https://beacon.danhle.net/api/profile/projects/{PROJECT_ID}").mock(
            return_value=httpx.Response(204)
        )
        respx_mock.post("https://beacon.danhle.net/api/profile/projects").mock(
            return_value=httpx.Response(201, json={"id": "new-id-xyz"})
        )
        out = apply_drift_patches(patches, dry_run=False)

    assert out["ok"] is True
    assert out["succeeded"] == 1
    assert out["applied"][0]["ok"] is True
    assert out["applied"][0]["new_id"] == "new-id-xyz"


def test_apply_drift_patches_validates_patch_shape() -> None:
    # missing project_id should bubble up as upstream error from DriftPatch.from_dict
    out = apply_drift_patches([{"name_hint": "no id"}], dry_run=True)
    assert out["ok"] is False
    assert out["error_kind"] == "upstream"
