"""FastMCP server exposing drift_agent as three LLM-callable tools.

Tools:
    audit_portfolio_drift   — full audit, structured per-project reports
    audit_single_project    — fast path for one project_id (~10s)
    apply_drift_patches     — HITL apply (dry_run=true by default)

Environment (read on tool invocation, not at server start, so missing config
returns a structured error instead of crashing the server):
    BEACON_API_URL        default https://beacon.danhle.net
    BEACON_JWT            required for Beacon adapter + apply
    ANTHROPIC_API_KEY     required for all audit tools
    GITHUB_TOKEN          optional, raises rate limit 60/hr → 5000/hr
    DRIFT_MODEL           default claude-sonnet-4-6
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict
from typing import Any

from fastmcp import FastMCP

from drift_mcp.envelope import err, ok

log = logging.getLogger("drift_mcp")

mcp = FastMCP("portfolio-drift")


def _require_env(*names: str) -> str | None:
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        return f"missing env vars: {', '.join(missing)}"
    return None


def _serialize_report(report) -> dict[str, Any]:
    """DriftReport dataclass → plain dict with derived flags."""
    out = asdict(report)
    out["has_changes"] = report.has_changes
    return out


@mcp.tool
def audit_portfolio_drift(
    adapter: str = "beacon",
    limit: int | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Audit every project in the portfolio against its GitHub repo.

    Args:
        adapter: which portfolio backend to read from. "beacon" (default) or
            "file". The "file" adapter additionally requires DRIFT_PROJECTS_FILE.
        limit: stop after N projects. Useful for fast smoke tests — the full
            audit on a 25-project portfolio takes ~2-3 minutes.
        model: Anthropic model id. Defaults to the env DRIFT_MODEL setting
            (claude-sonnet-4-6 if unset).

    Returns:
        {ok: true, adapter, analyzed, drift, skipped, reports: [...]} on success,
        or {ok: false, error_kind, error} on failure.
    """
    cfg = _require_env("ANTHROPIC_API_KEY")
    if cfg:
        return err("config", cfg)
    if adapter == "beacon":
        cfg = _require_env("BEACON_JWT")
        if cfg:
            return err("config", cfg)

    try:
        from anthropic import Anthropic
        from drift_agent.adapters import BeaconAdapter, FileAdapter
        from drift_agent.analyzer import DEFAULT_MODEL, analyze
        from drift_agent.github import build_client, fetch_snapshot, parse_owner_repo

        adapter_cls = {"beacon": BeaconAdapter, "file": FileAdapter}.get(adapter)
        if adapter_cls is None:
            return err("config", f"unknown adapter: {adapter}")
        portfolio = adapter_cls()
        projects = portfolio.fetch_projects()
        if limit:
            projects = projects[:limit]

        gh = build_client()
        client = Anthropic()
        use_model = model or DEFAULT_MODEL

        reports = []
        skipped = []
        for p in projects:
            if not p.url:
                skipped.append({"name": p.name, "reason": "no url"})
                continue
            parsed = parse_owner_repo(p.url)
            if not parsed:
                skipped.append({"name": p.name, "reason": f"url not github.com ({p.url})"})
                continue
            owner, repo = parsed
            snap = fetch_snapshot(gh, owner, repo)
            if snap is None:
                skipped.append({"name": p.name, "reason": f"github fetch failed {owner}/{repo}"})
                continue
            reports.append(_serialize_report(analyze(client, p, snap, model=use_model)))

        return ok(
            {
                "adapter": adapter,
                "model": use_model,
                "analyzed": len(reports),
                "drift": sum(1 for r in reports if r["has_changes"]),
                "skipped": skipped,
                "reports": reports,
            }
        )
    except Exception as e:  # noqa: BLE001
        log.exception("audit_portfolio_drift failed")
        return err("upstream", str(e))


