"""Runs openWakeWord's train.py (argv[1]) with its remaining arguments, patched for macOS.

openWakeWord only supports training on Linux. Two fixes, without editing its pinned source:

- Its DataLoader workers get closures, which only `fork` can hand over; macOS defaults to
  `spawn`, which must pickle them and fails.
- Its flags declare `default="False"` (a non-empty, truthy string), so `--convert_to_tflite` is
  always on and needs TensorFlow, which has no macOS arm64 wheel for its pinned version. The
  ONNX model is all the app uses.
"""

import argparse
import multiprocessing
import runpy
import sys

_add_argument = argparse.ArgumentParser.add_argument


def _add_argument_with_real_false(self, *args, **kwargs):
    if kwargs.get("default") == "False":
        kwargs["default"] = False
    return _add_argument(self, *args, **kwargs)


if __name__ == "__main__":
    multiprocessing.set_start_method("fork")
    argparse.ArgumentParser.add_argument = _add_argument_with_real_false
    sys.argv = sys.argv[1:]
    runpy.run_path(sys.argv[0], run_name="__main__")
