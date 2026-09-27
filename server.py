"""Milo web app: watch, browse memories and ask questions from the browser.

Run: python server.py [camera-ip]   (or set CAMERA_IP), then open http://localhost:8000
     python server.py --lan          also reachable from your phone on the same Wi-Fi
"""

import argparse
import os
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

import requests
import uvicorn
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

import ask
import world
from observe import connect
from watch import Watcher

WEB_DIR = Path(__file__).parent / "web"

app = FastAPI(title="Milo")
watcher = None  # created in main()
_live_supported = None  # None until the camera has answered once


def live_supported():
    """True if the camera can serve a live stream while Milo captures (the USB webcam can, the ESP32 can't)."""
    global _live_supported
    if _live_supported is None:
        try:
            resp = requests.get(watcher.camera_url, timeout=5)
            _live_supported = resp.headers.get("X-Camera-Viewers") == "many"
        except requests.RequestException:
            return False  # camera offline; ask again next time
    return _live_supported


class Question(BaseModel):
    question: str
    hours: float = 24


@app.get("/")
def index():
    # no-cache: browsers must fetch the latest page after an update instead of reusing an old copy
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/status")
def status():
    return {**watcher.status(), "live": live_supported()}


@app.get("/api/live")
def live_stream():
    """Relay the camera's live MJPEG stream, so the page (and a phone with --lan) can show smooth video."""
    if not live_supported():
        raise HTTPException(404, "This camera can't stream while Milo is watching")
    stream_url = watcher.camera_url.rsplit("/capture", 1)[0] + "/"
    try:
        upstream = requests.get(stream_url, stream=True, timeout=(5, 10))
    except requests.RequestException as e:
        raise HTTPException(502, f"Camera stream unavailable: {e}")

    def relay():
        try:
            yield from upstream.iter_content(16384)
        finally:
            upstream.close()

    return StreamingResponse(relay(), media_type=upstream.headers.get("Content-Type"))


@app.post("/api/watch/start")
def start_watching():
    watcher.start()
    return watcher.status()


@app.post("/api/watch/stop")
def stop_watching():
    watcher.stop()
    return watcher.status()


@app.get("/api/latest.jpg")
def latest_frame():
    """Most recent camera frame, kept in memory only (for aiming the camera)."""
    if not watcher.latest_jpeg:
        raise HTTPException(404, "No frame yet")
    return Response(watcher.latest_jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/memories")
def memories(hours: float = 24):
    return list(reversed(ask.load_memories(hours)))  # newest first for the timeline


@app.delete("/api/memories/{memory_id}")
def delete_memory(memory_id: int):
    with connect() as db:
        deleted = db.execute("DELETE FROM observations WHERE id = ?", (memory_id,)).rowcount
    if not deleted:
        raise HTTPException(404, "No such memory")
    world.forget(memory_id)
    return {"deleted": memory_id}


@app.get("/api/things")
def things(hours: float = 24):
    since = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
    return world.things(since)


@app.post("/api/ask")
def ask_milo(q: Question):
    if not q.question.strip():
        raise HTTPException(400, "Empty question")
    return ask.answer(q.question.strip(), q.hours)


def main():
    global watcher
    parser = argparse.ArgumentParser(description="Milo web app")
    parser.add_argument("camera_ip", nargs="?", default=os.environ.get("CAMERA_IP"))
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--lan", action="store_true",
                        help="listen on all interfaces so other devices on your Wi-Fi can open it")
    parser.add_argument("--paused", action="store_true", help="start without watching")
    args = parser.parse_args()
    if not args.camera_ip:
        sys.exit("Usage: python server.py <camera-ip>   (or set the CAMERA_IP environment variable)")

    watcher = Watcher(f"http://{args.camera_ip}/capture")
    if not args.paused:
        watcher.start()
    # Bring the world model up to date with any memories saved while it wasn't running.
    threading.Thread(target=world.catch_up, daemon=True).start()

    host = "0.0.0.0" if args.lan else "127.0.0.1"
    if args.lan:
        print("LAN mode: anyone on your Wi-Fi can open Milo and see your memories.")
    print(f"Milo is at http://localhost:{args.port}")
    uvicorn.run(app, host=host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
