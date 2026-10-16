#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 QuincyLeo
# SPDX-License-Identifier: GPL-3.0-or-later
"""Local web UI for the photo framer.

Preview and export are deliberately split: the browser gets a small JPEG
proxy so sliders feel immediate, while every export is rendered from the
untouched original by framer.py.  Nothing the browser does can degrade the
exported photo.

The server binds to 127.0.0.1 only and all /api/ calls require the one-time
token printed in the launch URL, so another page in your browser cannot drive
it.  It does read and write files anywhere your user account can -- that is
the point of the file pickers -- so treat it as a local tool, not a service.
"""

from __future__ import annotations

import io
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import framer
from framer import Params

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
TOKEN = secrets.token_urlsafe(16)
# Dropped photos are staged inside the checkout, not in a system temp
# directory, so the cache can never leak somewhere the user isn't looking.
# .gitignore keeps it out of commits; stale files are swept at startup.
CACHE_DIR = os.path.join(HERE, ".temp")
MAX_UPLOAD = 400 * 1024 * 1024
MAX_SESSIONS = 4
PREVIEW_LONG_SIDE = 1024

_sessions: dict[str, dict] = {}
_jobs: dict[str, dict] = {}
_lock = threading.Lock()


# --------------------------------------------------------------------------
# sessions
# --------------------------------------------------------------------------

def open_session(path):
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    photo, info = framer.load_source(path)
    values = framer.exif_values(photo, path)
    session = {
        "id": uuid.uuid4().hex[:12],
        "path": path,
        "photo": photo,
        "info": info,
        "values": values,
        "proxy": framer.downscale_for_preview(photo, PREVIEW_LONG_SIDE),
        "touched": time.time(),
    }
    with _lock:
        _sessions[session["id"]] = session
        for stale in sorted(_sessions.values(), key=lambda s: s["touched"])[:-MAX_SESSIONS]:
            _sessions.pop(stale["id"], None)
    return session


def session_summary(session):
    photo, info = session["photo"], session["info"]
    icc = info.get("icc_profile")
    name = ""
    if icc:
        try:
            from PIL import ImageCms
            name = ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(icc))).strip()
        except Exception:
            name = f"{len(icc)} bytes"
    return {
        "id": session["id"],
        "path": session["path"],
        "name": os.path.basename(session["path"]),
        "width": photo.width,
        "height": photo.height,
        "megapixels": round(photo.width * photo.height / 1e6, 1),
        "format": info.get("source_format") or "",
        "icc": name,
        "has_exif": bool(info.get("exif_raw") and len(info["exif_raw"])),
        "orientation": info.get("orientation", 1),
        "converted": info.get("converted", False),
        "source_mode": info.get("source_mode", ""),
        "bit_depth": info.get("bit_depth", 8),
        "values": session["values"],
        "auto_title": framer.auto_title(session["values"]),
        "auto_params": framer.auto_params(session["values"]),
    }


def get_session(session_id):
    with _lock:
        session = _sessions.get(session_id)
        if session:
            session["touched"] = time.time()
    if not session:
        raise KeyError("session expired, please reopen the photo")
    return session


# --------------------------------------------------------------------------
# native macOS pickers
# --------------------------------------------------------------------------

def native_pick(mode):
    if mode == "folder":
        script = 'POSIX path of (choose folder with prompt "选择照片文件夹")'
    else:
        script = ('POSIX path of (choose file with prompt "选择照片" '
                  'of type {"public.image"})')
    try:
        done = subprocess.run(["osascript", "-e", script], capture_output=True,
                              text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    if done.returncode != 0:
        detail = (done.stderr or "").strip()
        return None, "" if "cancel" in detail.lower() else detail
    return done.stdout.strip() or None, ""


def reveal(path):
    if os.path.exists(path):
        subprocess.Popen(["open", "-R", path])


def sweep_cache(max_age=86400):
    """Dropped photos are copied into .temp/; drop yesterday's leftovers."""
    cutoff = time.time() - max_age
    try:
        names = os.listdir(CACHE_DIR)
    except OSError:
        return
    for name in names:
        full = os.path.join(CACHE_DIR, name)
        try:
            if os.path.isfile(full) and os.path.getmtime(full) < cutoff:
                os.remove(full)
        except OSError:
            pass


# --------------------------------------------------------------------------
# batch jobs
# --------------------------------------------------------------------------

def list_images(folder):
    folder = os.path.abspath(os.path.expanduser(folder))
    try:
        entries = sorted(os.listdir(folder), key=str.lower)
    except OSError:
        return []
    out = []
    for entry in entries:
        if entry.startswith("."):
            continue
        full = os.path.join(folder, entry)
        if os.path.isfile(full) and os.path.splitext(entry)[1].lower() in framer.READABLE_EXT:
            out.append(full)
    return out


def start_batch(folder, params, fmt, out_dir, overrides):
    files = list_images(folder)
    base = os.path.abspath(os.path.expanduser(folder))
    job = {"id": uuid.uuid4().hex[:12], "total": len(files), "done": 0,
           "written": [], "errors": [], "finished": not files, "current": "",
           "out_dir": out_dir or os.path.join(base, "framed"), "cancel": False}
    with _lock:
        _jobs[job["id"]] = job
        for stale in [j for j in list(_jobs.values()) if j["finished"]][:-8]:
            _jobs.pop(stale["id"], None)

    def run():
        for path in files:
            if job["cancel"]:
                break
            job["current"] = os.path.basename(path)
            try:
                written, _ = framer.render_file(path, params, fmt, out_dir=job["out_dir"],
                                                overrides=overrides)
                job["written"].append(written)
            except Exception as exc:
                job["errors"].append(f"{os.path.basename(path)}: {type(exc).__name__}: {exc}")
            job["done"] += 1
        job["current"] = ""
        job["finished"] = True

    if files:
        threading.Thread(target=run, daemon=True).start()
    return job


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml",
                 ".ico": "image/x-icon", ".json": "application/json"}

