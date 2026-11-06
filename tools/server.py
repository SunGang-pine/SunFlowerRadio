"""Local studio server: static station + a small JSON API for authoring.

Only the DeepSeek key stays on the server.  The browser gets a masked status and
never the secret itself.  The server binds to localhost and refuses to serve
dotfiles (including .env.local).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import mimetypes
import re
import threading
import time
import traceback
import urllib.parse
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from .config import (
    EDGE_VOICE_CHOICES,
    LANG_META,
    ROOT,
    ENV_LOCAL,
    Settings,
    audio_path,
    episode_index_entries,
    episode_path,
    get_settings,
    read_json,
    setup_stdout,
    station_languages,
    mask_key,
)
from .deepseek import DeepSeekClient, DeepSeekError
from .news import FEEDS, FEED_BY_ID, collect_candidates, manual_candidates
from .script import write_episode
from .voice import edge_available, list_voices, sapi_available, synthesize_episode

MAX_BODY = 2 * 1024 * 1024
STATIC_ROOT = ROOT
STARTED = time.time()
VOICES_CACHE: list[str] = []
VOICES_AT = 0.0
LOCK = threading.Lock()


# ---------------------------------------------------------------- jobs
class Job:
    def __init__(self, kind: str, key: str = ""):
        self.id = uuid.uuid4().hex[:10]
        self.kind = kind
        self.key = key
        self.status = "queued"
        self.logs: list[str] = []
        self.result: dict | None = None
        self.error: str | None = None
        self.created = time.time()
        self.started: float | None = None
        self.ended: float | None = None
        self._lock = threading.Lock()

    def log(self, message: str) -> None:
        line = f"{time.strftime('%H:%M:%S')}  {message}"
        with self._lock:
            self.logs.append(line)
            if len(self.logs) > 500:
                self.logs = self.logs[-500:]
        print(f"[{self.kind}:{self.id}] {message}", flush=True)

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "id": self.id,
                "kind": self.kind,
                "status": self.status,
                "logs": list(self.logs),
                "result": self.result,
                "error": self.error,
                "created": dt.datetime.fromtimestamp(self.created).isoformat(timespec="seconds"),
                "elapsed": round((self.ended or time.time()) - (self.started or self.created), 1),
            }


JOBS: dict[str, Job] = {}


def _trim_jobs() -> None:
    if len(JOBS) <= 30:
        return
    for job in sorted(JOBS.values(), key=lambda j: j.created)[: len(JOBS) - 30]:
        JOBS.pop(job.id, None)


def run_job(job: Job, fn: Callable[[Job], dict]) -> None:
    job.status = "running"
    job.started = time.time()
    try:
        job.result = fn(job)
        job.status = "done"
    except Exception as exc:
        job.error = f"{type(exc).__name__}: {exc}"
        job.log(f"失败：{job.error}")
        print(traceback.format_exc(), flush=True)
        job.status = "error"
    finally:
        job.ended = time.time()
        _trim_jobs()


def start_job(kind: str, key: str, fn: Callable[[Job], dict]) -> Job | None:
    with LOCK:
        for job in JOBS.values():
            if job.status in ("queued", "running") and job.key == key:
                return None
        job = Job(kind, key)
        JOBS[job.id] = job
    threading.Thread(target=run_job, args=(job, fn), daemon=True).start()
    return job


# ---------------------------------------------------------------- helpers
def voices(force: bool = False) -> list[str]:
    global VOICES_CACHE, VOICES_AT
    now = time.time()
    if force or not VOICES_CACHE or now - VOICES_AT > 300:
        VOICES_CACHE = list_voices()
        VOICES_AT = now
    return VOICES_CACHE


def lang_label(lang: str) -> str:
    return LANG_META.get(lang, {}).get("label", lang)


def station_status() -> dict:
    langs = station_languages()
    entries = episode_index_entries()
    episodes = []
    for entry in entries:
        date = entry.get("date")
        if not date:
            continue
        for lang in langs:
            if len(langs) > 1 and lang not in entry:
                continue
            info = entry.get(lang) if len(langs) > 1 else entry
            if not isinstance(info, dict):
                info = {}
            path = episode_path(date, lang)
            ap = audio_path(date, lang)
            ep = None
            if path.exists():
                try:
                    ep = read_json(path)
                except Exception:
                    ep = None
            episodes.append({
                "date": date,
                "lang": lang,
                "title": info.get("title") or (ep or {}).get("title") or "",
                "seconds": info.get("seconds") or (ep or {}).get("seconds") or 0,
                "script": path.exists(),
                "audio": ap.exists(),
                "audio_name": ap.name,
                "voice": (ep or {}).get("voice") or "",
                "url": f"/#{date}/{lang}" if len(langs) > 1 else f"/#{date}",
            })
    station = {}
    try:
        station = json.loads((ROOT / "station.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    i18n = station.get("i18n", {})
    return {
        "ok": True,
        "station": {
            "name": station.get("name", "myradio"),
            "tagline": station.get("tagline", ""),
            "description": station.get("description", ""),
            "logo": station.get("logo"),
            "language": station.get("language", "ja"),
            "languages": langs,
            "i18n": i18n,
        },
        "settings": get_settings().public(),
        "voices": voices(),
        "voice_options": {
            lang: list(EDGE_VOICE_CHOICES.get(lang, [])) + [
                {"id": (LANG_META.get(lang) or {}).get("voice", ""),
                 "label": f"{(LANG_META.get(lang) or {}).get('voice', '')}（Windows SAPI · 备用）"}
            ] if (LANG_META.get(lang) or {}).get("voice") else list(EDGE_VOICE_CHOICES.get(lang, []))
            for lang in langs
        },
        "sapi": sapi_available(),
        "edge": edge_available(),
        "tts": "Microsoft Edge Neural" if edge_available() else ("Windows SAPI" if sapi_available() else "unavailable"),
        "default_voices": {
            lang: (LANG_META.get(lang) or {}).get("edge_voice") if edge_available() else (LANG_META.get(lang) or {}).get("voice", "")
            for lang in langs
        },
        "default_rates": {lang: (LANG_META.get(lang) or {}).get("rate", 0) for lang in langs},
        "feeds": [{"id": f["id"], "name": f["name"], "tags": f.get("tags", []), "url": f["url"]} for f in FEEDS],
        "episodes": episodes,
        "server_time": dt.datetime.now().isoformat(timespec="seconds"),
        "uptime_s": round(time.time() - STARTED, 0),
    }


def save_key(key: str) -> None:
    lines: list[str] = []
    if ENV_LOCAL.exists():
        lines = ENV_LOCAL.read_text(encoding="utf-8-sig").splitlines()
    out, found = [], False
    for line in lines:
        if re.match(r"^\s*DEEPSEEK_API_KEY\s*=", line):
            out.append(f"DEEPSEEK_API_KEY={key}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"DEEPSEEK_API_KEY={key}")
    ENV_LOCAL.write_text("\n".join(out) + "\n", encoding="utf-8")


def delete_local_key() -> None:
    if not ENV_LOCAL.exists():
        return
    lines = ENV_LOCAL.read_text(encoding="utf-8-sig").splitlines()
    out = [x for x in lines if not re.match(r"^\s*DEEPSEEK_API_KEY\s*=", x)]
    ENV_LOCAL.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")


def _date(value: Any) -> dt.date:
    if not value:
        return dt.date.today()
    return dt.date.fromisoformat(str(value))


def _feed_list(ids: Any) -> list[dict]:
    if not ids:
        return FEEDS
    out = []
    for fid in ids:
        if fid in FEED_BY_ID:
            out.append(FEED_BY_ID[fid])
    return out or FEEDS


def job_generate(job: Job, body: dict) -> dict:
    settings = get_settings()
    if not settings.configured:
        raise DeepSeekError("DeepSeek API key 未配置")
    client = DeepSeekClient(settings)
    date = _date(body.get("date"))
    lang = str(body.get("lang") or station_languages()[0])
    if lang not in LANG_META:
        raise ValueError(f"unsupported language: {lang}")
    if lang not in station_languages():
        job.log(f"注意：{lang} 不在 station.json 的 languages 里，播放器可能不显示这一期")
    count = int(body.get("count") or 4)
    if count not in (3, 4, 5):
        raise ValueError("count must be 3, 4 or 5")
    topic = str(body.get("topic") or "").strip()
    mode = str(body.get("mode") or "rss")
    job.log(f"{date.isoformat()} · {lang_label(lang)} · {count} 条 · {mode}")
    job.log(f"DeepSeek：{settings.key_source} · {settings.model}")

    if mode == "manual":
        urls = body.get("source_urls") or []
        if isinstance(urls, str):
            urls = urls.splitlines()
        cands = manual_candidates([str(u) for u in urls], str(body.get("source_text") or ""), job.log)
    else:
        cands = collect_candidates(_feed_list(body.get("feeds")), count, topic, job.log)
    if len(cands) < count:
        raise ValueError(f"只找到 {len(cands)} 条可用素材，不足以生成 {count} 条新闻")
    job.log(f"素材就绪：{len(cands)} 篇候选")

    ep, path = write_episode(client, cands, date, lang, count, topic, job.log)
    job.log(f"台本已保存：{path.relative_to(ROOT)}")
    result = {
        "date": date.isoformat(),
        "lang": lang,
        "title": ep["title"],
        "script": str(path.relative_to(ROOT)),
        "url": f"/#{date.isoformat()}/{lang}" if len(station_languages()) > 1 else f"/#{date.isoformat()}",
        "audio": False,
        "seconds": 0,
    }
    if bool(body.get("voice", True)):
        meta = LANG_META.get(lang, {})
        default_voice = meta.get("edge_voice") if edge_available() else meta.get("voice", "")
        voice_name = str(body.get("voice_name") or default_voice or "")
        rate = body.get("rate")
        rate = int(rate) if rate is not None and str(rate) != "" else meta.get("rate", 0)
        v = synthesize_episode(date.isoformat(), lang, voice=voice_name, rate=rate, force=True, log=job.log)
        result.update({"audio": True, "seconds": v.get("seconds"), "voice": v.get("voice"), "mp3": Path(v["audio"]).name})
    else:
        job.log("已跳过语音合成")
    job.log("完成。")
    return result


def job_voice(job: Job, body: dict) -> dict:
    date = _date(body.get("date")).isoformat()
    lang = str(body.get("lang") or station_languages()[0])
    if not episode_path(date, lang).exists():
        raise FileNotFoundError(f"没有台本：{episode_path(date, lang).name}")
    meta = LANG_META.get(lang, {})
    default_voice = meta.get("edge_voice") if edge_available() else meta.get("voice", "")
    voice_name = str(body.get("voice_name") or default_voice or "")
    rate = body.get("rate")
    rate = int(rate) if rate is not None and str(rate) != "" else meta.get("rate", 0)
    job.log(f"配音：{date}-{lang} · {voice_name} · rate {rate}")
    v = synthesize_episode(date, lang, voice=voice_name, rate=rate, force=bool(body.get("force", True)), log=job.log)
    job.log("完成。")
    return v


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "myradio-studio/1.0"
    protocol_version = "HTTP/1.1"

    # -- utilities -------------------------------------------------
    def log_message(self, fmt, *args):
        if self.path.startswith("/api/"):
            print(f"[http] {self.command} {self.path} {args[1] if len(args) > 1 else ''}", flush=True)

    def send_json(self, data: Any, status: int = 200) -> None:
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise ValueError("request body too large")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid JSON body: {exc}")
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        return data

    def error_json(self, status: int, message: str, **extra) -> None:
        self.send_json({"ok": False, "error": message, **extra}, status)

    # -- routing ---------------------------------------------------
    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        try:
            if path.startswith("/api/"):
                return self.api_get(path, urllib.parse.parse_qs(parsed.query))
            return self.serve_static(path)
        except BrokenPipeError:
            return
        except Exception as exc:
            print(traceback.format_exc(), flush=True)
            if path.startswith("/api/"):
                self.error_json(500, f"{type(exc).__name__}: {exc}")
            else:
                self.send_error(500)

    def do_HEAD(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path.startswith("/api/"):
            return self.send_json({"ok": True})
        return self.serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        try:
            body = self.read_json()
            return self.api_post(path, body)
        except ValueError as exc:
            self.error_json(400, str(exc))
        except BrokenPipeError:
            return
        except Exception as exc:
            print(traceback.format_exc(), flush=True)
            self.error_json(500, f"{type(exc).__name__}: {exc}")

    def do_DELETE(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/api/key":
            delete_local_key()
            return self.send_json({"ok": True, "settings": get_settings().public()})
        self.error_json(404, "not found")

    def api_get(self, path: str, query: dict) -> None:
        if path == "/api/status":
            return self.send_json(station_status())
        if path == "/api/jobs":
            with LOCK:
                jobs = sorted(JOBS.values(), key=lambda j: j.created, reverse=True)[:12]
            return self.send_json({"ok": True, "jobs": [j.to_dict() for j in jobs]})
        if path.startswith("/api/jobs/"):
            jid = path.rsplit("/", 1)[-1]
            job = JOBS.get(jid)
            if not job:
                return self.error_json(404, "job not found")
            return self.send_json({"ok": True, "job": job.to_dict()})
        if path == "/api/verify":
            settings = get_settings()
            if not settings.configured:
                return self.error_json(400, "DeepSeek API key 未配置")
            try:
                return self.send_json({"ok": True, "result": DeepSeekClient(settings).verify(), "settings": settings.public()})
            except Exception as exc:
                return self.error_json(502, str(exc))
        if path == "/api/voices":
            return self.send_json({"ok": True, "voices": voices(force=True)})
        return self.error_json(404, "not found")

    def api_post(self, path: str, body: dict) -> None:
        if path == "/api/key":
            key = str(body.get("key") or "").strip()
            if not re.match(r"^sk-[A-Za-z0-9_-]{12,}$", key):
                return self.error_json(400, "这个 key 看起来不对（应以 sk- 开头）")
            settings = Settings(key, "entered in studio", get_settings().base_url, get_settings().model)
            try:
                verify = DeepSeekClient(settings).verify()
            except Exception as exc:
                return self.error_json(400, f"DeepSeek 连接失败：{exc}")
            save_key(key)
            print(f"[key] saved a new DeepSeek key ({mask_key(key)}) to .env.local", flush=True)
            return self.send_json({"ok": True, "verify": verify, "settings": get_settings().public()})
        if path == "/api/key/delete":
            delete_local_key()
            return self.send_json({"ok": True, "settings": get_settings().public()})
        if path == "/api/generate":
            if not get_settings().configured:
                return self.error_json(400, "请先连接 DeepSeek API key")
            date = str(body.get("date") or dt.date.today().isoformat())
            lang = str(body.get("lang") or station_languages()[0])
            job_key = f"generate:{date}:{lang}"
            job = start_job("generate", job_key, lambda j: job_generate(j, body))
            if job is None:
                return self.error_json(409, "这一期正在生成中，请稍候")
            return self.send_json({"ok": True, "job": job.to_dict()})
        if path == "/api/voice":
            date = str(body.get("date") or "")
            lang = str(body.get("lang") or station_languages()[0])
            if not date:
                return self.error_json(400, "缺少 date")
            job = start_job("voice", f"voice:{date}:{lang}", lambda j: job_voice(j, body))
            if job is None:
                return self.error_json(409, "这一期正在配音中，请稍候")
            return self.send_json({"ok": True, "job": job.to_dict()})
        return self.error_json(404, "not found")

    # -- static ----------------------------------------------------
    def serve_static(self, path: str) -> None:
        rel = urllib.parse.unquote(path)
        if rel in ("/studio", "/studio/"):
            rel = "/studio/index.html"
        if rel in ("", "/"):
            rel = "/index.html"
        rel = rel.lstrip("/")
        parts = Path(rel).parts
        if not rel or any(p.startswith(".") for p in parts) or any(p in ("tools", "__pycache__", "node_modules") for p in parts):
            return self.error_json(403, "forbidden")
        full = (STATIC_ROOT / rel).resolve()
        try:
            full.relative_to(STATIC_ROOT.resolve())
        except ValueError:
            return self.error_json(403, "forbidden")
        if not full.is_file():
            return self.error_json(404, "not found")
        ctype = mimetypes.guess_type(full.name)[0] or "application/octet-stream"
        if full.suffix == ".mp3":
            ctype = "audio/mpeg"
        elif full.suffix == ".svg":
            ctype = "image/svg+xml"
        elif full.suffix == ".json":
            ctype = "application/json; charset=utf-8"
        elif full.suffix in (".html", ".js", ".css"):
            ctype += "; charset=utf-8"
        try:
            size = full.stat().st_size
            start, end = 0, size - 1
            range_header = self.headers.get("Range")
            status = 200
            if range_header:
                m = re.match(r"bytes=(\d*)-(\d*)$", range_header.strip())
                if m:
                    if m.group(1):
                        start = int(m.group(1))
                    if m.group(2):
                        end = int(m.group(2))
                    if start > end or start >= size:
                        self.send_error(416)
                        return
                    status = 206
            length = end - start + 1
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Cache-Control", "no-store" if full.suffix in (".html", ".json", ".js", ".css") else "public, max-age=60")
            self.end_headers()
            if self.command == "HEAD":
                return
            with open(full, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            return


def main(argv: list[str] | None = None) -> int:
    setup_stdout()
    parser = argparse.ArgumentParser(description="myradio local studio")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--open", action="store_true", help="open the studio in a browser")
    args = parser.parse_args(argv)

    import socket
    try:
        with socket.create_connection((args.host, args.port), timeout=0.4):
            print(f"端口 {args.port} 已被占用：可能已经有一个 SunFlowerRadio 在运行。")
            print(f"先打开 http://{args.host}:{args.port}/studio/ ，或换一个端口 --port 8091。")
            return 2
    except OSError:
        pass

    settings = get_settings()
    print("=" * 62)
    print("  SunFlowerRadio Studio")
    print(f"  player : http://{args.host}:{args.port}/")
    print(f"  studio : http://{args.host}:{args.port}/studio/")
    print(f"  key    : {settings.key_source} {mask_key(settings.api_key) or '(not configured)'}")
    print(f"  model  : {settings.model}   TTS: {'Edge Neural' if edge_available() else 'Windows SAPI'}")
    print("=" * 62, flush=True)
    voices()
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.daemon_threads = True
    if args.open:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://{args.host}:{args.port}/studio/")).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye", flush=True)
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
