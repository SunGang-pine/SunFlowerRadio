"""Validate the station: scripts, timing, segments and audio."""
from __future__ import annotations

import argparse
import subprocess

from .config import ROOT, audio_path, episode_path, episode_index_entries, load_station, read_json, setup_stdout, station_languages


def audio_duration(path) -> float | None:
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, timeout=30,
        )
        return float(proc.stdout.decode().strip()) if proc.returncode == 0 else None
    except Exception:
        return None


def check_episode(date: str, lang: str, entry: dict | None) -> list[str]:
    errors: list[str] = []
    path = episode_path(date, lang)
    if not path.exists():
        return [f"{date}-{lang}: script missing ({path.name})"]
    try:
        ep = read_json(path)
    except Exception as exc:
        return [f"{date}-{lang}: invalid JSON ({exc})"]
    for key in ("title", "opening", "news", "closing", "lines_timed", "segments", "seconds"):
        if key not in ep:
            errors.append(f"{date}-{lang}: missing '{key}'")
    news = ep.get("news")
    if not isinstance(news, list) or not news:
        errors.append(f"{date}-{lang}: news is empty")
        return errors
    for i, n in enumerate(news):
        for key in ("label", "points", "lines", "url", "link"):
            if key not in n:
                errors.append(f"{date}-{lang}: news[{i}] missing '{key}'")
        if n.get("url") and not str(n["url"]).startswith(("http://", "https://")):
            errors.append(f"{date}-{lang}: news[{i}].url is not a URL")
        if n.get("link") and not n["link"].get("url"):
            errors.append(f"{date}-{lang}: news[{i}].link has no url")
        if n.get("details") is not None and len(n["details"]) > len(n.get("points") or []):
            errors.append(f"{date}-{lang}: news[{i}].details is longer than points")
    lt = ep.get("lines_timed") or []
    seg = ep.get("segments") or []
    if not lt:
        errors.append(f"{date}-{lang}: lines_timed is empty")
    if not seg:
        errors.append(f"{date}-{lang}: segments is empty")
    last = -1.0
    for i, line in enumerate(lt):
        if not isinstance(line, dict):
            errors.append(f"{date}-{lang}: lines_timed[{i}] is not an object")
            continue
        s, e = line.get("start"), line.get("end")
        if not isinstance(s, (int, float)) or not isinstance(e, (int, float)) or e < s:
            errors.append(f"{date}-{lang}: lines_timed[{i}] bad start/end")
            continue
        if s < last - 0.002:
            errors.append(f"{date}-{lang}: lines_timed[{i}] overlaps the previous line")
        last = e
        if not isinstance(line.get("show"), str) or not line["show"].strip():
            errors.append(f"{date}-{lang}: lines_timed[{i}].show empty")
        if not isinstance(line.get("part"), int):
            errors.append(f"{date}-{lang}: lines_timed[{i}].part is not an integer")
    if seg:
        for i, s in enumerate(seg):
            if s.get("kind") not in ("opening", "news", "closing"):
                errors.append(f"{date}-{lang}: segments[{i}] bad kind")
            if not isinstance(s.get("start"), (int, float)) or not isinstance(s.get("end"), (int, float)):
                errors.append(f"{date}-{lang}: segments[{i}] bad start/end")
            if i and s["start"] < seg[i - 1]["end"] - 0.002:
                errors.append(f"{date}-{lang}: segments[{i}] starts before the previous ends")
        if abs(seg[-1]["end"] - float(ep.get("seconds") or 0)) > 0.01:
            errors.append(f"{date}-{lang}: last segment end {seg[-1]['end']} != seconds {ep.get('seconds')}")
    if entry is not None and entry.get("seconds") is not None and abs(float(entry["seconds"]) - float(ep.get("seconds") or 0)) > 0.05:
        errors.append(f"{date}-{lang}: episodes.json seconds {entry['seconds']} != script {ep.get('seconds')}")
    path_audio = audio_path(date, lang)
    if not path_audio.exists():
        errors.append(f"{date}-{lang}: audio missing ({path_audio.name})")
    else:
        dur = audio_duration(path_audio)
        if dur is not None and abs(dur - float(ep.get("seconds") or 0)) > 0.25:
            errors.append(f"{date}-{lang}: audio {dur:.2f}s != seconds {ep.get('seconds')}")
    return errors


def main() -> int:
    setup_stdout()
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-missing-audio", action="store_true")
    args = parser.parse_args()
    entries = episode_index_entries()
    if not entries:
        print("episodes.json is empty")
        return 1
    langs = station_languages()
    total, all_errors = 0, []
    print(f"station: {load_station().get('name', ROOT.name)} · languages: {', '.join(langs)}")
    for entry in entries:
        date = entry.get("date")
        if not date:
            continue
        for lang in langs:
            if len(langs) > 1 and lang not in entry:
                continue
            total += 1
            errs = check_episode(date, lang, entry)
            if args.allow_missing_audio:
                errs = [e for e in errs if "audio missing" not in e]
            all_errors.extend(errs)
            status = "OK " if not errs else "ERR"
            print(f"  [{status}] {date}-{lang}  {entry.get(lang, {}).get('title', '') if len(langs) > 1 else entry.get('title', '')}")
            for e in errs:
                print(f"        - {e}")
    print(f"\n{total} episodes checked · {len(all_errors)} problems")
    return 0 if not all_errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
