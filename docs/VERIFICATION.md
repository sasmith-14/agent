# Phase 1 verification

Checked on macOS with Python 3.12.14 (2026-10-28).

- 81 offline tests passed, two live smoke tests deselected. The suite covers
  normalized types, YAML/env/dotenv config precedence and invalid config,
  retries/status/timeouts/Retry-After, both provider wire formats, schemas,
  calculator safety, permissions, agent outcomes, limits, traces/redaction,
  sequential calls, separate instances, CLI approval, and exit codes.
- The full CLI test includes a real Ollama adapter against MockTransport, both
  calculator and get_time, interactive approvals, and token accounting. It is
  offline evidence, not a successful live-provider run.
- Ruff lint and formatting checks passed.
- Gemini generateContent schema, JSON-schema declarations, function-result IDs,
  and thought-signature history were checked against official documentation.
  Payload tests verify signed parts survive unchanged and tool results are
  grouped correctly.
- Package downloads from the shell are blocked by DNS/network restrictions.
  A network-enabled `uv sync` attempt was automatically rejected because
  sandbox escalation is disabled. Tests used copied existing uv cache entries
  and official Typer 0.16.1 sources fetched through the connected GitHub tool
  into `/private/tmp/pilot-dependency-src`, not into this repository. Two
  third-party Click deprecation warnings were reported.
- No local Ollama server responded on port 11434. A real Gemini CLI run was
  attempted using the existing environment key; it stopped at the provider
  connection boundary, with a RunFinished error recorded under `runs/`.
- Plain `uv run pytest`, clean-install packaging, and the installed `pilot`
  entry point remain unverified: Typer was not in the local cache, and approval
  for a build-only setuptools dependency is pending. No extra dependency was
  added beyond the brief's allowlist.
- The workspace initially had no Git repository; `git init` was denied by the
  filesystem policy. `.gitignore` is supplied, and nothing was committed.

Reproduce the temporary offline test setup in this environment:

```sh
PYTHONPATH=/private/tmp/pilot-dependency-src:src \
UV_CACHE_DIR=/private/tmp/pilot-uv-cache \
uv run --offline --no-project \
  --python /Users/sasmith/.local/share/uv/python/cpython-3.12.14-macos-aarch64-none/bin/python3 \
  --with pydantic --with pydantic-settings --with pyyaml --with pytest \
  --with httpx --with rich --with click --with ruff pytest -q
```

The temporary Typer sources are a test-environment workaround, not a vendoring
or installation strategy. Once dependency installation and packaging are
available, run the normal README commands, then the interactive two-tool task
against Gemini or Ollama before claiming live end-to-end acceptance.
