# Agent Instructions

## Project Context

- Read [Full_Project.MD](Full_Project.MD) for the authoritative goals, architecture, constraints, hardware inventory, privacy principles, and roadmap.
- The current implementation is a proof of concept, not the full planned distributed system.
- [observe.py](observe.py) captures one image, sends it to a local Ollama vision model, and stores the structured observation in SQLite. Its functions are reused by the other scripts.
- [watch.py](watch.py) `Watcher`: a capture thread (every 1 s, blur filter, frames grouped into scenes, sharpest kept) feeds a bounded queue; a describe thread runs the vision model and saves with the capture time. Built for walking, not holding still.
- [ask.py](ask.py) `answer()` returns `{answer, sources}` from stored memories only (model replies in JSON), using the same local model.
- [world.py](world.py) world model: objects become things + sightings (name match by nomic-embed-text similarity, thresholds measured, grey zone needs same last word), plus one search vector per memory. `relevant()` picks what `ask.py` sends to the model. All world tables are derived; `world.rebuild()` recreates them from `observations`.
- [server.py](server.py) FastAPI app that owns the `Watcher` and serves [web/index.html](web/index.html) plus a small JSON API under `/api`. Binds to localhost unless `--lan`.
- [camera_server.py](camera_server.py) serves a USB webcam (OpenCV) with the same `/` and `/capture` endpoints as the ESP32; it is the recommended camera (30 fps, many viewers). Milo is pointed at it with `localhost:8081`.
- [camera/camera.ino](camera/camera.ino) is the fallback: it runs the ESP32 camera HTTP server with `/` for MJPEG streaming and `/capture` for a single JPEG.

## Working Conventions

- Keep changes small and consistent with the existing standard-library Python and Arduino C++ style.
- Prefer local processing, structured observations, event-based memory, and incremental implementation over premature abstractions.
- Preserve the existing public endpoints and data shape unless the task explicitly requires a contract change.
- Do not commit Wi-Fi credentials, API keys, or other secrets. Wi-Fi credentials live only in the gitignored `camera/secrets.h` (template: `camera/secrets.example.h`); never copy them into tracked files. The camera IP is supplied at runtime, not hardcoded.
- Use ASCII text only. Never add em dash punctuation to source code, Markdown, comments, prompts, documentation, or other project files. Use a hyphen or rewrite the sentence.

## Validation

- There is no automated test suite or package manifest yet.
- For Python changes, run `python -m py_compile observe.py watch.py ask.py server.py camera_server.py world.py` and use a hardware-free check when possible.
- For camera changes, validate by compiling and uploading with the Arduino IDE or the project's configured ESP32 toolchain when available.
- The normal Python proof-of-concept invocation is `python observe.py <camera-ip>`, or set the `CAMERA_IP` environment variable and omit the argument.
- It expects the ESP32 `/capture` endpoint, a local Ollama server at `http://localhost:11434`, and the configured vision model.
- `memory.db` and captured JPEG files are runtime artifacts. Do not treat them as source files.

## Hardware and Runtime Notes

- The ESP32 camera uses RGB565 and software JPEG conversion because the GC0308 has no hardware JPEG encoder.
- The camera handles one client at a time. Close the stream before requesting `/capture`.
- The camera runs at VGA for both endpoints; runtime resolution changes do not work with RGB565 on this driver. `/capture` uses JPEG quality 100 (the user wants maximum quality; never lower it for speed). Board time is ~0.5 s (see the `X-Timing` header); the rest is Wi-Fi. XCLK is 10 MHz on purpose: 20 MHz drops frames.
- The vision model (~15 s per photo) is the throughput bottleneck, not the camera. `watch.py` and `server.py` must not run at the same time.
- The SQLite path is relative to the process working directory, so verify the working directory when investigating database behavior.
- Do not assume the Raspberry Pi, long-term memory, world model, querying, or physical action phases are implemented unless the workspace contains them.

## Change Discipline

- Before editing, identify the owning function or handler and a nearby behavior check.
- Do not broaden a focused task into unrelated refactoring.
- Update [Full_Project.MD](Full_Project.MD) only when the documented architecture or roadmap actually changes.