@mcp.tool
def audit_single_project(
    project_id: str,
    adapter: str = "beacon",
    model: str | None = None,
) -> dict[str, Any]:
    """Audit one project by id. Faster than the full audit (~10 seconds).

    Args:
        project_id: id from the portfolio backend (Beacon uses UUIDs).
        adapter: which portfolio backend to read from. Default "beacon".
        model: Anthropic model id override.

    Returns:
        {ok: true, report: {...}} on success, with error_kind="not_found"
        if the project_id isn't in the portfolio.
    """
    cfg = _require_env("ANTHROPIC_API_KEY")
    if cfg:
        return err("config", cfg)
    if adapter == "beacon":
        cfg = _require_env("BEACON_JWT")
        if cfg:
            return err("config", cfg)

    try:
        from anthropic import Anthropic
        from drift_agent.adapters import BeaconAdapter, FileAdapter
        from drift_agent.analyzer import DEFAULT_MODEL, analyze
        from drift_agent.github import build_client, fetch_snapshot, parse_owner_repo

        adapter_cls = {"beacon": BeaconAdapter, "file": FileAdapter}.get(adapter)
        if adapter_cls is None:
            return err("config", f"unknown adapter: {adapter}")
        portfolio = adapter_cls()
        project = next((p for p in portfolio.fetch_projects() if p.id == project_id), None)
        if project is None:
            return err("not_found", f"project_id {project_id} not in {adapter} portfolio")
        if not project.url:
            return err("config", f"project {project.name} has no url to audit")
        parsed = parse_owner_repo(project.url)
        if not parsed:
            return err("config", f"project url not github.com: {project.url}")
        owner, repo = parsed

        gh = build_client()
        snap = fetch_snapshot(gh, owner, repo)
        if snap is None:
            return err("network", f"github fetch failed for {owner}/{repo}")

        use_model = model or DEFAULT_MODEL
        client = Anthropic()
        report = analyze(client, project, snap, model=use_model)
        return ok({"adapter": adapter, "model": use_model, "report": _serialize_report(report)})
    except Exception as e:  # noqa: BLE001
        log.exception("audit_single_project failed")
        return err("upstream", str(e))


@mcp.tool
def apply_drift_patches(
    patches: list[dict[str, Any]],
    dry_run: bool = True,
) -> dict[str, Any]:
    """Apply curated drift suggestions to Beacon (HITL).

    Args:
        patches: list of patch dicts, each shaped like:
            {
                "project_id": "...",
                "name_hint": "...",
                "description": null | "...",
                "outcome": null | "...",
                "tech_additions": [...],
                "tech_removals": [...]
            }
        dry_run: if True (default), prints intent and returns without mutation.
            Set False to actually delete-then-recreate each project in Beacon.

    Returns:
        {ok: true, mode, attempted, succeeded, applied: [{name, new_id}]}
        on success, with error_kind="config" if BEACON_JWT is missing.
    """
    if not dry_run:
        cfg = _require_env("BEACON_JWT")
        if cfg:
            return err("config", cfg)

    try:
        from drift_agent.adapters import BeaconAdapter
        from drift_agent.apply_beacon import DriftPatch, _build_payload

        parsed = [DriftPatch.from_dict(p) for p in patches]

        if dry_run:
            return ok(
                {
                    "mode": "dry_run",
                    "attempted": len(parsed),
                    "intent": [
                        {
                            "name_hint": p.name_hint,
                            "project_id": p.project_id,
                            "tech_additions": list(p.tech_additions),
                            "tech_removals": list(p.tech_removals),
                            "description_chars": len(p.description) if p.description else 0,
                            "outcome_chars": len(p.outcome) if p.outcome else 0,
                        }
                        for p in parsed
                    ],
                }
            )

        adapter = BeaconAdapter()
        applied = []
        for patch in parsed:
            existing = adapter.fetch_one_raw(patch.project_id)
            if not existing:
                applied.append({"name_hint": patch.name_hint, "ok": False, "reason": "not_found"})
                continue
            payload = _build_payload(existing, patch)
            if not adapter.delete_project(patch.project_id):
                applied.append(
                    {"name_hint": patch.name_hint, "ok": False, "reason": "delete_failed"}
                )
                continue
            new_id = adapter.create_project(payload)
            if not new_id:
                applied.append(
                    {"name_hint": patch.name_hint, "ok": False, "reason": "create_failed"}
                )
                continue
            applied.append({"name_hint": patch.name_hint, "ok": True, "new_id": new_id})

        return ok(
            {
                "mode": "apply",
                "attempted": len(parsed),
                "succeeded": sum(1 for a in applied if a.get("ok")),
                "applied": applied,
                "note": "If your Beacon deployment indexes projects into Qdrant or similar, re-run the backfill so downstream features pick up the new project IDs.",
            }
        )
    except Exception as e:  # noqa: BLE001
        log.exception("apply_drift_patches failed")
        return err("upstream", str(e))


def main() -> None:
    """Entry point used by the console script and by `python -m drift_mcp.server`."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    mcp.run()


if __name__ == "__main__":
    main()
