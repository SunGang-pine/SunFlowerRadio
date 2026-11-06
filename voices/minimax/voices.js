"use strict";

const SAMPLE = {
  zh: "你好，欢迎收听太阳花电台。今天为你带来人工智能、3D 打印和电子制作的新鲜事。",
  ja: "おはようございます、サンフラワーレディオです。今日はAIと3Dのニュースをお届けします。",
  en: "Good morning, this is SunFlowerRadio. Here is today's AI, 3D printing and electronics news.",
};

const VOICES = [
  { slug: "zh-news-anchor", lang: "zh", langLabel: "中文", name: "新闻女声", style: "女声 · 推荐", id: "Chinese (Mandarin)_News_Anchor" },
  { slug: "zh-male-announcer", lang: "zh", langLabel: "中文", name: "新闻男声", style: "男声 · 播报", id: "Chinese (Mandarin)_Male_Announcer" },
  { slug: "zh-radio-host", lang: "zh", langLabel: "中文", name: "电台主持", style: "女声 · 电台", id: "Chinese (Mandarin)_Radio_Host" },
  { slug: "zh-warm-girl", lang: "zh", langLabel: "中文", name: "温暖女声", style: "女声 · 温暖", id: "Chinese (Mandarin)_Warm_Girl" },
  { slug: "ja-kind-lady", lang: "ja", langLabel: "日本語", name: "温柔女声", style: "女声 · 温柔", id: "Japanese_KindLady" },
  { slug: "ja-calm-lady", lang: "ja", langLabel: "日本語", name: "沉稳女声", style: "女声 · 沉稳", id: "Japanese_CalmLady" },
  { slug: "ja-graceful-maiden", lang: "ja", langLabel: "日本語", name: "优雅女声", style: "女声 · 优雅", id: "Japanese_GracefulMaiden" },
  { slug: "ja-intellectual-senior", lang: "ja", langLabel: "日本語", name: "知性男声", style: "男声 · 知性", id: "Japanese_IntellectualSenior" },
  { slug: "en-soft-spoken-girl", lang: "en", langLabel: "English", name: "Soft-Spoken Girl", style: "女声 · 温柔", id: "English_Soft-spokenGirl" },
  { slug: "en-expressive-narrator", lang: "en", langLabel: "English", name: "Expressive Narrator", style: "叙述 · 表现力", id: "English_expressive_narrator" },
  { slug: "en-graceful-lady", lang: "en", langLabel: "English", name: "Graceful Lady", style: "女声 · 优雅", id: "English_Graceful_Lady" },
  { slug: "en-trustworthy-man", lang: "en", langLabel: "English", name: "Trustworthy Man", style: "男声 · 可信", id: "English_Trustworth_Man" },
  { slug: "en-insightful-speaker", lang: "en", langLabel: "English", name: "Insightful Speaker", style: "叙述 · 洞察", id: "English_Insightful_Speaker" },
];

const grid = document.getElementById("grid");
const filters = document.getElementById("filters");

function render(lang = "all") {
  const list = VOICES.filter(v => lang === "all" || v.lang === lang);
  grid.innerHTML = list.map(v => `
    <article class="voice" data-lang="${v.lang}">
      <div class="vtop"><span class="lang">${v.langLabel}</span><span class="style">${v.style}</span><span class="mmtag">MINIMAX</span></div>
      <h2>${v.name}</h2>
      <code>${v.id}</code>
      <audio controls preload="metadata" src="samples/${v.slug}.mp3"></audio>
      <p class="sample">“${SAMPLE[v.lang]}”</p>
    </article>`).join("");
}

filters.addEventListener("click", e => {
  const b = e.target.closest("button[data-lang]");
  if (!b) return;
  filters.querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b));
  render(b.dataset.lang);
});

document.addEventListener("play", e => {
  const player = e.target;
  if (!(player instanceof HTMLAudioElement)) return;
  document.querySelectorAll("audio").forEach(a => { if (a !== player) a.pause(); });
}, true);

render("all");
