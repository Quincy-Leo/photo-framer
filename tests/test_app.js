// SPDX-FileCopyrightText: 2026 QuincyLeo
// SPDX-License-Identifier: GPL-3.0-or-later
"use strict";

// Run: node --test tests/test_app.js
// Exercises the UI through DOM events with an in-memory local API.
const assert = require("node:assert/strict");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

class Element {
  constructor(tag, document) {
    this.tagName = tag;
    this.document = document;
    this.children = [];
    this.listeners = {};
    this.attributes = {};
    this.dataset = {};
    this.style = {};
    this.className = "";
    this.value = "";
    this.disabled = false;
    this.textContent = "";
    this.classList = {
      contains: (name) => this.className.split(" ").includes(name),
      add: (name) => this.classList.toggle(name, true),
      remove: (name) => this.classList.toggle(name, false),
      toggle: (name, on) => {
        const names = new Set(this.className.split(" ").filter(Boolean));
        const enabled = on === undefined ? !names.has(name) : on;
        if (enabled) names.add(name); else names.delete(name);
        this.className = [...names].join(" ");
        return enabled;
      },
    };
  }

  set textContent(value) {
    this.text = String(value);
    this.children = [];
  }

  get textContent() {
    return this.text + this.children.map((child) =>
      typeof child === "string" ? child : child.textContent).join("");
  }

  append(...children) {
    this.children.push(...children);
    for (const child of children) {
      if (typeof child !== "string") child.parent = this;
    }
  }

  remove() {
    this.parent.children = this.parent.children.filter((child) => child !== this);
  }

  setAttribute(name, value) {
    this.attributes[name] = value;
  }

  addEventListener(name, callback) {
    (this.listeners[name] ||= []).push(callback);
  }

  dispatch(name, values = {}) {
    const event = { preventDefault() {}, ...values };
    for (const callback of this.listeners[name] || []) callback(event);
  }

  click() {
    if (this.tagName === "a") {
      this.document.downloads.push({ name: this.download, href: this.href });
    }
    this.dispatch("click");
  }
}

class Document extends Element {
  constructor() {
    super("document", null);
    this.document = this;
    this.elements = [];
    this.downloads = [];
    this.ids = new Map();
    this.body = this.createElement("body");
    for (const id of [
      "status", "g-look", "g-text", "g-export", "spinner", "preview", "canvas-size",
      "meta", "dropzone", "viewport", "export", "batch", "batch-cancel", "progress",
      "bar-fill", "progress-label", "pick-file", "pick-file-2", "reset", "stage",
    ]) {
      const node = this.createElement("div");
      this.ids.set(id, node);
      this.body.append(node);
    }
    for (const id of ["spinner", "meta", "viewport", "progress"]) {
      this.getElementById(id).className = "hidden";
    }
    this.getElementById("export").disabled = true;
    this.getElementById("batch").disabled = true;
  }

  createElement(tag) {
    const node = new Element(tag, this);
    this.elements.push(node);
    return node;
  }

  getElementById(id) {
    assert(this.ids.has(id), `Unexpected DOM id: ${id}`);
    return this.ids.get(id);
  }

  querySelectorAll(selector) {
    assert.equal(selector, ".sub[data-style]");
    return this.elements.filter((node) => node.dataset.style);
  }

  control(label) {
    const row = this.elements.find((node) =>
      node.children[0]?.tagName === "label" && node.children[0].textContent === label);
    assert(row, `Missing control: ${label}`);
    return row.children[1];
  }

  button(label) {
    const button = this.elements.find((node) =>
      node.tagName === "button" && node.textContent === label);
    assert(button, `Missing button: ${label}`);
    return button;
  }

  caption(label) {
    const head = this.elements.find((node) =>
      node.className === "field-head" && node.children[0].textContent === label);
    assert(head, `Missing caption: ${label}`);
    return { input: head.parent.children[1], revert: head.children[1] };
  }
}

class AppHarness {
  static ROOT = path.resolve(__dirname, "..");
  static DEFAULTS = JSON.parse(execFileSync("python3", ["-c",
    "import json, framer; print(json.dumps(framer.Params().to_dict()))",
  ], { cwd: AppHarness.ROOT, encoding: "utf8" }));

  constructor({ saved = null, search = "", token = "test-token", fail = "" } = {}) {
    this.document = new Document();
    this.window = new Element("window", this.document);
    this.window.FRAMER_TOKEN = token;
    this.storage = new Map(saved === null ? [] : [["photo-framer.params.v5", saved]]);
    this.requests = [];
    this.timers = new Map();
    this.confirmations = [];
    this.fail = fail;
    this.nextTimer = 0;
    this.jobFinished = false;
    this.holdPreview = false;
    this.summary = {
      id: "photo-session", name: "a.jpg", width: 600, height: 400,
      megapixels: 0.24, format: "JPEG", icc: "sRGB", source_mode: "RGB",
      bit_depth: 8, auto_title: "NIKON  Z8", auto_params: "50mm | f/4",
      auto_bar_params: "50mm, F4", auto_bar_lens: "NIKKOR Z 50mm",
      auto_bar_model: "NIKON Z8", auto_bar_date: "2026-10-22",
    };
    this.context = vm.createContext({
      window: this.window, document: this.document, location: { search },
      URLSearchParams, console, encodeURIComponent, decodeURIComponent,
      URL: { createObjectURL: () => "blob:test", revokeObjectURL() {} },
      localStorage: {
        getItem: (key) => this.storage.get(key) ?? null,
        setItem: (key, value) => this.storage.set(key, value),
      },
      setTimeout: (callback, delay) => {
        const id = ++this.nextTimer;
        this.timers.set(id, { callback, delay });
        return id;
      },
      clearTimeout: (id) => this.timers.delete(id),
      confirm: (message) => { this.confirmations.push(message); return true; },
      fetch: (route, options) => this.fetch(route, options),
    });
  }

