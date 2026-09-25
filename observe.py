"""Grab one photo from the ESP32 camera, describe it with a local vision model, store it in SQLite."""

import base64
import json
import os
import sqlite3
import sys
from datetime import datetime

import requests

CAMERA_URL = None  # set in main() from the command line or the CAMERA_IP env var
OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen2.5vl:7b"
DB_PATH = "memory.db"
IMAGE_PATH = "last_capture.jpg"

PROMPT = """Describe this photo, taken from a camera worn on my shoulder, as a memory record.
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
- Only include text you can read clearly. Do NOT guess blurry, partial, or tiny text; leave it out.
- Only include dates that are actually visible in the image.
- Use empty lists when nothing applies."""


def capture():
    resp = requests.get(CAMERA_URL, timeout=20)
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


def save(obs):
    timestamp = datetime.now().isoformat(timespec="seconds")
    with sqlite3.connect(DB_PATH) as db:
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
    global CAMERA_URL
    camera_ip = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("CAMERA_IP")
    if not camera_ip:
        sys.exit("Usage: python observe.py <camera-ip>   (or set the CAMERA_IP environment variable)")
    CAMERA_URL = f"http://{camera_ip}/capture"

    jpeg = capture()
    print(f"Captured {len(jpeg)} bytes -> {IMAGE_PATH}")
    obs = describe(jpeg)
    row_id, timestamp = save(obs)
    print(f"Saved observation #{row_id} at {timestamp}:")
    print(json.dumps(obs, indent=2))


if __name__ == "__main__":
    main()
