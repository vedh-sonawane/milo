"""Check that scenes are recorded instantly with their text, and described later (no hardware, throwaway DB).

Run: python tests/instant_record_eval.py
"""

import io
import json
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import observe  # noqa: E402

observe.DB_PATH = str(Path(tempfile.mkdtemp()) / "test.db")  # never touch the real memory.db
import watch  # noqa: E402


def poster(lines, color):
    img = Image.new("RGB", (640, 480), color)
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 34)
    except OSError:
        font = ImageFont.load_default()
    for i, line in enumerate(lines):
        d.text((50, 100 + 70 * i), line, fill=(20, 20, 20), font=font)
    for i in range(12):  # some texture so the frame isn't blank
        d.rectangle([20 + 50 * i, 400, 45 + 50 * i, 460], fill=(40 * (i % 6), 90, 160))
    b = io.BytesIO()
    img.save(b, "JPEG", quality=95)
    return b.getvalue()


def main():
    observe.read_text(poster(["warm up"], (240, 240, 240)))  # load the text reader once, like a running Milo
    w = watch.Watcher("http://test", log=lambda m: None)
    scenes = [["ROBOTICS CLUB", "Meeting Thu Oct 8"], ["SCIENCE FAIR", "Friday Oct 16"], ["Library", "Quiet please"]]
    fed = []
    for i, lines in enumerate(scenes):  # walking past three posters, one second each
        jpeg = poster(lines, (235 - 30 * i, 230, 220))
        fed.append(time.time())
        w._handle_frame(jpeg, float(i))
    w._handle_frame(poster(["end"], (90, 90, 90)), 10.0)  # walking on: the last poster's scene ends

    deadline = time.time() + 15
    rows = []
    while time.time() < deadline:
        with observe.connect() as db:
            rows = db.execute("SELECT id, text_seen, described FROM observations ORDER BY id").fetchall()
        if len(rows) >= len(scenes):
            break
        time.sleep(0.05)
    print("instant records:")
    for (row_id, text, described), lines in zip(rows, scenes):
        print(f"  #{row_id} text read: {json.loads(text)}  (poster said {lines})")
    print(f"  all {len(scenes)} recorded {time.time() - fed[0]:.1f}s after the first poster was seen"
          f" (Windows OCR ~20 ms per photo)")

    print("now describing with the vision model (slow path)...")
    w.start_describing_only = True
    import threading
    threading.Thread(target=w._describe_loop, daemon=True).start()
    first = rows[0][0]
    t = time.time()
    while time.time() - t < 180:
        with observe.connect() as db:
            summary, described, text = db.execute(
                "SELECT summary, described, text_seen FROM observations WHERE id = ?", (first,)).fetchone()
        if described:
            print(f"  #{first} described after {time.time() - t:.0f}s: {summary[:100]}")
            print(f"  text kept after describing: {json.loads(text)}")
            break
        time.sleep(1)
    else:
        print("  not described within 3 minutes")


if __name__ == "__main__":
    main()