  async boot() {
    const filename = process.env.FRAMER_APP_SOURCE || path.join(AppHarness.ROOT, "static/app.js");
    vm.runInContext(fs.readFileSync(filename, "utf8"), this.context, { filename });
    await this.settle();
    return this;
  }

  async settle() {
    for (let i = 0; i < 30; i++) await Promise.resolve();
  }

  async timersAt(delay) {
    for (const [id, timer] of [...this.timers]) {
      if (timer.delay !== delay) continue;
      this.timers.delete(id);
      timer.callback();
    }
    await this.settle();
  }

  async fetch(route, options) {
    const body = typeof options.body === "string" ? JSON.parse(options.body) : options.body;
    this.requests.push({ route, body, headers: options.headers, method: options.method });
    if (this.fail && route.startsWith(this.fail)) throw new Error("test failure");
    if (route === "/api/preview" && this.holdPreview) {
      this.holdPreview = false;
      return new Promise((resolve) => { this.releasePreview = () => resolve(this.response(route)); });
    }
    return this.response(route);
  }

  response(route) {
    let data;
    const headers = new Map([["Content-Type", "application/json"]]);
    if (route === "/api/defaults") data = { params: AppHarness.DEFAULTS };
    else if (route === "/api/pick?mode=file") data = { path: "/photos/a.jpg" };
    else if (route === "/api/pick?mode=folder") data = { path: "/photos" };
    else if (route === "/api/load" || route === "/api/upload") data = this.summary;
    else if (route.startsWith("/api/scan?")) data = { count: 2 };
    else if (route === "/api/batch") data = { id: "batch-job", total: 2 };
    else if (route.startsWith("/api/batch?job=")) {
      data = { id: "batch-job", total: 2, done: this.jobFinished ? 2 : 1,
        current: "a.jpg", finished: this.jobFinished, errors: [],
        written: this.jobFinished ? ["/photos/framed/a.png", "/photos/framed/b.png"] : [] };
    } else if (route === "/api/cancel") {
      this.jobFinished = true;
      data = { ok: true };
    } else if (route === "/api/reveal") data = { ok: true };
    else if (route === "/api/preview" || route === "/api/export") {
      headers.set("Content-Type", "image/png");
      headers.set("X-Canvas", "700x550");
      headers.set("X-Text-Light", "100");
      headers.set("X-Filename", "a_framed.jpg");
      data = { size: 1234 };
    } else throw new Error(`Unexpected API route: ${route}`);
    return { ok: true, headers, json: async () => data, blob: async () => data };
  }

  saved() {
    return JSON.parse(this.storage.get("photo-framer.params.v5"));
  }

  calls(route) {
    return this.requests.filter((request) => request.route === route);
  }

  async open() {
    this.document.getElementById("pick-file").click();
    await this.settle();
  }

  async input(label, value) {
    const input = this.document.control(label);
    input.value = value;
    input.dispatch("input");
    await this.timersAt(55);
  }
}

test("restores known settings and keeps the storage format", async () => {
  const app = await new AppHarness({ saved: JSON.stringify({
    params: { border: 2, unknown: "ignored" }, format: "jpeg",
  }) }).boot();
  assert.equal(app.document.control("边框宽度").value, 2);
  assert.equal(app.saved().params.unknown, undefined);
  assert.equal(app.saved().format, "jpeg");
  assert.equal(app.requests[0].headers["X-Framer-Token"], "test-token");
});

test("opens photos, edits EXIF fields and switches between frame styles", async () => {
  const app = await new AppHarness().boot();
  await app.open();
  assert.equal(app.document.getElementById("export").disabled, false);
  assert.match(app.document.getElementById("canvas-size").textContent, /700 × 550/);
  const caption = app.document.caption("厂商 / 型号");
  assert.equal(caption.input.value, "NIKON  Z8");
  caption.input.value = "My camera";
  caption.input.dispatch("input");
  await app.timersAt(55);
  assert.equal(app.calls("/api/preview").at(-1).body.params.title_auto, false);
  caption.revert.click();
  await app.timersAt(55);
  assert.equal(caption.input.value, "NIKON  Z8");
  app.document.button("信息底框").click();
  await app.timersAt(55);
  assert.equal(app.saved().params.style, "bar");
  for (const node of app.document.querySelectorAll(".sub[data-style]")) {
    assert.equal(node.classList.contains("hidden"), node.dataset.style !== "bar");
  }
  const lens = app.document.caption("镜头型号（左下）").input;
  lens.value = "";
  lens.dispatch("input");
  await app.timersAt(55);
  assert.equal(app.saved().params.bar_lens_auto, false);
  assert.equal(app.saved().params.bar_lens, "");
});

