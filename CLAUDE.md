# anomalog

Self-hosted ML anomaly detection for logs. Python 3.12 + Rust (PyO3) extension.

## Commands

| Command | Description |
|---------|-------------|
| `uv sync` | Install dependencies + build Rust extension |
| `uv run pytest` | Run all tests |
| `uv run ruff check src/ tests/` | Lint |
| `uv run ruff format src/ tests/` | Format |
| `uv run anomalog serve -c config.yaml` | Start server |
| `uv run anomalog check-config -c config.yaml` | Validate config |
| `uv run anomalog train -c config.yaml -s nginx` | Manual baseline training |

## Architecture

```
src/anomalog/
├── parser/     # PyO3 bridge to logmole-core + Python fallback
├── ingest/     # File tailing (watchdog), HTTP POST, Loki push
├── ml/         # Baseline training, 4 detection algorithms, orchestrator
├── alert/      # Telegram, email, Slack, Alertmanager + dispatcher + cooldown
├── storage/    # DuckDB (analytics) + SQLite (state)
├── dashboard/  # htmx dashboard (Jinja2 templates)
├── server.py   # FastAPI app factory with lifespan
├── cli.py      # Click CLI (serve, check-config, train)
└── config.py   # Pydantic v2 YAML config
rust/           # PyO3 extension (logmole-core bridge + DrainTree)
```

## Key Patterns

- **Parser bridge**: Tries Rust (logmole-core via PyO3), falls back to pure Python
- **Storage**: DuckDB for analytics (logs, anomalies), SQLite for state (offsets, cooldowns, model metadata)
- **Detection**: 4 algorithms (error rate z-score, novel patterns, latency KS-test, frequency deviation)
- **Alerting**: 4 channels with cooldown dedup, parallel dispatch
- **Dashboard**: htmx + Jinja2, polling every 30s
- **Logging**: structlog (not print or logging)
- **Config**: pydantic v2 with YAML loading
- **Testing**: pytest with pytest-asyncio

## Cross-references

- Product definition: /home/krtffl/Documents/product-portfolio/products/P3-anomalog.md
- Tech specification: /home/krtffl/Documents/product-portfolio/products/P3-anomalog-tech.md
- logmole-core (Rust parsing): /home/krtffl/Documents/logmole/logmole-core/
