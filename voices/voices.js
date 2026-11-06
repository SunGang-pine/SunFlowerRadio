"use strict";

const SAMPLE = {
  zh: "你好，欢迎收听太阳花电台。今天为你带来人工智能、3D 打印和电子制作的新鲜事。",
  ja: "おはようございます、サンフラワーレディオです。今日はAIと3Dのニュースをお届けします。",
  en: "Good morning, this is SunFlowerRadio. Here is today's AI, 3D printing and electronics news.",
};

const VOICES = [
  { id: "zh-CN-XiaoxiaoNeural", lang: "zh", langLabel: "中文", name: "晓晓", style: "女声 · 温暖" },
  { id: "zh-CN-XiaoyiNeural", lang: "zh", langLabel: "中文", name: "晓伊", style: "女声 · 活泼" },
  { id: "zh-CN-YunxiNeural", lang: "zh", langLabel: "中文", name: "云希", style: "男声 · 阳光" },
  { id: "zh-CN-YunjianNeural", lang: "zh", langLabel: "中文", name: "云健", style: "男声 · 激情" },
  { id: "zh-CN-YunyangNeural", lang: "zh", langLabel: "中文", name: "云扬", style: "男声 · 专业" },
  { id: "ja-JP-NanamiNeural", lang: "ja", langLabel: "日本語", name: "Nanami", style: "女声 · 自然" },
  { id: "ja-JP-KeitaNeural", lang: "ja", langLabel: "日本語", name: "Keita", style: "男声 · 友好" },
  { id: "en-US-AvaNeural", lang: "en", langLabel: "English", name: "Ava", style: "女声 · 自然" },
  { id: "en-US-EmmaNeural", lang: "en", langLabel: "English", name: "Emma", style: "女声 · 清亮" },
  { id: "en-US-AndrewNeural", lang: "en", langLabel: "English", name: "Andrew", style: "男声 · 温暖" },
  { id: "en-US-BrianNeural", lang: "en", langLabel: "English", name: "Brian", style: "男声 · 轻松" },
];

const grid = document.getElementById("grid");
const filters = document.getElementById("filters");

function render(lang = "all") {
  const list = VOICES.filter(v => lang === "all" || v.lang === lang);
  grid.innerHTML = list.map(v => `
    <article class="voice" data-lang="${v.lang}">
      <div class="vtop"><span class="lang">${v.langLabel}</span><span class="style">${v.style}</span></div>
      <h2>${v.name}</h2>
      <code>${v.id}</code>
      <audio controls preload="metadata" src="samples/${encodeURIComponent(v.id)}.mp3"></audio>
      <p class="sample">“${SAMPLE[v.lang]}”</p>
    </article>`).join("");
  if (!list.length) grid.innerHTML = `<p class="note">这个语言还没有音色。</p>`;
}

filters.addEventListener("click", e => {
  const b = e.target.closest("button[data-lang]");
  if (!b) return;
  filters.querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b));
  render(b.dataset.lang);
});

// one voice at a time
document.addEventListener("play", e => {
  const player = e.target;
  if (!(player instanceof HTMLAudioElement)) return;
  document.querySelectorAll("audio").forEach(a => { if (a !== player) a.pause(); });
}, true);

render("all");
