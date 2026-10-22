#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 QuincyLeo
# SPDX-License-Identifier: GPL-3.0-or-later
"""Local HTTP interface for the photo framer."""

from __future__ import annotations

import io
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import framer
from framer import Params


class SessionStore:
    """The photos open in the browser right now, the newest-touched kept.

    A session holds the decoded full-size photo and its preview proxy, so the
    store is where this process's memory goes -- hence the cap.  The lock
    guards the dict and nothing else: decoding happens outside it, or every
    preview would queue behind whatever large file someone just opened.
    """

    MAX_SESSIONS = 4
    PREVIEW_LONG_SIDE = 1024

    def __init__(self, limit=MAX_SESSIONS):
        self._limit = limit
        self._sessions = {}
        self._lock = threading.Lock()

    def open(self, path):
        path = os.path.abspath(os.path.expanduser(path))
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        photo, info = framer.ImageSource.load(path)
        values = framer.ExifMetadata.read(photo, path)
        session = {
            "id": uuid.uuid4().hex[:12],
            "path": path,
            "photo": photo,
            "info": info,
            "values": values,
            "proxy": framer.ImageSource.preview(photo, self.PREVIEW_LONG_SIDE),
            "touched": time.time(),
        }
        with self._lock:
            # the new session goes in first, so the freshest entry is always
            # one of the ones kept
            self._sessions[session["id"]] = session
            stale = sorted(self._sessions.values(),
                           key=lambda s: s["touched"])[:-self._limit]
            for old in stale:
                self._sessions.pop(old["id"], None)
        return session

    def get(self, session_id):
        with self._lock:
            session = self._sessions.get(session_id)
            if session:
                session["touched"] = time.time()
        if not session:
            raise KeyError("session expired, please reopen the photo")
        return session

    @staticmethod
    def summary(session):
        photo, info = session["photo"], session["info"]
        icc = info.get("icc_profile")
        name = ""
        if icc:
            try:
                from PIL import ImageCms
                name = ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(icc))).strip()
            except Exception:
                name = f"{len(icc)} bytes"
        summary = {
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
            "auto_title": framer.CaptionContent.auto_title(session["values"]),
            "auto_params": framer.CaptionContent.auto_params(session["values"]),
        }
        # what each info-bar field shows while it is still following the EXIF
        for slot, auto in framer.CaptionContent.bar_slots().items():
            summary[f"auto_bar_{slot}"] = auto(session["values"])
        return summary

    def preview(self, session_id, params, overrides=None):
        """Render the UI preview and report its full-resolution canvas."""
        session = self.get(session_id)
        values = session["values"] | (overrides or {})
        title, settings = framer.CaptionContent.resolve_texts(params, values)
        texts = framer.CaptionContent.bar_texts(params, values)
        mark = framer.CaptionContent.resolve_mark(params, values, title, texts)
        report = {}
        image = framer.FrameRenderer.render(
            session["proxy"], params, title, settings, report, mark, texts)
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=84, subsampling=1, optimize=False)
        full = framer.FrameRenderer.layout(
            session["photo"].size, params, title, settings, mark, texts)["canvas"]
        return buffer.getvalue(), {
            "X-Canvas": f"{full[0]}x{full[1]}",
            "X-Text-Light": f"{report.get('text_light', params.text_light):.0f}",
        }


class NativeDialogs:
    """macOS file selection and Finder integration."""

    @staticmethod
    def pick(mode):
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

    @staticmethod
    def reveal(path):
        if os.path.exists(path):
            subprocess.Popen(["open", "-R", path])


class UploadCache:
    """Stage dropped photos and remove expired staged uploads."""

    STAGED_NAME = re.compile(r"^[0-9a-f]{8}-")

    def __init__(self, directory):
        self.directory = directory

    def stage(self, payload, filename):
        name = os.path.basename(filename or "dropped.jpg")
        os.makedirs(self.directory, exist_ok=True)
        target = os.path.join(self.directory, f"{uuid.uuid4().hex[:8]}-{name}")
        with open(target, "wb") as handle:
            handle.write(payload)
        return target

    def sweep(self, max_age=86400):
        """Dropped photos are copied into .tmp/; drop yesterday's leftovers.

        Only this server's own staged copies are touched -- never a file someone
        put in the folder themselves.
        """
        cutoff = time.time() - max_age
        try:
            names = os.listdir(self.directory)
        except OSError:
            return
        for name in names:
            if not self.STAGED_NAME.match(name):
                continue
            full = os.path.join(self.directory, name)
            try:
                if os.path.isfile(full) and os.path.getmtime(full) < cutoff:
                    os.remove(full)
            except OSError:
                pass


