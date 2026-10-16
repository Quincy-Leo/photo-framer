# SPDX-FileCopyrightText: 2026 QuincyLeo
# SPDX-License-Identifier: GPL-3.0-or-later
"""End-to-end check of the HTTP layer.  Starts a server on a spare port,
drives every endpoint, and asserts on the results.

Run:  python3 tests/test_server.py [optional/photo.jpg]
"""

import io
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import server as srv
from http.server import ThreadingHTTPServer

PASS, FAIL = [], []
BASE = ""


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))


def call(path, body=None, method=None, token=srv.TOKEN, origin=None, raw=None, filename=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    request = urllib.request.Request(BASE + path, data=data,
                                     method=method or ("POST" if data is not None else "GET"))
    if token:
        request.add_header("X-Framer-Token", token)
    if origin:
        request.add_header("Origin", origin)
    if raw is not None:
        request.add_header("Content-Type", "application/octet-stream")
        request.add_header("X-Filename", filename or "dropped.jpg")
    elif data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = response.read()
            ctype = response.headers.get("Content-Type", "")
            parsed = json.loads(payload) if "json" in ctype else payload
            return response.status, parsed, dict(response.headers)
    except urllib.error.HTTPError as err:
        return err.code, err.read(), dict(err.headers)


def make_photo(path):
    from PIL import Image, ImageCms
    from PIL.TiffImagePlugin import IFDRational
    img = Image.new("RGB", (1400, 933))
    img.putdata([((x * 3) % 256, (y * 5) % 256, (x * y) % 256)
                 for y in range(933) for x in range(1400)])
    exif = Image.Exif()
    exif[0x010F], exif[0x0110] = "FUJIFILM", "FUJIFILM X-T5"
    sub = exif.get_ifd(0x8769)
    sub[0x829A], sub[0x829D] = IFDRational(1, 250), IFDRational(28, 10)
    sub[0x920A], sub[0x8827] = IFDRational(35, 1), 160
    sub[0x9003] = "2025:09:01 08:12:00"
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    img.save(path, "JPEG", quality=95, exif=exif.tobytes(), icc_profile=icc)


def main():
    global BASE
    import tempfile
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    BASE = f"http://127.0.0.1:{httpd.server_address[1]}"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    time.sleep(0.2)

    tmp = tempfile.mkdtemp(prefix="framer-http-")
    photo = sys.argv[1] if len(sys.argv) > 1 else os.path.join(tmp, "a.jpg")
    if len(sys.argv) <= 1:
        make_photo(photo)

    # ---- auth ----
    check("no token is rejected", call("/api/defaults", {}, token="")[0] == 403)
    check("wrong token is rejected", call("/api/defaults", {}, token="nope")[0] == 403)
    check("foreign Origin is rejected",
          call("/api/defaults", {}, origin="https://evil.example")[0] == 403)
    status, data, _ = call("/api/defaults", {})
    check("defaults served", status == 200 and "border" in data["params"]
          and data["params"]["title_auto"] is True, repr(data)[:120])
    params = data["params"]

    # ---- static ----
    status, body, headers = call("/")
    check("index page served with the token injected",
          status == 200 and srv.TOKEN.encode() in body
          and b"__TOKEN__" not in body)
    check("static assets served", call("/static/app.js")[0] == 200
          and call("/static/style.css")[0] == 200)
    check("path traversal blocked", call("/static/../server.py")[0] == 404)
    check("favicon answered without auth noise", call("/favicon.ico")[0] == 204)

    # ---- keep-alive framing ----
    # A handler that forgets to drain the request body leaves bytes in the
    # socket, and the browser's next request on that connection gets parsed
    # as garbage.  Reuse one connection for three POSTs to catch that.
    import http.client
    conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=30)
    codes = []
    for _ in range(3):
        conn.request("POST", "/api/defaults", body=b"{}",
                     headers={"X-Framer-Token": srv.TOKEN, "Content-Type": "application/json"})
        response = conn.getresponse()
        codes.append(response.status)
        response.read()
    conn.close()
    check("keep-alive connection survives repeated POSTs", codes == [200, 200, 200], repr(codes))

    # ---- load ----
    status, summary, _ = call("/api/load", {"path": photo})
    check("photo loaded", status == 200 and summary["width"] > 0, repr(status))
    if status != 200:
        print(summary)
        return 1
    check("EXIF surfaced to the UI", bool(summary["values"]["brand"])
          and summary["auto_title"] == "FUJIFILM  X-T5"
          and "|" in summary["auto_params"],
          f'{summary["auto_title"]!r} / {summary["auto_params"]!r}')
    check("missing file reports 404", call("/api/load", {"path": "/nope/x.jpg"})[0] == 404)
    session_id = summary["id"]

    # ---- preview ----
    started = time.time()
    status, blob, headers = call("/api/preview", {"id": session_id, "params": params})
    elapsed = time.time() - started
    check("preview returns a JPEG", status == 200 and blob[:2] == b"\xff\xd8",
          f"{len(blob)} bytes in {elapsed * 1000:.0f}ms")
    check("preview reports the full-resolution canvas size",
          "x" in headers.get("X-Canvas", ""), headers.get("X-Canvas", ""))
    check("preview reports where auto text lightness landed",
          headers.get("X-Text-Light", "").isdigit(), headers.get("X-Text-Light", ""))
    check("stale session reports 404",
          call("/api/preview", {"id": "deadbeef", "params": params})[0] == 404)

    # ---- preview honours parameter changes ----
    _, _, headers2 = call("/api/preview", {"id": session_id,
                                           "params": dict(params, border=3.0)})
    check("the border multiplier changes the canvas",
          headers2.get("X-Canvas") != headers.get("X-Canvas"),
          f'{headers.get("X-Canvas")} -> {headers2.get("X-Canvas")}')
    _, _, headers3 = call("/api/preview", {"id": session_id,
                                           "params": dict(params, one_line=True)})
    check("one-line mode shrinks the canvas",
          headers3.get("X-Canvas") != headers.get("X-Canvas"),
          f'{headers.get("X-Canvas")} -> {headers3.get("X-Canvas")}')

    # ---- export streams the bytes back for the browser to download ----
    from PIL import Image
    import framer
    original = Image.open(photo)
    original.load()
    short = min(original.size)
    pad = round(params["border"] * framer.BASE_PAD / 100 * short)
    # square corners isolate the question "did the photo pixels survive?"
    # from the corner arcs, which have to blend with the backdrop by design.
    square = dict(params, radius=0.0, shadow=0.0)
    out_dir = os.path.join(tmp, "out")
    os.makedirs(out_dir, exist_ok=True)

    for fmt, magic in (("png", b"\x89PNG"), ("jpeg", b"\xff\xd8")):
        status, payload, head = call("/api/export", {
            "id": session_id, "params": square, "format": fmt,
        })
        check(f"{fmt} export returns image bytes, not a path",
              status == 200 and isinstance(payload, bytes) and payload[:len(magic)] == magic,
              f"{len(payload) / 1e6:.1f}MB {head.get('X-Dimensions')} "
              f"{head.get('X-Seconds')}s")
        if status != 200:
            continue
        check(f"{fmt}: served as a download with a sensible filename",
              "attachment" in head.get("Content-Disposition", "")
              and head.get("X-Filename", "").endswith(
                  ".png" if fmt == "png" else ".jpg"),
              head.get("Content-Disposition", ""))

        written = os.path.join(out_dir, f"download.{fmt}")
        with open(written, "wb") as handle:
            handle.write(payload)
        saved = Image.open(written)
        saved.load()
        region = saved.crop((pad, pad, pad + original.width, pad + original.height))
        if fmt == "png":
            check("png: photo region is bit-identical to the source",
                  region.tobytes() == original.tobytes())
        else:
            worst = max(abs(a - b) for a, b in zip(region.tobytes(), original.tobytes()))
            check("jpeg: photo region within a few levels of the source", worst <= 6,
                  f"max channel deviation {worst}/255")
        check(f"{fmt}: ICC profile carried over",
              saved.info.get("icc_profile") == original.info.get("icc_profile"))
        check(f"{fmt}: camera model still in EXIF",
              "X-T5" in str(saved.getexif().get(0x0110, "")))

    # ---- with rounded corners, only the arcs may move ----
    _, payload, _ = call("/api/export", {
        "id": session_id, "params": params, "format": "png",
    })
    rounded = Image.open(io.BytesIO(payload))
    rounded.load()
    radius = int(round(params["radius"] / 100 * framer.BASE_RADIUS / 100 * short)) + 2
    got = rounded.crop((pad + radius, pad, pad + original.width - radius, pad + original.height))
    want = original.crop((radius, 0, original.width - radius, original.height))
    check("png with rounded corners: everything but the arcs is bit-identical",
          got.tobytes() == want.tobytes())

    # ---- drag & drop upload path ----
    with open(photo, "rb") as handle:
        status, dropped, _ = call("/api/upload", raw=handle.read(), filename="dropped.jpg")
    check("drag-and-drop upload opens a session",
          status == 200 and dropped.get("temporary") and dropped["width"] == original.width,
          repr(status))

    # ---- batch ----
    folder = os.path.join(tmp, "batch")
    os.makedirs(folder, exist_ok=True)
    for name in ("one.jpg", "two.jpg", "three.jpg"):
        make_photo(os.path.join(folder, name))
    open(os.path.join(folder, "notes.txt"), "w").close()
    status, scan, _ = call(f"/api/scan?folder={folder}")
    check("folder scan counts only images", status == 200 and scan["count"] == 3,
          repr(scan))

    status, job, _ = call("/api/batch", {
        "folder": folder, "params": params, "format": "png",
        "out_dir": os.path.join(tmp, "batch-out"),
    })
    check("batch job accepted", status == 200 and job["total"] == 3, repr(job))
    deadline = time.time() + 120
    while time.time() < deadline:
        _, job, _ = call(f"/api/batch?job={job['id']}")
        if job["finished"]:
            break
        time.sleep(0.2)
    check("batch finished with every file written",
          job["finished"] and len(job["written"]) == 3 and not job["errors"],
          f'{len(job["written"])} written, errors={job["errors"]}')
    check("batch output lands in the chosen folder",
          all(os.path.isfile(p) for p in job["written"]))

    status, _, _ = call("/api/batch", {"folder": "/not/a/folder", "params": params})
    check("batch rejects a bad folder", status == 404)
    check("unknown job reports 404", call("/api/batch?job=zzz")[0] == 404)

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("failed: " + ", ".join(FAIL))
    httpd.shutdown()
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
