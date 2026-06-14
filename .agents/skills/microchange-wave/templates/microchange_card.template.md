# Microchange Card

<!--
This template is filled in by the apply agent for every microchange.
Fill in every section. Do not delete sections; if a section is not
applicable, write "N/A" with a one-line reason.
-->

## Plan item

- Plan reference: <PLAN_ID> / <ITEM_ID>
- Description: <one-sentence summary of the change>
- Owner: <agent id or human name>
- Date (UTC): <YYYY-MM-DD>

## File:line of change

- File: <relative/path/from/repo/root>
- Function/block: <symbol or block name>
- Line(s): <start-end>
- Other files touched in same delta: <list, or "none">

## BIG_DELTA

<one-sentence human-readable description of the semantic change.
  What is now true that was not true before?>

## SMARTER_DELTA

<one-sentence description of the simpler / less-coupled / less-surprising
  shape this change took vs. the naive fix. If the change is already the
  simplest possible form, write "N/A — change is already minimal.">

## DELTA_SHIPPED

- [ ] Code change applied (yes / no)
- [ ] Re-read of cited line range performed (yes / no, with timestamp)
- [ ] No other files modified outside this card's scope (yes / no)
- [ ] No tests suppressed to make change pass (yes / no)
- [ ] No compatibility shim / alias / deprecation path left behind (yes / no)

## PROOF_COMMAND output

<!--
Paste the exact command run, then the captured output. Do not trim.
If output is long, attach as artifact://<id> and link here.
-->

Command:

```bash
<command>
```

Output:

```
<verbatim output>
```

## SAFETY CHECK output

<!--
Run the 3 safety checks (see safety_oneshot.template.sh) and paste the
combined output. Each check must report OK. If any reports FAIL, the
delta is not shipped — fix and re-run before marking DELTA_SHIPPED.
-->

Command:

```bash
<safety_oneshot command with FILE and EXPECTED_LINES filled in>
```

Output:

```
<verbatim output>
```

## diff (before/after)

<!--
Show the exact before/after for the changed range. Prefer unified diff
format. If the change is multi-file, include a diff per file.
-->

```diff
--- a/<file>
+++ b/<file>
@@ -<start>,<count> +<start>,<count> @@
-<before line>
+<after line>
```

## post-edit re-read

<!--
After applying the edit, re-read the cited line range and confirm the
new content matches what the diff claims. Paste the re-read snippet
with line numbers (use `read` with offset/limit, not :raw).
-->

Re-read command:

```bash
read <file>:<start>-<end>
```

Re-read snippet:

```
<numbered lines from re-read, must match the +lines in the diff>
```

## Acceptance checklist

- [ ] Every section above is filled in (or marked N/A with reason)
- [ ] PROOF_COMMAND output is verbatim — not paraphrased
- [ ] SAFETY CHECK output shows all three checks OK
- [ ] diff matches the post-edit re-read exactly
- [ ] No claim in this card is unobserved