class JobRegistry:
    """The batch renders this process has started, and their progress.

    Its own lock, separate from the session store's: the two are never
    touched in the same breath, so one lock each is the finer grain.  What
    the lock covers is the registry dict -- which jobs exist.  A job's own
    counters are written by its worker thread without any lock, so a report
    is a consistent view of the registry, not an instant of the render.
    """

    def __init__(self):
        self._jobs = {}
        self._lock = threading.Lock()

    @staticmethod
    def _public(job):
        """A job as the browser sees it: everything but the cancel flag."""
        return {k: v for k, v in job.items() if k != "cancel"}

    def start(self, folder, params, fmt, out_dir, overrides):
        files = self.list_images(folder)
        base = os.path.abspath(os.path.expanduser(folder))
        job = {"id": uuid.uuid4().hex[:12], "total": len(files), "done": 0,
               "written": [], "errors": [], "finished": not files, "current": "",
               "out_dir": out_dir or os.path.join(base, "framed"), "cancel": False}
        with self._lock:
            self._jobs[job["id"]] = job
            stale = [j for j in list(self._jobs.values()) if j["finished"]][:-8]
            for old in stale:
                self._jobs.pop(old["id"], None)


        if files:
            threading.Thread(target=self._run,
                             args=(job, files, params, fmt, overrides),
                             daemon=True).start()
        with self._lock:
            return self._public(job)

    def report(self, job_id):
        """How `job_id` is getting on, or None when there is no such job."""
        with self._lock:
            job = self._jobs.get(job_id)
            return self._public(job) if job else None

    def cancel(self, job_id):
        """Ask `job_id` to stop after the file it is on.  False if unknown."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job["cancel"] = True
            return bool(job)

    def _run(self, job, files, params, fmt, overrides):
        for path in files:
            if job["cancel"]:
                break
            job["current"] = os.path.basename(path)
            try:
                written, _ = framer.PhotoFramer.render_file(path, params, fmt,
                                                out_dir=job["out_dir"],
                                                overrides=overrides)
                job["written"].append(written)
            except Exception as exc:
                job["errors"].append(
                    f"{os.path.basename(path)}: {type(exc).__name__}: {exc}")
            job["done"] += 1
        job["current"] = ""
        job["finished"] = True

    @staticmethod
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
            if os.path.isfile(full) and os.path.splitext(entry)[1].lower() in framer.ImageSource.READABLE_EXT:
                out.append(full)
        return out


class ServerApplication:
    """Own the sessions, batch jobs and uploads for one local server."""

    def __init__(self, root=None):
        self.root = root or os.path.dirname(os.path.abspath(__file__))
        self.static = os.path.join(self.root, "static")
        self.token = secrets.token_urlsafe(16)
        self.sessions = SessionStore()
        self.jobs = JobRegistry()
        self.cache = UploadCache(os.path.join(self.root, ".tmp"))
        self.desktop = NativeDialogs()

    def create_server(self, address):
        return ThreadingHTTPServer(
            address, partial(Handler, application=self))

    def run(self):
        self.cache.sweep()
        port = int(os.environ.get("FRAMER_PORT", "8787"))
        server = None
        for candidate in range(port, port + 20):
            try:
                server = self.create_server(("127.0.0.1", candidate))
                port = candidate
                break
            except OSError:
                continue
        if server is None:
            print("no free port in range", file=sys.stderr)
            return 1
        url = f"http://127.0.0.1:{port}/?t={self.token}"
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


class Handler(BaseHTTPRequestHandler):
    server_version = "PhotoFramer"
    protocol_version = "HTTP/1.1"
    raw_body = b""
    MAX_UPLOAD = 400 * 1024 * 1024
    CONTENT_TYPES = {
        ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml",
        ".ico": "image/x-icon", ".json": "application/json",
    }
    MIME_FOR_FORMAT = {"png": "image/png", "jpeg": "image/jpeg", "tiff": "image/tiff"}

    def __init__(self, *args, application=None, **kwargs):
        self.app = application if application is not None else APPLICATION
        super().__init__(*args, **kwargs)

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
        if length > self.MAX_UPLOAD:
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
        return secrets.compare_digest(supplied, self.app.token)

    # -- routing ----------------------------------------------------------
    def do_GET(self):
        route = urlparse(self.path)
        query = parse_qs(route.query)
        if route.path in ("/", "/index.html"):
            return self._file(os.path.join(self.app.static, "index.html"),
                              inject=b'"__TOKEN__"', value=json.dumps(self.app.token).encode())
        if route.path.startswith("/static/"):
            name = os.path.basename(route.path)
            return self._file(os.path.join(self.app.static, name))
        if route.path == "/favicon.ico":
            return self._send(204)
        if not self._authorised(query):
            return self._json({"error": "unauthorised"}, 403)
        if route.path == "/api/pick":
            path, error = self.app.desktop.pick((query.get("mode") or ["file"])[0])
            if error:
                return self._json({"error": error}, 500)
            return self._json({"path": path})
        if route.path == "/api/batch":
            report = self.app.jobs.report((query.get("job") or [""])[0])
            if report is None:
                return self._json({"error": "unknown job"}, 404)
            return self._json(report)
        if route.path == "/api/scan":
            folder = (query.get("folder") or [""])[0]
            files = self.app.jobs.list_images(folder)
            return self._json({"count": len(files),
                               "names": [os.path.basename(f) for f in files[:200]]})
        return self._json({"error": "not found"}, 404)

    def _file(self, path, inject=None, value=None):
        if not os.path.isfile(path) or not os.path.abspath(path).startswith(self.app.static):
            return self._json({"error": "not found"}, 404)
        with open(path, "rb") as handle:
            body = handle.read()
        if inject:
            body = body.replace(inject, value)
        ext = os.path.splitext(path)[1].lower()
        self._send(200, body, self.CONTENT_TYPES.get(ext, "application/octet-stream"))

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
        self._json(self.app.sessions.summary(self.app.sessions.open(data.get("path", ""))))

    def _upload(self):
        target = self.app.cache.stage(
            self.raw_body, self.headers.get("X-Filename"))
        summary = self.app.sessions.summary(self.app.sessions.open(target))
        summary["temporary"] = True
        self._json(summary)

    def _preview(self):
        data = self._payload()
        payload, headers = self.app.sessions.preview(
            data.get("id", ""), Params.from_dict(data.get("params")),
            data.get("overrides"))
        self._send(200, payload, "image/jpeg", headers)

    def _export(self):
        """Render at full resolution and hand the bytes to the browser.

        The browser saves them wherever downloads go, so the tool never has
        to ask for a destination.  A Blob download is a byte-for-byte copy --
        nothing on the way re-encodes the image.
        """
        data = self._payload()
        session = self.app.sessions.get(data.get("id", ""))
        params = Params.from_dict(data.get("params"))
        fmt = (data.get("format") or "png").lower()
        started = time.time()
        payload, size = framer.PhotoFramer.render_bytes(session["path"], params, fmt,
                                            overrides=data.get("overrides") or {})
        name = framer.ImageExporter.output_name(session["path"], fmt)
        quoted = quote(name, safe="")
        self._send(200, payload, self.MIME_FOR_FORMAT.get(fmt, "application/octet-stream"), {
            "Content-Disposition": f"attachment; filename=\"{self._ascii_name(name)}\"; "
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
        job = self.app.jobs.start(folder, Params.from_dict(data.get("params")),
                         (data.get("format") or "png").lower(), out_dir,
                         data.get("overrides") or {})
        self._json(job)

    def _cancel(self):
        self._json({"ok": self.app.jobs.cancel(self._payload().get("job", ""))})

    def _reveal(self):
        path = os.path.abspath(os.path.expanduser(self._payload().get("path", "")))
        self.app.desktop.reveal(path)
        self._json({"ok": True})

    def _defaults(self):
        self._json({"params": Params().to_dict()})

    @staticmethod
    def _ascii_name(name):
        """A plain-ASCII fallback for the Content-Disposition filename."""
        stripped = name.encode("ascii", "ignore").decode().replace('"', "")
        return stripped or "framed.png"


# The command-line app also backs the original Handler and module-level API.
APPLICATION = ServerApplication()
HERE = APPLICATION.root
STATIC = APPLICATION.static
TOKEN = APPLICATION.token
SESSIONS = APPLICATION.sessions
JOBS = APPLICATION.jobs
CACHE_DIR = APPLICATION.cache.directory
STAGED_NAME = UploadCache.STAGED_NAME
MAX_UPLOAD = Handler.MAX_UPLOAD
MAX_SESSIONS = SessionStore.MAX_SESSIONS
PREVIEW_LONG_SIDE = SessionStore.PREVIEW_LONG_SIDE
CONTENT_TYPES = Handler.CONTENT_TYPES
MIME_FOR_FORMAT = Handler.MIME_FOR_FORMAT
session_summary = SessionStore.summary
native_pick = NativeDialogs.pick
reveal = NativeDialogs.reveal
sweep_cache = APPLICATION.cache.sweep
list_images = JobRegistry.list_images
_ascii_name = Handler._ascii_name
main = APPLICATION.run


if __name__ == "__main__":
    sys.exit(main())
