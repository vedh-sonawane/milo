"""Grab one photo from the ESP32 camera, describe it with a local vision model, store it in SQLite."""

import base64
import io
import json
import os
import socket
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import urlsplit

import requests

# 127.0.0.1, not localhost: on Windows "localhost" tries IPv6 first and Ollama only listens on IPv4,
# which added ~2 s to every single request.
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
ASKING = threading.Event()  # set while a question is being answered; describing pauses so it goes first
# One model request at a time. On integrated graphics two at once run at half speed each, and the
# vision model crashed when the answering model ran alongside it.
GPU = threading.RLock()
MODEL = "qwen2.5vl:7b"
NUM_CTX = 4096  # every call to MODEL uses the same size; a different size makes Ollama reload the model


class Interrupted(Exception):
    """Describing was stopped so a question could use the model; try the photo again later."""
DB_PATH = "memory.db"
IMAGE_PATH = "last_capture.jpg"

PROMPT = """Describe this photo, taken from my own point of view, as a memory record.
Reply with ONLY a JSON object with exactly these keys:
{
  "summary": "one or two sentences describing the scene",
  "location": "exactly one of: bedroom, bathroom, kitchen, living room, dining room, office, hallway, classroom, garage, outside, car, store, other",
  "objects": [{"name": "object name", "where": "where it is in the scene"}],
  "text_seen": ["each piece of text visible, copied exactly"],
  "dates": [{"date": "a date that appears in the scene", "about": "what the date refers to"}],
  "activity": "what I appear to be doing"
}
Rules:
- Only list objects you can clearly see. Do not invent objects that might be there.
- Only include text you can read clearly. Do NOT guess blurry, partial, or tiny text; leave it out.
- Only include dates that are actually visible in the image.
- Use empty lists when nothing applies."""


def camera_url_from_args():
    camera_ip = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("CAMERA_IP")
    if not camera_ip:
        sys.exit(f"Usage: python {os.path.basename(sys.argv[0])} <camera-ip>"
                 "   (or set the CAMERA_IP environment variable)")
    return f"http://{camera_ip}/capture"


# Finding the camera. Windows looks up ".local" names (milo.local) unreliably: it works sometimes and
# fails other times even with the Pi online. So: name lookup first, then the last address that worked
# (kept in memory.db across restarts), then a search of the local network for a camera.
_resolved = {}                                   # host -> IPv4 in use
_find_lock = threading.Lock()
_search = {"next_try": 0.0, "wait": 2.0}         # network search backs off 2 s, 4 s ... 60 s while none is found


def by_ip(url):
    """The URL with the camera's host name replaced by its IPv4 address. Raises OSError if not found."""
    parts = urlsplit(url)
    host = parts.hostname
    if host not in _resolved:
        with _find_lock:
            if host not in _resolved:
                _resolved[host] = _find(host, parts.port or 80)
    return url.replace(host, _resolved[host], 1)


def _find(host, port):
    try:  # unrestricted getaddrinfo: on Windows, gethostbyname and IPv4-only lookups always fail for .local
        return next(a[4][0] for a in socket.getaddrinfo(host, None) if a[0] == socket.AF_INET)
    except (OSError, StopIteration):
        pass
    saved = _setting(f"address:{host}")
    if saved and _port_open(saved, port):
        return saved
    if time.monotonic() >= _search["next_try"]:
        found = _search_network(port)
        if found:
            _search["wait"] = 2.0
            return found
        _search["next_try"] = time.monotonic() + _search["wait"]
        _search["wait"] = min(_search["wait"] * 2, 60.0)
    raise OSError(f"can't find the camera at {host}")


def _port_open(ip, port):
    try:
        with socket.create_connection((ip, port), timeout=0.5):
            return True
    except OSError:
        return False


