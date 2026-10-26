# Stream rules

omp-style **time-traveling rules**: regex patterns that sit dormant until the
model goes off-script. When assistant output matches a rule, Airgent aborts
the stream mid-token, injects the rule as an instruction, and replays the
task — the model never gets to finish the offending response.

Off by default; enable with:

```bash
export AIRGENT_STREAM_RULES=1
```

## Config

`~/.airgent/stream-rules.json` (override with `AIRGENT_STREAM_RULES_CONFIG`):

```json
{
  "rules": [
    {
      "pattern": "rm\\s+-rf\\s+/",
      "instruction": "Never run destructive filesystem commands; propose a safe alternative."
    },
    {
      "pattern": "TODO: implement",
      "instruction": "Do not leave TODO stubs; complete the implementation."
    }
  ]
}
```

Malformed entries (bad regex, missing instruction) are skipped silently.

## Behavior

- Matching runs against the **accumulated assistant text** of the current
  message as it streams; the kernel's `abort` RPC fires as soon as a rule
  matches, mid-token.
- The aborted attempt is discarded and the task is replayed with a retry
  prompt: `[Airgent stream rule] Your previous response was aborted because it
  matched the rule /…/. Instruction: …` followed by the original request.
- Up to `AIRGENT_STREAM_RULES_MAX_RETRIES` (default 2) replays per turn; if
  the model keeps tripping the rule, the last aborted response is returned
  with a `rule_triggered` progress event so surfaces can show what happened.
- With the flag off (or an empty/missing config) the run loop behaves exactly
  as before.
