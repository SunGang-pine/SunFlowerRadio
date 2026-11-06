"""Shared configuration for the myradio production tools.

Everything here is local-first: the station is still a static site.  The tools
only add an authoring layer (news -> script -> voice) and a small local server
for the studio.  Secrets never go into the station JSON or into the repository.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AUDIO_DIR = ROOT / "audio"
EPISODES_DIR = ROOT / "episodes"
STATION_FILE = ROOT / "station.json"
EPISODE_INDEX = ROOT / "episodes.json"
ENV_LOCAL = ROOT / ".env.local"

# DeepSeek's OpenAI-compatible endpoint.  RADIO_* variables make it easy to
# point the studio at a compatible gateway later.
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"

# The station's languages.  "voice" is a Windows SAPI name; the studio lets the
# user pick another installed voice.
LANG_META = {
    "ja": {
        "label": "日本語",
        "english": "Japanese",
        "voice": "Microsoft Haruka",
        "rate": 2,
        "weekdays": ["月", "火", "水", "木", "金", "土", "日"],
        "date_word": lambda d: f"{d.month}月{d.day}日",
    },
    "en": {
        "label": "English",
        "english": "English",
        "voice": "Microsoft Zira Desktop",
        "rate": 2,
        "weekdays": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
        "date_word": lambda d: d.strftime("%B ") + str(d.day),
    },
    "zh": {
        "label": "中文",
        "english": "Chinese",
        "voice": "Microsoft Huihui Desktop",
        "rate": 0,
        "weekdays": ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"],
        "date_word": lambda d: f"{d.month}月{d.day}日",
    },
}


def setup_stdout() -> None:
    """Make Japanese / Chinese tool output readable in a Windows console."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def read_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value:
            out[key] = value
    return out


def _dsh_credentials_key() -> str | None:
    """Use the DeepSeek key already configured for the DSH harness, if any.

    This makes "接上 DeepSeek" work on a machine that is already signed in to
    DSH, without copying the secret into the repository.  The key is read at
    runtime and never exposed through the API.
    """
    path = Path.home() / ".dsh" / ".credentials.yaml"
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    m = re.search(r"^\s*DEEPSEEK_API_KEY:\s*(sk-[A-Za-z0-9_-]+)\s*$", text, re.M)
    return m.group(1) if m else None


def find_api_key() -> tuple[str | None, str]:
    """Return (key, where it came from).  Environment wins, then .env.local."""
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key, "environment"
    for path in (ENV_LOCAL, ROOT / ".env"):
        key = read_env_file(path).get("DEEPSEEK_API_KEY", "").strip()
        if key:
            return key, str(path.relative_to(ROOT))
    key = _dsh_credentials_key()
    if key:
        return key, "DSH credentials"
    key = read_env_file(Path.home() / ".dsh" / ".env").get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key, "DSH .env"
    return None, "not configured"


def mask_key(key: str | None) -> str:
    if not key:
        return ""
    if len(key) <= 10:
        return "•" * len(key)
    return key[:5] + "…" + key[-4:]


@dataclass(frozen=True)
class Settings:
    api_key: str | None
    key_source: str
    base_url: str
    model: str

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def public(self) -> dict:
        return {
            "configured": self.configured,
            "masked": mask_key(self.api_key),
            "source": self.key_source,
            "base_url": self.base_url,
            "model": self.model,
        }


def get_settings() -> Settings:
    key, source = find_api_key()
    env = {**read_env_file(ROOT / ".env"), **read_env_file(ENV_LOCAL)}
    base = os.environ.get("DEEPSEEK_BASE_URL") or env.get("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL
    model = os.environ.get("DEEPSEEK_MODEL") or env.get("DEEPSEEK_MODEL") or DEFAULT_MODEL
    return Settings(key, source, base.rstrip("/"), model)


def load_station() -> dict:
    try:
        return json.loads(STATION_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"name": "myradio", "language": "ja", "languages": ["ja"]}


def station_languages() -> list[str]:
    station = load_station()
    langs = station.get("languages")
    if isinstance(langs, list) and langs:
        return [str(x) for x in langs]
    return [str(station.get("language") or "ja")]


def audio_path(date: str, lang: str) -> Path:
    langs = station_languages()
    if len(langs) == 1 and lang == langs[0]:
        name = f"{date}.mp3"
    else:
        name = f"{date}-{lang}.mp3"
    return AUDIO_DIR / name


def episode_path(date: str, lang: str) -> Path:
    langs = station_languages()
    if len(langs) == 1 and lang == langs[0]:
        name = f"{date}.json"
    else:
        name = f"{date}-{lang}.json"
    return EPISODES_DIR / name


def episode_stem(date: str, lang: str) -> str:
    return episode_path(date, lang).stem


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def update_episode_index(date: str, lang: str, title: str, seconds: float) -> None:
    """Update episodes.json (newest first) without disturbing other languages."""
    try:
        entries = read_json(EPISODE_INDEX)
        if not isinstance(entries, list):
            entries = []
    except (OSError, json.JSONDecodeError):
        entries = []
    entry = next((e for e in entries if isinstance(e, dict) and e.get("date") == date), None)
    if entry is None:
        entry = {"date": date}
        entries.append(entry)
    langs = station_languages()
    if len(langs) == 1:
        entry["title"] = title
        entry["seconds"] = round(seconds, 2)
    else:
        entry[lang] = {"title": title, "seconds": round(seconds, 2)}
    # keep a stable language order (station.json languages), so the player's
    # language switch does not jump around between episodes
    ordered = {"date": date}
    for code in langs:
        if code in entry:
            ordered[code] = entry[code]
    for key, value in entry.items():
        if key not in ordered:
            ordered[key] = value
    entries[entries.index(entry)] = ordered
    entries.sort(key=lambda e: str(e.get("date", "")), reverse=True)
    tmp = EPISODE_INDEX.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    tmp.replace(EPISODE_INDEX)


def episode_index_entries() -> list[dict]:
    try:
        data = read_json(EPISODE_INDEX)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []
