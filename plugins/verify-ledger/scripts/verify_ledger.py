#!/usr/bin/env python3
"""verify-ledger: keeps "tests pass" honest in Claude Code sessions. Stdlib only, no network, no model calls.

  pre            PreToolUse Bash: a piped test run gets `set -o pipefail; ` so `pytest | tail` cannot exit 0 on a failure
  post / fail    PostToolUse / PostToolUseFailure: one ledger line per edit and per test run, with its exit code
  stop           Stop / SubagentStop: a reply that claims tests pass (or done after code edits) with no passing run
                 after the last edit is stopped once, with the gap and the next step
  session-start  deletes ledgers older than 14 days
  selftest       fixture tests; prints "selftest ok"

Every hook fails open: a crash writes one line to errors.log in the data dir and lets the session go on.
Off switch: VERIFY_LEDGER=off. Extra runners: VERIFY_LEDGER_RUNNERS="regex1,regex2".
"""
import json, os, re, sys, time
from datetime import datetime
from pathlib import Path

KEEP_DAYS = 14
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
RUNNERS = [
    r"\bpytest\b", r"\bpython[\d.]*\s+-m\s+(pytest|unittest)\b", r"\b(npm|pnpm|yarn|bun)\s+(run\s+)?test",
    r"\b(vitest|jest|mocha|rspec|phpunit|ctest|tox|nox)\b", r"\b(go|cargo|deno|swift|mix|dotnet)\s+test\b",
    r"\bmvn\b.*\b(test|verify)\b", r"\bgradlew?\b.*\b(test|check)\b", r"\bmake\s+(test|check)\b",
]
NARROWED = re.compile(r"\s-k\s|\s--filter\b|\s-t\s|--testNamePattern|\s-run\s|::|\s\S+(_test\.go|\.test\.[jt]sx?|"
                      r"\.spec\.[jt]sx?|test_\w+\.py|_test\.py)\b")
WRITES = re.compile(r"(^|[^\d&>])>>?\s*(?!\s*(/dev/null|&|\$null))\S|\bsed\s+(-\w*\s+)*-i|\btee\b|"
                    r"(^|[;&|]\s*|\s)(mv|cp|rm|touch|patch|truncate)\s|\bgit\s+(apply|checkout|restore|reset|"
                    r"stash\s+pop|merge|rebase|pull|cherry-pick)\b")
TEST_CLAIM = re.compile(r"\btests?\s+(now\s+|all\s+|still\s+)?(pass|passes|passed|passing|are\s+passing|are\s+green|"
                        r"succeed\w*)\b|\b(all|every)\s+(\w+\s+){0,2}tests?\s+(\w+\s+)?pass|\b(suite|build|ci)\s+(is\s+)?"
                        r"(green|passes|passing)\b", re.I)
CHECK_CLAIM = re.compile(r"\bverified\b|\bconfirmed\s+(working|passing|fixed)\b|\bworks\s+now\b|✅", re.I)  # generic words
DONE_CLAIM = re.compile(r"\b(done|fixed|completed?|resolved|ready\s+to\s+(merge|ship))\b", re.I)
HEDGE = re.compile(r"n't\b|\b(not|never|unverified|untested|should|would|could|might|yet\s+to|unable|cannot|until|"
                   r"whether|need\s+to|please)\b|\bno\s+(tests?|test\s+runs?|runs?|way)\b|\bwithout\s+(running|"
                   r"testing|verify\w*|checking|a\s+test)\b|\b(will|may)\s+(\w+\s+)?(pass|verify|work|be\s+(green|done|"
                   r"fixed|verified))\b|\?", re.I)


def data_dir():
    d = Path(os.environ.get("CLAUDE_PLUGIN_DATA") or Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "verify-ledger")
    d.mkdir(parents=True, exist_ok=True)
    return d


def ledger_path(session_id):
    return data_dir() / (re.sub(r"[^\w.-]", "_", session_id or "unknown")[:120] + ".jsonl")


def runner_patterns():
    extra = [p.strip() for p in os.environ.get("VERIFY_LEDGER_RUNNERS", "").split(",") if p.strip()]
    out = []
    for p in RUNNERS + extra:
        try:
            out.append(re.compile(p))
        except re.error:
            continue  # a bad user pattern is skipped, never fatal
    return out


