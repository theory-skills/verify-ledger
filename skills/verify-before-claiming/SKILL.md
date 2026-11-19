---
name: verify-before-claiming
description: Prevents false completion. Before ending a turn with "tests pass", "fixed" or "done", re-run the tests after the last edit and report that run; pipe test runs with pipefail; say plainly when nothing was verified.
---

# Verify before claiming

A claim that tests pass, that a fix works or that work is done is a report of a test run. Make the run, then report it.

This skill is the text only: nothing checks it. The hooks that enforce it are the verify-ledger plugin for Claude Code. It keeps a per-session ledger of edits and test runs, adds pipefail to piped test runs, and blocks a pass claim the ledger contradicts, once. To add the hooks:

```
/plugin marketplace add theory-skills/verify-ledger
/plugin install verify-ledger
```

## Before you end a turn with a pass claim

1. Look at the order of events in this session. If you edited any file after the last passing test run, run the tests again now.
2. Run the full suite, not a narrowed one (one file, `-k`, `--filter`, `-t`), before you say "all tests pass". If you only ran a narrowed set, say which set.
3. Keep the exit code visible. When you pipe a test run in bash or zsh, start it with `set -o pipefail;` (for example `set -o pipefail; pytest | tail -20`), so a failing run cannot exit 0. Do not hide the code with `; echo $?` or `|| true`.
4. If the last run after your edits failed, say it failed. Do not report an earlier passing run.
5. Report the run you made: the command, and passed or failed.

## When you cannot verify

Say so in plain words: "I did not run the tests" or "this needs a device I cannot reach, so it is not verified". That is an honest end. Do not write "should pass" as if it were a result.

## What does not need this

- An end where tests ran after the last edit and passed.
- Work with no tests at all. "Done" is fine there, as long as you do not claim a test result.
- Negated, future or quoted claims, such as "not verified yet".
