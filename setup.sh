#!/usr/bin/env bash
# ===========================================================================
#  Quran Video Generator - Surah Al-Fatihah
#  One-time setup for macOS and Linux.
#
#      chmod +x setup.sh && ./setup.sh
# ===========================================================================
set -euo pipefail

cd "$(dirname "$0")"

echo
echo " =========================================="
echo "  Quran Video Generator - setup"
echo " =========================================="
echo

# --- 1. Find Python --------------------------------------------------------
PY=""
for candidate in python3.13 python3.12 python3.11 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        version=$("$candidate" -c 'import sys; print("%d%02d" % sys.version_info[:2])' 2>/dev/null || echo 0)
        if [ "$version" -ge 311 ]; then
            PY="$candidate"
            break
        fi
    fi
done

if [ -z "$PY" ]; then
    echo " [ERROR] Python 3.11 or newer was not found."
    echo
    echo "   macOS :  brew install python@3.12"
    echo "   Ubuntu:  sudo apt install python3.12 python3.12-venv"
    echo
    exit 1
fi

echo " [1/4] Using Python: $($PY --version)"
echo

# --- 2. Virtual environment ------------------------------------------------
if [ -x ".venv/bin/python" ]; then
    echo " [2/4] Virtual environment already exists - reusing .venv"
else
    echo " [2/4] Creating the virtual environment in .venv ..."
    "$PY" -m venv .venv
fi
echo

VENV_PY=".venv/bin/python"

# --- 3. Dependencies -------------------------------------------------------
echo " [3/4] Installing dependencies (this can take a few minutes) ..."
"$VENV_PY" -m pip install --upgrade pip --quiet
"$VENV_PY" -m pip install -r requirements.txt
echo

# --- 4. Fonts and Quran text ----------------------------------------------
echo " [4/4] Downloading the SIL OFL fonts and the verified Quran text ..."
"$VENV_PY" run.py fonts
if [ ! -f "data/al_fatihah.json" ]; then
    "$VENV_PY" run.py fetch-text
else
    echo " data/al_fatihah.json already exists - leaving it alone."
fi
echo

if ! command -v ffmpeg >/dev/null 2>&1; then
    echo " Note: FFmpeg is not on your PATH. The bundled imageio-ffmpeg build"
    echo " will be used instead. To install a system FFmpeg:"
    echo "   macOS :  brew install ffmpeg"
    echo "   Ubuntu:  sudo apt install ffmpeg"
    echo
fi

echo " =========================================="
echo "  Setup finished."
echo " =========================================="
echo
echo " Next steps:"
echo
echo "   1. Copy your Arabic recitation into assets/audio/recitation/"
echo "        001.mp3 ... 007.mp3"
echo "   2. Copy your Urdu narration into assets/audio/urdu/"
echo "        001.mp3 ... 007.mp3"
echo "   3. Fill in the \"reciter:\" block in config.yaml"
echo
echo " Then run, in this order:"
echo
echo "    .venv/bin/python run.py validate"
echo "    .venv/bin/python run.py preview"
echo "    .venv/bin/python run.py render"
echo
echo " To check the layout before you have any audio:"
echo
echo "    .venv/bin/python run.py preview --no-audio"
echo
