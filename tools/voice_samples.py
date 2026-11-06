"""Generate short audition clips for every Edge neural voice.

The clips are static assets for ``voices/index.html``.  They are intentionally
not committed (see .gitignore); `tools/publish.ps1` copies them to gh-pages.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from .config import EDGE_VOICE_CHOICES, setup_stdout

SAMPLE_TEXT = {
    "zh": "你好，欢迎收听太阳花电台。今天为你带来人工智能、3D 打印和电子制作的新鲜事。",
    "ja": "おはようございます、サンフラワーレディオです。今日はAIと3Dのニュースをお届けします。",
    "en": "Good morning, this is SunFlowerRadio. Here is today's AI, 3D printing and electronics news.",
}


async def _one(voice_id: str, lang: str, out: Path, sem: asyncio.Semaphore) -> tuple[str, str]:
    import edge_tts
    async with sem:
        text = SAMPLE_TEXT.get(lang, SAMPLE_TEXT["en"])
        await edge_tts.Communicate(text, voice_id, rate="+0%").save(str(out))
    return voice_id, "ok"


def main(argv: list[str] | None = None) -> int:
    setup_stdout()
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "voices" / "samples"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    jobs = []
    for lang, choices in EDGE_VOICE_CHOICES.items():
        for voice in choices:
            path = out / f"{voice['id']}.mp3"
            if path.exists() and path.stat().st_size > 1000 and not args.force:
                print(f"skip  {voice['id']}")
                continue
            jobs.append((voice["id"], lang, path))
    if not jobs:
        print("all voice samples already exist")
        return 0

    async def run() -> list[tuple[str, str]]:
        sem = asyncio.Semaphore(3)
        return await asyncio.gather(*[_one(v, l, p, sem) for v, l, p in jobs])

    saved = {k: os.environ.get(k) for k in ("NO_PROXY", "no_proxy")}
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"
    try:
        results = asyncio.run(run())
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    for voice_id, status in results:
        print(f"{status:4}  {voice_id}")
    print(f"voice samples: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
