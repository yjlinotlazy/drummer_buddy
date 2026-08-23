# Drummer Buddy

Local-first accompaniment library for drummers. Add local media directly or record system audio playing in another browser page.

## Development

Requirements: Python 3.11+, Node.js 20+, npm, FFmpeg, and mpv.

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
cd frontend && npm install && cd ..
```

Build the frontend once, then serve the app:

```bash
cd frontend && npm run build && cd ..
python3 server.py
```

Open `http://127.0.0.1:7005`.

For frontend development, run this in another terminal:

```bash
cd frontend
npm run dev
```

Open `http://localhost:5173`. Use `python3 server.py --reload` for the backend. On first launch, it creates
`~/.config/drummer_buddy/config.yaml` and defaults the library to
`~/Music/Drummer Buddy`. Edit the YAML before importing if you want another location.

`server.py` automatically uses `.venv` and reads the host and port from the app config.

## Record system audio

The **Utils** tab includes a recorder with editable output directory and song name. Start it, play the source in another page, stop it, then click **Clean recorded** to copy the WAV into the library. The raw recording is retained. Future editing tools also live in Utils.

The local-file path field starts in the configured recording directory and supports case-insensitive terminal-style completion: type a partial path and press `Tab`; use the arrow keys to select among matches.

During playback, choose **This device** for browser audio or **Server** for audio from the computer running Drummer Buddy. Switching transfers the current position. Server playback uses one shared `mpv` process, so the most recent browser controls the host output.

The machine-specific monitor is configured locally in `~/.config/drummer_buddy/config.yaml`:

```yaml
recording_dir: /home/yli/Dropbox/Music/Mp3
recording_device: pulse:alsa_output.pci-0000_00_1f.3.analog-stereo.monitor
```

## Tests

```bash
.venv/bin/pytest
cd frontend && npm run build
```

## GPU analysis environment

The web service and model packages use separate virtual environments. After the normal setup, install the CUDA analysis stack with:

```bash
bash scripts/setup_analysis.sh
```

The pinned environment uses PyTorch 2.7 with CUDA 12.8, Demucs 4.1, Beat This! 1.1, and the tested ADTOF PyTorch commit. The first analysis run downloads the Demucs and Beat This! model weights into the normal PyTorch cache.

For local songs, **Make drumless** starts the serial GPU pipeline. It normalizes the source to 44.1 kHz stereo FLAC, runs `htdemucs_ft`, retains the drum stem, mixes the other stems, and verifies that original and drumless assets have the same sample count.
