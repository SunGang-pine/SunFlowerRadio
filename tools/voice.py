"""Offline voice generation with the Windows speech engine.

The player needs one MP3 per episode plus exact ``lines_timed`` / ``segments``.
We synthesize every line separately, measure the WAVs, then assemble the audio
and the timeline from the same sample counts.  That keeps subtitles, slides and
the character's mouth in sync without relying on word timestamps from a cloud
TTS service.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import uuid
import wave
from pathlib import Path
from typing import Callable

from .config import AUDIO_DIR, LANG_META, audio_path, episode_path, read_json, update_episode_index, write_json

SAPI_PS1 = Path(__file__).resolve().parent / "sapi.ps1"
LEAD_IN = 0.60
LINE_GAP = 0.28
PART_GAP = 1.00
TAIL = 2.00
RATE_HZ = 44100


def _powershell() -> str:
    return shutil.which("powershell.exe") or shutil.which("powershell") or "powershell.exe"


def sapi_available() -> bool:
    return sys.platform == "win32" and SAPI_PS1.exists()


def list_voices(timeout: int = 60) -> list[str]:
    if not sapi_available():
        return []
    try:
        proc = subprocess.run(
            [_powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SAPI_PS1), "-List"],
            capture_output=True, timeout=timeout,
        )
        text = proc.stdout.decode("utf-8", errors="replace")
        for line in reversed(text.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                data = json.loads(line)
                return list(data.get("voices") or [])
    except Exception:
        pass
    return []


def _run_sapi(items: list[dict], out_dir: Path, voice: str, rate: int) -> dict:
    job = {
        "voice": voice,
        "rate": rate,
        "outDir": str(out_dir),
        "items": items,
    }
    job_path = out_dir / "job.json"
    job_path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    proc = subprocess.run(
        [_powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SAPI_PS1), "-Job", str(job_path)],
        capture_output=True, timeout=900,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout).decode("utf-8", errors="replace")[-1500:]
        raise RuntimeError(f"Windows TTS failed: {err}")
    text = proc.stdout.decode("utf-8", errors="replace")
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                pass
    raise RuntimeError("Windows TTS returned no result")


def _read_wav(path: Path) -> tuple[bytes, float]:
    with wave.open(str(path), "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2 or w.getframerate() != RATE_HZ:
            raise RuntimeError(
                f"unexpected WAV format {w.getnchannels()}ch/{w.getsampwidth() * 8}bit/{w.getframerate()}Hz"
            )
        frames = w.readframes(w.getnframes())
        duration = w.getnframes() / float(w.getframerate())
    return frames, duration


def _silence(seconds: float) -> bytes:
    return b"\x00\x00" * int(round(seconds * RATE_HZ))


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg is required to encode MP3")
    return exe


def _probe_duration(path: Path) -> float | None:
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, timeout=30,
        )
        return float(proc.stdout.decode().strip()) if proc.returncode == 0 else None
    except Exception:
        return None


def _build_timeline(parts: list[tuple[int, list[tuple[dict, float]]]]) -> tuple[list[dict], list[dict], float]:
    """parts: [(segment_index, [(line, duration), ...]), ...]"""
    cursor = LEAD_IN
    lines_timed: list[dict] = []
    segment_starts: list[float] = []
    for pi, (seg_index, lines) in enumerate(parts):
        if pi:
            cursor += PART_GAP
        seg_start = cursor
        segment_starts.append(seg_start)
        for li, (line, dur) in enumerate(lines):
            start = cursor
            end = start + dur
            lines_timed.append({"start": round(start, 3), "end": round(end, 3), "show": line["show"], "part": seg_index})
            cursor = end
            if li < len(lines) - 1:
                cursor += LINE_GAP
    seconds = cursor + TAIL
    segments = []
    for i, start in enumerate(segment_starts):
        end = segment_starts[i + 1] if i + 1 < len(segment_starts) else seconds
        parts_meta = parts[i][0]
        kind = "opening" if parts_meta == 0 else ("closing" if parts_meta == len(parts) - 1 else "news")
        index = None if kind in ("opening", "closing") else parts_meta - 1
        segments.append({"kind": kind, "index": index, "start": round(start, 3), "end": round(end, 3)})
    return lines_timed, segments, seconds


def synthesize_episode(
    date: str,
    lang: str,
    *,
    voice: str | None = None,
    rate: int | None = None,
    force: bool = False,
    log: Callable[[str], None] | None = None,
) -> dict:
    say = log or (lambda _: None)
    if not sapi_available():
        raise RuntimeError("Windows SAPI is not available on this machine")
    path = episode_path(date, lang)
    if not path.exists():
        raise FileNotFoundError(f"episode not found: {path}")
    ep = read_json(path)
    out = audio_path(date, lang)
    if out.exists() and not force:
        say(f"audio already exists: {out.name} (use force to rebuild)")
        return {"date": date, "lang": lang, "audio": str(out), "seconds": ep.get("seconds", 0), "skipped": True}

    meta = LANG_META.get(lang, LANG_META["ja"])
    voice = voice or meta["voice"]
    rate = meta["rate"] if rate is None else rate

    parts: list[tuple[int, list[dict]]] = []
    parts.append((0, list(ep.get("opening") or [])))
    for i, item in enumerate(ep.get("news") or [], 1):
        parts.append((i, list(item.get("lines") or [])))
    parts.append((len(ep.get("news") or []) + 1, list(ep.get("closing") or [])))
    if not any(lines for _, lines in parts):
        raise ValueError("episode has no spoken lines")

    items: list[dict] = []
    flat: list[tuple[int, int, dict]] = []
    n = 0
    for seg_index, lines in parts:
        for li, line in enumerate(lines):
            text = (line.get("say") or line.get("show") or "").strip()
            if not text:
                continue
            items.append({"id": f"l{n:03d}", "text": text})
            flat.append((seg_index, li, line))
            n += 1
    if not items:
        raise ValueError("episode has no non-empty spoken lines")

    tmp = AUDIO_DIR / f".tmp-{date}-{lang}-{uuid.uuid4().hex[:8]}"
    tmp.mkdir(parents=True, exist_ok=True)
    started = time.time()
    try:
        say(f"synthesizing {len(items)} lines with 「{voice}」…")
        result = _run_sapi(items, tmp, voice, rate)
        chosen = result.get("voice") or voice
        say(f"voice: {chosen} (requested {voice})")

        durations: dict[str, float] = {}
        for item in items:
            wav = tmp / f"{item['id']}.wav"
            if not wav.exists():
                raise RuntimeError(f"missing TTS output: {wav.name}")
            _, dur = _read_wav(wav)
            durations[item["id"]] = dur

        # Build the exact timeline first, then write audio from the same numbers.
        built: list[tuple[int, list[tuple[dict, float]]]] = []
        ids = iter([i["id"] for i in items])
        for seg_index, lines in parts:
            pairs = []
            for line in lines:
                text = (line.get("say") or line.get("show") or "").strip()
                if not text:
                    continue
                lid = next(ids)
                pairs.append((line, durations[lid]))
            if pairs:
                built.append((seg_index, pairs))
        lines_timed, segments, seconds = _build_timeline(built)

        # Assemble PCM in the same order as the timeline.
        master = tmp / "master.wav"
        ordered: list[tuple[int, dict, str]] = []
        k = 0
        for seg_index, lines in parts:
            for line in lines:
                text = (line.get("say") or line.get("show") or "").strip()
                if not text:
                    continue
                ordered.append((seg_index, line, items[k]["id"]))
                k += 1
        with wave.open(str(master), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE_HZ)
            w.writeframes(_silence(LEAD_IN))
            last_seg = None
            for idx, (seg_index, line, lid) in enumerate(ordered):
                if last_seg is not None and seg_index != last_seg:
                    w.writeframes(_silence(PART_GAP))
                frames, _ = _read_wav(tmp / f"{lid}.wav")
                w.writeframes(frames)
                next_seg = ordered[idx + 1][0] if idx + 1 < len(ordered) else None
                if next_seg == seg_index:
                    w.writeframes(_silence(LINE_GAP))
                last_seg = seg_index
            w.writeframes(_silence(TAIL))

        out.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            [_ffmpeg(), "-y", "-loglevel", "error", "-i", str(master), "-codec:a", "libmp3lame", "-b:a", "128k",
             "-ar", str(RATE_HZ), str(out)],
            capture_output=True, timeout=300,
        )
        if proc.returncode != 0:
            raise RuntimeError("ffmpeg failed: " + proc.stderr.decode("utf-8", errors="replace")[-1000:])
        probed = _probe_duration(out)

        ep["lines_timed"] = lines_timed
        ep["segments"] = segments
        ep["seconds"] = round(seconds, 3)
        ep["made_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        ep["voice"] = chosen
        write_json(path, ep)
        update_episode_index(date, lang, ep.get("title", ""), seconds)
        say(f"audio: {out.name} · {seconds:.2f}s · {len(lines_timed)} lines" + (f" (mp3 {probed:.2f}s)" if probed else ""))
        if probed and abs(probed - seconds) > 0.2:
            say(f"note: mp3 duration differs by {probed - seconds:+.2f}s (timeline uses the WAV master)")
        return {
            "date": date,
            "lang": lang,
            "audio": str(out),
            "seconds": round(seconds, 3),
            "mp3_seconds": round(probed, 3) if probed else None,
            "lines": len(lines_timed),
            "voice": chosen,
            "elapsed_s": round(time.time() - started, 1),
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
