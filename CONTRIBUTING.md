# Contributing

Use Python 3.12 and install the project with `uv sync --locked --python 3.12`.
Copy `config.example.yaml` to `config.yaml`; keep keys in environment variables or `.env`.
Keep changes focused and document any behavior or configuration changes.
Run `uv run --offline ruff check` and `uv run --offline ruff format --check`.
Run `uv run --offline pytest -m "not live"`; live tests require separate, explicit setup.
Never commit `.env`, credentials, generated traces, or virtual environments.
Use Conventional Commits, for example `docs: clarify setup` or `fix: handle invalid arguments`.
Explain the change and relevant verification in your pull request.
