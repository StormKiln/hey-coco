#!/usr/bin/env python3
"""Train the "hey coco" openWakeWord model on this Mac (make wakeword-train; docs/wake-word.md).

    train.py --work DIR --out display/WakeWord/hey_coco.onnx [--smoke] [--steps N]

Follows openWakeWord's automatic_model_training notebook, pinned: synthetic "hey coco" clips
(and adversarial near-misses) from Piper TTS via piper-sample-generator, augmented with MIT room
impulse responses and AudioSet background noise, then trained against openWakeWord's
precomputed ACAV100M negative features with its validation set for false-positive early
stopping. Every stage is resumable: downloads continue where they stopped, and openWakeWord's
train.py skips clips and features it already has, so re-running after an interruption is fine.

--smoke runs the same stages on a tiny slice (a few clips, a 60 MB piece of the negative
features, synthetic noise for background, 100 steps) in WORK/smoke, to check the toolchain in
minutes. Its model is useless and is never copied to --out.

Downloads (full run): ACAV100M features 17.3 GB, validation features 185 MB, Piper voice 204 MB,
AudioSet 2 shards ~1.4 GB, room impulse responses 8 MB; plus this environment (~1.1 GB).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

# Pinned sources. openWakeWord main at 368c037 (2025-12-30): its train.py only converts to
# tflite (TensorFlow, no macOS arm64 wheel for the pinned version) when asked to.
OWW_COMMIT = "368c03716d1e92591906a84949bc477f3a834455"
OWW_ZIP = f"https://codeload.github.com/dscripka/openWakeWord/zip/{OWW_COMMIT}"
# piper-sample-generator v2.0.0: the generate_samples() API openWakeWord's train.py calls.
PIPER_GEN_COMMIT = "195e3bd967d54589c2137c9de2b22ad526ba6b6f"
PIPER_GEN_ZIP = f"https://codeload.github.com/rhasspy/piper-sample-generator/zip/{PIPER_GEN_COMMIT}"
PIPER_VOICE_URL = (
    "https://github.com/rhasspy/piper-sample-generator/releases/download/v2.0.0/"
    "en_US-libritts_r-medium.pt"
)
HF = "https://huggingface.co"
FEATURES_URL = (
    f"{HF}/datasets/davidscripka/openwakeword_features/resolve/main/"
    "openwakeword_features_ACAV100M_2000_hrs_16bit.npy"
)
VALIDATION_URL = (
    f"{HF}/datasets/davidscripka/openwakeword_features/resolve/main/validation_set_features.npy"
)
RIR_TREE = f"{HF}/api/datasets/davidscripka/MIT_environmental_impulse_responses/tree/main/16khz"
RIR_FILE = f"{HF}/datasets/davidscripka/MIT_environmental_impulse_responses/resolve/main"
AUDIOSET_SHARDS = [
    f"{HF}/datasets/agkphysics/AudioSet/resolve/main/data/bal_train/{n:02d}.parquet"
    for n in range(2)
]

# The two feature models are committed next to the output; openWakeWord's train.py loads them
# from its package's resources/models. Their checksums are in display/WakeWord/README.md.
FEATURE_MODELS = {
    "melspectrogram.onnx": "ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f",
    "embedding_model.onnx": "70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f",
}

# Near-misses the model must NOT fire on, on top of the ones openWakeWord derives itself.
NEGATIVE_PHRASES = [
    "hey cocoa",
    "coco",
    "hey google",
    "hey coach",
    "hey kokomo",
    "okay coco",
    "hey cuckoo",
    "go go",
    "hey Rocco",
    "hey Coca-Cola",
    "hey Co",
    "hello",
]


def log(message: str) -> None:
    print(f"[wakeword-train] {message}", flush=True)


def download(url: str, dest: Path, limit: int | None = None) -> Path:
    """Downloads url to dest, resuming a partial .part file. `limit` stops after that many bytes."""
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = {"User-Agent": "hey-coco-wakeword-train"}
    end = "" if limit is None else str(limit - 1)
    if have or limit is not None:
        headers["Range"] = f"bytes={have}-{end}"
    log(f"downloading {url} -> {dest}" + (f" (resuming at {have >> 20} MB)" if have else ""))
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request) as response, part.open("ab" if have else "wb") as out:
        if have and response.status != 206:
            raise SystemExit(f"{url} ignored the resume request; delete {part} and retry")
        shutil.copyfileobj(response, out, length=8 << 20)
    part.rename(dest)
    return dest


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def unpack_zip(url: str, dest: Path) -> Path:
    if dest.exists():
        return dest
    archive = download(url, dest.with_suffix(".zip"))
    with zipfile.ZipFile(archive) as z:
        top = z.namelist()[0].split("/")[0]
        z.extractall(dest.parent)
    (dest.parent / top).rename(dest)
    return dest


def npy_head(url: str, dest: Path, rows: int) -> Path:
    """The first `rows` rows of a remote .npy (an HTTP range), saved as a valid .npy."""
    import numpy as np

    if dest.exists():
        return dest
    probe = urllib.request.Request(url, headers={"Range": "bytes=0-4095"})
    with urllib.request.urlopen(probe) as response:
        header = response.read()
    stream = io.BytesIO(header)
    version = np.lib.format.read_magic(stream)
    if version == (1, 0):
        shape, _, dtype = np.lib.format.read_array_header_1_0(stream)
    else:
        shape, _, dtype = np.lib.format.read_array_header_2_0(stream)
    offset = stream.tell()
    rows = min(rows, shape[0])
    row_bytes = int(np.prod(shape[1:])) * dtype.itemsize
    raw = download(url, dest.with_suffix(".raw"), limit=offset + rows * row_bytes)
    data = np.frombuffer(raw.read_bytes()[offset:], dtype=dtype).reshape((rows, *shape[1:]))
    np.save(dest, data)
    raw.unlink()
    return dest


def rirs(dest: Path, limit: int | None) -> Path:
    with urllib.request.urlopen(RIR_TREE) as response:
        files = [f["path"] for f in json.load(response) if f["path"].endswith(".wav")]
    for path in files[:limit]:
        download(f"{RIR_FILE}/{path}", dest / Path(path).name)
    return dest


def audioset_background(dest: Path, shards: list[Path]) -> Path:
    """Decodes AudioSet parquet shards to 16 kHz mono 16-bit wavs."""
    import numpy as np
    import pyarrow.parquet as pq
    import scipy.io.wavfile
    import scipy.signal
    import soundfile

    done = dest / ".done"
    if done.exists():
        return dest
    dest.mkdir(parents=True, exist_ok=True)
    for shard in shards:
        table = pq.read_table(shard, columns=["video_id", "audio"])
        for video_id, row in zip(
            table.column("video_id").to_pylist(), table.column("audio").to_pylist(), strict=True
        ):
            name = f"{video_id}.wav"
            try:
                audio, rate = soundfile.read(io.BytesIO(row["bytes"]), dtype="float32")
            except RuntimeError:
                continue
            if audio.ndim > 1:
                audio = audio.mean(axis=1)
            if rate != 16000:
                audio = scipy.signal.resample_poly(audio, 16000, rate)
            pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
            scipy.io.wavfile.write(dest / name, 16000, pcm)
    done.touch()
    return dest


def synthetic_noise(dest: Path, clips: int) -> Path:
    """Smoke only: a few seconds of coloured noise instead of AudioSet."""
    import numpy as np
    import scipy.io.wavfile

    dest.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(0)
    for n in range(clips):
        noise = np.cumsum(rng.normal(size=16000 * 5)) * 0.02
        noise -= noise.mean()
        pcm = (np.clip(noise / (np.abs(noise).max() + 1e-9), -1, 1) * 8000).astype(np.int16)
        scipy.io.wavfile.write(dest / f"noise{n}.wav", 16000, pcm)
    return dest


def check_model(path: Path) -> None:
    """The app feeds the classifier 16 embeddings of 96 (OnnxWakeWordModelRunner)."""
    import onnx

    dims = [d.dim_value for d in onnx.load(str(path)).graph.input[0].type.tensor_type.shape.dim]
    if dims[1:] != [16, 96]:
        raise SystemExit(f"{path} takes input {dims}, the app expects [1, 16, 96]")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--work", type=Path, required=True, help="downloads and intermediate data")
    parser.add_argument("--out", type=Path, required=True, help="where hey_coco.onnx goes")
    parser.add_argument("--smoke", action="store_true", help="tiny end-to-end check, no output")
    parser.add_argument("--steps", type=int, help="training steps (default 50000; smoke 100)")
    parser.add_argument("--samples", type=int, help="positive clips (default 10000; smoke 24)")
    args = parser.parse_args()

    if sys.platform != "darwin" and not os.environ.get("WAKEWORD_ANY_OS"):
        log("Written for macOS; set WAKEWORD_ANY_OS=1 to try elsewhere.")
    work = (args.work / "smoke" if args.smoke else args.work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    data = args.work.resolve() / "data"  # shared by smoke and full runs
    repo = Path(__file__).resolve().parents[3]
    log(f"work dir {work}")

    src = unpack_zip(OWW_ZIP, data / "src" / f"openWakeWord-{OWW_COMMIT[:7]}")
    piper = unpack_zip(PIPER_GEN_ZIP, data / "src" / f"piper-gen-{PIPER_GEN_COMMIT[:7]}")
    download(PIPER_VOICE_URL, piper / "models" / "en_US-libritts_r-medium.pt")
    resources = src / "openwakeword" / "resources" / "models"
    resources.mkdir(parents=True, exist_ok=True)
    for name, digest in FEATURE_MODELS.items():
        committed = repo / "display" / "WakeWord" / name
        if sha256(committed) != digest:
            raise SystemExit(f"{committed} does not match its pinned checksum")
        shutil.copyfile(committed, resources / name)

    if args.smoke:
        features = npy_head(FEATURES_URL, data / "smoke" / "acav_head.npy", rows=20_000)
        validation = npy_head(VALIDATION_URL, data / "smoke" / "validation_head.npy", rows=40_000)
        rir_dir = rirs(data / "mit_rirs", limit=20)
        background = synthetic_noise(work / "noise", clips=10)
    else:
        features = download(FEATURES_URL, data / Path(FEATURES_URL).name)
        validation = download(VALIDATION_URL, data / "validation_set_features.npy")
        rir_dir = rirs(data / "mit_rirs", limit=None)
        shards = [download(url, data / "audioset" / Path(url).name) for url in AUDIOSET_SHARDS]
        background = audioset_background(data / "audioset_16k", shards)

    import yaml

    config = yaml.safe_load((src / "examples" / "custom_model.yml").read_text())
    samples = args.samples or (24 if args.smoke else config["n_samples"])
    config.update(
        {
            "model_name": "hey_coco",
            "target_phrase": ["hey coco"],
            "custom_negative_phrases": NEGATIVE_PHRASES,
            "n_samples": samples,
            "n_samples_val": max(8, samples // 10),
            "tts_batch_size": 8 if args.smoke else 50,
            "augmentation_batch_size": 8 if args.smoke else 16,
            "augmentation_rounds": 1,
            "steps": args.steps or (100 if args.smoke else 50_000),
            "piper_sample_generator_path": str(piper),
            "output_dir": str(work / "model"),
            "rir_paths": [str(rir_dir)],
            "background_paths": [str(background)],
            "background_paths_duplication_rate": [1],
            "false_positive_validation_data_path": str(validation),
            "feature_data_files": {"ACAV100M_sample": str(features)},
            "batch_n_per_class": {
                "ACAV100M_sample": 64 if args.smoke else 1024,
                "adversarial_negative": 8 if args.smoke else 50,
                "positive": 8 if args.smoke else 50,
            },
        }
    )
    config_path = work / "hey_coco.yml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    log(f"config {config_path}")

    # run_oww_train.py patches openWakeWord's Linux-only assumptions (fork, tflite). The ObjC
    # flag stops the Objective-C runtime aborting forked children; it applies to these
    # processes only.
    env = {
        **os.environ,
        "PYTHONPATH": f"{src}{os.pathsep}{piper}",
        "OBJC_DISABLE_INITIALIZE_FORK_SAFETY": "YES",
    }
    started = time.monotonic()
    for stage in ("--generate_clips", "--augment_clips", "--train_model"):
        log(f"openWakeWord train.py {stage}")
        subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("run_oww_train.py")),
                str(src / "openwakeword" / "train.py"),
                "--training_config",
                str(config_path),
                stage,
            ],
            check=True,
            cwd=work,
            env=env,
        )
        log(f"{stage} done after {(time.monotonic() - started) / 60:.1f} min")

    model = work / "model" / "hey_coco.onnx"
    check_model(model)
    if args.smoke:
        log(f"smoke run OK: {model} ({model.stat().st_size} bytes); not copied to {args.out}")
        return
    args.out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(model, args.out)
    log(
        f"wrote {args.out} ({args.out.stat().st_size} bytes). Next: make display-build, then "
        "test on the iPad (docs/wake-word.md, On-device checks)."
    )


if __name__ == "__main__":
    main()
