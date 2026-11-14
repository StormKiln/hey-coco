"""Every Hub module imports on its own, in a fresh interpreter (no import-order luck)."""

import pkgutil
import subprocess
import sys

import coco_hub

MODULES = sorted(m.name for m in pkgutil.walk_packages(coco_hub.__path__, prefix="coco_hub."))


def test_every_module_imports_standalone():
    # One interpreter per module, all at once (sequentially this takes ~20 s).
    procs = {
        module: subprocess.Popen(
            [sys.executable, "-c", f"import {module}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        for module in MODULES
    }
    failures = {}
    for module, proc in procs.items():
        _, err = proc.communicate(timeout=60)
        if proc.returncode != 0:
            failures[module] = err.strip().splitlines()[-1]
    assert failures == {}
