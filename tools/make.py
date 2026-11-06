"""Command line production tools for the station.

Examples (run from the repository root):

  python -m tools.make write --lang ja --count 4
  python -m tools.make voice --all
  python -m tools.make episode --lang ja --count 4 --force-voice
  python -m tools.make check
"""
from __future__ import annotations

import argparse
import datetime as dt
import time

from .config import EDGE_VOICE_CHOICES, LANG_META, ROOT, get_settings, setup_stdout, station_languages
from .deepseek import DeepSeekClient
from .news import FEEDS, FEED_BY_ID, collect_candidates, manual_candidates
from .script import write_episode
from .voice import list_voices, sapi_available, synthesize_episode


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def _date(value: str | None) -> dt.date:
    if not value:
        return dt.date.today()
    return dt.date.fromisoformat(value)


def _feeds(value: str | None) -> list[dict]:
    if not value:
        return FEEDS
    out = []
    for fid in [x.strip() for x in value.split(",") if x.strip()]:
        if fid not in FEED_BY_ID:
            raise SystemExit(f"unknown feed: {fid} (choose from {', '.join(FEED_BY_ID)})")
        out.append(FEED_BY_ID[fid])
    return out or FEEDS


def _require_key() -> DeepSeekClient:
    settings = get_settings()
    if not settings.configured:
        raise SystemExit(
            "DeepSeek API key 未配置。\n"
            "  推荐：打开制作台 http://127.0.0.1:8090/studio/ 填入 key，或\n"
            "  在仓库根目录创建 .env.local：DEEPSEEK_API_KEY=sk-...\n"
            "  也可以设置环境变量 DEEPSEEK_API_KEY。"
        )
    log(f"DeepSeek key: {settings.key_source} · model {settings.model}")
    return DeepSeekClient(settings)


def cmd_write(args) -> int:
    client = _require_key()
    date = _date(args.date)
    if args.lang not in station_languages():
        log(f"warning: {args.lang} is not listed in station.json languages; the player may not show it")
    if args.source_url:
        cands = manual_candidates(args.source_url, args.source_text or "", log)
    else:
        cands = collect_candidates(_feeds(args.feeds), args.count, args.topic or "", log)
    if len(cands) < args.count:
        raise SystemExit(f"only {len(cands)} usable sources; lower --count or add sources")
    ep, path = write_episode(client, cands, date, args.lang, args.count, args.topic or "", log)
    log(f"script saved: {path.relative_to(ROOT)} · {ep['title']}")
    return 0


def cmd_voice(args) -> int:
    if not sapi_available():
        raise SystemExit("Windows SAPI is not available. This build uses the built-in Windows voices.")
    if args.all:
        targets = []
        for path in sorted((ROOT / "episodes").glob("*.json")):
            stem = path.stem
            if len(stem) >= 11 and stem[4] == "-" and stem[7] == "-":
                date = stem[:10]
                rest = stem[11:]
                if rest in LANG_META:
                    targets.append((date, rest))
    else:
        if not args.date or not args.lang:
            raise SystemExit("--date and --lang are required unless --all is used")
        targets = [(_date(args.date).isoformat(), args.lang)]
    if not targets:
        raise SystemExit("no episodes found")
    for date, lang in targets:
        log(f"voice: {date}-{lang}")
        try:
            synthesize_episode(date, lang, voice=args.voice, rate=args.rate, force=args.force, log=log)
        except Exception as exc:
            log(f"failed: {type(exc).__name__}: {exc}")
            if not args.all:
                return 1
    return 0


def cmd_episode(args) -> int:
    rc = cmd_write(args)
    if rc:
        return rc
    args.all = False
    args.force = args.force_voice
    return cmd_voice(args)


def cmd_voices(args) -> int:
    print("Edge neural voices (default):")
    for lang, choices in EDGE_VOICE_CHOICES.items():
        for voice in choices:
            print(f"  {lang}  {voice['id']:<30} {voice['label']}")
    print("\nWindows SAPI fallback:")
    for name in list_voices():
        print("  " + name)
    return 0


def cmd_check(args) -> int:
    from .check import main as check_main
    return check_main()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m tools.make", description="myradio production tools")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--date", help="YYYY-MM-DD (default: today)")
        sp.add_argument("--lang", default=None, choices=sorted(LANG_META), help="episode language")
        sp.add_argument("--voice", help="Edge neural voice id (e.g. zh-CN-XiaoxiaoNeural) or SAPI voice name")
        sp.add_argument("--rate", type=int, default=None, help="SAPI rate -10..10")
        sp.add_argument("--force", action="store_true", help="rebuild existing audio")

    w = sub.add_parser("write", help="let DeepSeek write an episode script from the news")
    common(w)
    w.add_argument("--count", type=int, default=4, choices=[3, 4, 5])
    w.add_argument("--topic", default="", help="optional keyword filter")
    w.add_argument("--feeds", default="", help="comma separated feed ids")
    w.add_argument("--source-url", action="append", default=[], help="manual source URL (repeatable)")
    w.add_argument("--source-text", default="", help="extra notes for manual sources")
    w.set_defaults(func=cmd_write)

    v = sub.add_parser("voice", help="synthesize audio and timing for an existing script")
    common(v)
    v.add_argument("--all", action="store_true", help="all episodes found in episodes/")
    v.set_defaults(func=cmd_voice)

    e = sub.add_parser("episode", help="write + voice in one go")
    common(e)
    e.add_argument("--count", type=int, default=4, choices=[3, 4, 5])
    e.add_argument("--topic", default="")
    e.add_argument("--feeds", default="")
    e.add_argument("--source-url", action="append", default=[])
    e.add_argument("--source-text", default="")
    e.add_argument("--force-voice", action="store_true")
    e.set_defaults(func=cmd_episode)

    sub.add_parser("voices", help="list Windows SAPI voices").set_defaults(func=cmd_voices)
    sub.add_parser("check", help="validate scripts, audio and timeline").set_defaults(func=cmd_check)
    return p


def main(argv: list[str] | None = None) -> int:
    setup_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "lang", None) is None:
        args.lang = station_languages()[0]
    return args.func(args) or 0


if __name__ == "__main__":
    raise SystemExit(main())
