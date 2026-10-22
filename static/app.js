// SPDX-FileCopyrightText: 2026 QuincyLeo
// SPDX-License-Identifier: GPL-3.0-or-later
"use strict";

class Dom {
  static get(id) {
    return document.getElementById(id);
  }

  static element(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }
}

/** Authenticated requests to the local server. */
class ApiClient {
  constructor() {
    this.token = (window.FRAMER_TOKEN && window.FRAMER_TOKEN !== "__TOKEN__")
      ? window.FRAMER_TOKEN
      : new URLSearchParams(location.search).get("t") || "";
  }

  async request(path, { method = "POST", body, raw, filename } = {}) {
    const headers = { "X-Framer-Token": this.token };
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
}

/** Persist only the settings understood by the current interface. */
class SettingsStorage {
  static KEY = "photo-framer.params.v5";

  restore(state) {
    let saved = null;
    try { saved = JSON.parse(localStorage.getItem(SettingsStorage.KEY) || "null"); } catch (_) {}
    if (saved && saved.params) {
      for (const key of Object.keys(state.params)) {
        if (key in saved.params) state.params[key] = saved.params[key];
      }
      if (saved.format) state.format = saved.format;
    }
  }

  save(state) {
    try {
      localStorage.setItem(SettingsStorage.KEY, JSON.stringify({
        params: state.params, format: state.format,
      }));
    } catch (_) { /* private browsing */ }
  }
}

/** Status messages and photo metadata. */
class AppView {
  say(message, kind = "") {
    const node = Dom.get("status");
    node.className = "status" + (kind ? " " + kind : "");
    node.textContent = "";
    node.append(message);
  }

