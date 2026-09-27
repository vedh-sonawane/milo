"""Watch continuously, built for walking around, not holding still.

Two things run side by side:
- Capture (fast, every ~2 s): grab a frame, drop blurry/dark ones, and group frames into scenes.
  A scene is a run of frames that look alike; only its sharpest frame is kept.
- Describe (slow, ~15 s per photo on the GPU): the vision model works through queued scenes at its
  own pace. Each memory keeps the time its photo was taken, not the time it was described.

If you move faster than the model can keep up, the queue fills and the most redundant scenes are
dropped first, so a walk through the house still ends up as a spread of distinct memories.

Run: python watch.py <camera-ip>   (or set CAMERA_IP). Stop with Ctrl+C.
"""

import io
import threading
import time
from dataclasses import dataclass
from datetime import datetime

from PIL import Image, ImageChops, ImageFilter, ImageStat

import world
from observe import ASKING, camera_url_from_args, capture, describe, save

CAPTURE_INTERVAL = 1.0   # seconds between captures (a USB webcam capture takes ~0.2 s)
MIN_SHARPNESS = 400      # measured on the USB webcam: sharp 670-850, blurred ~220, dark or covered ~10
MIN_CHANGE = 20          # measured: brightness flicker ~8, small wobble ~16, new scene ~55
SCENE_SETTLE = 4.0       # a scene that stays this long is queued without waiting for it to end
MAX_QUIET = 600          # remember an unchanged scene again after this long, to record time spent there
RECENT_SECONDS = 120     # a scene matching one queued this recently is a look-back, not a new scene
QUEUE_LIMIT = 30         # scenes waiting for the model; beyond this the most redundant is dropped


@dataclass
class Frame:
    jpeg: bytes
    taken_at: datetime
    sharpness: float
    thumb: Image.Image


@dataclass
class Scene:
    anchor: Image.Image     # thumbnail the scene is compared against
    best: Frame             # sharpest frame so far
    started: float
    queued_at: float = 0.0  # 0 until the scene has been queued once


def sharpness(img):
    """Edge variance: high for crisp detail, low for blur, darkness or a blank wall."""
    gray = img.convert("L").resize((320, 240))
    return ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES)).var[0]


def thumbnail(img):
    return img.convert("L").resize((32, 24))


def change(a, b):
    """Mean pixel difference (0-255) between two thumbnails."""
    return ImageStat.Stat(ImageChops.difference(a, b)).mean[0]


def print_log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


