# SigLab

SigLab is an on-chain signal discovery loop for the SoSoValue ecosystem.

It uses SoSoValue Terminal feeds and SoDEX context to run a simple loop:
discover -> backtest -> review -> evolve -> ↻

Built for WaveHack as a one-person on-chain finance business.

## What it uses

- SoSoValue Terminal data for ETF flows, macro news, and sentiment
- SoDEX API for execution context and orderbook-aware evaluation
- Claude for planning, writing, and review loops

## Quick start

```bash
pip install -e .
cp .env.example .env
export SOSOVALUE_API_KEY=...
export CLAUDE_API_KEY=...
siglab --help
```

## CLI

```bash
siglab challenge init --challenge trend_signals_open
siglab challenge eval --challenge trend_signals_open
siglab challenge status
siglab deploy
siglab deployments
```

## About

This repository is the SigLab buildathon codebase for SoSoValue and SoDEX.
