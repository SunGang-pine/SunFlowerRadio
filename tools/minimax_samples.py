"""Generate audition clips for the MiniMax voices (requires MINIMAX_API_KEY)."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .config import MINIMAX_VOICES, find_minimax_key, setup_stdout
from .voice import _run_minimax
from .voice_samples import SAMPLE_TEXT


def main(argv: list[str] | None = None) -> int:
    setup_stdout()
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "voices" / "minimax" / "samples"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    key, _ = find_minimax_key()
    if not key:
        print("MINIMAX_API_KEY 未配置；先写入 .env.local")
        return 1
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="mmx-samples-"))
    try:
        for lang, choices in MINIMAX_VOICES.items():
            for voice in choices:
                slug = voice.get("slug") or voice["id"].rsplit(":", 1)[-1].replace(" ", "-")
                mp3 = out / f"{slug}.mp3"
                if mp3.exists() and mp3.stat().st_size > 1000 and not args.force:
                    print(f"skip  {slug}")
                    continue
                print(f"make  {slug}  {voice['label']}")
                _run_minimax(
                    [{"id": slug, "text": SAMPLE_TEXT[lang]}],
                    tmp, voice["id"], 1, lang, print,
                )
                wav = tmp / f"{slug}.wav"
                subprocess.run(
                    ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-codec:a", "libmp3lame",
                     "-b:a", "128k", "-ar", "44100", str(mp3)],
                    check=True,
                )
                print(f"   -> {mp3.stat().st_size} bytes")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"MiniMax samples: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
