// SPDX-FileCopyrightText: 2026 QuincyLeo
// SPDX-License-Identifier: GPL-3.0-or-later
"use strict";

const TOKEN = (window.FRAMER_TOKEN && window.FRAMER_TOKEN !== "__TOKEN__")
  ? window.FRAMER_TOKEN
  : new URLSearchParams(location.search).get("t") || "";

const $ = (id) => document.getElementById(id);
const STORE_KEY = "photo-framer.params.v4";

const state = {
  session: null,
  params: null,
  defaults: null,
  format: "png",
  previewUrl: null,
  inflight: false,
  queued: false,
  job: null,
  jobTimer: null,
};

async function api(path, { method = "POST", body, raw, filename } = {}) {
  const headers = { "X-Framer-Token": TOKEN };
  let payload;
  if (raw) {
    headers["Content-Type"] = "application/octet-stream";
    headers["X-Filename"] = encodeURIComponent(filename || "dropped.jpg").replace(/%/g, "_");
    payload = raw;
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const res = await fetch(path, { method, headers, body: payload });
  const type = res.headers.get("Content-Type") || "";
  if (type.startsWith("image/")) {
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return { blob: await res.blob(), headers: res.headers };
  }
  const data = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
  if (!res.ok || data.error) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function say(message, kind = "") {
  const node = $("status");
  node.className = "status" + (kind ? " " + kind : "");
  node.textContent = "";
  node.append(message);
}

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

// ---------------------------------------------------------------- controls

const SETTERS = {};
let previewTimer = null;

function schedulePreview() {
  persist();
  if (!state.session) return;
  clearTimeout(previewTimer);
  previewTimer = setTimeout(runPreview, 55);
}

function slider(spec) {
  const row = el("div", "row");
  row.append(el("label", null, spec.label));
  const input = el("input");
  input.type = "range";
  Object.assign(input, { min: spec.min, max: spec.max, step: spec.step });
  const readout = el("div", "readout");

  const paint = (value) => {
    readout.textContent = spec.unit === "x"
      ? `${Number(value).toFixed(1)}×`
      : String(Math.round(value));
  };
  const sync = (value, commit) => {
    const clamped = Math.min(spec.max, Math.max(spec.min, Number(value)));
    input.value = clamped;
    paint(clamped);
    state.params[spec.k] = clamped;
    if (commit) {
      if (spec.onInput) spec.onInput(clamped);
      schedulePreview();
    }
  };
  input.addEventListener("input", () => sync(input.value, true));
  row.append(input, readout);
  return { row, set: (v) => sync(v, false), paint };
}

function switcher(spec) {
  const row = el("div", "row wide");
  row.append(el("label", null, spec.label));
  const group = el("div", "seg");
  const buttons = spec.choices.map(([value, text]) => {
    const button = el("button", null, text);
    button.type = "button";
    button.addEventListener("click", () => {
      state.params[spec.k] = value;
      buttons.forEach((b, i) => b.setAttribute("aria-pressed",
        String(spec.choices[i][0] === value)));
      schedulePreview();
    });
    group.append(button);
    return button;
  });
  row.append(group);
  return {
    row,
    set: (v) => buttons.forEach((b, i) =>
      b.setAttribute("aria-pressed", String(spec.choices[i][0] === v))),
  };
}

/**
 * A caption field. It shows the real text pulled from EXIF; the moment you
 * type something different it becomes an override and stops following the
 * photo. The revert button hands it back to EXIF.
 */
function captionField(spec) {
  const row = el("div", "row stack");
  const head = el("div", "field-head");
  head.append(el("label", null, spec.label));
  const revert = el("button", "link small", "跟随 EXIF");
  revert.type = "button";
  revert.title = "放弃手改，重新按照片的 EXIF 填充";
  head.append(revert);

  const input = el("input", "text");
  input.type = "text";
  input.placeholder = spec.placeholder;

  const autoText = () => (state.session && state.session[spec.autoKey]) || "";
  const refresh = () => {
    const following = state.params[spec.flagKey];
    input.value = following ? autoText() : (state.params[spec.k] || "");
    revert.classList.toggle("hidden", !!following);
  };

  input.addEventListener("input", () => {
    const typed = input.value;
    const following = typed === autoText();
    state.params[spec.flagKey] = following;
    state.params[spec.k] = typed;
    revert.classList.toggle("hidden", following);
    schedulePreview();
  });
  revert.addEventListener("click", () => {
    state.params[spec.flagKey] = true;
    state.params[spec.k] = "";
    refresh();
    schedulePreview();
  });

  row.append(head, input);
  return { row, set: refresh, refresh };
}

/** Lightness slider with an "auto" lamp; dragging it takes manual control. */
function lightnessField(spec) {
  const row = el("div", "row");
  row.append(el("label", null, spec.label));
  const input = el("input");
  input.type = "range";
  Object.assign(input, { min: 0, max: 100, step: 1 });
  const readout = el("button", "readout toggle");
  readout.type = "button";
  readout.title = "点一下切回自动：按文字下方的背景明暗选纯白或纯黑";

  const paint = () => {
    const auto = state.params.text_auto;
    readout.textContent = auto ? "自动" : String(Math.round(state.params.text_light));
    readout.classList.toggle("on", auto);
    input.value = state.params.text_light;
  };
  input.addEventListener("input", () => {
    state.params.text_light = Number(input.value);
    state.params.text_auto = false;
    paint();
    schedulePreview();
  });
  readout.addEventListener("click", () => {
    state.params.text_auto = !state.params.text_auto;
    paint();
    schedulePreview();
  });
  row.append(input, readout);
  return { row, set: paint, paint };
}

const FORMAT_HINT = {
  png: "照片区域与原图逐位一致，ICC 与 EXIF 原样写入。体积最大。",
  jpeg: "质量 100、4:4:4 无色度抽样，ICC 与 EXIF 保留。肉眼无差，但非逐位无损。",
};
function buildPanel() {
  const look = $("g-look");
  for (const spec of [
    { k: "border", label: "边框宽度", min: 0.1, max: 5, step: 0.1, unit: "x" },
    { k: "radius", label: "圆角", min: 0, max: 100, step: 1 },
    { k: "shadow", label: "阴影", min: 0, max: 100, step: 1 },
    { k: "blur", label: "模糊度", min: 3, max: 100, step: 1 },
  ]) {
    const control = slider(spec);
    SETTERS[spec.k] = control.set;
    look.append(control.row);
  }
  const hint = el("p", "hint", "三边等宽；下边框＝文字高＋一个边框宽，文字居中。");
  hint.title = "下边框是文字高度加上一个边框宽度，这一个宽度平分到文字上下，"
    + "所以文字上下的留白加起来正好等于其他三边。";
  look.append(hint);

  const text = $("g-text");
  const line = switcher({
    k: "one_line", label: "排版",
    choices: [[false, "两行"], [true, "一行"]],
  });
  SETTERS.one_line = line.set;
  text.append(line.row);

  const title = captionField({
    k: "title", flagKey: "title_auto", autoKey: "auto_title",
    label: "厂商 / 型号", placeholder: "打开照片后自动填充",
  });
  const settings = captionField({
    k: "params_text", flagKey: "params_auto", autoKey: "auto_params",
    label: "拍摄参数", placeholder: "焦距 | 光圈 | 快门 | ISO",
  });
  SETTERS.title = title.set;
  SETTERS.params_text = settings.set;
  state.refreshCaptions = () => { title.refresh(); settings.refresh(); };
  const marks = el("p", "hint", "Nikon / DJI / FUJIFILM / Apple 出图显示 logo。");
  marks.title = "按 EXIF 的厂商字段判断，出图时把标题里的厂商名换成 logo，比型号文字略高、"
    + "同一条基线；手改标题后改按标题开头的文字判断，改掉厂商名就回到纯文字。";
  text.append(title.row, marks, settings.row);

  const size = slider({ k: "text_scale", label: "字号", min: 0.5, max: 2.5, step: 0.05, unit: "x" });
  SETTERS.text_scale = size.set;
  text.append(size.row);

  const light = lightnessField({ label: "文字明暗" });
  SETTERS.text_light = light.set;
  state.paintLight = light.paint;
  text.append(light.row);

  buildExportGroup();
}

function buildExportGroup() {
  const host = $("g-export");
  const row = el("div", "row wide");
  row.append(el("label", null, "格式"));
  const group = el("div", "seg");
  const hint = el("p", "hint");
  const buttons = [["png", "PNG 无损"], ["jpeg", "JPEG q100"]].map(([value, label]) => {
    const button = el("button", null, label);
    button.type = "button";
    button.setAttribute("aria-pressed", String(state.format === value));
    button.addEventListener("click", () => {
      state.format = value;
      buttons.forEach((b) => b.setAttribute("aria-pressed", String(b === button)));
      hint.textContent = FORMAT_HINT[value];
      persist();
    });
    group.append(button);
    return button;
  });
  hint.textContent = FORMAT_HINT[state.format];
  row.append(group);
  host.append(row, hint);
}

function applyParams() {
  for (const [key, set] of Object.entries(SETTERS)) set(state.params[key]);
  persist();
}

function persist() {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify({
      params: state.params, format: state.format,
    }));
  } catch (_) { /* private browsing */ }
}

