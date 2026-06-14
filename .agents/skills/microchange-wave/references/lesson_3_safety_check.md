# Lesson 3 — Safety Check

**Lesson**: A microchange that touches the live boundary (SoDEX signed writes, SoSoValue `x-soso-api-key` calls, deploy gating, B.AI credits-vs-USD conflation) MUST be sanity-checked with a single one-liner that confirms the safety property still holds. Reading the diff and nodding is not a safety check.

**Rule**: Before any apply step that touches `siglab/cli/*`, `siglab/data/sodex*`, `siglab/data/sosovalue*`, or `siglab/orchestration/*_runner.py`, run the safety check one-liner and paste its output in the apply report. If the check does not exist yet for the property, write it.

## Safety check one-liner

```bash
# Live-boundary safety sweep — run from repo root, paste the exit code + summary.
python -c "
import re, pathlib
ROOT = pathlib.Path('siglab')
hits = []
for p in ROOT.rglob('*.py'):
    text = p.read_text(encoding='utf-8', errors='ignore')
    if re.search(r'live_write\s*=\s*True', text):  hits.append((str(p), 'live_write=True'))
    if re.search(r'submitted\s*=\s*True', text):    hits.append((str(p), 'submitted=True'))
    if 'usd_cost_claimed' in text and 'True' in text.split('usd_cost_claimed')[1][:80]:
        hits.append((str(p), 'usd_cost_claimed=True (must be False)'))
    if 'SODEX_PRIVATE_KEY' in text and 'print' in text:
        hits.append((str(p), 'private key in print path'))
print('exit_code:', 0 if not hits else 1)
for h in hits: print('HIT:', h)
" ; echo "safety-check-exit=$?"
```

What it checks:

1. **`live_write=True`** — any code path that can actually write to the SoDEX venue must be gated, and `live_write=True` outside a preflight-confirmed path is the canonical regression.
2. **`submitted=True`** — a signed-request builder that flips into "submitted" without going through the preflight gate is the worst class of bug.
3. **`usd_cost_claimed=True`** — B.AI credits must never be presented as USD. This guard fires if a manifest or report claims USD cost for provider usage.
4. **Private key in print path** — `SODEX_PRIVATE_KEY` (or any signer secret) appearing inside a `print(...)` / log statement is a one-step leak.

Exit code 0 means clean. Exit code 1 means a hit was printed — the apply step MUST stop and revise. Paste the exact `safety-check-exit=$?` line in the apply report.

## Failure mode this lesson prevents

Lesson 3 was learned when an apply step changed a preflight helper's return type from `bool` to a richer `dict` and silently dropped the `live_write_allowed` field at one branch. The diff looked innocuous. The preflight now returned a dict that *looked* like it had `live_write_allowed` (it had a `live_write` sibling) — and `deploy` consumed it, treating the missing flag as False (default), which is the safe direction, but the safety property of "the report dict has a typed `live_write_allowed` field" was broken. The one-liner would have caught the regression by flagging the new code path's shape; the diff review did not.

## How to apply

- Touching live boundary code? Run the one-liner. Paste the output. Do not summarize it as "ran safety check, all clear" — paste the lines.
- The one-liner is the floor, not the ceiling. Add module-specific checks (e.g. `--no-preflight` overrides, shell=True with f-string interpolation) to a per-module safety script that the one-liner grows into over time.
- If the check fires: stop, fix the regression, re-run. Do not "acknowledge and proceed."
