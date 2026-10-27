# verify-ledger

A Claude Code plugin that asks the agent, once, to re-run the tests when it ends a turn with "all tests pass" or "done" and its own session shows no passing test run since the last edit.

Three small hooks in standard-library Python. No model calls, no network, no telemetry.

## What it does

- **Keeps exit codes honest.** When a test command is piped (`pytest | tail -20`), it runs as `set -o pipefail; pytest | tail -20`, so a failing run cannot exit 0.
- **Keeps a ledger.** Every edit and every test run is written to a per-session file on your machine: what ran, when, and its exit code.
- **Checks the last word.** When the agent's final reply claims tests pass, a fix is verified, or work is done, and the ledger disagrees, the stop is blocked once with one plain instruction:

  > verify-ledger: your reply says all tests pass, but the last edit (src/calc.py, 14:02) came after the last passing run (13:58). Run the tests now (pytest -q) and report that result, or say plainly that you did not verify.

  The agent then re-runs the tests or says it did not verify. Claude Code shows the block as "Stop hook feedback" in the transcript, and may also show a "Stop hook error occurred" notice: that is Claude Code's label for any Stop hook that blocks, not a crash. Each block also leaves a `gate` line in the session ledger, so you can count afterwards how often it asked.

## What it blocks

- A pass claim with no test run in the session.
- A pass claim when the last edit came after the last passing run.
- A pass claim when the last run after your edits failed.
- An "all tests pass" claim when only narrowed runs (one file, `-k`, `--filter`, `-t`) passed.
- A pass claim when the last run's exit code was hidden (`pytest; echo $?`, `pytest || true`).

## What it never blocks

- An honest end: tests ran after the last edit and passed.
- Work with no tests: "done", "verified" and "works now" count only in a session that both edited files and ran tests.
- Negated, future or quoted claims: "not verified yet", "should pass", "you asked me to say \"all tests pass\"".
- A second stop in a row. It blocks at most once, so it can never trap a session (for example when the tests need a device).
- Anything, when it breaks. Every hook fails open and writes one line to `errors.log` in its data folder.

## Install

```
/plugin marketplace add theory-skills/verify-ledger
/plugin install verify-ledger
```

Needs `python3` on PATH. On Windows, Python from python.org or the Microsoft Store installs `python3`; if only `python` exists, add a `python3` alias. Without it every hook reports a hook error and the plugin does nothing; your session still works.

## Limits

- Claims are found by English phrase patterns, not by understanding. An unusual wording can slip through; a rare honest one can be asked once.
- It sees only the agent's own Bash and edit tool calls in this session. Tests run in the PowerShell tool, in another terminal or in CI are not in the ledger.
- Test runners are recognised by name (pytest, npm test, cargo test and others). Add yours with `VERIFY_LEDGER_RUNNERS`.

## Settings

- `VERIFY_LEDGER=off` turns every hook off.
- `VERIFY_LEDGER_RUNNERS` adds test-runner patterns: comma-separated regular expressions, for example `\bjust test\b,\bmake verify\b`. A bad pattern is skipped.

Ledgers live in the plugin data folder (`CLAUDE_PLUGIN_DATA`) and are deleted after 14 days.

## Check it

```
python3 plugins/verify-ledger/scripts/verify_ledger.py selftest
```

MIT licence.