class Watcher:
    def __init__(self, camera_url, log=print_log):
        self.camera_url = camera_url
        self.log = log
        self.latest_jpeg = None
        self._queue = []              # Frames waiting for the model, oldest first
        self._recent = []             # (thumb, monotonic time) of recently queued scenes
        self._scene = None
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._capture_thread = None
        self._describe_thread = None
        self._describing = False
        self.camera_online = None
        self.last_capture_at = None
        self.last_error = None
        self.last_memory = None
        self.counts = {"frames": 0, "blurry": 0, "queued": 0, "dropped": 0, "saved": 0}

    # ---- control ----

    @property
    def running(self):
        return bool(self._capture_thread and self._capture_thread.is_alive())

    def start(self):
        if self.running:
            return
        self._stop.clear()
        self._capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._capture_thread.start()
        if not (self._describe_thread and self._describe_thread.is_alive()):
            self._describe_thread = threading.Thread(target=self._describe_loop, daemon=True)
            self._describe_thread.start()
        self.log(f"Watching {self.camera_url}")

    def stop(self):
        """Stop capturing. Scenes already captured are still described."""
        self._stop.set()
        if self._capture_thread:
            self._capture_thread.join()
        self.log("Paused")

    def pending(self):
        with self._cond:
            return len(self._queue) + (1 if self._describing else 0)

    def status(self):
        return {
            "running": self.running,
            "camera_online": self.camera_online,
            "last_capture_at": self.last_capture_at,
            "last_error": self.last_error,
            "pending": self.pending(),
            "last_memory": self.last_memory,
            "counts": dict(self.counts),
        }

    # ---- capture side ----

    def _capture_loop(self):
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                jpeg = capture(self.camera_url)
                if self.camera_online is not True:
                    self.log("Camera online")
                self.camera_online, self.last_error = True, None
                self.latest_jpeg = jpeg
                self.last_capture_at = datetime.now().isoformat(timespec="seconds")
                self._handle_frame(jpeg, started)
            except Exception as e:
                if self.camera_online is not False:
                    self.log(f"Camera offline: {e.__class__.__name__}: {e}")
                self.camera_online, self.last_error = False, f"{e.__class__.__name__}: {e}"
            self._stop.wait(max(0.0, CAPTURE_INTERVAL - (time.monotonic() - started)))

        # Don't lose the scene we were in when paused.
        if self._scene and not self._scene.queued_at:
            self._enqueue(self._scene.best, "last scene before pause")
        self._scene = None

    def _handle_frame(self, jpeg, now):
        self.counts["frames"] += 1
        img = Image.open(io.BytesIO(jpeg))
        frame = Frame(jpeg, datetime.now(), sharpness(img), thumbnail(img))
        if frame.sharpness < MIN_SHARPNESS:
            self.counts["blurry"] += 1
            return

        scene = self._scene
        if scene and change(frame.thumb, scene.anchor) < MIN_CHANGE:
            if frame.sharpness > scene.best.sharpness:
                scene.best = frame
            if not scene.queued_at and now - scene.started >= SCENE_SETTLE:
                self._enqueue(scene.best, "new scene", check_recent=True)
                scene.queued_at, scene.best = now, frame
            elif scene.queued_at and now - scene.queued_at >= MAX_QUIET:
                self._enqueue(scene.best, "still here")
                scene.queued_at, scene.best = now, frame
            return

        # The view changed. A scene that ended before settling (a glance while walking) still counts.
        if scene and not scene.queued_at:
            self._enqueue(scene.best, "passing view", check_recent=True)
        self._scene = Scene(anchor=frame.thumb, best=frame, started=now)

    def _enqueue(self, frame, reason, check_recent=False):
        now = time.monotonic()
        with self._cond:
            self._recent = [(t, at) for t, at in self._recent if now - at < RECENT_SECONDS]
            if check_recent and any(change(frame.thumb, t) < MIN_CHANGE for t, _ in self._recent):
                return  # looked away and back; already have this view
            self._recent.append((frame.thumb, now))
            self._queue.append(frame)
            self.counts["queued"] += 1
            if len(self._queue) > QUEUE_LIMIT:
                self._drop_most_redundant()
            self._cond.notify()

    def _drop_most_redundant(self):
        """Drop the queued frame most similar to another queued frame (ties: the blurrier one)."""
        def redundancy(i):
            others = (change(self._queue[i].thumb, f.thumb) for j, f in enumerate(self._queue) if j != i)
            return (min(others), self._queue[i].sharpness)
        victim = min(range(len(self._queue)), key=redundancy)
        del self._queue[victim]
        self.counts["dropped"] += 1

    # ---- describe side ----

    def _describe_loop(self):
        while True:
            with self._cond:
                while not self._queue:
                    self._cond.wait()
                frame = self._queue.pop(0)
                self._describing = True
            try:
                while ASKING.is_set():  # a question is being answered: let it use the model first
                    time.sleep(0.2)
                obs = describe(frame.jpeg)
                row_id, timestamp = save(obs, frame.taken_at)
                self.counts["saved"] += 1
                self.last_memory = {"id": row_id, "timestamp": timestamp, "summary": obs.get("summary", "")}
                text = ", ".join(map(str, obs.get("text_seen", []))) or "-"
                self.log(f"#{row_id} (taken {frame.taken_at:%H:%M:%S}, {self.pending() - 1} waiting): "
                         f"{obs.get('summary', '')} | text: {text}")
                world.catch_up()  # add this memory's objects to the world model
            except Exception as e:  # bad model output or Ollama down: skip this frame, keep going
                self.log(f"Describe failed: {e.__class__.__name__}: {e}")
            finally:
                with self._cond:
                    self._describing = False


def main():
    watcher = Watcher(camera_url_from_args())
    watcher.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    watcher.stop()
    try:
        while watcher.pending():
            print_log(f"Finishing {watcher.pending()} captured scene(s). Ctrl+C again to quit now.")
            time.sleep(5)
    except KeyboardInterrupt:
        pass
    print_log(f"Stopped. {watcher.counts}")


if __name__ == "__main__":
    main()
