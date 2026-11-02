#!/usr/bin/env bash
# Double-click installer for the optional open decision engine (Laya).
cd "$(dirname "$0")" || exit 1
if [ ! -x ".venv/bin/python" ]; then
  echo "First run the main starter (start-host.sh) once, then try again."
  exit 1
fi
echo "Installing Laya (open decision engine)…"
# Linux: the default PyPI torch bundles CUDA (~2.5 GB); the CPU build is ~10x smaller.
# (Have an NVIDIA GPU and want to use it? Remove these two lines to get the CUDA build.)
if [ "$(uname -s)" = "Linux" ]; then
  .venv/bin/python -m pip install --quiet torch --index-url https://download.pytorch.org/whl/cpu
fi
if .venv/bin/python -m pip install --quiet -e ".[laya]"; then
  echo ""
  echo "Done. Laya is optional and runs 100% locally — restart the host to use it."
else
  echo "[!] Installation failed — check your internet connection."
  exit 1
fi