test("debounces slider changes and queues the latest preview during a request", async () => {
  const app = await new AppHarness().boot();
  await app.open();
  const initial = app.calls("/api/preview").length;
  const slider = app.document.control("边框宽度");
  for (const value of [1.5, 2, 2.5]) {
    slider.value = value;
    slider.dispatch("input");
  }
  assert.equal(app.calls("/api/preview").length, initial);
  app.holdPreview = true;
  await app.timersAt(55);
  assert.equal(app.calls("/api/preview").length, initial + 1);
  await app.input("边框宽度", 3);
  assert.equal(app.calls("/api/preview").length, initial + 1);
  app.releasePreview();
  await app.settle();
  assert.equal(app.calls("/api/preview").length, initial + 2);
  assert.equal(app.calls("/api/preview").at(-1).body.params.border, 3);
  assert(app.document.getElementById("spinner").classList.contains("hidden"));
});

test("exports the chosen format and restores defaults through the reset button", async () => {
  const app = await new AppHarness().boot();
  await app.open();
  await app.input("文字明暗", 35);
  assert.equal(app.saved().params.text_auto, false);
  app.document.control("文字明暗").parent.children[2].click();
  await app.timersAt(55);
  assert.equal(app.saved().params.text_auto, true);
  app.document.button("JPEG q100").click();
  app.document.getElementById("export").click();
  await app.settle();
  assert.equal(app.calls("/api/export").at(-1).body.format, "jpeg");
  assert.equal(app.document.downloads.at(-1).name, "a_framed.jpg");
  await app.input("边框宽度", 3);
  app.document.getElementById("reset").click();
  await app.settle();
  assert.deepEqual(app.saved().params, AppHarness.DEFAULTS);
  assert.equal(app.saved().format, "jpeg");
});

test("starts, polls and cancels a batch and reveals the output directory", async () => {
  const app = await new AppHarness().boot();
  await app.open();
  app.document.getElementById("batch").click();
  await app.settle();
  assert.equal(app.document.getElementById("batch").disabled, true);
  assert.match(app.confirmations[0], /EXIF/);
  assert.equal(app.calls("/api/batch").at(-1).body.folder, "/photos");
  app.document.getElementById("batch-cancel").click();
  await app.settle();
  assert.equal(app.calls("/api/cancel").at(-1).body.job, "batch-job");
  await app.timersAt(400);
  assert.equal(app.document.getElementById("batch").disabled, false);
  assert.match(app.document.getElementById("status").textContent, /成功 2 张/);
  app.document.button("打开输出目录").click();
  await app.settle();
  assert.equal(app.calls("/api/reveal").at(-1).body.path, "/photos/framed/a.png");
});

test("accepts dropped files and handles keyboard open and export", async () => {
  const app = await new AppHarness().boot();
  app.document.getElementById("stage").dispatch("drop", {
    dataTransfer: { files: [{ name: "照片.jpg", arrayBuffer: async () => new ArrayBuffer(2) }] },
  });
  await app.settle();
  assert.equal(app.calls("/api/upload").length, 1);
  assert.equal(app.calls("/api/upload")[0].headers["X-Filename"], "_E7_85_A7_E7_89_87.jpg");
  app.document.dispatch("keydown", { metaKey: true, key: "s" });
  await app.settle();
  assert.equal(app.document.downloads.length, 1);
  app.document.dispatch("keydown", { ctrlKey: true, key: "o" });
  await app.settle();
  assert.equal(app.calls("/api/load").length, 1);
});

test("reports request failures and restores the preview and export controls", async () => {
  const app = await new AppHarness().boot();
  await app.open();
  app.fail = "/api/preview";
  await app.input("边框宽度", 2);
  assert.match(app.document.getElementById("status").textContent, /test failure/);
  assert(app.document.getElementById("spinner").classList.contains("hidden"));
  app.fail = "/api/export";
  app.document.getElementById("export").click();
  await app.settle();
  assert.equal(app.document.getElementById("export").disabled, false);
  assert.match(app.document.getElementById("status").textContent, /导出失败/);
});

test("handles invalid saved settings, URL tokens and photos opened on launch", async () => {
  const app = await new AppHarness({
    saved: "{broken", token: "__TOKEN__", search: "?t=url-token&open=%2Fphotos%2Fa.jpg",
  }).boot();
  assert.equal(app.requests[0].headers["X-Framer-Token"], "url-token");
  assert.equal(app.calls("/api/load")[0].body.path, "/photos/a.jpg");
  assert.equal(app.document.caption("厂商 / 型号").input.value, "NIKON  Z8");
  const failed = await new AppHarness({ fail: "/api/defaults" }).boot();
  assert.equal(failed.document.body.textContent, "无法连接后端：test failure");
});