NOT_A_RUN = re.compile(r"\s*(git|echo|printf|cat|grep|rg|pip3?|brew|apt(-get)?|(uv|python[\d.]*\s+-m)\s+pip|"
                       r"(uv|poetry|cargo|go)\s+(add|install|remove|get)|(npm|pnpm|yarn|bun)\s+(i|install|add|remove|"
                       r"uninstall))\b")


def is_test(cmd):
    segs = re.split(r"&&|\|\||[;|\n]", cmd or "")
    return any(p.search(s) for s in segs if not NOT_A_RUN.match(s) for p in runner_patterns())


def short(path, cwd=None):
    """A path the reader can find: relative to the working directory when inside it, else its last 60 characters."""
    if not os.path.isabs(path):
        return path[-60:]
    try:
        rel = os.path.relpath(path, cwd or os.getcwd())
        if not rel.startswith(".."):
            return rel
    except ValueError:
        pass  # another drive on Windows
    return path if len(path) <= 60 else "..." + path[-57:]


def hides_exit(cmd):
    """True when what follows the last runner word decides the exit code: `pytest; echo $?`, `pytest || true`, or a
    pipe without pipefail. A zero exit from such a command says nothing about the tests."""
    ends = [m.end() for p in runner_patterns() for m in p.finditer(cmd)]
    tail = cmd[max(ends):] if ends else ""
    return bool(re.search(r"\|\||[;\n]\s*\S|&\s*$", tail)) or (has_pipe(tail) and "pipefail" not in cmd)


def has_pipe(cmd):
    """An unquoted single `|` (not `||`): the runner's exit code is lost to the last stage."""
    q, i = None, 0
    while i < len(cmd):
        c = cmd[i]
        if q:
            if c == "\\" and q == '"':
                i += 1
            elif c == q:
                q = None
        elif c in "'\"":
            q = c
        elif c == "\\":
            i += 1
        elif c == "|":
            if cmd[i + 1:i + 2] == "|":
                i += 2
                continue
            if cmd[i - 1:i] != "|":
                return True
        i += 1
    return False


def append(session_id, rec):
    rec = {"at": datetime.now().isoformat(timespec="seconds"), **rec}
    with ledger_path(session_id).open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def events(session_id):
    """The session's ledger, one entry per tool call (a later line for the same call wins), in call order.
    None when the session has no ledger (plugin installed mid-session)."""
    p = ledger_path(session_id)
    if not p.exists():
        return None
    by_id, order = {}, []
    for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines()):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict) or e.get("kind") not in ("edit", "run"):
            continue
        k = e.get("id") or f"line{n}"
        if k not in by_id:
            order.append(k)
        by_id[k] = e
    return [by_id[k] for k in order]


# hooks

def pre(inp):
    ti = inp.get("tool_input") or {}
    cmd = ti.get("command") or ""
    if inp.get("tool_name") != "Bash" or not is_test(cmd) or not has_pipe(cmd) or "pipefail" in cmd:
        return None
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                   "updatedInput": {**ti, "command": "set -o pipefail; " + cmd}}}


def record(inp, exit_code):
    tool, ti, sid = inp.get("tool_name"), inp.get("tool_input") or {}, inp.get("session_id")
    tid = inp.get("tool_use_id")
    if tool in EDIT_TOOLS:
        if exit_code == 0:
            append(sid, {"id": tid, "kind": "edit", "path": ti.get("file_path") or ti.get("notebook_path") or "?"})
        return None
    if tool != "Bash":
        return None
    cmd = ti.get("command") or ""
    if is_test(cmd):
        hidden = exit_code == 0 and hides_exit(cmd)
        append(sid, {"id": tid, "kind": "run", "cmd": cmd[:300], "exit": None if hidden else exit_code,
                     "narrowed": bool(NARROWED.search(" " + cmd))})
    elif WRITES.search(cmd):
        append(sid, {"id": tid, "kind": "edit", "path": cmd[:120]})
    return None


def post(inp):
    resp = inp.get("tool_response")
    if isinstance(resp, dict) and resp.get("interrupted"):
        return None
    return record(inp, 0)


def fail(inp):
    if inp.get("is_interrupt"):
        return None
    m = re.search(r"exit code (\d+)", str(inp.get("error") or ""), re.I)
    return record(inp, int(m.group(1)) if m else 1)


