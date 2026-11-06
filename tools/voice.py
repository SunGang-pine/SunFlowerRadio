"""Voice generation with Microsoft Edge neural voices (SAPI fallback).

The player needs one MP3 per episode plus exact ``lines_timed`` / ``segments``.
We synthesize every line separately, measure the WAVs, then assemble the audio
and the timeline from the same sample counts.  That keeps subtitles, slides and
the character's mouth in sync without relying on word timestamps from a cloud
TTS service.

Edge-TTS gives natural neural voices (Xiaoxiao / Nanami / Ava).  If it is not
installed or the network is unavailable, the Windows SAPI voices still work.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
import wave
from pathlib import Path
from typing import Callable

from .config import (
    AUDIO_DIR,
    EDGE_VOICE_IDS,
    LANG_META,
    audio_path,
    episode_path,
    find_minimax_key,
    minimax_base_url,
    minimax_group_id,
    minimax_voice_parts,
    read_json,
    update_episode_index,
    write_json,
)
from .deepseek import http_session

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


def edge_available() -> bool:
    try:
        import edge_tts  # noqa: F401
        return True
    except Exception:
        return False


def minimax_available() -> bool:
    return bool(find_minimax_key()[0])


def _edge_rate(rate: int) -> str:
    return f"{max(-50, min(50, int(rate) * 5)):+d}%"


def _is_edge_voice(voice: str | None) -> bool:
    return bool(voice) and (voice in EDGE_VOICE_IDS or voice.endswith("Neural"))


def _run_edge(items: list[dict], out_dir: Path, voice: str, rate: int, log: Callable[[str], None]) -> dict:
    """Synthesize every line with the Microsoft Edge neural voice."""
    import edge_tts

    async def one(sem: asyncio.Semaphore, item: dict) -> None:
        async with sem:
            mp3 = out_dir / f"{item['id']}.mp3"
            wav = out_dir / f"{item['id']}.wav"
            last: Exception | None = None
            for attempt in range(3):
                try:
                    await edge_tts.Communicate(item["text"], voice, rate=_edge_rate(rate)).save(str(mp3))
                    break
                except Exception as exc:
                    last = exc
                    await asyncio.sleep(1.0 * (attempt + 1))
            else:
                raise RuntimeError(f"Edge-TTS failed for {item['id']}: {last}")
            proc = subprocess.run(
                [_ffmpeg(), "-y", "-loglevel", "error", "-i", str(mp3), "-ar", str(RATE_HZ),
                 "-ac", "1", "-sample_fmt", "s16", str(wav)],
                capture_output=True, timeout=120,
            )
            if proc.returncode != 0 or not wav.exists() or wav.stat().st_size <= 44:
                raise RuntimeError(f"Edge-TTS produced no audio for {item['id']}")

    async def main() -> None:
        sem = asyncio.Semaphore(4)
        await asyncio.gather(*(one(sem, item) for item in items))

    saved = {k: os.environ.get(k) for k in ("NO_PROXY", "no_proxy")}
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"
    try:
        asyncio.run(main())
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    log(f"edge voice ready: {voice} · rate {_edge_rate(rate)}")
    return {"voice": voice, "engine": "edge"}


def _run_minimax(
    items: list[dict],
    out_dir: Path,
    voice: str,
    rate: int,
    lang: str,
    log: Callable[[str], None],
) -> dict:
    """Synthesize every line with the MiniMax Speech-02/2.8 API."""
    parts = minimax_voice_parts(voice)
    if not parts:
        raise ValueError(f"invalid MiniMax voice: {voice}")
    model, voice_id = parts
    key, source = find_minimax_key()
    if not key:
        raise RuntimeError("MINIMAX_API_KEY is not configured")
    base = minimax_base_url()
    group = minimax_group_id()
    url = base + "/v1/t2a_v2" + (f"?GroupId={group}" if group else "")
    boost = {"zh": "Chinese", "ja": "Japanese", "en": "English"}.get(lang, "auto")
    speed = round(max(0.5, min(2.0, 1.0 + int(rate) * 0.05)), 2)
    usage = 0
    lock = threading.Lock()

    def one(item: dict) -> None:
        nonlocal usage
        mp3 = out_dir / f"{item['id']}.mp3"
        wav = out_dir / f"{item['id']}.wav"
        body = {
            "model": model,
            "text": item["text"],
            "stream": False,
            "language_boost": boost,
            "output_format": "hex",
            "voice_setting": {"voice_id": voice_id, "speed": speed, "vol": 1, "pitch": 0},
            "audio_setting": {"sample_rate": RATE_HZ, "bitrate": 128000, "format": "mp3", "channel": 1},
        }
        last: Exception | None = None
        for attempt in range(3):
            try:
                session = http_session()
                r = session.post(url, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                                 json=body, timeout=180)
                if r.status_code in (429, 500, 502, 503, 504):
                    raise RuntimeError(f"MiniMax HTTP {r.status_code}: {r.text[:300]}")
                if r.status_code >= 400:
                    raise RuntimeError(f"MiniMax HTTP {r.status_code}: {r.text[:400]}")
                data = r.json()
                base_resp = data.get("base_resp") or {}
                if base_resp.get("status_code") not in (0, None):
                    raise RuntimeError(f"MiniMax error {base_resp.get('status_code')}: {base_resp.get('status_msg')}")
                payload = data.get("data") or {}
                audio_hex = payload.get("audio")
                if not audio_hex:
                    raise RuntimeError("MiniMax returned no audio")
                mp3.write_bytes(bytes.fromhex(audio_hex))
                info = data.get("extra_info") or {}
                with lock:
                    usage += int(info.get("usage_characters") or 0)
                proc = subprocess.run(
                    [_ffmpeg(), "-y", "-loglevel", "error", "-i", str(mp3), "-ar", str(RATE_HZ),
                     "-ac", "1", "-sample_fmt", "s16", str(wav)],
                    capture_output=True, timeout=120,
                )
                if proc.returncode != 0 or not wav.exists() or wav.stat().st_size <= 44:
                    raise RuntimeError(f"MiniMax audio conversion failed for {item['id']}")
                return
            except Exception as exc:
                last = exc
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"MiniMax failed for {item['id']}: {last}")

    log(f"MiniMax: {model} · {voice_id} · {source} · speed {speed}")
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(one, item) for item in items]
        for fut in concurrent.futures.as_completed(futures):
            fut.result()
    log(f"MiniMax ready · {usage} characters billed")
    return {"voice": voice, "engine": "minimax", "usage_characters": usage}


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
    out_path: Path | None = None,
    update: bool = True,
    log: Callable[[str], None] | None = None,
) -> dict:
    say = log or (lambda _: None)
    if not sapi_available() and not edge_available() and not minimax_available():
        raise RuntimeError("No TTS engine is available (install edge-tts, configure MiniMax, or use Windows SAPI)")
    path = episode_path(date, lang)
    if not path.exists():
        raise FileNotFoundError(f"episode not found: {path}")
    ep = read_json(path)
    out = Path(out_path) if out_path else audio_path(date, lang)
    if out.exists() and not force:
        say(f"audio already exists: {out.name} (use force to rebuild)")
        return {"date": date, "lang": lang, "audio": str(out), "seconds": ep.get("seconds", 0), "skipped": True}

    meta = LANG_META.get(lang, LANG_META["ja"])
    if not voice:
        voice = meta.get("edge_voice") if edge_available() else meta["voice"]
    use_minimax = minimax_voice_parts(voice) is not None
    if use_minimax and not minimax_available():
        raise RuntimeError("MINIMAX_API_KEY 未配置；把 key 写进 .env.local 后重试")
    use_edge = (not use_minimax) and _is_edge_voice(voice) and edge_available()
    if _is_edge_voice(voice) and not edge_available() and not use_minimax:
        say("edge-tts is not installed; using the Windows SAPI voice")
        voice = meta["voice"]
        use_edge = False
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
        engine = "sapi"
        if use_minimax:
            result = _run_minimax(items, tmp, voice, rate, lang, say)
            chosen = result.get("voice") or voice
            engine = "minimax"
            say(f"voice: {chosen} (MiniMax)")
        elif use_edge:
            try:
                result = _run_edge(items, tmp, voice, rate, say)
                chosen = result.get("voice") or voice
                engine = "edge"
                say(f"voice: {chosen} (Edge neural)")
            except Exception as exc:
                say(f"Edge-TTS failed ({type(exc).__name__}: {exc}); falling back to Windows SAPI")
                result = _run_sapi(items, tmp, meta["voice"], rate)
                chosen = result.get("voice") or meta["voice"]
                engine = "sapi"
                say(f"voice: {chosen} (SAPI fallback)")
        else:
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
        ep["tts"] = engine
        if update:
            write_json(path, ep)
            update_episode_index(date, lang, ep.get("title", ""), seconds)
        else:
            say("comparison audio only: episode JSON/index unchanged")
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
            "engine": engine,
            "updated": update,
            "elapsed_s": round(time.time() - started, 1),
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
