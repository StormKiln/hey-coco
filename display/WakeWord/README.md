# Wake-word models (committed, bundled into the app)

openWakeWord's three-stage pipeline (<https://github.com/dscripka/openWakeWord>). XcodeGen adds
every file here except this README to the app bundle. See `docs/wake-word.md`.

| File | Source | Licence | SHA-256 |
|---|---|---|---|
| `melspectrogram.onnx` | <https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/melspectrogram.onnx> | Apache-2.0 (openWakeWord) | `ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f` |
| `embedding_model.onnx` | <https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/embedding_model.onnx> | Apache-2.0 (Google's `speech_embedding`, re-implemented by openWakeWord) | `70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f` |
| `hey_coco.onnx` | trained here: `make wakeword-train` (`display/tools/wakeword/`) | see below | |
| `LICENSE-openWakeWord.txt` | openWakeWord's Apache-2.0 licence, shipped with the two models above | | |

`make wakeword-train` checks the two feature models against these checksums before training.

`hey_coco.onnx` is trained on openWakeWord's negative datasets (ACAV100M features, AudioSet),
which is why openWakeWord licenses its own trained wake-word models CC BY-NC-SA 4.0. Treat this
one the same way: fine for this personal app, not for anything sold.

Without `hey_coco.onnx` the app runs with tap-to-talk only ("Wake word off").