MIME_FOR_FORMAT = {"png": "image/png", "jpeg": "image/jpeg", "tiff": "image/tiff"}


def _ascii_name(name):
    """A plain-ASCII fallback for the Content-Disposition filename."""
    stripped = name.encode("ascii", "ignore").decode().replace('"', "")
    return stripped or "framed.png"


class Handler(BaseHTTPRequestHandler):
    server_version = "PhotoFramer"
    protocol_version = "HTTP/1.1"
    raw_body = b""

    def log_message(self, fmt, *args):  # quieter console
        if "/api/preview" not in (self.path or ""):
            sys.stderr.write("  %s\n" % (fmt % args))

    # -- helpers ----------------------------------------------------------
    def _send(self, code, body=b"", ctype="application/octet-stream", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, payload, code=200):
        self._send(code, json.dumps(payload, ensure_ascii=False).encode(),
                   "application/json; charset=utf-8")

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_UPLOAD:
            raise ValueError("payload too large")
        return self.rfile.read(length) if length else b""

    def _authorised(self, query):
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost", "[::1]", "::1"):
            return False
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).hostname not in ("127.0.0.1", "localhost", "::1"):
            return False
        supplied = self.headers.get("X-Framer-Token") or (query.get("t") or [""])[0]
        return secrets.compare_digest(supplied, TOKEN)

    # -- routing ----------------------------------------------------------
    def do_GET(self):
        route = urlparse(self.path)
        query = parse_qs(route.query)
        if route.path in ("/", "/index.html"):
            return self._file(os.path.join(STATIC, "index.html"),
                              inject=b'"__TOKEN__"', value=json.dumps(TOKEN).encode())
        if route.path.startswith("/static/"):
            name = os.path.basename(route.path)
            return self._file(os.path.join(STATIC, name))
        if route.path == "/favicon.ico":
            return self._send(204)
        if not self._authorised(query):
            return self._json({"error": "unauthorised"}, 403)
        if route.path == "/api/pick":
            path, error = native_pick((query.get("mode") or ["file"])[0])
            if error:
                return self._json({"error": error}, 500)
            return self._json({"path": path})
        if route.path == "/api/batch":
            job = _jobs.get((query.get("job") or [""])[0])
            if not job:
                return self._json({"error": "unknown job"}, 404)
            return self._json({k: v for k, v in job.items() if k != "cancel"})
        if route.path == "/api/scan":
            folder = (query.get("folder") or [""])[0]
            files = list_images(folder)
            return self._json({"count": len(files),
                               "names": [os.path.basename(f) for f in files[:200]]})
        return self._json({"error": "not found"}, 404)

    def _file(self, path, inject=None, value=None):
        if not os.path.isfile(path) or not os.path.abspath(path).startswith(STATIC):
            return self._json({"error": "not found"}, 404)
        with open(path, "rb") as handle:
            body = handle.read()
        if inject:
            body = body.replace(inject, value)
        ext = os.path.splitext(path)[1].lower()
        self._send(200, body, CONTENT_TYPES.get(ext, "application/octet-stream"))

    def do_POST(self):
        route = urlparse(self.path)
        query = parse_qs(route.query)
        # The body must be drained before anything else: this is a keep-alive
        # HTTP/1.1 server, and bytes left in the socket would be parsed as the
        # start of the next request line.
        try:
            self.raw_body = self._body()
        except Exception as exc:
            # the body was not consumed, so this connection can't be reused
            self.close_connection = True
            return self._json({"error": str(exc)}, 413)
        if not self._authorised(query):
            return self._json({"error": "unauthorised"}, 403)
        try:
            handler = {
                "/api/load": self._load, "/api/upload": self._upload,
                "/api/preview": self._preview, "/api/export": self._export,
                "/api/batch": self._batch, "/api/cancel": self._cancel,
                "/api/reveal": self._reveal, "/api/defaults": self._defaults,
            }.get(route.path)
            if handler is None:
                return self._json({"error": "not found"}, 404)
            handler()
        except (FileNotFoundError, KeyError) as exc:
            self._json({"error": str(exc) or "not found"}, 404)
        except ValueError as exc:
            self._json({"error": str(exc)}, 400)
        except Exception as exc:
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def _payload(self):
        return json.loads(self.raw_body.decode("utf-8")) if self.raw_body else {}

    def _load(self):
        data = self._payload()
        self._json(session_summary(open_session(data.get("path", ""))))

    def _upload(self):
        name = os.path.basename(self.headers.get("X-Filename") or "dropped.jpg")
        os.makedirs(CACHE_DIR, exist_ok=True)
        target = os.path.join(CACHE_DIR, f"{uuid.uuid4().hex[:8]}-{name}")
        with open(target, "wb") as handle:
            handle.write(self.raw_body)
        summary = session_summary(open_session(target))
        summary["temporary"] = True
        self._json(summary)

    def _preview(self):
        data = self._payload()
        session = get_session(data.get("id", ""))
        params = Params.from_dict(data.get("params"))
        values = session["values"] | (data.get("overrides") or {})
        title, settings = framer.resolve_texts(params, values)
        mark = framer.resolve_mark(params, values, title)
        report = {}
        image = framer.render(session["proxy"], params, title, settings, report, mark)
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=84, subsampling=1, optimize=False)
        full = framer.layout(session["photo"].size, params, title, settings, mark)["canvas"]
        self._send(200, buffer.getvalue(), "image/jpeg", {
            "X-Canvas": f"{full[0]}x{full[1]}",
            "X-Text-Light": f"{report.get('text_light', params.text_light):.0f}",
        })

    def _export(self):
        """Render at full resolution and hand the bytes to the browser.

        The browser saves them wherever downloads go, so the tool never has
        to ask for a destination.  A Blob download is a byte-for-byte copy --
        nothing on the way re-encodes the image.
        """
        data = self._payload()
        session = get_session(data.get("id", ""))
        params = Params.from_dict(data.get("params"))
        fmt = (data.get("format") or "png").lower()
        started = time.time()
        payload, size = framer.render_bytes(session["path"], params, fmt,
                                            overrides=data.get("overrides") or {})
        name = framer.output_name(session["path"], fmt)
        quoted = quote(name, safe="")
        self._send(200, payload, MIME_FOR_FORMAT.get(fmt, "application/octet-stream"), {
            "Content-Disposition": f"attachment; filename=\"{_ascii_name(name)}\"; "
                                   f"filename*=UTF-8''{quoted}",
            "X-Filename": quoted,
            "X-Dimensions": f"{size[0]}x{size[1]}",
            "X-Seconds": f"{time.time() - started:.2f}",
        })

    def _batch(self):
        data = self._payload()
        folder = os.path.abspath(os.path.expanduser(data.get("folder") or ""))
        if not os.path.isdir(folder):
            raise FileNotFoundError(folder)
        out_dir = data.get("out_dir") or None
        if out_dir:
            out_dir = os.path.abspath(os.path.expanduser(out_dir))
        job = start_batch(folder, Params.from_dict(data.get("params")),
                          (data.get("format") or "png").lower(), out_dir,
                          data.get("overrides") or {})
        self._json({k: v for k, v in job.items() if k != "cancel"})

    def _cancel(self):
        job = _jobs.get(self._payload().get("job", ""))
        if job:
            job["cancel"] = True
        self._json({"ok": bool(job)})

    def _reveal(self):
        reveal(os.path.abspath(os.path.expanduser(self._payload().get("path", ""))))
        self._json({"ok": True})

    def _defaults(self):
        self._json({"params": Params().to_dict()})


def main():
    sweep_cache()
    port = int(os.environ.get("FRAMER_PORT", "8787"))
    server = None
    for candidate in range(port, port + 20):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate), Handler)
            port = candidate
            break
        except OSError:
            continue
    if server is None:
        print("no free port in range", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{port}/?t={TOKEN}"
    opening = [a for a in sys.argv[1:] if not a.startswith("-")]
    if opening:
        url += "&open=" + quote(os.path.abspath(os.path.expanduser(opening[0])))
    print("照片加边框工具已启动")
    print(f"  {url}")
    print("  Ctrl-C 退出")
    print("  photo-framer  Copyright (C) 2026 QuincyLeo  ·  GPL-3.0-or-later, 无任何担保\n")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.4, webbrowser.open, (url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
