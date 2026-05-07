# AI Code Usage Metrics

This directory is produced by `.opencode/plugins/ai-code-usage.js`.

The metric is based on code-file git diff lines created during an OpenCode session:

- `added`: code lines added since the session baseline
- `deleted`: code lines deleted since the session baseline
- `net`: `added - deleted`
- `files`: changed code files counted in the metric
- `byLanguage`: totals grouped by file extension

The plugin does not count tokens. It counts code that actually lands in the working tree.

## Files

```text
.opencode/ai-usage/usage.jsonl
.opencode/ai-usage/summary.json
.opencode/ai-usage/state.json
```

`usage.jsonl` is append-only event history.

`summary.json` is the current aggregate view by date and by session.

`state.json` stores the per-session baseline used to subtract pre-existing dirty changes.

## Requirements

Run OpenCode inside a git repository. The plugin uses:

```bash
git diff --numstat --
```

If the project is not a git repository, the totals will stay at zero.
