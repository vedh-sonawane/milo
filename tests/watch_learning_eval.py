"""Check the watcher's learned thresholds on simulated camera frames (no hardware, throwaway database).

Run: python tests/watch_learning_eval.py
"""

import io
import random
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import observe  # noqa: E402

observe.DB_PATH = str(Path(tempfile.mkdtemp()) / "test.db")  # never touch the real memory.db
import watch  # noqa: E402


def scene(seed):
    r = random.Random(seed)
    img = Image.new("RGB", (640, 480), tuple(r.randrange(256) for _ in range(3)))
    d = ImageDraw.Draw(img)
    for _ in range(40):
        x, y = r.randrange(600), r.randrange(440)
        d.rectangle([x, y, x + r.randrange(20, 200), y + r.randrange(20, 150)], fill=tuple(r.randrange(256) for _ in range(3)))
    return img


def wobble(img, r):
    """The same view a moment later: a few pixels of movement and a little brightness change."""
    dx, dy = r.randint(-4, 4), r.randint(-4, 4)
    moved = img.transform(img.size, Image.AFFINE, (1, 0, dx, 0, 1, dy))
    return ImageEnhance.Brightness(moved).enhance(r.uniform(0.95, 1.05))


def jpeg(img):
    b = io.BytesIO()
    img.save(b, "JPEG", quality=90)
    return b.getvalue()


def feed(w, frames, t0):
    t = t0
    for img in frames:
        w._handle_frame(jpeg(img), t)
        t += 1.0
    return t


def evening(r, seed0):
    """Sitting still (with wobble), walking past new views, motion blur, and covering the lens."""
    frames, seed = [], seed0
    for _ in range(6):
        base = scene(seed); seed += 1
        frames += [wobble(base, r) for _ in range(r.randint(20, 40))]          # a stay
        for _ in range(r.randint(4, 8)):                                          # a walk
            frames.append(scene(seed)); seed += 1
            if r.random() < 0.6:
                frames.append(scene(seed - 1).filter(ImageFilter.GaussianBlur(r.uniform(4, 8))))  # motion blur
        if r.random() < 0.5:
            frames += [Image.new("RGB", (640, 480), (8, 8, 8))] * 3            # lens covered
    return frames


def main():
    r = random.Random(1)
    w = watch.Watcher("http://test", log=lambda m: None)

    # 1. Before learning, nothing may be thrown away.
    t = feed(w, [scene(9000).filter(ImageFilter.GaussianBlur(6)) for _ in range(5)], 0.0)
    print(f"1. before learning: {w.counts['blurry']} of 5 blurry frames thrown away (expect 0)")

    # 2. Learn from a realistic evening.
    t = feed(w, evening(r, 100), t)
    status = w.status()["learned"]
    print(f"2. learned from an evening: {status}")

    # 3. Decisions on a new evening, compared with what each frame really was.
    dark_kept = dark_total = sharp_dropped = sharp_total = 0
    unusable_below = w.blur.cutoff()
    for img in evening(random.Random(2), 5000):
        s = watch.sharpness(img)
        dark = img.getextrema()[0][1] < 20      # scoring only: the generated "lens covered" frames
        blurred = not dark and s < 300          # scoring only: generated motion blur (kept; per-scene sharpest wins)
        dropped = unusable_below is not None and s < unusable_below
        dark_total += dark
        dark_kept += dark and not dropped
        sharp_total += not dark and not blurred
        sharp_dropped += (not dark and not blurred) and dropped
    print(f"3. new evening: covered-lens frames kept {dark_kept}/{dark_total} (expect 0), "
          f"sharp frames thrown away {sharp_dropped}/{sharp_total} (expect 0)")
    queued_before = w.counts["queued"]
    feed(w, [wobble(scene(7000), r) for _ in range(90)], t + 1000)
    print(f"   a 90 s stay at one view queued {w.counts['queued'] - queued_before} memory (expect 1-2)")

    # 4. A fresh learner that only ever sees still, sharp frames must not start throwing frames away.
    observe.DB_PATH = str(Path(tempfile.mkdtemp()) / "test2.db")
    still = watch.Watcher("http://test", log=lambda m: None)
    base = scene(42)
    feed(still, [wobble(base, r) for _ in range(300)], 0.0)
    print(f"4. only still, sharp frames: blur cutoff {still.blur.cutoff()} (expect None), "
          f"frames thrown away {still.counts['blurry']} (expect 0)")


if __name__ == "__main__":
    main()
