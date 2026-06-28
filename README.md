# SigLab

[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/release/python-3120/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-556%20passing-brightgreen.svg)]()

On-chain signal discovery loop. Fetches evidence from SoSoValue (ETF flows, news, currencies) and SoDEX market data, runs bounded research pipelines, and surfaces operator-facing reports via CLI and dashboard.

---

## Quick Start

```bash
pip install -e .
cp .env.example .env   # configure API keys
```

Required in `.env`:
- `SOSOVALUE_API_KEY` — SoSoValue data API
- `ANTHROPIC_AUTH_TOKEN` — LLM-powered research tools
- `SODEX_API_KEY` / `SODEX_SECRET` — SoDEX market data

## Commands

| Command | What |
|---------|------|
| `siglab demo run` | Full pipeline: collect evidence → research → report |
| `siglab demo manifest` | Index all generated artifacts |
| `siglab market-report` | Build operator-facing decision report from evidence |
| `siglab telemetry-report` | Aggregate LLM/tool telemetry |
| `siglab operator` | Evidence-to-decision cycle (interactive) |
| `siglab evidence-build` | Fetch SoSoValue + SoDEX evidence to JSONL |
| `siglab dashboard-start` | Start FastAPI dashboard (:8080 or $PORT) |
| `siglab dashboard-stop` | Stop dashboard |
| `siglab sodex-preflight` | Verify SoDEX connectivity |

## Project Layout

```
siglab/
├── cli/              # 8 command modules + rich_utils
├── data/             # feeds, providers, store, evidence
├── evaluation/       # backtest, compile, events, feature_dsl
├── live/             # paper_client, sodex_ws, exporter
├── dashboard/        # FastAPI + Jinja2 templates + static assets
├── llm/              # Anthropic SDK tools + tool definitions
├── operator/         # evidence-to-decision pipeline
├── config.py         # pydantic-settings config
├── telemetry.py      # LLM/tool metrics aggregation
└── utils.py          # shared helpers
```

## Deployments

| Target | URL | Method |
|--------|-----|--------|
| Railway | `https://dashboard-production-9d67.up.railway.app` | Docker + uvicorn |
| Vercel | `https://siglab-snowy.vercel.app` | Serverless FastAPI |

## Status

- **Tests:** 556 pass, `-m "not slow"` fast path ~14s
- **Files:** 61 tracked, ~15,500 lines (siglab/ + tests/)
- **Python:** 3.12
- **License:** MIT

## License

MIT — see [LICENSE](LICENSE).
