"""Tests for check-required-contexts.py and check-action-pins.py (make repo-check)."""

import importlib.util
import pathlib
import unittest

HERE = pathlib.Path(__file__).resolve().parent


def load(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


contexts = load("check-required-contexts")
pins = load("check-action-pins")

GOOD = """\
name: CI
on:
  pull_request:
  push:
    branches: [main]
jobs:
  hub:
    runs-on: ubuntu-latest
  display:
    name: display (Xcode ${{ matrix.xcode }})
    strategy:
      matrix:
        xcode: ["16.0"]
  ci-gate:
    if: always()
    needs: [hub, display]
    runs-on: ubuntu-latest
"""


class RequiredContextsTest(unittest.TestCase):
    def test_current_workflow_passes(self):
        self.assertEqual(contexts.problems(contexts.WORKFLOW.read_text()), [])

    def test_good(self):
        self.assertEqual(contexts.problems(GOOD), [])

    def test_block_list_needs(self):
        text = GOOD.replace("needs: [hub, display]", "needs:\n      - hub\n      - display")
        self.assertEqual(contexts.problems(text), [])

    def test_renamed_gate_fails(self):
        found = contexts.problems(GOOD.replace("  ci-gate:", "  gate:"))
        self.assertEqual(len(found), 1)
        self.assertIn("ci-gate", found[0])

    def test_name_overrides_id(self):
        text = GOOD.replace("  ci-gate:\n", "  ci-gate:\n    name: All checks\n")
        self.assertTrue(any("'ci-gate'" in p for p in contexts.problems(text)))
        text = GOOD.replace("  ci-gate:\n", "  gate:\n    name: ci-gate\n")
        self.assertEqual(contexts.problems(text), [])

    def test_trigger_named_like_the_gate_does_not_count(self):
        text = GOOD.replace("  pull_request:", "  ci-gate:").replace(
            "  ci-gate:\n    if", "  gate:\n    if"
        )
        self.assertTrue(contexts.problems(text))

    def test_gate_must_run_always(self):
        found = contexts.problems(GOOD.replace("    if: always()\n", ""))
        self.assertTrue(any("always()" in p for p in found))
        found = contexts.problems(GOOD.replace("if: always()", "if: success()"))
        self.assertTrue(any("always()" in p for p in found))

    def test_gate_must_need_every_job(self):
        found = contexts.problems(GOOD.replace("needs: [hub, display]", "needs: [hub]"))
        self.assertEqual(found, ["ci-gate does not need: display. Add them to its `needs:`."])

    def test_gate_must_not_be_a_matrix(self):
        text = GOOD + "    strategy:\n      matrix:\n        x: [1]\n"
        self.assertTrue(any("matrix" in p for p in contexts.problems(text)))

    def test_floor(self):
        self.assertEqual(len(contexts.REQUIRED_CONTEXTS), contexts.EXPECTED_COUNT)


SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"


class ActionPinsTest(unittest.TestCase):
    def check(self, uses: str):
        return pins.problems(
            f"permissions:\n  contents: read\njobs:\n  a:\n    steps:\n      - {uses}\n"
        )

    def test_current_workflows_pass(self):
        files = pins.workflow_files()
        self.assertGreaterEqual(len(files), 3)
        total = 0
        for f in files:
            found, checked = pins.problems(f.read_text())
            self.assertEqual(found, [], f.name)
            total += checked
        self.assertGreater(total, 0)

    def test_pinned_with_comment(self):
        self.assertEqual(self.check(f"uses: actions/checkout@{SHA} # v7.0.1"), ([], 1))
        self.assertEqual(self.check(f"uses: owner/repo/sub/path@{SHA} # v2"), ([], 1))

    def test_tag_fails(self):
        found, _ = self.check("uses: actions/checkout@v4")
        self.assertIn("40-hex", found[0])

    def test_short_sha_fails(self):
        found, _ = self.check(f"uses: actions/checkout@{SHA[:12]} # v7.0.1")
        self.assertIn("40-hex", found[0])

    def test_missing_comment_fails(self):
        found, _ = self.check(f"uses: actions/checkout@{SHA}")
        self.assertIn("# vX.Y.Z", found[0])

    def test_local_action_exempt(self):
        self.assertEqual(self.check("uses: ./.github/actions/setup"), ([], 0))

    def test_docker_ref_fails(self):
        found, _ = self.check("uses: docker://rhysd/actionlint:latest")
        self.assertTrue(found)

    def test_top_level_permissions_required(self):
        found, _ = pins.problems("jobs:\n  a:\n    permissions:\n      contents: read\n")
        self.assertTrue(any("permissions" in p for p in found))


if __name__ == "__main__":
    unittest.main()