  showMeta(session) {
    const s = session;
    const node = Dom.get("meta");
    node.textContent = "";
    node.classList.remove("hidden");
    const line = (label, value, cls) => {
      const row = Dom.element("div");
      row.append(Dom.element("b", null, label + " "));
      row.append(Dom.element("span", cls || null, value));
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
}

/** Build controls that edit the current photo settings. */
class ControlFactory {
  constructor(app) {
    this.app = app;
  }

  slider(spec) {
    const row = Dom.element("div", "row");
    row.append(Dom.element("label", null, spec.label));
    const input = Dom.element("input");
    input.type = "range";
    Object.assign(input, { min: spec.min, max: spec.max, step: spec.step });
    const readout = Dom.element("div", "readout");

    const paint = (value) => {
      readout.textContent = spec.unit === "x"
        ? `${Number(value).toFixed(1)}×`
        : String(Math.round(value));
    };
    const sync = (value, commit) => {
      const clamped = Math.min(spec.max, Math.max(spec.min, Number(value)));
      input.value = clamped;
      paint(clamped);
      this.app.state.params[spec.k] = clamped;
      if (commit) {
        if (spec.onInput) spec.onInput(clamped);
        this.app.preview.schedule();
      }
    };
    input.addEventListener("input", () => sync(input.value, true));
    row.append(input, readout);
    return { row, set: (v) => sync(v, false), paint };
  }

  switcher(spec) {
    const row = Dom.element("div", "row wide");
    row.append(Dom.element("label", null, spec.label));
    const group = Dom.element("div", "seg");
    const buttons = spec.choices.map(([value, text]) => {
      const button = Dom.element("button", null, text);
      button.type = "button";
      button.addEventListener("click", () => {
        this.app.state.params[spec.k] = value;
        buttons.forEach((b, i) => b.setAttribute("aria-pressed",
          String(spec.choices[i][0] === value)));
        if (spec.onInput) spec.onInput(value);
        this.app.preview.schedule();
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

  captionField(spec) {
    const row = Dom.element("div", "row stack");
    const head = Dom.element("div", "field-head");
    head.append(Dom.element("label", null, spec.label));
    const revert = Dom.element("button", "link small", "跟随 EXIF");
    revert.type = "button";
    revert.title = "放弃手改，重新按照片的 EXIF 填充";
    head.append(revert);

    const input = Dom.element("input", "text");
    input.type = "text";
    input.placeholder = spec.placeholder;

    const autoText = () => (this.app.state.session && this.app.state.session[spec.autoKey]) || "";
    const refresh = () => {
      const following = this.app.state.params[spec.flagKey];
      input.value = following ? autoText() : (this.app.state.params[spec.k] || "");
      revert.classList.toggle("hidden", !!following);
    };

    input.addEventListener("input", () => {
      const typed = input.value;
      const following = typed === autoText();
      this.app.state.params[spec.flagKey] = following;
      this.app.state.params[spec.k] = typed;
      revert.classList.toggle("hidden", following);
      this.app.preview.schedule();
    });
    revert.addEventListener("click", () => {
      this.app.state.params[spec.flagKey] = true;
      this.app.state.params[spec.k] = "";
      refresh();
      this.app.preview.schedule();
    });

    row.append(head, input);
    return { row, set: refresh, refresh };
  }

  lightnessField(spec) {
    const row = Dom.element("div", "row");
    row.append(Dom.element("label", null, spec.label));
    const input = Dom.element("input");
    input.type = "range";
    Object.assign(input, { min: 0, max: 100, step: 1 });
    const readout = Dom.element("button", "readout toggle");
    readout.type = "button";
    readout.title = "点一下切回自动：按文字下方的背景明暗选纯白或纯黑";

    const paint = () => {
      const auto = this.app.state.params.text_auto;
      readout.textContent = auto ? "自动" : String(Math.round(this.app.state.params.text_light));
      readout.classList.toggle("on", auto);
      input.value = this.app.state.params.text_light;
    };
    input.addEventListener("input", () => {
      this.app.state.params.text_light = Number(input.value);
      this.app.state.params.text_auto = false;
      paint();
      this.app.preview.schedule();
    });
    readout.addEventListener("click", () => {
      this.app.state.params.text_auto = !this.app.state.params.text_auto;
      paint();
      this.app.preview.schedule();
    });
    row.append(input, readout);
    return { row, set: paint, paint };
  }

  subgroup(host, style) {
    const node = Dom.element("div", "sub");
    node.dataset.style = style;
    host.append(node);
    return node;
  }

  addHint(host, text, title) {
    const hint = Dom.element("p", "hint", text);
    if (title) hint.title = title;
    host.append(hint);
    return hint;
  }
}

/** Frame controls, caption fields and export format selection. */
class SettingsPanel {
  static FORMAT_HINT = {
    png: "照片区域与原图逐位一致，ICC 与 EXIF 原样写入。体积最大。",
    jpeg: "质量 100、4:4:4 无色度抽样，ICC 与 EXIF 保留。肉眼无差，但非逐位无损。",
  };

  static BAR_FIELDS = [
    { k: "bar_params", flagKey: "bar_params_auto", autoKey: "auto_bar_params",
      label: "拍摄参数（左上）", placeholder: "50mm, F4.5, 1/125s, ISO100" },
    { k: "bar_lens", flagKey: "bar_lens_auto", autoKey: "auto_bar_lens",
      label: "镜头型号（左下）", placeholder: "NIKKOR Z DX 50-250mm f/4.5-6.3 VR" },
    { k: "bar_model", flagKey: "bar_model_auto", autoKey: "auto_bar_model",
      label: "相机型号（右上）", placeholder: "NIKON Z50II" },
    { k: "bar_date", flagKey: "bar_date_auto", autoKey: "auto_bar_date",
      label: "日期时间（右下）", placeholder: "2026-10-18 16:21" },
  ];

  constructor(app) {
    this.app = app;
    this.controls = new ControlFactory(app);
    this.setters = {};
    this.captionFields = [];
    this.light = null;
  }

  build() {
    const look = Dom.get("g-look");
    const kind = this.controls.switcher({
      k: "style", label: "边框类型", onInput: () => this.showStyle(),
      choices: [["blur", "模糊背景"], ["bar", "信息底框"]],
    });
    this.setters.style = kind.set;
    look.append(kind.row);

    const lookBlur = this.controls.subgroup(look, "blur");
    this.addSliders(lookBlur, [
      { k: "border", label: "边框宽度", min: 0.1, max: 5, step: 0.1, unit: "x" },
      { k: "radius", label: "圆角", min: 0, max: 100, step: 1 },
      { k: "shadow", label: "阴影", min: 0, max: 100, step: 1 },
      { k: "blur", label: "模糊度", min: 3, max: 100, step: 1 },
    ]);
    this.controls.addHint(lookBlur, "三边等宽；下边框＝文字高＋一个边框宽，文字居中。",
      "下边框是文字高度加上一个边框宽度，这一个宽度平分到文字上下，"
      + "所以文字上下的留白加起来正好等于其他三边。");

    const lookBar = this.controls.subgroup(look, "bar");
    const shade = this.controls.switcher({
      k: "bar_color", label: "底框颜色",
      choices: [["white", "白"], ["black", "黑"]],
    });
    this.setters.bar_color = shade.set;
    lookBar.append(shade.row);
    this.addSliders(lookBar, [
      { k: "bar_width", label: "底框宽度", min: 0.5, max: 5, step: 0.1, unit: "x" },
      { k: "bar_logo_scale", label: "LOGO 大小", min: 0.5, max: 5, step: 0.1, unit: "x" },
    ]);
    this.controls.addHint(lookBar, "照片四边不留白，底框挂在下方，宽度按照片宽度比例计算。"
      + "LOGO 和文字上下平齐后不再变大，需要调大底框宽度。");

    const text = Dom.get("g-text");
    const textBlur = this.controls.subgroup(text, "blur");
    const line = this.controls.switcher({
      k: "one_line", label: "排版",
      choices: [[false, "两行"], [true, "一行"]],
    });
    this.setters.one_line = line.set;
    textBlur.append(line.row);

    const fields = [
      this.controls.captionField({
        k: "title", flagKey: "title_auto", autoKey: "auto_title",
        label: "厂商 / 型号", placeholder: "打开照片后自动填充",
      }),
      this.controls.captionField({
        k: "params_text", flagKey: "params_auto", autoKey: "auto_params",
        label: "拍摄参数", placeholder: "焦距 | 光圈 | 快门 | ISO",
      }),
    ];
    this.setters.title = fields[0].set;
    this.setters.params_text = fields[1].set;
    textBlur.append(fields[0].row);
    this.controls.addHint(textBlur, "Nikon / DJI / FUJIFILM / Apple 出图显示 logo。",
      "按 EXIF 的厂商字段判断，出图时把标题里的厂商名换成 logo，比型号文字略高、"
      + "同一条基线；手改标题后改按标题开头的文字判断，改掉厂商名就回到纯文字。");
    textBlur.append(fields[1].row);
    this.addSliders(textBlur, [
      { k: "text_scale", label: "字号", min: 0.5, max: 2.5, step: 0.05, unit: "x" },
    ]);
    const light = this.controls.lightnessField({ label: "文字明暗" });
    this.setters.text_light = light.set;
    this.light = light;
    textBlur.append(light.row);

    const textBar = this.controls.subgroup(text, "bar");
    for (const spec of SettingsPanel.BAR_FIELDS) {
      const field = this.controls.captionField(spec);
      this.setters[spec.k] = field.set;
      fields.push(field);
      textBar.append(field.row);
    }
    this.addSliders(textBar, [
      { k: "bar_text_scale", label: "字号", min: 0.5, max: 5, step: 0.1, unit: "x" },
    ]);
    this.controls.addHint(textBar, "清空一行即不显示该行。文字颜色随底框自动取黑或白；"
      + "字号排满整行后不再变大。");

    this.captionFields = fields;
    this.buildExportGroup();
  }

  showStyle() {
    const style = this.app.state.params.style || "blur";
    document.querySelectorAll(".sub[data-style]").forEach((node) =>
      node.classList.toggle("hidden", node.dataset.style !== style));
  }

  addSliders(host, specs) {
    for (const spec of specs) {
      const control = this.controls.slider(spec);
      this.setters[spec.k] = control.set;
      host.append(control.row);
    }
  }

  buildExportGroup() {
    const host = Dom.get("g-export");
    const row = Dom.element("div", "row wide");
    row.append(Dom.element("label", null, "格式"));
    const group = Dom.element("div", "seg");
    const hint = Dom.element("p", "hint");
    const buttons = [["png", "PNG 无损"], ["jpeg", "JPEG q100"]].map(([value, label]) => {
      const button = Dom.element("button", null, label);
      button.type = "button";
      button.setAttribute("aria-pressed", String(this.app.state.format === value));
      button.addEventListener("click", () => {
        this.app.state.format = value;
        buttons.forEach((b) => b.setAttribute("aria-pressed", String(b === button)));
        hint.textContent = SettingsPanel.FORMAT_HINT[value];
        this.app.persist();
      });
      group.append(button);
      return button;
    });
    hint.textContent = SettingsPanel.FORMAT_HINT[this.app.state.format];
    row.append(group);
    host.append(row, hint);
  }

  applyParams() {
    for (const [key, set] of Object.entries(this.setters)) set(this.app.state.params[key]);
    this.showStyle();
    this.app.persist();
  }

  refreshCaptions() {
    this.captionFields.forEach((field) => field.refresh());
  }

  paintLight() {
    this.light.paint();
  }
}

/** Debounce preview requests and keep the latest preview visible. */
class PreviewController {
  constructor(app) {
    this.app = app;
    this.timer = null;
    this.url = null;
    this.inflight = false;
    this.queued = false;
  }

  schedule() {
    this.app.persist();
    if (!this.app.state.session) return;
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.run(), 55);
  }

  async run() {
    if (!this.app.state.session) return;
    if (this.inflight) { this.queued = true; return; }
    this.inflight = true;
    Dom.get("spinner").classList.remove("hidden");
    try {
      const { blob, headers } = await this.app.api.request("/api/preview", {
        body: { id: this.app.state.session.id, params: this.app.state.params },
      });
      const url = URL.createObjectURL(blob);
      Dom.get("preview").src = url;
      if (this.url) URL.revokeObjectURL(this.url);
      this.url = url;

      const canvas = headers.get("X-Canvas") || "";
      const s = this.app.state.session;
      Dom.get("canvas-size").textContent = canvas
        ? `导出 ${canvas.replace("x", " × ")}　·　照片 ${s.width} × ${s.height} 原样嵌入`
        : "";
      if (this.app.state.params.text_auto) {
        this.app.state.params.text_light = Number(headers.get("X-Text-Light")) || 100;
        this.app.panel.paintLight();
      }
    } catch (error) {
      this.app.view.say(String(error.message || error), "bad");
    } finally {
      this.inflight = false;
      Dom.get("spinner").classList.add("hidden");
      if (this.queued) { this.queued = false; this.run(); }
    }
  }
}

/** Open photos, accept dropped files and adopt loaded sessions. */
class PhotoController {
  constructor(app) {
    this.app = app;
  }

  adopt(summary) {
    this.app.state.session = summary;
    Dom.get("dropzone").classList.add("hidden");
    Dom.get("viewport").classList.remove("hidden");
    Dom.get("export").disabled = false;
    Dom.get("batch").disabled = false;
    this.app.panel.refreshCaptions();
    this.app.view.showMeta(summary);
    this.app.view.say("");
    this.app.preview.run();
  }

  async open(path) {
    if (!path) return;
    this.app.view.say("正在读取…");
    try {
      this.adopt(await this.app.api.request("/api/load", { body: { path } }));
    } catch (error) {
      this.app.view.say(`打开失败：${error.message}`, "bad");
    }
  }

  async upload(file) {
    this.app.view.say(`正在读取 ${file.name}…`);
    try {
      this.adopt(await this.app.api.request("/api/upload", {
        raw: await file.arrayBuffer(), filename: file.name,
      }));
    } catch (error) {
      this.app.view.say(`读取失败：${error.message}`, "bad");
    }
  }

  async pick() {
    try {
      const { path } = await this.app.api.request("/api/pick?mode=file", { method: "GET" });
      await this.open(path);
    } catch (error) {
      this.app.view.say(`无法打开选择器：${error.message}`, "bad");
    }
  }
}

/** Download the current photo in the chosen image format. */
class ExportController {
  constructor(app) {
    this.app = app;
  }

  static human(bytes) {
    return bytes >= 1e6 ? `${(bytes / 1e6).toFixed(1)} MB` : `${Math.round(bytes / 1e3)} KB`;
  }

  async run() {
    const button = Dom.get("export");
    button.disabled = true;
    button.textContent = "正在渲染原分辨率…";
    try {
      const { blob, headers } = await this.app.api.request("/api/export", {
        body: { id: this.app.state.session.id, params: this.app.state.params, format: this.app.state.format },
      });
      // hand the bytes to the browser's own downloader; it lands wherever
      // downloads go, so the tool never has to ask for a folder
      const name = decodeURIComponent(headers.get("X-Filename") || "framed.png");
      const url = URL.createObjectURL(blob);
      const link = Dom.element("a");
      link.href = url;
      link.download = name;
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 30000);

      const dims = headers.get("X-Dimensions") || "";
      const seconds = headers.get("X-Seconds") || "";
      this.app.view.say(`已下载 ${name}　${dims.replace("x", "×")}　${ExportController.human(blob.size)}　${seconds}s`, "good");
    } catch (error) {
      this.app.view.say(`导出失败：${error.message}`, "bad");
    } finally {
      button.disabled = false;
      button.textContent = "导出当前照片";
    }
  }
}

/** Start, monitor and cancel folder batch jobs. */
class BatchController {
  constructor(app) {
    this.app = app;
    this.job = null;
    this.timer = null;
  }

  static followed(names) {
    return names.length
      ? `${names.join("、")}按每张照片自己的 EXIF 填充`
      : "文字全部用你手改的内容";
  }

  async start() {
    const { path: folder } = await this.app.api.request("/api/pick?mode=folder", { method: "GET" });
    if (!folder) return;
    const { count } = await this.app.api.request(`/api/scan?folder=${encodeURIComponent(folder)}`, { method: "GET" });
    if (!count) { this.app.view.say("该文件夹里没有可读的图片。", "bad"); return; }

    const following = (this.app.state.params.style === "bar"
      ? [[this.app.state.params.bar_params_auto, "拍摄参数"], [this.app.state.params.bar_lens_auto, "镜头型号"],
         [this.app.state.params.bar_model_auto, "相机型号"], [this.app.state.params.bar_date_auto, "日期时间"]]
      : [[this.app.state.params.title_auto, "厂商型号"], [this.app.state.params.params_auto, "拍摄参数"]]
    ).filter(([on]) => on).map(([, name]) => name);
    const textNote = BatchController.followed(following);
    if (!confirm(`将用当前参数处理 ${count} 张照片\n\n来源：${folder}\n输出：${folder.replace(/\/$/, "")}/framed/\n格式：${this.app.state.format.toUpperCase()}\n文字：${textNote}\n\n继续？`)) return;

    this.job = await this.app.api.request("/api/batch", {
      body: { folder, params: this.app.state.params, format: this.app.state.format },
    });
    Dom.get("progress").classList.remove("hidden");
    Dom.get("batch-cancel").classList.remove("hidden");
    Dom.get("batch").disabled = true;
    this.poll();
  }

  async poll() {
    clearTimeout(this.timer);
    let job;
    try {
      job = await this.app.api.request(`/api/batch?job=${this.job.id}`, { method: "GET" });
    } catch (error) {
      this.app.view.say(`批量任务中断：${error.message}`, "bad");
      Dom.get("batch").disabled = false;
      return;
    }
    Dom.get("bar-fill").style.width = `${job.total ? (job.done / job.total) * 100 : 100}%`;
    Dom.get("progress-label").textContent = job.finished
      ? `完成 ${job.written.length}/${job.total}`
      : `${job.done}/${job.total}　${job.current || ""}`;
    if (!job.finished) {
      this.timer = setTimeout(() => this.poll(), 400);
      return;
    }

    Dom.get("batch").disabled = false;
    Dom.get("batch-cancel").classList.add("hidden");
    const node = Dom.get("status");
    node.className = "status " + (job.errors.length ? "bad" : "good");
    node.textContent = `批量完成：成功 ${job.written.length} 张，失败 ${job.errors.length} 张　`;
    if (job.written.length) {
      const link = Dom.element("button", "link", "打开输出目录");
      link.addEventListener("click", () => this.app.api.request("/api/reveal", { body: { path: job.written[0] } }));
      node.append(link);
    }
    for (const message of job.errors.slice(0, 5)) node.append(Dom.element("div", null, message));
  }

  async cancel() {
    if (this.job) await this.app.api.request("/api/cancel", { body: { job: this.job.id } });
  }
}

/** Coordinate settings, photo loading and UI actions. */
class PhotoFramerApp {
  constructor() {
    this.state = { session: null, params: null, defaults: null, format: "png" };
    this.api = new ApiClient();
    this.storage = new SettingsStorage();
    this.view = new AppView();
    this.panel = new SettingsPanel(this);
    this.preview = new PreviewController(this);
    this.photos = new PhotoController(this);
    this.exporter = new ExportController(this);
    this.batch = new BatchController(this);
  }

  persist() {
    this.storage.save(this.state);
  }

  wire() {
    Dom.get("pick-file").addEventListener("click", () => this.photos.pick());
    Dom.get("pick-file-2").addEventListener("click", () => this.photos.pick());

    Dom.get("reset").addEventListener("click", () => {
      this.state.params = { ...this.state.defaults.params };
      this.panel.applyParams();
      this.panel.refreshCaptions();
      this.preview.run();
      this.view.say("已恢复默认参数。");
    });

    Dom.get("export").addEventListener("click", () => this.exporter.run());
    Dom.get("batch").addEventListener("click", () => this.batch.start());
    Dom.get("batch-cancel").addEventListener("click", () => this.batch.cancel());

    const stage = Dom.get("stage");
    stage.addEventListener("dragover", (event) => {
      event.preventDefault();
      stage.classList.add("dragging");
    });
    stage.addEventListener("dragleave", () => stage.classList.remove("dragging"));
    stage.addEventListener("drop", (event) => {
      event.preventDefault();
      stage.classList.remove("dragging");
      const file = event.dataTransfer.files && event.dataTransfer.files[0];
      if (file) this.photos.upload(file);
    });
    window.addEventListener("dragover", (event) => event.preventDefault());
    window.addEventListener("drop", (event) => event.preventDefault());

    document.addEventListener("keydown", (event) => {
      if (!(event.metaKey || event.ctrlKey)) return;
      if (event.key === "s" && this.state.session) {
        event.preventDefault();
        this.exporter.run();
      } else if (event.key === "o") {
        event.preventDefault();
        this.photos.pick();
      }
    });
  }

  async boot() {
    try {
      this.state.defaults = await this.api.request("/api/defaults", { body: {} });
    } catch (error) {
      document.body.textContent = "无法连接后端：" + error.message;
      return;
    }
    this.state.params = { ...this.state.defaults.params };
    this.storage.restore(this.state);
    this.panel.build();
    this.panel.applyParams();
    this.wire();
    const initial = new URLSearchParams(location.search).get("open");
    if (initial) this.photos.open(initial);
  }
}

new PhotoFramerApp().boot();
