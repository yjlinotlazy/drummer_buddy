from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path


def emit(stage: str, progress: float, message: str) -> None:
    print(json.dumps({"stage": stage, "progress": progress, "message": message}), flush=True)


def run_mock(output_dir: Path, parameters: dict) -> None:
    steps = max(1, int(parameters.get("steps", 5)))
    delay = max(0, float(parameters.get("delay", 0.05)))
    if parameters.get("fail"):
        emit("mock", 0.25, "Simulating failure")
        raise RuntimeError("mock failure requested")
    for index in range(steps):
        emit("mock", (index + 1) / steps, f"Mock step {index + 1}/{steps}")
        time.sleep(delay)
    artifact = output_dir / "artifact.txt"
    artifact.write_text("mock result\n", encoding="utf-8")
    (output_dir / "manifest.json").write_text(
        json.dumps({"jobType": "mock", "files": [artifact.name]}), encoding="utf-8"
    )


def run_command(command: list[str], stage: str, start: float, end: float) -> None:
    emit(stage, start, f"Starting {stage}")
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert process.stdout is not None
    lines = 0
    for line in process.stdout:
        message = line.strip()
        if message:
            lines += 1
            progress = min(end - 0.01, start + min(lines, 20) / 20 * (end - start))
            emit(stage, progress, message[-500:])
    if process.wait() != 0:
        raise RuntimeError(f"{stage} exited with code {process.returncode}")
    emit(stage, end, f"Finished {stage}")


def audio_samples(path: Path) -> int:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=duration_ts", "-of", "default=nw=1:nk=1", str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return int(result.stdout.strip())