// ---------------------------------------------------------------- preview

async function runPreview() {
  if (!state.session) return;
  if (state.inflight) { state.queued = true; return; }
  state.inflight = true;
  $("spinner").classList.remove("hidden");
  try {
    const { blob, headers } = await api("/api/preview", {
      body: { id: state.session.id, params: state.params },
    });
    const url = URL.createObjectURL(blob);
    $("preview").src = url;
    if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
    state.previewUrl = url;

    const canvas = headers.get("X-Canvas") || "";
    const s = state.session;
    $("canvas-size").textContent = canvas
      ? `导出 ${canvas.replace("x", " × ")}　·　照片 ${s.width} × ${s.height} 原样嵌入`
      : "";
    if (state.params.text_auto) {
      state.params.text_light = Number(headers.get("X-Text-Light")) || 100;
      state.paintLight();
    }
  } catch (error) {
    say(String(error.message || error), "bad");
  } finally {
    state.inflight = false;
    $("spinner").classList.add("hidden");
    if (state.queued) { state.queued = false; runPreview(); }
  }
}

// ---------------------------------------------------------------- loading

function showMeta() {
  const s = state.session;
  const node = $("meta");
  node.textContent = "";
  node.classList.remove("hidden");
  const line = (label, value, cls) => {
    const row = el("div");
    row.append(el("b", null, label + " "));
    row.append(el("span", cls || null, value));
    node.append(row);
  };
  line(s.name, `${s.width} × ${s.height}　${s.megapixels} MP　${s.format}`);
  line("色彩", s.icc ? `嵌入 ${s.icc}，导出时原样写回` : "无 ICC 描述，按 sRGB 处理",
       s.icc ? "ok" : "warn");
  const hasCaption = (s.auto_title || "") || (s.auto_params || "");
  line("EXIF", hasCaption ? "相机信息已读取，文字自动填充"
                          : "无相机信息，文字请手填（拍摄参数已被上一个工具剥掉）",
       hasCaption ? "ok" : "warn");
  if (s.converted) line("注意", `原图为 ${s.source_mode} 模式，已转为 RGB`, "warn");
  if (s.bit_depth > 8) {
    line("位深", `原图 ${s.bit_depth} bit/通道，Pillow 读取时截断为 8 bit`, "warn");
  }
  if (s.orientation && s.orientation !== 1) {
    line("方向", `EXIF 方向 ${s.orientation}，已做无损旋转`, "ok");
  }
}

