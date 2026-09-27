"""Grab one photo from the ESP32 camera, describe it with a local vision model, store it in SQLite."""

import base64
import json
import os
import sqlite3
import sys
import threading
from datetime import datetime

import requests

OLLAMA_URL = "http://localhost:11434/api/chat"
ASKING = threading.Event()  # set while a question is being answered; the watcher waits so it goes first
MODEL = "qwen2.5vl:7b"
DB_PATH = "memory.db"
IMAGE_PATH = "last_capture.jpg"

PROMPT = """Describe this photo, taken from my own point of view, as a memory record.
Reply with ONLY a JSON object with exactly these keys:
{
  "summary": "one or two sentences describing the scene",
  "location": "the kind of place this is (e.g. kitchen, office desk, street)",
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


def capture(camera_url):
    resp = requests.get(camera_url, timeout=20)
    resp.raise_for_status()
    with open(IMAGE_PATH, "wb") as f:
        f.write(resp.content)
    return resp.content


def describe(jpeg_bytes):
    resp = requests.post(
        OLLAMA_URL,
        json={
            "model": MODEL,
            "format": "json",
            "stream": False,
            "messages": [{
                "role": "user",
                "content": PROMPT,
                "images": [base64.b64encode(jpeg_bytes).decode("ascii")],
            }],
        },
        timeout=300,
    )
    resp.raise_for_status()
    return json.loads(resp.json()["message"]["content"])


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
    return db


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
