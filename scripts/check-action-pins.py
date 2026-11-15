#!/usr/bin/env python3
"""Every third-party action is pinned to a commit, and every workflow starts least-privileged.

- `uses:` must be `owner/repo[/path]@<40-hex commit SHA> # vX.Y.Z`. A tag is mutable: whoever
  controls the action can repoint it at new code that then runs with the job's token and
  secrets (the TestFlight job holds the App Store Connect key). A SHA cannot be repointed; the
  comment says which release it is. Dependabot (.github/dependabot.yml) updates both.
  Local `./` actions are exempt: they are this repository.
- Each workflow sets top-level `permissions:`, so a job gets only what it asks for.

Borrowed from headstate's scripts/check-supply-chain-pins.py. Stdlib only, line-based.
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

USES = re.compile(r"^\s*(?:-\s*)?uses:\s*(\S+)(.*)$")
PINNED = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}$")
VERSION_COMMENT = re.compile(r"^\s*#\s*v\d+(\.\d+){0,2}\b")


def workflow_files(root: pathlib.Path = ROOT) -> list[pathlib.Path]:
    gh = root / ".github"
    return sorted(
        list((gh / "workflows").glob("*.yml"))
        + list((gh / "workflows").glob("*.yaml"))
        + list((gh / "actions").glob("*/action.yml"))
    )


def problems(text: str, is_workflow: bool = True) -> tuple[list[str], int]:
    """(problems, number of `uses:` lines checked)."""
    found = []
    checked = 0
    for n, line in enumerate(text.splitlines(), 1):
        m = USES.match(line)
        if not m:
            continue
        ref, rest = m.group(1).strip("'\""), m.group(2)
        if ref.startswith("./"):
            continue
        checked += 1
        if not PINNED.match(ref):
            found.append(f"line {n}: {ref} is not pinned to a 40-hex commit SHA")
        elif not VERSION_COMMENT.match(rest):
            found.append(f"line {n}: {ref} needs a trailing `# vX.Y.Z` comment")
    if is_workflow and not re.search(r"^permissions:", text, re.MULTILINE):
        found.append("no top-level `permissions:` (start with `contents: read`)")
    return found, checked


def main() -> int:
    files = workflow_files()
    if not files:
        print("no workflow files found", file=sys.stderr)
        return 1
    bad = False
    total = 0
    for f in files:
        found, checked = problems(f.read_text(), is_workflow=f.parent.name == "workflows")
        total += checked
        for p in found:
            bad = True
            print(f"{f.relative_to(ROOT)}: {p}", file=sys.stderr)
    # A guard that matched nothing must not report success.
    if total == 0:
        print("no `uses:` lines found at all (parser out of date?)", file=sys.stderr)
        return 1
    if bad:
        return 1
    print(f"Action pins OK: {total} uses in {len(files)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
