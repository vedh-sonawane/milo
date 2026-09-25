# Agent Instructions

## Project Context

- Read [Full_Project.MD](Full_Project.MD) for the authoritative goals, architecture, constraints, hardware inventory, privacy principles, and roadmap.
- The current implementation is a proof of concept, not the full planned distributed system.
- [observe.py](observe.py) captures one image, sends it to a local Ollama vision model, and stores the structured observation in SQLite.
- [camera/camera.ino](camera/camera.ino) runs the ESP32 camera HTTP server with `/` for MJPEG streaming and `/capture` for a single JPEG.

## Working Conventions

- Keep changes small and consistent with the existing standard-library Python and Arduino C++ style.
- Prefer local processing, structured observations, event-based memory, and incremental implementation over premature abstractions.
- Preserve the existing public endpoints and data shape unless the task explicitly requires a contract change.
- Do not commit Wi-Fi credentials, API keys, or other secrets. Wi-Fi credentials live only in the gitignored `camera/secrets.h` (template: `camera/secrets.example.h`); never copy them into tracked files. The camera IP is supplied at runtime, not hardcoded.
- Use ASCII text only. Never add em dash punctuation to source code, Markdown, comments, prompts, documentation, or other project files. Use a hyphen or rewrite the sentence.

## Validation

- There is no automated test suite or package manifest yet.
- For Python changes, run `python -m py_compile observe.py` and use a hardware-free check when possible.
- For camera changes, validate by compiling and uploading with the Arduino IDE or the project's configured ESP32 toolchain when available.
- The normal Python proof-of-concept invocation is `python observe.py <camera-ip>`, or set the `CAMERA_IP` environment variable and omit the argument.
- It expects the ESP32 `/capture` endpoint, a local Ollama server at `http://localhost:11434`, and the configured vision model.
- `memory.db` and captured JPEG files are runtime artifacts. Do not treat them as source files.

## Hardware and Runtime Notes

- The ESP32 camera uses RGB565 and software JPEG conversion because the GC0308 has no hardware JPEG encoder.
- The camera handles one client at a time. Close the stream before requesting `/capture`.
- `/capture` reinitializes the camera for VGA and takes about one second before returning to QVGA.
- The SQLite path is relative to the process working directory, so verify the working directory when investigating database behavior.
- Do not assume the Raspberry Pi, long-term memory, world model, querying, or physical action phases are implemented unless the workspace contains them.

## Change Discipline

- Before editing, identify the owning function or handler and a nearby behavior check.
- Do not broaden a focused task into unrelated refactoring.
- Update [Full_Project.MD](Full_Project.MD) only when the documented architecture or roadmap actually changes.