function adoptSession(summary) {
  state.session = summary;
  $("dropzone").classList.add("hidden");
  $("viewport").classList.remove("hidden");
  $("export").disabled = false;
  $("batch").disabled = false;
  state.refreshCaptions();
  showMeta();
  say("");
  runPreview();
}

async function openPath(path) {
  if (!path) return;
  say("正在读取…");
  try {
    adoptSession(await api("/api/load", { body: { path } }));
  } catch (error) {
    say(`打开失败：${error.message}`, "bad");
  }
}

async function uploadFile(file) {
  say(`正在读取 ${file.name}…`);
  try {
    adoptSession(await api("/api/upload", {
      raw: await file.arrayBuffer(), filename: file.name,
    }));
  } catch (error) {
    say(`读取失败：${error.message}`, "bad");
  }
}

// ---------------------------------------------------------------- export

function human(bytes) {
  return bytes >= 1e6 ? `${(bytes / 1e6).toFixed(1)} MB` : `${Math.round(bytes / 1e3)} KB`;
}

async function doExport() {
  const button = $("export");
  button.disabled = true;
  button.textContent = "正在渲染原分辨率…";
  try {
    const { blob, headers } = await api("/api/export", {
      body: { id: state.session.id, params: state.params, format: state.format },
    });
    // hand the bytes to the browser's own downloader; it lands wherever
    // downloads go, so the tool never has to ask for a folder
    const name = decodeURIComponent(headers.get("X-Filename") || "framed.png");
    const url = URL.createObjectURL(blob);
    const link = el("a");
    link.href = url;
    link.download = name;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);

    const dims = headers.get("X-Dimensions") || "";
    const seconds = headers.get("X-Seconds") || "";
    say(`已下载 ${name}　${dims.replace("x", "×")}　${human(blob.size)}　${seconds}s`, "good");
  } catch (error) {
    say(`导出失败：${error.message}`, "bad");
  } finally {
    button.disabled = false;
    button.textContent = "导出当前照片";
  }
}