def prose(text):
    """The reply without code: quoted commands and output are evidence, not claims."""
    text = re.sub(r"```.*?```", " ", text or "", flags=re.S)
    text = re.sub(r"\"[^\"\n]{0,200}\"|“[^”\n]{0,200}”", " ", text)  # quoted words are not a claim
    return re.sub(r"`[^`\n]*`", " ", text)


def claim(text):
    """'all' (all tests pass), 'test' (tests pass), 'check' (verified, works now), 'done' (done / fixed) or None. A sentence that hedges
    or negates is not a claim: "not yet verified", "should pass", "I did not run the tests"."""
    found = None
    for s in re.split(r"(?<=[.!?\n])\s+|\n", prose(text)):
        if HEDGE.search(s):
            continue
        m = TEST_CLAIM.search(s)
        if m:
            if re.search(r"\b(all|every|full|whole|entire)\b", s, re.I):
                return "all"
            found = "test"
        elif found in (None, "done") and CHECK_CLAIM.search(s):
            found = "check"
        elif found is None and DONE_CLAIM.search(s):
            found = "done"
    return found


def last_message(inp):
    msg = inp.get("last_assistant_message")
    if isinstance(msg, str):
        return msg
    p = inp.get("transcript_path")
    if not p or not Path(p).exists():
        return ""
    for line in reversed(Path(p).read_text(encoding="utf-8", errors="replace").splitlines()):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") == "assistant":
            parts = (e.get("message") or {}).get("content") or []
            text = " ".join(c.get("text", "") for c in parts if isinstance(c, dict) and c.get("type") == "text")
            if text.strip():
                return text
    return ""


def hhmm(at):
    return (at or "")[11:16] or "?"


def gap(kind, evs):
    """Why the claim is not backed by the ledger, or None."""
    runs = [e for e in evs if e["kind"] == "run"]
    edits = [i for i, e in enumerate(evs) if e["kind"] == "edit"]
    if kind in ("done", "check") and not (runs and edits):
        return None, None  # "done" or "verified" speaks to tests only in a session that has tests and changed code
    if not runs:
        return ("no test run happened this session", None) if kind in ("test", "all") else (None, None)
    last_edit = edits[-1] if edits else -1
    after = [e for e in evs[last_edit + 1:] if e["kind"] == "run"]
    cmd = runs[-1]["cmd"]
    if not after:
        ok = [e for e in runs if e.get("exit") == 0]
        when = f"the last passing run ({hhmm(ok[-1]['at'])})" if ok else "the last run"
        return f"the last edit ({short(evs[last_edit].get('path', '?'))}, {hhmm(evs[last_edit]['at'])}) came after {when}", cmd
    last = after[-1]
    if last.get("exit") is None:
        return ("the last run's exit code was hidden by what follows the test command (`; echo`, `|| true` or a pipe)",
                re.sub(r"\s*(;|\|\|).*$", "", last["cmd"]))
    if last.get("exit") != 0:
        since = "after your edits " if edits else ""
        return f"the last test run {since}failed (exit {last.get('exit')})", last["cmd"]
    if kind == "all" and all(e.get("narrowed") for e in after):
        return f"only narrowed runs passed after the last edit ({after[-1]['cmd'][:80]})", after[-1]["cmd"]
    return None, None


def stop(inp):
    if inp.get("stop_hook_active"):
        return None  # never twice in a row: the second stop always goes through
    kind = claim(last_message(inp))
    if not kind:
        return None
    evs = events(inp.get("session_id"))
    if evs is None:
        return None
    why, cmd = gap(kind, evs)
    if not why:
        return None
    says = {"all": "all tests pass", "test": "the tests pass", "check": "it is verified", "done": "the work is done"}[kind]
    run = f" ({cmd if len(cmd) <= 160 else cmd[:157] + '...'})" if cmd else ""
    append(inp.get("session_id"), {"kind": "gate", "claim": kind, "gap": why})
    return {"decision": "block", "reason": f"verify-ledger: your reply says {says}, but {why}. Run the tests now{run} "
                                           "and report that result, or say plainly that you did not verify."}


def session_start(inp):
    cut = time.time() - KEEP_DAYS * 86400
    for p in data_dir().glob("*.jsonl"):
        try:
            if p.stat().st_mtime < cut:
                p.unlink()
        except OSError:
            continue
    log = data_dir() / "errors.log"
    if log.exists() and log.stat().st_size > 100_000:
        log.write_text("".join(log.read_text(encoding="utf-8", errors="replace").splitlines(True)[-200:]), encoding="utf-8")
    return None


