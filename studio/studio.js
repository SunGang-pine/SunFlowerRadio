"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const LANG_LABEL = { ja: "日本語", en: "English", zh: "中文" };
const LANG_BADGE = { ja: "JA", en: "EN", zh: "ZH" };
const SOURCE_LABEL = {
  "environment": "环境变量",
  ".env.local": ".env.local",
  "DSH credentials": "DSH 配置",
  "DSH .env": "DSH .env",
  "entered in studio": "制作台保存",
  "not configured": "未配置",
};

const state = { status: null, mode: "rss", count: 4, jobTimer: null, jobId: null, busy: false, voiceTouched: false };

function toast(message, isError = false) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.toggle("err", isError);
  el.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.remove("show"), 3600);
}

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: { "Content-Type": "application/json" } };
  if (opts.body !== undefined) init.body = JSON.stringify(opts.body);
  const res = await fetch(path, init);
  let data = {};
  try { data = await res.json(); } catch { /* keep empty */ }
  if (!res.ok) {
    const err = new Error(data.error || `HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return data;
}

const fmt = s => {
  s = Math.max(0, Math.round(Number(s) || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};

function localToday() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function setState(label, cls = "idle") {
  const el = $("#jobState");
  el.textContent = label;
  el.className = `state ${cls}`;
}

/* ---------------------------------------------------------------- status */
async function loadStatus() {
  const s = await api("/api/status");
  state.status = s;
  $("#stationName").textContent = s.station.name || "SunFlowerRadio";
  document.title = `制作台 · ${s.station.name || "SunFlowerRadio"}`;

  $("#statEpisodes").textContent = s.episodes.length;
  $("#statVoiced").textContent = s.episodes.filter(e => e.audio).length;
  $("#statModel").textContent = s.settings.model || "—";
  $("#statTts").textContent = s.sapi ? "Windows SAPI" : "不可用";

  updateKeyPill();
  renderLanguages();
  renderFeeds();
  renderVoices();
  renderEpisodes();

  // an unfinished job survived a page reload: attach to it
  try {
    const j = await api("/api/jobs");
    const active = (j.jobs || []).find(x => x.status === "running" || x.status === "queued");
    if (active && !state.jobId) watchJob(active.id, active);
  } catch { /* ignore */ }
}

function updateKeyPill() {
  const st = state.status?.settings;
  const pill = $("#keyPill"), text = $("#keyText");
  pill.classList.remove("ok", "bad");
  if (st?.configured) {
    pill.classList.add("ok");
    text.textContent = "DeepSeek 已连接";
    pill.title = `${st.masked} · ${SOURCE_LABEL[st.source] || st.source} · ${st.model}`;
  } else {
    pill.classList.add("bad");
    text.textContent = "连接 DeepSeek";
    pill.title = "点击填入 API Key";
  }
  $("#keyNow").textContent = st?.configured
    ? `${st.masked} · ${SOURCE_LABEL[st.source] || st.source}`
    : "未连接";
}

function renderLanguages() {
  const sel = $("#lang");
  const langs = state.status?.station?.languages || ["ja"];
  const previous = sel.value;
  sel.innerHTML = langs.map(l => `<option value="${esc(l)}">${esc(LANG_LABEL[l] || l)}</option>`).join("");
  const prefer = langs.includes(previous) ? previous : (state.status?.station?.language || langs[0]);
  sel.value = prefer;
}

function renderFeeds() {
  const wrap = $("#feeds");
  const checked = new Set($$("#feeds input:checked").map(x => x.value));
  const feeds = state.status?.feeds || [];
  wrap.innerHTML = feeds.map(f => `
    <label class="feed">
      <input type="checkbox" value="${esc(f.id)}" ${checked.size === 0 || checked.has(f.id) ? "checked" : ""}>
      <span class="nm"><b>${esc(f.name)}</b><span>${esc((f.tags || []).join(" / "))}</span></span>
    </label>`).join("");
}

function renderVoices() {
  const st = state.status;
  const lang = $("#lang").value || "ja";
  const sel = $("#voiceName");
  const previous = sel.value;
  const voices = st?.voices?.length ? st.voices : Object.values(st?.default_voices || {});
  const unique = [...new Set(voices.filter(Boolean))];
  sel.innerHTML = unique.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join("");
  const fallback = st?.default_voices?.[lang] || unique[0] || "";
  const match = (name) => {
    if (!name) return "";
    if (unique.includes(name)) return name;
    const low = name.toLowerCase();
    return unique.find(v => v.toLowerCase().includes(low)) ||
      unique.find(v => low.includes(v.toLowerCase())) || "";
  };
  const wanted = state.voiceTouched ? (match(previous) || match(fallback)) : (match(fallback) || match(previous));
  sel.value = wanted || unique[0] || "";
  const rate = $("#rate");
  const want = String(st?.default_rates?.[lang] ?? 0);
  if (!rate.dataset.touched) {
    if (![...rate.options].some(o => o.value === want)) {
      const opt = new Option(want, want);
      rate.add(opt);
    }
    rate.value = want;
  }
}

function renderEpisodes() {
  const wrap = $("#episodes");
  const eps = [...(state.status?.episodes || [])].sort((a, b) =>
    (b.date + b.lang).localeCompare(a.date + a.lang));
  if (!eps.length) {
    wrap.innerHTML = `<p class="fine">还没有节目。生成第一期后，它会出现在这里。</p>`;
    return;
  }
  wrap.innerHTML = eps.map(e => {
    const d = e.date || "";
    const day = d.length >= 10 ? `${d.slice(5, 7)}.${d.slice(8, 10)}` : d;
    const badge = e.script ? (e.audio ? `<span class="badge audio">已配音</span>` : `<span class="badge script">缺语音</span>`) : `<span class="badge">缺台本</span>`;
    return `<div class="ep" data-date="${esc(d)}" data-lang="${esc(e.lang)}">
      <span class="date" title="${esc(d)}">${esc(day)}</span>
      <span class="lng">${esc(LANG_BADGE[e.lang] || e.lang)}</span>
      <span class="ttl" title="${esc(e.title)}">${esc(e.title || "（无标题）")}</span>
      <span class="dur">${e.audio ? fmt(e.seconds) : "—"}</span>
      ${badge}
      <span class="acts">
        <a href="${esc(e.url)}" target="_blank" rel="noopener">打开</a>
        <button type="button" data-voice="1" ${e.script ? "" : "disabled"}>${e.audio ? "重新配音" : "生成语音"}</button>
      </span>
    </div>`;
  }).join("");
}

/* ---------------------------------------------------------------- job */
function jobKind(kind) {
  return kind === "voice" ? "语音合成" : "生成节目";
}

function watchJob(id, preload = null) {
  clearInterval(state.jobTimer);
  state.jobId = id;
  state.busy = true;
  $("#go").disabled = true;
  $("#jobIdle").hidden = true;
  $("#jobResult").hidden = true;
  $("#jobLive").hidden = false;
  $("#jobKind").textContent = jobKind(preload?.kind || "generate");
  $("#jobLog").textContent = "";
  setState("运行中", "running");
  if (preload) paintJob(preload);
  const tick = async () => {
    try {
      const r = await api(`/api/jobs/${encodeURIComponent(id)}`);
      paintJob(r.job);
      if (r.job.status === "done" || r.job.status === "error") {
        clearInterval(state.jobTimer);
        state.jobTimer = null;
        state.busy = false;
        state.jobId = null;
        $("#go").disabled = false;
        await loadStatus().catch(() => {});
      }
    } catch (e) {
      clearInterval(state.jobTimer);
      state.jobTimer = null;
      state.busy = false;
      state.jobId = null;
      $("#go").disabled = false;
      setState("连接中断", "error");
      toast(e.message, true);
    }
  };
  state.jobTimer = setInterval(tick, 750);
  tick();
}

function paintJob(job) {
  $("#jobKind").textContent = jobKind(job.kind);
  $("#jobElapsed").textContent = `${Number(job.elapsed || 0).toFixed(1)}s`;
  const log = $("#jobLog");
  const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 24;
  log.textContent = (job.logs || []).join("\n");
  if (atBottom) log.scrollTop = log.scrollHeight;

  if (job.status === "done") {
    setState("完成", "done");
    $("#jobLive").hidden = true;
    renderResult(job);
    toast("制作完成");
  } else if (job.status === "error") {
    setState("失败", "error");
    $("#jobLive").hidden = true;
    $("#jobResult").hidden = false;
    $("#jobResult").innerHTML = `<div class="rt">ERROR</div><div class="errbox">${esc(job.error || "未知错误")}</div>
      <div class="acts" style="margin-top:12px"><button type="button" id="retryBtn">回到表单</button></div>`;
    $("#retryBtn").onclick = () => $("#genForm").scrollIntoView({ behavior: "smooth", block: "center" });
    toast(job.error || "制作失败", true);
  } else {
    setState(job.status === "queued" ? "排队中" : "运行中", "running");
  }
}

function renderResult(job) {
  const r = job.result || {};
  const date = r.date || "";
  const lang = r.lang || "ja";
  const audio = r.audio && r.mp3 ? `<audio controls preload="metadata" src="/audio/${encodeURIComponent(r.mp3)}"></audio>` : "";
  $("#jobResult").hidden = false;
  $("#jobResult").innerHTML = `
    <div class="rt">ON AIR READY</div>
    <h3>${esc(r.title || "这一期已完成")}</h3>
    <div class="meta">
      <span>${esc(date)}</span><span>${esc(LANG_BADGE[lang] || lang)}</span>
      <span>${r.seconds ? fmt(r.seconds) : "未配音"}</span>
      ${r.voice ? `<span>${esc(r.voice)}</span>` : ""}
    </div>
    ${audio}
    <div class="acts">
      <a href="${esc(r.url || "/")}" target="_blank" rel="noopener">打开节目 ↗</a>
      <button type="button" id="againBtn">再做一期</button>
    </div>`;
  $("#againBtn").onclick = () => $("#genForm").scrollIntoView({ behavior: "smooth", block: "center" });
}

/* ---------------------------------------------------------------- form */
function setMode(mode) {
  state.mode = mode;
  $$(".tabs button").forEach(b => b.classList.toggle("on", b.dataset.mode === mode));
  $("#rssPane").hidden = mode !== "rss";
  $("#manualPane").hidden = mode !== "manual";
}

function setCount(n) {
  state.count = n;
  $$("#countSeg button").forEach(b => b.classList.toggle("on", Number(b.dataset.v) === n));
}

async function startVoice(date, lang, button) {
  if (state.busy) return toast("上一项任务还在进行", true);
  const st = state.status;
  const formLang = $("#lang").value;
  const voiceName = (formLang === lang ? $("#voiceName").value : st?.default_voices?.[lang]) || "";
  const rate = formLang === lang ? $("#rate").value : (st?.default_rates?.[lang] ?? 0);
  button.disabled = true;
  try {
    const r = await api("/api/voice", { method: "POST", body: { date, lang, voice_name: voiceName, rate, force: true } });
    watchJob(r.job.id, r.job);
  } catch (e) {
    button.disabled = false;
    toast(e.message, true);
  }
}

$("#genForm").addEventListener("submit", async e => {
  e.preventDefault();
  if (state.busy) return toast("上一项任务还在进行", true);
  if (!state.status?.settings?.configured) {
    $("#keyDialog").showModal();
    return toast("请先连接 DeepSeek API key", true);
  }
  const body = {
    date: $("#date").value,
    lang: $("#lang").value,
    count: state.count,
    topic: $("#topic").value.trim(),
    mode: state.mode,
    voice: $("#voice").checked,
    voice_name: $("#voiceName").value,
    rate: $("#rate").value,
  };
  if (state.mode === "manual") {
    body.source_urls = $("#sourceUrls").value.split(/\r?\n/).map(x => x.trim()).filter(Boolean);
    body.source_text = $("#sourceText").value;
    if (!body.source_urls.length) return toast("请至少填一个文章链接", true);
  } else {
    body.feeds = $$("#feeds input:checked").map(x => x.value);
    if (!body.feeds.length) return toast("请至少选一个 RSS 源", true);
  }
  $("#go").disabled = true;
  try {
    const r = await api("/api/generate", { method: "POST", body });
    watchJob(r.job.id, r.job);
  } catch (err) {
    $("#go").disabled = false;
    toast(err.message, true);
  }
});

$("#countSeg").addEventListener("click", e => {
  const b = e.target.closest("button[data-v]");
  if (b) setCount(Number(b.dataset.v));
});
$$(".tabs button").forEach(b => b.addEventListener("click", () => setMode(b.dataset.mode)));
$("#lang").addEventListener("change", () => { state.voiceTouched = false; renderVoices(); });
$("#voiceName").addEventListener("change", () => { state.voiceTouched = true; });
$("#rate").addEventListener("change", () => { $("#rate").dataset.touched = "1"; });

$("#episodes").addEventListener("click", e => {
  const btn = e.target.closest("button[data-voice]");
  if (!btn) return;
  const row = btn.closest(".ep");
  startVoice(row.dataset.date, row.dataset.lang, btn);
});

/* ---------------------------------------------------------------- key dialog */
$("#keyPill").addEventListener("click", () => {
  $("#keyMsg").textContent = "";
  $("#keyMsg").className = "keymsg";
  $("#keyInput").value = "";
  updateKeyPill();
  $("#keyDialog").showModal();
  setTimeout(() => $("#keyInput").focus(), 50);
});

$("#keyForm").addEventListener("submit", async e => {
  e.preventDefault();
  const key = $("#keyInput").value.trim();
  if (!key) return;
  const btn = $("#saveKey");
  btn.disabled = true;
  $("#keyMsg").className = "keymsg";
  $("#keyMsg").textContent = "正在测试 DeepSeek 连接…";
  try {
    const r = await api("/api/key", { method: "POST", body: { key } });
    $("#keyMsg").textContent = `连接成功 · ${r.verify?.model || ""} · ${r.verify?.latency_ms || 0}ms`;
    $("#keyInput").value = "";
    await loadStatus();
    setTimeout(() => { $("#keyDialog").close(); toast("DeepSeek 已连接"); }, 450);
  } catch (err) {
    $("#keyMsg").className = "keymsg bad";
    $("#keyMsg").textContent = err.message;
  } finally {
    btn.disabled = false;
  }
});

$("#verifyKey").addEventListener("click", async () => {
  const btn = $("#verifyKey");
  btn.disabled = true;
  $("#keyMsg").className = "keymsg";
  $("#keyMsg").textContent = "正在测试…";
  try {
    const r = await api("/api/verify");
    $("#keyMsg").textContent = `连接正常 · ${r.result.model} · ${r.result.latency_ms}ms`;
    await loadStatus();
  } catch (err) {
    $("#keyMsg").className = "keymsg bad";
    $("#keyMsg").textContent = err.message;
  } finally {
    btn.disabled = false;
  }
});

$("#deleteKey").addEventListener("click", async () => {
  try {
    await api("/api/key", { method: "DELETE" });
    await loadStatus();
    $("#keyMsg").textContent = "已删除本机保存的 key";
  } catch (err) {
    $("#keyMsg").className = "keymsg bad";
    $("#keyMsg").textContent = err.message;
  }
});

$("#keyClose").addEventListener("click", () => $("#keyDialog").close());

/* ---------------------------------------------------------------- boot */
(async function init() {
  $("#date").value = localToday();
  try {
    await loadStatus();
    const q = new URLSearchParams(location.search).get("lang");
    const sel = $("#lang");
    if (q && [...sel.options].some(o => o.value === q)) {
      sel.value = q;
      state.voiceTouched = false;
      renderVoices();
    }
    setCount(4);
    setMode("rss");
  } catch (err) {
    toast(`无法连接本地服务：${err.message}`, true);
    setState("离线", "error");
  }
})();
