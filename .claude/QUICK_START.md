# Quick start (most-run commands)

```sh
uv sync                       # install deps + dev tools (pytest, ruff, mypy)
uv run pytest -q              # run the test suite (add a path for one module)
uv run ruff check .          # lint (select E,F,I,UP,B,SIM,RUF; line-length 100)
uv run ruff format .         # format (CI gates on `--check`)
uv run mypy src               # type-check

# Run the service locally (API + worker share Redis):
uv run uvicorn caldrith.api.app:create_app --factory --port 8000
uv run arq caldrith.worker.worker.WorkerSettings

# Liveness/readiness:
#   GET /healthz  (no deps)   GET /readyz  (Redis reachable)

# Docs (docs group: mkdocs-material):
uv run --group docs mkdocs serve   # live preview at :8000
uv run --group docs mkdocs build   # render ./site
```

(If `uv` is not on PATH, `python -m uv ...` works after `pip install uv`.)