def _search_network(port):
    """A camera on this PC's network (home networks use one /24 block): try every address at the camera's
    port and keep the first whose /capture returns an image. About 1-2 s."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("192.0.2.1", 9))  # documentation-only address: nothing is sent, it just picks our interface
        prefix = s.getsockname()[0].rsplit(".", 1)[0]
    addresses = [f"{prefix}.{i}" for i in range(1, 255)]
    with ThreadPoolExecutor(max_workers=64) as pool:
        listening = [ip for ip, ok in zip(addresses, pool.map(lambda ip: _port_open(ip, port), addresses)) if ok]
    for ip in listening:
        try:
            resp = requests.get(f"http://{ip}:{port}/capture", timeout=5)
            if resp.ok and resp.headers.get("Content-Type", "").startswith("image/"):
                return ip
        except requests.RequestException:
            pass
    return None


def _setting(key, value=None):
    """Read (value=None) or store a small setting in memory.db."""
    with connect() as db:
        db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        if value is None:
            row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return row[0] if row else None
        db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))


def capture(camera_url):
    try:
        resp = requests.get(by_ip(camera_url), timeout=20)
    except (requests.RequestException, OSError):
        _resolved.clear()  # the camera may have a new address; find it again next time
        raise
    host = urlsplit(camera_url).hostname
    if host in _resolved and _setting(f"address:{host}") != _resolved[host]:
        _setting(f"address:{host}", _resolved[host])  # remember it for when name lookup fails
    resp.raise_for_status()
    with open(IMAGE_PATH, "wb") as f:
        f.write(resp.content)
    return resp.content


def describe(jpeg_bytes, interruptible=False):
    """The vision model's JSON description of a photo. With interruptible=True, raises Interrupted as soon
    as a question is asked (closing the stream makes Ollama stop at once)."""
    with GPU:
        if interruptible and ASKING.is_set():
            raise Interrupted()
        resp = requests.post(
            OLLAMA_URL,
            json={
                "model": MODEL,
                "format": "json",
                "stream": True,
                # num_predict: a safety cap; a model stuck repeating text would otherwise hold the queue for minutes
                "options": {"num_ctx": NUM_CTX, "num_predict": 800},
                "messages": [{
                    "role": "user",
                    "content": PROMPT,
                    "images": [base64.b64encode(jpeg_bytes).decode("ascii")],
                }],
            },
            stream=True,
            timeout=300,
        )
        with resp:
            resp.raise_for_status()
            parts = []
            for line in resp.iter_lines():
                if interruptible and ASKING.is_set():
                    raise Interrupted()
                if line:
                    chunk = json.loads(line)
                    parts.append(chunk.get("message", {}).get("content", ""))
                    if chunk.get("done"):
                        break
    return loads_lenient("".join(parts))


def loads_lenient(text):
    """JSON from a model, recovering what was written if the answer was cut off: drop the unfinished
    last item and close the open brackets (a cut-off description used to be thrown away entirely)."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    stack, in_string, escaped, cuts = [], False, False, []
    for i, ch in enumerate(text):
        if in_string:
            escaped = not escaped and ch == "\\"
            if ch == '"' and not escaped:
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack:
            stack.pop()
        elif ch == ",":
            cuts.append((i, list(stack)))  # everything before this comma is complete
    for i, open_brackets in reversed(cuts):
        try:
            return json.loads(text[:i] + "".join(reversed(open_brackets)))
        except json.JSONDecodeError:
            continue
    raise json.JSONDecodeError("could not recover a cut-off answer", text, 0)


def background_json(prompt, num_predict=400):
    """Ask the vision model (as a text model) for JSON, for background work. Stops at once with Interrupted
    when a question is asked, so questions never wait behind background work."""
    with GPU:
        if ASKING.is_set():
            raise Interrupted()
        resp = requests.post(OLLAMA_URL, json={
            "model": MODEL, "stream": True, "format": "json",
            "options": {"num_ctx": NUM_CTX, "temperature": 0.1, "num_predict": num_predict},
            "messages": [{"role": "user", "content": prompt}],
        }, stream=True, timeout=600)
        content = ""
        with resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if ASKING.is_set():
                    raise Interrupted()
                if line:
                    part = json.loads(line)
                    content += part.get("message", {}).get("content", "")
                    if part.get("done"):
                        break
    return loads_lenient(content)


