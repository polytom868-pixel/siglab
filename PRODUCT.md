# Product

## Register

product

## Users

Crypto researchers and strategy operators in the SoSoValue ecosystem. They use SigLab to collect evidence (ETF flows, news, SoDEX quotes), evaluate trading strategies (backtest, walk-forward, regime gates), and make honest decisions about what to deploy. Their context: sitting at a terminal, scanning data, comparing runs, checking live-boundary readiness. They need to trust what they see because the next step might be real money.

## Product Purpose

SigLab is a research-to-action pipeline for crypto trading strategies. It collects live evidence from SoSoValue APIs and SoDEX WebSocket, evaluates strategies through walk-forward backtesting, and produces operator-facing reports that explicitly refuse to claim more than the evidence supports. Success means the operator can make a confident, evidence-backed decision about whether to deploy a strategy, and the system never lies about what's live vs. what's simulated.

## Brand Personality

Honest, technical, evidence-driven. The demo script's philosophy: "no causal claims", "signed execution refused unless preflight passes", "USD cost is not enforced — B.AI Credits are enforced as Credits, not dollars." The interface should feel like a serious research tool, not a marketing dashboard. Voice: direct, specific, no hype. When something is unknown or refused, say so clearly.

## Anti-references

- **Crypto-bro aesthetic**: neon colors, laser eyes, rocket emojis, "to the moon" energy, gamified UI. The demo script explicitly refuses hype and causal claims.
- **Generic SaaS dashboard**: blue-gradient hero, card-grid layout, hero-metric template, "empower your workflow" copy. SigLab is a research tool, not a productivity app.

## Design Principles

1. **Truth over hype**: Show what's real. When evidence is missing, say "No data available". When live execution is refused, say why. Never imply a strategy is ready when it's not.
2. **Evidence before claims**: Every report links to its evidence. Every metric shows its source. Every readiness check lists its prerequisites. The operator should be able to trace any claim back to its data.
3. **Operator clarity**: Information density serves the operator, not the designer. Show the numbers, the status, the blockers. Don't hide complexity behind clean whitespace — the operator needs to see the full picture to make a decision.

## Accessibility & Inclusion

WCAG 2.2 AA. The operator may be scanning this at 2am during a market event. Good contrast, keyboard navigation, screen reader support, reduced-motion alternatives. The dark theme is primary; light theme exists for bright environments.
