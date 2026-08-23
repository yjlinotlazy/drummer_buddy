#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
uv_cache_dir="${UV_CACHE_DIR:-/tmp/drummer-buddy-uv}"

UV_CACHE_DIR="$uv_cache_dir" uv venv --python "$project_dir/.venv/bin/python" "$project_dir/.analysis-venv"
UV_CACHE_DIR="$uv_cache_dir" uv pip install \
  --python "$project_dir/.analysis-venv/bin/python" \
  torch==2.7.0 torchaudio==2.7.0 \
  --index-url https://download.pytorch.org/whl/cu128
UV_CACHE_DIR="$uv_cache_dir" uv pip install \
  --python "$project_dir/.analysis-venv/bin/python" \
  --requirements "$project_dir/analysis/requirements.txt"

"$project_dir/.analysis-venv/bin/python" -c \
  'import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available())'
