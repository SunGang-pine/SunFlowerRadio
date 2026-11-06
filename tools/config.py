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
DEFAULT_MINIMAX_BASE_URL = "https://api.minimax.io"
DEFAULT_MINIMAX_MODEL = "speech-2.8-hd"

# The station's languages.  "voice" is a Windows SAPI fallback; "edge_voice"
# is the high-quality Microsoft Edge neural voice used by default.
LANG_META = {
    "ja": {
        "label": "日本語",
        "english": "Japanese",
        "voice": "Microsoft Haruka",
        "edge_voice": "ja-JP-NanamiNeural",
        "rate": 4,
        "weekdays": ["月", "火", "水", "木", "金", "土", "日"],
        "date_word": lambda d: f"{d.month}月{d.day}日",
    },
    "en": {
        "label": "English",
        "english": "English",
        "voice": "Microsoft Zira Desktop",
        "edge_voice": "en-US-AvaNeural",
        "rate": 2,
        "weekdays": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
        "date_word": lambda d: d.strftime("%B ") + str(d.day),
    },
    "zh": {
        "label": "中文",
        "english": "Chinese",
        "voice": "Microsoft Huihui Desktop",
        "edge_voice": "zh-CN-XiaoxiaoNeural",
        "rate": 2,
        "weekdays": ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"],
        "date_word": lambda d: f"{d.month}月{d.day}日",
    },
}

# Voices offered in the studio.  The SAPI fallbacks are appended by the server.
EDGE_VOICE_CHOICES = {
    "ja": [
        {"id": "ja-JP-NanamiNeural", "label": "Nanami（女声 · 自然）"},
        {"id": "ja-JP-KeitaNeural", "label": "Keita（男声）"},
    ],
    "en": [
        {"id": "en-US-AvaNeural", "label": "Ava（女声 · 自然）"},
        {"id": "en-US-AndrewNeural", "label": "Andrew（男声）"},
        {"id": "en-US-EmmaNeural", "label": "Emma（女声 · 清亮）"},
        {"id": "en-US-BrianNeural", "label": "Brian（男声 · 轻松）"},
    ],
    "zh": [
        {"id": "zh-CN-XiaoxiaoNeural", "label": "晓晓（女声 · 温暖）"},
        {"id": "zh-CN-XiaoyiNeural", "label": "晓伊（女声 · 活泼）"},
        {"id": "zh-CN-YunxiNeural", "label": "云希（男声 · 阳光）"},
        {"id": "zh-CN-YunjianNeural", "label": "云健（男声 · 激情）"},
        {"id": "zh-CN-YunyangNeural", "label": "云扬（男声 · 专业）"},
    ],
}
EDGE_VOICE_IDS = {v["id"] for choices in EDGE_VOICE_CHOICES.values() for v in choices}

# MiniMax Speech-02 / 2.8 voices.  The studio value is
# "mmx:<model>:<voice_id>" so the engine, model and voice travel together.
MINIMAX_VOICES = {
    "zh": [
        {"id": "mmx:speech-2.8-hd:Chinese (Mandarin)_News_Anchor",
         "label": "MiniMax 新闻女声（推荐）"},
        {"id": "mmx:speech-2.8-hd:Chinese (Mandarin)_Male_Announcer",
         "label": "MiniMax 新闻男声"},
        {"id": "mmx:speech-2.8-hd:Chinese (Mandarin)_Radio_Host",
         "label": "MiniMax 电台主持"},
        {"id": "mmx:speech-2.8-hd:Chinese (Mandarin)_Warm_Girl",
         "label": "MiniMax 温暖女声"},
    ],
    "ja": [
        {"id": "mmx:speech-2.8-hd:Japanese_KindLady",
         "label": "MiniMax 温柔女声"},
        {"id": "mmx:speech-2.8-hd:Japanese_CalmLady",
         "label": "MiniMax 沉稳女声"},
        {"id": "mmx:speech-2.8-hd:Japanese_GracefulMaiden",
         "label": "MiniMax 优雅女声"},
        {"id": "mmx:speech-2.8-hd:Japanese_IntellectualSenior",
         "label": "MiniMax 知性男声"},
    ],
    "en": [
        {"id": "mmx:speech-2.8-hd:English_expressive_narrator",
         "label": "MiniMax Expressive Narrator"},
        {"id": "mmx:speech-2.8-hd:English_Graceful_Lady",
         "label": "MiniMax Graceful Lady"},
        {"id": "mmx:speech-2.8-hd:English_Trustworth_Man",
         "label": "MiniMax Trustworthy Man"},
        {"id": "mmx:speech-2.8-hd:English_Insightful_Speaker",
         "label": "MiniMax Insightful Speaker"},
    ],
}
MINIMAX_VOICE_IDS = {v["id"] for choices in MINIMAX_VOICES.values() for v in choices}


def minimax_voice_parts(voice: str | None) -> tuple[str, str] | None:
    """Parse 'mmx:<model>:<voice_id>' into (model, voice_id)."""
    if not voice or not voice.startswith("mmx:"):
        return None
    parts = voice.split(":", 2)
    if len(parts) != 3 or not parts[2].strip():
        return None
    model = parts[1].strip() or DEFAULT_MINIMAX_MODEL
    return model, parts[2].strip()


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


def find_minimax_key() -> tuple[str | None, str]:
    """MiniMax key: env, .env.local, .env, then DSH credentials."""
    key = (os.environ.get("MINIMAX_API_KEY") or os.environ.get("MINIMAX_KEY") or "").strip()
    if key:
        return key, "environment"
    for path in (ENV_LOCAL, ROOT / ".env"):
        key = (read_env_file(path).get("MINIMAX_API_KEY") or read_env_file(path).get("MINIMAX_KEY") or "").strip()
        if key:
            return key, str(path.relative_to(ROOT))
    path = Path.home() / ".dsh" / ".credentials.yaml"
    try:
        text = path.read_text(encoding="utf-8-sig")
        m = re.search(r"^\s*MINIMAX_API_KEY:\s*([^\s#]+)\s*$", text, re.M)
        if m:
            return m.group(1).strip(), "DSH credentials"
    except OSError:
        pass
    return None, "not configured"


def minimax_base_url() -> str:
    env = {**read_env_file(ROOT / ".env"), **read_env_file(ENV_LOCAL)}
    return (os.environ.get("MINIMAX_BASE_URL") or env.get("MINIMAX_BASE_URL") or DEFAULT_MINIMAX_BASE_URL).rstrip("/")


def minimax_model() -> str:
    env = {**read_env_file(ROOT / ".env"), **read_env_file(ENV_LOCAL)}
    return os.environ.get("MINIMAX_MODEL") or env.get("MINIMAX_MODEL") or DEFAULT_MINIMAX_MODEL


def minimax_group_id() -> str | None:
    env = {**read_env_file(ROOT / ".env"), **read_env_file(ENV_LOCAL)}
    return (os.environ.get("MINIMAX_GROUP_ID") or env.get("MINIMAX_GROUP_ID") or "").strip() or None


def minimax_configured() -> bool:
    return bool(find_minimax_key()[0])


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