async function doBatch() {
  const { path: folder } = await api("/api/pick?mode=folder", { method: "GET" });
  if (!folder) return;
  const { count } = await api(`/api/scan?folder=${encodeURIComponent(folder)}`, { method: "GET" });
  if (!count) { say("该文件夹里没有可读的图片。", "bad"); return; }

  const followed = [
    state.params.title_auto ? "厂商型号" : null,
    state.params.params_auto ? "拍摄参数" : null,
  ].filter(Boolean);
  const textNote = followed.length
    ? `${followed.join("、")}按每张照片自己的 EXIF 填充`
    : "文字全部用你手改的内容";
  if (!confirm(`将用当前参数处理 ${count} 张照片\n\n来源：${folder}\n输出：${folder.replace(/\/$/, "")}/framed/\n格式：${state.format.toUpperCase()}\n文字：${textNote}\n\n继续？`)) return;

  state.job = await api("/api/batch", {
    body: { folder, params: state.params, format: state.format },
  });
  $("progress").classList.remove("hidden");
  $("batch-cancel").classList.remove("hidden");
  $("batch").disabled = true;
  pollJob();
}

async function pollJob() {
  clearTimeout(state.jobTimer);
  let job;
  try {
    job = await api(`/api/batch?job=${state.job.id}`, { method: "GET" });
  } catch (error) {
    say(`批量任务中断：${error.message}`, "bad");
    $("batch").disabled = false;
    return;
  }
  $("bar-fill").style.width = `${job.total ? (job.done / job.total) * 100 : 100}%`;
  $("progress-label").textContent = job.finished
    ? `完成 ${job.written.length}/${job.total}`
    : `${job.done}/${job.total}　${job.current || ""}`;
  if (!job.finished) {
    state.jobTimer = setTimeout(pollJob, 400);
    return;
  }

  $("batch").disabled = false;
  $("batch-cancel").classList.add("hidden");
  const node = $("status");
  node.className = "status " + (job.errors.length ? "bad" : "good");
  node.textContent = `批量完成：成功 ${job.written.length} 张，失败 ${job.errors.length} 张　`;
  if (job.written.length) {
    const link = el("button", "link", "打开输出目录");
    link.addEventListener("click", () => api("/api/reveal", { body: { path: job.written[0] } }));
    node.append(link);
  }
  for (const message of job.errors.slice(0, 5)) node.append(el("div", null, message));
}

// ---------------------------------------------------------------- wiring

async function pickPhoto() {
  try {
    const { path } = await api("/api/pick?mode=file", { method: "GET" });
    await openPath(path);
  } catch (error) {
    say(`无法打开选择器：${error.message}`, "bad");
  }
}

function wire() {
  $("pick-file").addEventListener("click", pickPhoto);
  $("pick-file-2").addEventListener("click", pickPhoto);

  $("reset").addEventListener("click", () => {
    state.params = { ...state.defaults.params };
    applyParams();
    state.refreshCaptions();
    runPreview();
    say("已恢复默认参数。");
  });

  $("export").addEventListener("click", doExport);
  $("batch").addEventListener("click", doBatch);
  $("batch-cancel").addEventListener("click", async () => {
    if (state.job) await api("/api/cancel", { body: { job: state.job.id } });
  });

  const stage = $("stage");
  stage.addEventListener("dragover", (event) => {
    event.preventDefault();
    stage.classList.add("dragging");
  });
  stage.addEventListener("dragleave", () => stage.classList.remove("dragging"));
  stage.addEventListener("drop", (event) => {
    event.preventDefault();
    stage.classList.remove("dragging");
    const file = event.dataTransfer.files && event.dataTransfer.files[0];
    if (file) uploadFile(file);
  });
  window.addEventListener("dragover", (event) => event.preventDefault());
  window.addEventListener("drop", (event) => event.preventDefault());

  document.addEventListener("keydown", (event) => {
    if (!(event.metaKey || event.ctrlKey)) return;
    if (event.key === "s" && state.session) {
      event.preventDefault();
      doExport();
    } else if (event.key === "o") {
      event.preventDefault();
      pickPhoto();
    }
  });
}

async function boot() {
  try {
    state.defaults = await api("/api/defaults", { body: {} });
  } catch (error) {
    document.body.textContent = "无法连接后端：" + error.message;
    return;
  }
  state.params = { ...state.defaults.params };
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem(STORE_KEY) || "null"); } catch (_) {}
  if (saved && saved.params) {
    for (const key of Object.keys(state.params)) {
      if (key in saved.params) state.params[key] = saved.params[key];
    }
    if (saved.format) state.format = saved.format;
  }
  buildPanel();
  applyParams();
  wire();
  const initial = new URLSearchParams(location.search).get("open");
  if (initial) openPath(initial);
}

boot();
