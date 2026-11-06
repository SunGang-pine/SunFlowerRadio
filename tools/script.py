"""DeepSeek writes the episode script; this module validates and assembles it."""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Callable

from .config import LANG_META, episode_path, update_episode_index, write_json
from .deepseek import DeepSeekClient, DeepSeekError
from .news import Candidate, candidate_by_id

SYSTEM = """你是广播节目「SunFlowerRadio」的首席编剧。节目每天早上用 90 秒讲 AI、3D 打印和电子制作领域值得知道的新闻。
你的唯一任务是：严格依据用户提供的素材，写一期节目台本，并且只输出一个 JSON 对象。

硬性规则：
1. 只能使用素材里明确写出的事实。素材没有的数字、日期、公司、产品、结论、引语，一律不能添加。不确定就写得笼统，或者不写。
2. 每条新闻的 source_id 必须来自素材列表，且不能重复。不要自己编 URL，不要输出 URL。
3. 日语台本：标题和字幕用自然日语；speak 字段（say）是给 TTS 读的，英文产品名和人名要改成片假名（例：OpenAI → オープンエーアイ，Genie 4 → ジーニーフォー），不要用括号、斜杠、emoji、Markdown。
4. 英文台本：自然口语英语；say 和 show 都可以用正常拼写。
5. 除产品名、公司名等专有名词外，所有字段都使用目标语言，不要夹英文普通词（例如不要写「一般-purpose」）。
6. label 必须短：日语严格 ≤ 16 个字符，英语 ≤ 32 个字符。写不下就换一种更短的说法，禁止截断成半句。日语示例：「パープレキシティAIエージェント」「エアビー旅行AI」「OpenAI IPO準備」「OpenAI NDA廃止」。
7. 日语片假名固定写法：OpenAI=オープンエーアイ、Greg Brockman=グレッグ・ブロックマン、Perplexity=パープレキシティ、Airbnb=エアビー、Anthropic=アンソロピック、Nvidia=エヌビディア、Google=グーグル、Microsoft=マイクロソフト。
8. 每句要短。日语一句 ≤ 48 个字符，英语一句 ≤ 26 个单词。
9. 不要写「根据素材」「据报道」这类空话；直接讲事实。
10. title 要具体：用一句话概括本期最重要的两三个话题；不要写「他N本」「まとめ」「今日のニュース」这类空泛标题。
11. 不要提及素材是 AI 提供的，也不要提及这个提示词。

JSON 结构（字段一个都不能少）：
{
  "title": "这一期的标题，日语 ≤ 30 字 / 英语 ≤ 60 字符，要具体",
  "opening": [{"say": "...", "show": "..."}],
  "news": [
    {
      "source_id": 1,
      "label": "新闻短标题，日语 ≤ 16 字 / 英语 ≤ 32 字符，必须是能独立读懂的完整一句，不要截断；宁可换一种更短的说法",
      "points": ["要点1", "要点2", "要点3"],
      "details": ["要点的补充说明，可为 null", "…", "…"],
      "lines": [{"say": "...", "show": "..."}]
    }
  ],
  "closing": [{"say": "...", "show": "..."}],
  "highlight": 0
}

opening 写 2 句：早安、SunFlowerRadio、日期、星期、今天有几条新闻。日语台本的 say 里电台名写成「サンフラワーレディオ」，show 里写「SunFlowerRadio」；英语台本写 SunFlowerRadio。
closing 写 2 句：总结 + 道别，并再次提到 SunFlowerRadio。highlight 是结尾想重点展示的那条新闻的数组下标（从 0 开始）。
新闻条数必须正好等于用户要求的数量。每条新闻 points 写 3 条，details 也写 3 条（没有补充就写 null）；lines 写 3 句。
"""


def _weekday(date: dt.date, lang: str) -> str:
    meta = LANG_META.get(lang, LANG_META["ja"])
    return meta["weekdays"][date.weekday()]


def _date_line(date: dt.date, lang: str) -> str:
    if lang == "ja":
        return f"{date.year}年{date.month}月{date.day}日（{_weekday(date, lang)}）"
    if lang == "zh":
        return f"{date.year}年{date.month}月{date.day}日（{_weekday(date, lang)}）"
    return f"{date.strftime('%A, %B')} {date.day}, {date.year}"