def run_drumless(input_path: Path, output_dir: Path, analysis_python: Path, parameters: dict) -> None:
    if not analysis_python.is_file():
        raise RuntimeError(f"analysis Python does not exist: {analysis_python}")
    output_format = str(parameters.get("format", "flac")).lower()
    if output_format not in {"flac", "wav"}:
        raise RuntimeError("drumless format must be flac or wav")

    original = output_dir / "original.flac"
    run_command(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "warning", "-i", str(input_path),
            "-vn", "-ar", "44100", "-ac", "2", "-c:a", "flac", str(original),
        ],
        "normalization", 0.0, 0.08,
    )

    separation_dir = output_dir / "separation"
    model = str(parameters.get("model", "htdemucs_ft"))
    run_command(
        [
            str(analysis_python), "-m", "demucs", "-d", "cuda", "-n", model,
            "--segment", str(int(parameters.get("segment", 7))),
            "--overlap", str(float(parameters.get("overlap", 0.25))),
            "--shifts", str(int(parameters.get("shifts", 1))),
            "--float32", "-o", str(separation_dir), str(original),
        ],
        "separation", 0.08, 0.82,
    )
    stem_dir = separation_dir / model / original.stem
    drums, bass, other, vocals = [stem_dir / name for name in ("drums.wav", "bass.wav", "other.wav", "vocals.wav")]
    if not all(path.is_file() for path in (drums, bass, other, vocals)):
        raise RuntimeError("Demucs did not produce the expected four stems")

    sample_count = audio_samples(original)
    drums_cache = output_dir / "drums.flac"
    run_command(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "warning", "-i", str(drums),
            "-af", f"apad=whole_len={sample_count},atrim=end_sample={sample_count}",
            "-ar", "44100", "-ac", "2", "-c:a", "flac", str(drums_cache),
        ],
        "drums-cache", 0.82, 0.88,
    )
    drumless = output_dir / f"drumless.{output_format}"
    codec = ["-c:a", "flac"] if output_format == "flac" else ["-c:a", "pcm_s24le"]
    run_command(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "warning",
            "-i", str(bass), "-i", str(other), "-i", str(vocals),
            "-filter_complex",
            f"[0:a][1:a][2:a]amix=inputs=3:normalize=0,apad=whole_len={sample_count},atrim=end_sample={sample_count}[out]",
            "-map", "[out]", "-ar", "44100", "-ac", "2", *codec, str(drumless),
        ],
        "mixing", 0.88, 0.98,
    )
    shutil.rmtree(separation_dir)
    manifest = {
        "jobType": "drumless",
        "model": model,
        "sampleRate": 44100,
        "channels": 2,
        "samples": sample_count,
        "files": {"original": original.name, "drums": drums_cache.name, "drumless": drumless.name},
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    emit("finalizing", 1.0, "Drumless assets ready")


def run_score(
    input_path: Path,
    output_dir: Path,
    analysis_python: Path,
    parameters: dict,
    cached_drums: Path | None,
) -> None:
    if not analysis_python.is_file():
        raise RuntimeError(f"analysis Python does not exist: {analysis_python}")
    original = output_dir / "original.flac"
    run_command(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "warning", "-i", str(input_path),
            "-vn", "-ar", "44100", "-ac", "2", "-c:a", "flac", str(original),
        ],
        "normalization", 0.0, 0.06,
    )
    drums = output_dir / "drums.flac"
    model = str(parameters.get("separatorModel", "htdemucs_ft"))
    if cached_drums and cached_drums.is_file():
        shutil.copy2(cached_drums, drums)
        emit("separation", 0.48, "Reused cached drum stem")
    else:
        separation_dir = output_dir / "separation"
        run_command(
            [
                str(analysis_python), "-m", "demucs", "-d", "cuda", "-n", model,
                "--segment", str(int(parameters.get("segment", 7))),
                "--overlap", str(float(parameters.get("overlap", 0.25))),
                "--shifts", str(int(parameters.get("shifts", 1))),
                "--float32", "-o", str(separation_dir), str(original),
            ],
            "separation", 0.06, 0.48,
        )
        separated_drums = separation_dir / model / original.stem / "drums.wav"
        if not separated_drums.is_file():
            raise RuntimeError("Demucs did not produce a drum stem")
        run_command(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "warning", "-i", str(separated_drums),
                "-ar", "44100", "-ac", "2", "-c:a", "flac", str(drums),
            ],
            "drums-cache", 0.48, 0.52,
        )
        shutil.rmtree(separation_dir)

    beats = output_dir / "beats.tsv"
    run_command(
        [
            str(analysis_python.parent / "beat_this"), str(original),
            "--output", str(beats), "--gpu", "0", "--float16",
        ],
        "beat-tracking", 0.52, 0.72,
    )
    midi = output_dir / "drums.mid"
    run_command(
        [
            str(analysis_python.parent / "adtof"), "--audio", str(drums),
            "--out", str(midi), "--device", "cuda",
        ],
        "drum-transcription", 0.72, 0.91,
    )
    score = output_dir / "score.json"
    bridge = Path(__file__).with_name("analysis_bridge.py")
    run_command(
        [
            str(analysis_python), str(bridge), "--midi", str(midi),
            "--beats", str(beats), "--output", str(score),
        ],
        "quantization", 0.91, 0.99,
    )
    manifest = {
        "jobType": "score",
        "models": {"separator": model, "beatTracker": "beat-this/final0", "transcriber": "adtof-pytorch"},
        "files": {
            "original": original.name,
            "drums": drums.name,
            "beats": beats.name,
            "midi": midi.name,
            "score": score.name,
        },
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    emit("finalizing", 1.0, "Score assets ready")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-type", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--input-path", required=True, type=Path)
    parser.add_argument("--analysis-python", required=True, type=Path)
    parser.add_argument("--cached-drums", type=Path)
    parser.add_argument("--parameters", default="{}")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    parameters = json.loads(args.parameters)
    try:
        if args.job_type == "mock":
            run_mock(args.output_dir, parameters)
        elif args.job_type == "drumless":
            run_drumless(args.input_path, args.output_dir, args.analysis_python, parameters)
        elif args.job_type == "score":
            run_score(args.input_path, args.output_dir, args.analysis_python, parameters, args.cached_drums)
        else:
            raise RuntimeError(f"{args.job_type} worker is not implemented")
    except Exception as error:
        emit("failed", 0, str(error))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
