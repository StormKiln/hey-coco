#!/usr/bin/env python3
"""`ci.yml` must still report the status context the `main` ruleset requires.

The ruleset requires exactly one check, `ci-gate` (see .github/CLAUDE.md). A required check
that never REPORTS blocks every merge forever: rename the job and the ruleset goes on waiting
for a context nothing produces, and no branch can fix that, only a ruleset edit by an admin.
(Borrowed from headstate's scripts/check-required-contexts.py, which exists for that lockout.)

So this asserts, against the committed REQUIRED_CONTEXTS (the contract; not the live API, so
an unreachable GitHub never fails lint):

- a job reports each required context (its id, or its `name:` if it has one);
- the `ci-gate` job `needs` every other job, so a new job cannot merge unchecked;
- the gate runs `if: always()`, or a failed dependency would SKIP it, and branch protection
  treats a skipped required check as passing.

Parsed by indentation, stdlib only: `ci.yml` is hand-written and consistently indented, and a
guard that needs its own package install is a guard that gets dropped.
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

# The contexts the `main` ruleset requires. Change this in the same PR as any ruleset edit or
# gate rename, and change the ruleset in Settings right after that PR merges.
REQUIRED_CONTEXTS = ("ci-gate",)
GATE = "ci-gate"
# Asserted first so a truncated or emptied list cannot agree with every workflow.
EXPECTED_COUNT = 1


def jobs_of(text: str) -> dict[str, list[str]]:
    """{job id: its lines}, from the top-level `jobs:` block only (jobs sit at 2 spaces)."""
    jobs: dict[str, list[str]] = {}
    current = None
    in_jobs = False
    for line in text.splitlines():
        if re.match(r"^[A-Za-z0-9_-]+:", line):
            in_jobs = line.startswith("jobs:")
            current = None
            continue
        if not in_jobs:
            continue
        m = re.match(r"^  ([A-Za-z0-9_-]+):\s*(#.*)?$", line)
        if m:
            current = m.group(1)
            jobs[current] = []
        elif current is not None:
            jobs[current].append(line)
    return jobs


def job_key(lines: list[str], key: str) -> str | None:
    """Value of a job-level (4-space) scalar key, quotes and comments stripped."""
    for line in lines:
        m = re.match(rf"^    {key}:\s*(.*?)\s*(#.*)?$", line)
        if m:
            return m.group(1).strip("'\"")
    return None


def job_needs(lines: list[str]) -> list[str]:
    for i, line in enumerate(lines):
        m = re.match(r"^    needs:\s*(.*?)\s*(#.*)?$", line)
        if not m:
            continue
        value = m.group(1)
        if value.startswith("["):
            return [n.strip().strip("'\"") for n in value.strip("[]").split(",") if n.strip()]
        if value:
            return [value.strip("'\"")]
        needs = []
        for item in lines[i + 1 :]:
            mm = re.match(r"^      -\s*([A-Za-z0-9_-]+)", item)
            if not mm:
                break
            needs.append(mm.group(1))
        return needs
    return []


def reported_context(job_id: str, lines: list[str]) -> str:
    return job_key(lines, "name") or job_id


def problems(text: str) -> list[str]:
    if len(REQUIRED_CONTEXTS) != EXPECTED_COUNT:
        return [
            f"REQUIRED_CONTEXTS has {len(REQUIRED_CONTEXTS)} entries, expected {EXPECTED_COUNT}"
        ]
    jobs = jobs_of(text)
    if not jobs:
        return ["no jobs found in ci.yml (parser out of date?)"]
    found = []
    contexts = {reported_context(j, lines): j for j, lines in jobs.items()}
    for ctx in REQUIRED_CONTEXTS:
        if ctx not in contexts:
            found.append(
                f"no job reports the required context {ctx!r}. Renaming it locks every merge "
                "until the ruleset is edited (.github/CLAUDE.md)."
            )
    gate = contexts.get(GATE)
    if gate is None:
        return found
    lines = jobs[gate]
    if (job_key(lines, "if") or "").replace(" ", "") not in ("always()", "${{always()}}"):
        found.append(f"{GATE} must run `if: always()`: a skipped required check counts as passing")
    if any(re.match(r"^    strategy:", line) for line in lines):
        found.append(f"{GATE} must not be a matrix: that renames its context")
    missing = sorted(set(jobs) - {gate} - set(job_needs(lines)))
    if missing:
        found.append(f"{GATE} does not need: {', '.join(missing)}. Add them to its `needs:`.")
    return found


def main() -> int:
    found = problems(WORKFLOW.read_text())
    for p in found:
        print(f"{WORKFLOW.relative_to(ROOT)}: {p}", file=sys.stderr)
    if found:
        return 1
    print(f"Required contexts OK: {', '.join(REQUIRED_CONTEXTS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