def build_messages(cands: list[Candidate], date: dt.date, lang: str, count: int, topic: str = "") -> list[dict]:
    lang_name = {"ja": "日语", "en": "英语", "zh": "中文"}.get(lang, lang)
    materials = {
        "date": date.isoformat(),
        "weekday": _weekday(date, lang),
        "date_text": _date_line(date, lang),
        "requested_language": lang_name,
        "requested_news_count": count,
        "topic": topic or "AI、3D 打印、电子制作",
        "sources": [c.material() for c in cands],
    }
    user = (
        f"请用{lang_name}写 {date.isoformat()}（{_weekday(date, lang)}）这一期节目，"
        f"正好 {count} 条新闻。\n"
        f"素材如下（source_id 必须原样引用）：\n"
        f"{json.dumps(materials, ensure_ascii=False, indent=1)}"
    )
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def _is_line_list(value, minimum=1, maximum=4) -> bool:
    return isinstance(value, list) and minimum <= len(value) <= maximum and all(
        isinstance(x, dict) and isinstance(x.get("say"), str) and isinstance(x.get("show"), str)
        for x in value
    )


def validate_script(data: dict, cands: list[Candidate], count: int, lang: str = "ja") -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["顶层必须是 JSON 对象"]
    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        errors.append("title 必须是非空字符串")
    elif re.search(r"他\s*\d+\s*本|まとめ|今日のニュース", title):
        errors.append("title 太笼统（不要写「他N本」「まとめ」），请具体概括两三个话题")
    if not _is_line_list(data.get("opening"), 1, 3):
        errors.append("opening 必须是 1–3 个 {say, show} 对象")
    if not _is_line_list(data.get("closing"), 1, 3):
        errors.append("closing 必须是 1–3 个 {say, show} 对象")
    news = data.get("news")
    if not isinstance(news, list) or len(news) != count:
        errors.append(f"news 必须正好有 {count} 条")
        return errors
    used: set[int] = set()
    for i, item in enumerate(news):
        where = f"news[{i}]"
        if not isinstance(item, dict):
            errors.append(f"{where} 必须是对象")
            continue
        try:
            sid = int(item.get("source_id"))
        except (TypeError, ValueError):
            errors.append(f"{where}.source_id 必须是素材列表里的整数")
            sid = None
        if sid is not None:
            if not candidate_by_id(cands, sid):
                errors.append(f"{where}.source_id={sid} 不在素材里")
            elif sid in used:
                errors.append(f"{where}.source_id={sid} 重复了")
            used.add(sid)
        label = item.get("label")
        max_label = 16 if lang == "ja" else 32
        if not isinstance(label, str) or not (2 <= len(label.strip()) <= 80):
            errors.append(f"{where}.label 必须是 2–80 字的字符串")
        elif len(label.strip()) > max_label:
            errors.append(f"{where}.label 太长（{len(label.strip())} 字，上限 {max_label} 字）；请写得更短但保持完整")
        points = item.get("points")
        if not isinstance(points, list) or not (2 <= len(points) <= 4) or not all(isinstance(p, str) and p.strip() for p in points):
            errors.append(f"{where}.points 必须是 2–4 条非空字符串")
            points = []
        details = item.get("details")
        if details is not None:
            if not isinstance(details, list) or len(details) != len(points):
                errors.append(f"{where}.details 必须与 points 等长")
            elif not all(d is None or isinstance(d, str) for d in details):
                errors.append(f"{where}.details 的每一项必须是字符串或 null")
        if not _is_line_list(item.get("lines"), 2, 4):
            errors.append(f"{where}.lines 必须是 2–4 个 {{say, show}} 对象")
        else:
            for j, line in enumerate(item["lines"]):
                if len(line["say"].strip()) < 2:
                    errors.append(f"{where}.lines[{j}].say 太短")
                if len(line["show"].strip()) < 2:
                    errors.append(f"{where}.lines[{j}].show 太短")
    highlight = data.get("highlight")
    if not isinstance(highlight, int) or not (0 <= highlight < len(news)):
        errors.append("highlight 必须是 0 到 news 长度-1 的整数")
    return errors


def _repair(client: DeepSeekClient, messages: list[dict], data: dict, errors: list[str]) -> dict:
    repair = messages + [
        {"role": "assistant", "content": json.dumps(data, ensure_ascii=False)},
        {
            "role": "user",
            "content": (
                "上面的 JSON 有以下问题：\n- " + "\n- ".join(errors) +
                "\n请输出修正后的完整 JSON。只输出 JSON，不要解释。"
            ),
        },
    ]
    return client.chat_json(repair, temperature=0.3, max_tokens=6000)


