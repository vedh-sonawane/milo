"""USB webcam as Milo's eyes: the same endpoints as the ESP32 camera, but smooth.

  /          MJPEG live stream (full frame rate of the webcam)
  /capture   one JPEG photo at quality 100 (maximum)

Unlike the ESP32, any number of viewers can watch while Milo captures.
Runs on this PC now and on the Raspberry Pi later (the same script).

Run: python camera_server.py --list          show connected cameras
     python camera_server.py --device 1      serve camera 1 on port 8081
Then point Milo at it: python server.py localhost:8081
"""

import argparse
import platform
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2

CAPTURE_QUALITY = 100  # photos for memory: maximum quality, never lower
STREAM_QUALITY = 90    # live view
# Tried in order; the first one the webcam accepts is used.
RESOLUTIONS = [(1920, 1080), (1280, 720), (1024, 768), (800, 600), (640, 480)]


def open_camera(index):
    backend = cv2.CAP_DSHOW if platform.system() == "Windows" else cv2.CAP_V4L2
    return cv2.VideoCapture(index, backend)


def list_cameras():
    for i in range(6):
        cap = open_camera(i)
        if cap.isOpened() and cap.read()[0]:
            w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            print(f"  camera {i}: working, default {w}x{h}")
        cap.release()


class Camera:
    """Reads frames nonstop in a background thread and keeps only the newest."""

    def __init__(self, index):
        self.cap = open_camera(index)
        if not self.cap.isOpened():
            raise SystemExit(f"Could not open camera {index}. Run with --list to see cameras.")
        # Ask for the webcam's own MJPEG format first: it allows higher resolution at full frame rate.
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        for w, h in RESOLUTIONS:
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
            if int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)) == w:
                break
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # always hand out the newest frame, not a queued one
        self.frame = None
        self.frame_id = 0
        self.fps = 0.0
        self.cond = threading.Condition()
        threading.Thread(target=self._read_loop, daemon=True).start()

    def _read_loop(self):
        count, window_start = 0, time.monotonic()
        while True:
            ok, frame = self.cap.read()
            if not ok:
                time.sleep(0.05)
                continue
            with self.cond:
                self.frame, self.frame_id = frame, self.frame_id + 1
                self.cond.notify_all()
            count += 1
            if time.monotonic() - window_start >= 2:
                self.fps, count, window_start = count / (time.monotonic() - window_start), 0, time.monotonic()

    def wait_frame(self, after_id=-1, timeout=5):
        """Newest frame with an id greater than after_id (waits for it)."""
        with self.cond:
            self.cond.wait_for(lambda: self.frame is not None and self.frame_id > after_id, timeout)
            return self.frame, self.frame_id

    def jpeg(self, frame, quality):
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return buf.tobytes() if ok else None


class Handler(BaseHTTPRequestHandler):
    camera: Camera = None

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/capture":
            self.capture()
        elif path == "/":
            self.stream()
        else:
            self.send_error(404)

    def capture(self):
        started = time.monotonic()
        frame, _ = self.camera.wait_frame()
        data = frame is not None and self.camera.jpeg(frame, CAPTURE_QUALITY)
        if not data:
            self.send_error(500, "Frame grab failed")
            return
        self.send_response(200)
        self.send_header("X-Camera-Viewers", "many")  # tells Milo it may show the live stream too
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Timing", f"encode={(time.monotonic() - started) * 1000:.0f}ms fps={self.camera.fps:.1f}")
        self.end_headers()
        self.wfile.write(data)

    def stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        last_id = -1
        try:
            while True:
                frame, last_id = self.camera.wait_frame(last_id)
                data = frame is not None and self.camera.jpeg(frame, STREAM_QUALITY)
                if not data:
                    continue
                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                 + str(len(data)).encode() + b"\r\n\r\n" + data + b"\r\n")
        except (ConnectionError, OSError):
            pass  # viewer closed the page

    def log_message(self, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description="Serve a USB webcam as Milo's camera")
    parser.add_argument("--device", type=int, default=0, help="camera number (see --list)")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--list", action="store_true", help="list cameras and exit")
    args = parser.parse_args()

    if args.list:
        list_cameras()
        return

    Handler.camera = camera = Camera(args.device)
    frame, _ = camera.wait_frame()
    if frame is None:
        raise SystemExit("Camera opened but sent no frames.")
    h, w = frame.shape[:2]
    print(f"Camera {args.device}: {w}x{h}")
    print(f"Live view: http://localhost:{args.port}/")
    print(f"Capture:   http://localhost:{args.port}/capture")
    print(f"Milo:      python server.py localhost:{args.port}")
    ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
