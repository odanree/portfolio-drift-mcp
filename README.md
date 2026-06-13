# portfolio-drift-mcp

MCP server that wraps [`portfolio-drift-agent`](https://github.com/odanree/portfolio-drift-agent) — lets LLM clients (Claude Desktop, Claude Code, custom agents) audit a portfolio of projects against GitHub conversationally instead of round-tripping through a terminal.

## Why this exists

If you maintain a portfolio of projects (a resume, a profile site, an internal project tracker) the source records drift the moment the underlying repos evolve. `portfolio-drift-agent` audits that drift via CLI. This server exposes the same audit as MCP tools so an LLM agent can call it mid-conversation:

> _"Before I push this PR, audit Beacon's own record on my portfolio. If the tech stack is missing anything that just shipped, propose patches and dry-run them."_

The model can call `audit_single_project`, read the response, then call `apply_drift_patches` with `dry_run=true` — all without a human typing a command.

## Tools

| Tool | Args | Purpose |
|---|---|---|
| `audit_portfolio_drift` | `adapter?, limit?, model?` | Full audit. Use `limit` for quick scans — the full ~25-project audit takes 2-3 min. |
| `audit_single_project` | `project_id, adapter?, model?` | Single project, ~10 seconds. |
| `apply_drift_patches` | `patches[], dry_run=true` | HITL apply. Dry-run prints intent; pass `dry_run=false` to actually mutate Beacon. |

Every tool returns a structured envelope:

```json
{ "ok": true, ...payload }
// or
{ "ok": false, "error_kind": "config|network|not_found|upstream", "error": "..." }
```

`error_kind` lets the calling LLM branch deterministically (re-prompt for missing config vs. retry on network vs. give up on not-found) without parsing tracebacks.

## Install

```bash
pip install git+https://github.com/odanree/portfolio-drift-mcp
```

Then register in your MCP client (Claude Code example):

```bash
claude mcp add portfolio-drift python -m drift_mcp.server \
  --env ANTHROPIC_API_KEY=sk-ant-... \
  --env BEACON_JWT=... \
  --env BEACON_API_URL=https://beacon.danhle.net \
  --env GITHUB_TOKEN=ghp_...
```

Claude Desktop config (in `~/Library/Application Support/Claude/claude_desktop_config.json` or the Windows equivalent):

```json
{
  "mcpServers": {
    "portfolio-drift": {
      "command": "python",
      "args": ["-m", "drift_mcp.server"],
      "env": {
        "ANTHROPIC_API_KEY": "sk-ant-...",
        "BEACON_JWT": "...",
        "BEACON_API_URL": "https://beacon.danhle.net",
        "GITHUB_TOKEN": "ghp_..."
      }
    }
  }
}
```

## Environment

| Var | Default | When required |
|---|---|---|
| `BEACON_API_URL` | `https://beacon.danhle.net` | Always |
| `BEACON_JWT` | — | Audit with `adapter="beacon"`; apply with `dry_run=false` |
| `ANTHROPIC_API_KEY` | — | All audit tools |
| `GITHUB_TOKEN` | _(unauth)_ | Optional. Raises GitHub rate limit 60/hr → 5000/hr — strongly recommended for non-trivial portfolios. |
| `DRIFT_MODEL` | `claude-sonnet-4-6` | Anthropic model id used by the analyzer |
| `DRIFT_STALE_DAYS` | `180` | Project is flagged stale if its last commit is older than this |

Env is read on tool invocation, not at server start — missing config returns a structured `config` error rather than crashing the server.

## Tests

```bash
pip install -e .[dev]
pytest
```

13 tests cover: missing env → structured config error, unknown adapter rejection, full audit happy path with mocked Anthropic + Beacon + GitHub via `respx`, drift counting, single-project not_found path, dry-run vs apply branching, and patch validation. No network calls in CI.

## License

MIT — see [LICENSE](LICENSE).