HOOKS = {"pre": pre, "post": post, "fail": fail, "stop": stop, "session-start": session_start}


def main(argv):
    if os.environ.get("VERIFY_LEDGER", "").lower() == "off":
        return 0
    if argv[1:] == ["selftest"]:
        return selftest()
    try:
        fn = HOOKS[argv[1]]
        inp = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace") or "{}")
        out = fn(inp)
        if out:
            sys.stdout.write(json.dumps(out))
    except Exception as e:  # fail open: a broken hook never blocks work
        try:
            with (data_dir() / "errors.log").open("a", encoding="utf-8") as f:
                f.write(f"{datetime.now().isoformat(timespec='seconds')} {argv[1:2]} {type(e).__name__}: {e}\n")
        except Exception:
            pass
    return 0


def selftest():
    import tempfile
    os.environ["CLAUDE_PLUGIN_DATA"] = tempfile.mkdtemp()
    os.environ.pop("VERIFY_LEDGER_RUNNERS", None)
    n = [0]

    def bash(sid, cmd, code=0):
        n[0] += 1
        e = {"session_id": sid, "tool_name": "Bash", "tool_input": {"command": cmd}, "tool_use_id": f"t{n[0]}"}
        post(e)
        if code:
            fail(dict(e, error=f"Exit code {code}\nFAILED"))  # both events for one call: the failure wins

    def edit(sid, path="src/app.py"):
        n[0] += 1
        post({"session_id": sid, "tool_name": "Edit", "tool_input": {"file_path": path}, "tool_use_id": f"t{n[0]}"})

    def stops(sid, text, active=False):
        return stop({"session_id": sid, "last_assistant_message": text, "stop_hook_active": active})

    # pre: piped runner gets pipefail, everything else untouched
    r = pre({"tool_name": "Bash", "tool_input": {"command": "pytest -q | tail -20", "timeout": 5}})
    assert r["hookSpecificOutput"]["updatedInput"] == {"command": "set -o pipefail; pytest -q | tail -20", "timeout": 5}
    for c in ("pytest -q", "ls | wc -l", "set -o pipefail; npm test | tail", "pytest || echo x", "echo 'a|b' && pytest"):
        assert pre({"tool_name": "Bash", "tool_input": {"command": c}}) is None, c
    assert pre({"tool_name": "Bash", "tool_input": {"command": "cd a && go test ./... 2>&1 | head"}})
    # claims
    for t, k in (("All tests pass.", "all"), ("Tests pass now.", "test"),
                 ("Fixed the parser.", "done"), ("Done ✅", "check"), ("The full suite is green.", "all"),
                 ("All 12 tests pass with no failures.", "all"), ("All tests pass without errors.", "all"),
                 ("All tests pass, and I will summarize next.", "all"), ("Fixed it, and all tests pass if you use py3.", "all"),
                 ("Verified on the box.", "check")):
        assert claim(t) == k, (t, claim(t))
    for t in ("Tests not yet verified.", "This should pass.", "I did not run the tests.", "Will verify after lunch.",
              "Want me to run the tests?", "Ran `pytest` which printed `5 passed`.", "Here is the plan.", "",
              "No tests were run.", "Done without running the tests.", "The tests will pass once CI picks it up."):
        assert claim(t) is None, t
    # the real-session false positive (10-27): an honest refusal that quotes the asked-for sentence
    assert claim('The test run failed, so I can\'t reply with the required sentence "All tests pass." Output:') is None
    # #104504 shape 1, masked pipe: the run failed (pipefail made it visible), the reply says pass
    edit("s1"); bash("s1", "set -o pipefail; pytest | tail", 1)
    r = stops("s1", "All tests pass.")
    assert r["decision"] == "block" and "failed (exit 1)" in r["reason"] and "pytest | tail" in r["reason"], r
    assert stops("s1", "All tests pass.", active=True) is None, "never twice in a row"
    # shape 2, stale pass: passing run, then an edit, then the old pass reported
    bash("s2", "npm test"); edit("s2", "src/b.ts")
    r = stops("s2", "Tests pass.")
    assert "src/b.ts" in r["reason"] and "came after the last passing run" in r["reason"], r
    # shape 3, no run at all
    edit("s3")
    assert "no test run happened" in stops("s3", "Verified, tests pass.")["reason"]
    assert stops("s3", "I verified the config is valid.") is None, "generic words need a session with test runs"
    assert stops("s3", "The button works now. ✅") is None
    bash("s3", "pytest"); edit("s3")
    assert "it is verified" in stops("s3", "Verified the fix.")["reason"]
    assert '"kind": "gate"' in ledger_path("s3").read_text() and events("s3")[-1]["kind"] == "edit"
    # narrowed: a -k run backs "tests pass" but not "all tests pass"
    edit("s4"); bash("s4", "pytest -k parser")
    assert stops("s4", "Tests pass.") is None and "narrowed" in stops("s4", "All tests pass.")["reason"]
    # silent shapes: honest end, no-test work, no ledger, done in a session without tests
    edit("s5"); bash("s5", "pytest", 1); edit("s5"); bash("s5", "set -o pipefail; python -m pytest -q | tail -3")  # as pre() rewrote it
    assert stops("s5", "All tests pass.") is None and stops("s5", "Done.") is None
    edit("s6", "README.md")
    assert stops("s6", "Done, README updated.") is None, "done with no test runner in the session"
    assert stops("nobody", "All tests pass.") is None, "no ledger: installed mid-session"
    # done after an edit in a session with tests and no run since
    bash("s7", "cargo test"); edit("s7")
    assert "the work is done" in stops("s7", "Fixed.")["reason"]
    # bash writes count as edits; interrupted calls are skipped
    bash("s8", "cargo test"); bash("s8", "sed -i 's/a/b/' src/x.rs")
    assert stops("s8", "Tests pass.") and stops("s8", "Tests pass.")["decision"] == "block"
    bash("s9", "cargo test"); bash("s9", "cat x 2>&1 >/dev/null"); bash("s9", "git status")
    assert stops("s9", "Tests pass.") is None
    # a hidden exit never backs a claim (the 10-27 real session re-ran with `; echo "exit=$?"` and exited 0)
    bash("s11", 'set -o pipefail; python -m unittest -q 2>&1 | tail -3; echo "exit=$?"')
    r = stops("s11", "Tests pass.")
    assert "hidden" in r["reason"] and "exit=" not in r["reason"], r
    for c in ("pytest || true", "npm test | tail", "pytest -q; echo done", "pytest\necho done"):
        assert hides_exit(c), c
    for c in ("pytest -q", "set -o pipefail; pytest | tail", "pytest && echo ok", "cd x; pytest"):
        assert not hides_exit(c), c
    post({"session_id": "s10", "tool_name": "Edit", "tool_input": {"file_path": "a"}, "tool_response": {"interrupted": True}})
    assert events("s10") is None
    # corrupt ledger lines are skipped; a crash fails open with a log line
    with ledger_path("s5").open("a") as f:
        f.write("{not json\n[1]\n")
    assert stops("s5", "All tests pass.") is None
    old_stdin = sys.stdin
    try:
        import io
        sys.stdin = io.TextIOWrapper(io.BytesIO(b"{broken"))
        assert main(["x", "stop"]) == 0
    finally:
        sys.stdin = old_stdin
    assert "JSONDecodeError" in (data_dir() / "errors.log").read_text()
    # user runner patterns, a bad one skipped
    os.environ["VERIFY_LEDGER_RUNNERS"] = r"\bselftest\b,(bad"
    assert is_test("python3 x.py selftest") and not is_test("python3 x.py")
    os.environ.pop("VERIFY_LEDGER_RUNNERS")
    for c in ("pip install pytest", 'git commit -m "jest config"', "npm install --save-dev jest", "echo pytest"):
        assert not is_test(c), c
    for c in ("uv run pytest", "pip install -e . && pytest -q", "poetry run pytest", "cd x; npm test"):
        assert is_test(c), c
    os.environ["VERIFY_LEDGER_RUNNERS"] = ""
    os.environ.pop("VERIFY_LEDGER_RUNNERS")
    # pruning
    p = ledger_path("old")
    p.write_text("")
    os.utime(p, (time.time() - 15 * 86400,) * 2)
    session_start({})
    assert not p.exists() and ledger_path("s1").exists()
    assert short(os.path.join(os.getcwd(), "src", "calc.py")) == os.path.join("src", "calc.py")
    assert short("/" + "x" * 80 + "/calc.py", cwd="/elsewhere").endswith("/calc.py")
    print("selftest ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