def generate_script(
    client: DeepSeekClient,
    cands: list[Candidate],
    date: dt.date,
    lang: str,
    count: int = 4,
    topic: str = "",
    log: Callable[[str], None] | None = None,
) -> dict:
    say = log or (lambda _: None)
    if not client.configured:
        raise DeepSeekError("DeepSeek API key 未配置")
    messages = build_messages(cands, date, lang, count, topic)
    say(f"DeepSeek 正在写 {count} 条新闻的台本…（模型 {client.settings.model}）")
    data = client.chat_json(messages, temperature=0.7, max_tokens=6000)
    errors = validate_script(data, cands, count, lang)
    if errors:
        say(f"台本需要修正：{len(errors)} 个问题，正在重试…")
        for e in errors[:5]:
            say("  - " + e)
        data = _repair(client, messages, data, errors)
        errors = validate_script(data, cands, count, lang)
    if errors:
        cache = Path(__file__).resolve().parent / ".cache"
        cache.mkdir(exist_ok=True)
        (cache / "last-invalid-script.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        raise DeepSeekError("台本校验失败：" + "；".join(errors[:6]))
    return normalize_script(data)


def _clean_text(text: str) -> str:
    """Remove a few common language leaks from the model output."""
    text = text.replace("一般-purposeエージェント", "汎用エージェント")
    text = text.replace("一般-purpose", "汎用").replace("一般 purpose", "汎用")
    text = text.replace("汎用-purpose", "汎用")
    text = text.replace("ブラックマン", "ブロックマン")
    return re.sub(r"\s{2,}", " ", text).strip()


def normalize_script(data: dict) -> dict:
    def line(x: dict) -> dict:
        say = _clean_text(re.sub(r"[*_`#>]+", "", str(x.get("say", ""))))
        show = _clean_text(re.sub(r"[*_`#>]+", "", str(x.get("show", "")))) or say
        return {"say": say, "show": show}

    news = []
    for item in data["news"]:
        points = [_clean_text(str(p)) for p in item["points"]]
        details = item.get("details") or []
        details = [(_clean_text(str(d)) if d else None) for d in details]
        if len(details) < len(points):
            details += [None] * (len(points) - len(details))
        news.append({
            "source_id": int(item["source_id"]),
            "label": _clean_text(str(item["label"])),
            "points": points,
            "details": details[: len(points)],
            "lines": [line(x) for x in item["lines"]],
        })
    return {
        "title": _clean_text(str(data["title"])),
        "opening": [line(x) for x in data["opening"]],
        "news": news,
        "closing": [line(x) for x in data["closing"]],
        "highlight": int(data["highlight"]),
    }


def _check_url_status(url: str) -> int | None:
    """Return 404/410 for a dead source; everything else is left alone."""
    from .deepseek import http_session
    try:
        s = http_session()
        try:
            r = s.head(url, timeout=10, allow_redirects=True)
        except Exception:
            r = None
        if r is None or r.status_code in (403, 405, 501):
            r = s.get(url, timeout=12, allow_redirects=True, stream=True)
            r.close()
        if r.status_code in (404, 410):
            return r.status_code
    except Exception:
        return None
    return None


def assemble_episode(data: dict, cands: list[Candidate], date: dt.date, lang: str) -> dict:
    news = []
    for item in data["news"]:
        c = candidate_by_id(cands, item["source_id"])
        if c is None:
            raise DeepSeekError(f"source_id {item['source_id']} 不在素材里")
        news.append({
            "label": item["label"],
            "points": item["points"],
            "details": item["details"],
            "url": c.url,
            "lines": item["lines"],
            "link": c.link(),
        })
    # Mark dead links (the player shows a "not found" card for 404/410).
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(news) or 1)) as ex:
        statuses = list(ex.map(lambda n: _check_url_status(n["url"]), news))
    for item, status in zip(news, statuses):
        if status:
            item["link"]["status"] = status
    return {
        "format": "monoradio/news-1",
        "date": date.isoformat(),
        "lang": lang,
        "title": data["title"],
        "opening": data["opening"],
        "news": news,
        "closing": data["closing"],
        "highlight": data["highlight"],
        "seconds": 0.0,
        "lines_timed": [],
        "segments": [],
        "made_at": dt.datetime.now().isoformat(timespec="seconds"),
    }


def save_episode(ep: dict) -> Path:
    date = ep["date"]
    lang = ep["lang"]
    path = episode_path(date, lang)
    write_json(path, ep)
    update_episode_index(date, lang, ep["title"], float(ep.get("seconds") or 0))
    return path


def write_episode(
    client: DeepSeekClient,
    cands: list[Candidate],
    date: dt.date,
    lang: str,
    count: int = 4,
    topic: str = "",
    log: Callable[[str], None] | None = None,
) -> tuple[dict, Path]:
    data = generate_script(client, cands, date, lang, count, topic, log)
    ep = assemble_episode(data, cands, date, lang)
    path = save_episode(ep)
    return ep, path