def connect():
    db = sqlite3.connect(DB_PATH)
    db.execute("""
        CREATE TABLE IF NOT EXISTS observations (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            summary   TEXT,
            location  TEXT,
            objects   TEXT,
            text_seen TEXT,
            dates     TEXT,
            activity  TEXT
        )""")
    if "described" not in {row[1] for row in db.execute("PRAGMA table_info(observations)")}:
        # 0 while a memory holds only its instant record (time + text read); 1 once the vision model described it
        db.execute("ALTER TABLE observations ADD COLUMN described INTEGER NOT NULL DEFAULT 1")
    return db


# ---- instant record: text read on the processor while the vision model is busy on the graphics chip ----

_ocr = None
_ocr_lock = threading.Lock()


def read_text(jpeg_bytes):
    """Text in a photo, read locally without the GPU, so it never waits for the vision model.
    Windows' built-in OCR: ~20 ms per photo, keeps spaces. Fallback (e.g. not on Windows): RapidOCR,
    ~1 s per photo with text on this laptop."""
    global _ocr
    try:
        import winocr
        from PIL import Image
        result = winocr.recognize_pil_sync(Image.open(io.BytesIO(jpeg_bytes)), "en")
        return [line["text"] for line in result["lines"] if line["text"].strip()]
    except ImportError:
        pass
    with _ocr_lock:
        if _ocr is None:
            from rapidocr_onnxruntime import RapidOCR
            _ocr = RapidOCR()
        result, _ = _ocr(jpeg_bytes)
    return [r[1] for r in (result or []) if r[1].strip()]


def record(taken_at, text_seen):
    """Store a memory instantly, before the vision model has looked at it. Returns (id, timestamp)."""
    timestamp = taken_at.isoformat(timespec="seconds")
    with connect() as db:
        cur = db.execute(
            "INSERT INTO observations (timestamp, summary, location, objects, text_seen, dates, activity, described)"
            " VALUES (?, '', '', '[]', ?, '[]', '', 0)", (timestamp, json.dumps(text_seen)))
        return cur.lastrowid, timestamp


def complete(row_id, obs):
    """Fill in an instant record with the vision model's description. Text read by OCR is kept; text the
    model read that OCR didn't is added."""
    with connect() as db:
        ocr_text = json.loads(db.execute("SELECT text_seen FROM observations WHERE id = ?", (row_id,)).fetchone()[0] or "[]")
        seen = {t.lower().replace(" ", "") for t in ocr_text}
        merged = ocr_text + [t for t in map(str, obs.get("text_seen", [])) if t.lower().replace(" ", "") not in seen]
        db.execute(
            "UPDATE observations SET summary = ?, location = ?, objects = ?, text_seen = ?, dates = ?, activity = ?,"
            " described = 1 WHERE id = ?",
            (obs.get("summary", ""), obs.get("location", ""), json.dumps(obs.get("objects", [])),
             json.dumps(merged), json.dumps(obs.get("dates", [])), obs.get("activity", ""), row_id))


def save(obs, taken_at=None):
    """Store one observation. taken_at is when the photo was captured (defaults to now)."""
    timestamp = (taken_at or datetime.now()).isoformat(timespec="seconds")
    with connect() as db:
        cur = db.execute(
            "INSERT INTO observations (timestamp, summary, location, objects, text_seen, dates, activity)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                timestamp,
                obs.get("summary", ""),
                obs.get("location", ""),
                json.dumps(obs.get("objects", [])),
                json.dumps(obs.get("text_seen", [])),
                json.dumps(obs.get("dates", [])),
                obs.get("activity", ""),
            ),
        )
        return cur.lastrowid, timestamp


def main():
    jpeg = capture(camera_url_from_args())
    print(f"Captured {len(jpeg)} bytes -> {IMAGE_PATH}")
    obs = describe(jpeg)
    row_id, timestamp = save(obs)
    print(f"Saved observation #{row_id} at {timestamp}:")
    print(json.dumps(obs, indent=2))


if __name__ == "__main__":
    main()
