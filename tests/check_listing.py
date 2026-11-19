"""Listing check for the repo (spec listings-2026-11-19, section 8.2).

Fails when:
- "## Related: design gate pro" is not the section right after "## Install";
- that section's text differs from the same section at commit c1e9982;
- the drafted repo description is over 120 characters or lost its phrases;
- a listing keyword is missing from either plugin JSON file.

Run from the repo root: python3 tests/check_listing.py
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "c1e9982"
PAID = "## Related: design gate pro"
DESCRIPTION = "Claude Code stop hook that blocks 'tests pass' and 'done' claims no fresh test run backs"
DESCRIPTION_PHRASES = ["tests pass", "done", "stop hook"]
KEYWORDS = ["stop-hook", "done-gate", "completion-gate"]
JSON_FILES = [".claude-plugin/marketplace.json", "plugins/verify-ledger/.claude-plugin/plugin.json"]


def sections(text):
    """Split markdown into [(heading, full section text)] at '## ' headings."""
    out, cur = [], None
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.startswith("## "):
            cur = [line, [line]]
            out.append(cur)
        elif cur:
            cur[1].append(line)
    return [(h, "\n".join(body).strip()) for h, body in out]


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def check_readme():
    """The paid section stands right after Install, its text unchanged since BASE."""
    errors = []
    now = sections(read("README.md"))
    heads = [h for h, _ in now]
    if heads.count(PAID) != 1:
        errors.append(f"{PAID!r} occurs {heads.count(PAID)} times, want 1")
    elif "## Install" not in heads or heads.index(PAID) != heads.index("## Install") + 1:
        errors.append(f"{PAID!r} is not the section right after '## Install': {heads}")
    got = subprocess.run(["git", "show", f"{BASE}:README.md"], cwd=ROOT, capture_output=True)
    if got.returncode != 0:  # a shallow clone lacks BASE: say so, never a trace
        return errors + [f"base commit {BASE} not available (fetch full history)"]
    old = got.stdout.decode("utf-8")
    before = dict(sections(old)).get(PAID)
    if before is None:
        errors.append(f"{PAID!r} not found at {BASE}")
    elif dict(now).get(PAID) != before:
        errors.append(f"{PAID!r} text differs from {BASE}")
    return errors


def check_description():
    """The drafted repo description: 120 characters at most, its phrases kept, the local form in step."""
    errors = []
    if len(DESCRIPTION) > 120:
        errors.append(f"description is {len(DESCRIPTION)} characters, max 120")
    errors += [f"description lacks {p!r}" for p in DESCRIPTION_PHRASES if p not in DESCRIPTION]
    form = os.path.join(ROOT, "directory-form.md")  # local only (gitignored)
    if os.path.exists(form) and DESCRIPTION not in read("directory-form.md"):
        errors.append("directory-form.md does not carry the drafted description")
    return errors


def check_keywords():
    """Each listing keyword in both plugin JSON files."""
    errors = []
    for rel in JSON_FILES:
        data = json.loads(read(rel))
        kw = data["plugins"][0]["keywords"] if "plugins" in data else data["keywords"]
        errors += [f"{rel}: keyword {k!r} missing" for k in KEYWORDS if k not in kw]
    return errors


def main():
    errors = check_readme() + check_description() + check_keywords()
    for e in errors:
        print("FAIL:", e)
    if not errors:
        print(f"check_listing ok (description {len(DESCRIPTION)} chars)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
