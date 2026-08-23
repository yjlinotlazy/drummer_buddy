from __future__ import annotations

import argparse
import bisect
import json
from pathlib import Path
from uuid import uuid4

import pretty_midi


PPQ = 960
MIDI_INSTRUMENTS = {
    35: "kick",
    38: "snare",
    47: "tom_mid",
    42: "hihat_closed",
    49: "crash",
}


def read_beats(path: Path) -> list[tuple[float, int]]:
    beats: list[tuple[float, int]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            beats.append((float(parts[0]), int(parts[1])))
    if len(beats) < 2:
        raise ValueError("beat tracker produced fewer than two beat anchors")
    return sorted(beats)


def musical_tick(time_sec: float, times: list[float]) -> int:
    index = bisect.bisect_right(times, time_sec) - 1
    index = max(0, min(index, len(times) - 2))
    interval = max(0.001, times[index + 1] - times[index])
    beat_position = index + (time_sec - times[index]) / interval
    raw_tick = beat_position * PPQ
    return max(0, round(raw_tick / 80) * 80)


def convert(midi_path: Path, beats_path: Path, output_path: Path) -> None:
    raw_beats = read_beats(beats_path)
    beat_times = [item[0] for item in raw_beats]
    measure = 1
    beat_anchors: list[dict] = []
    for index, (time_sec, beat_number) in enumerate(raw_beats):
        if index and beat_number == 1:
            measure += 1
        beat_anchors.append({"timeSec": time_sec, "measure": measure, "beat": beat_number})

    numerator = max((beat for _, beat in raw_beats), default=4)
    if numerator < 3 or numerator > 12:
        numerator = 4
    midi = pretty_midi.PrettyMIDI(str(midi_path))
    events: list[dict] = []
    for instrument in midi.instruments:
        for note in instrument.notes:
            semantic_instrument = MIDI_INSTRUMENTS.get(note.pitch)
            if not semantic_instrument:
                continue
            events.append(
                {
                    "id": str(uuid4()),
                    "instrument": semantic_instrument,
                    "detectedTimeSec": round(float(note.start), 6),
                    "onsetTick": musical_tick(float(note.start), beat_times),
                    "durationTicks": 240,
                }
            )
    events.sort(key=lambda event: (event["onsetTick"], event["instrument"]))
    score = {
        "schemaVersion": 1,
        "revision": 1,
        "ppq": PPQ,
        "beats": beat_anchors,
        "timeSignatures": [{"measure": 1, "numerator": numerator, "denominator": 4}],
        "events": events,
    }
    output_path.write_text(json.dumps(score, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--midi", required=True, type=Path)
    parser.add_argument("--beats", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    convert(args.midi, args.beats, args.output)


if __name__ == "__main__":
    main()
