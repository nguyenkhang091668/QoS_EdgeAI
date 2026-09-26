# USB camera on Raspberry Pi 4

Validated base environment: Debian 13 arm64, Python 3.13; install python3-venv,
python3-numpy and python3-opencv via apt. Create .venv-pi with
`python3 -m venv --system-site-packages .venv-pi`.
The shared requirement.txt is a macOS snapshot, not a verified Pi lock.

From the repository root on Pi, close other camera programs and run:

```bash
source .venv-pi/bin/activate
python pipeline_edge_ai.py --source usb --device /dev/video0 --motion-only --seconds 30
```

The USB source requests V4L2, MJPG, 640x480 and 30 FPS. Drivers may negotiate
other settings; the logged FPS measures processed frames, not a guarantee of
30 FPS. Move a textured object within the drawn ROI (X=150:450, Y=100:400).
Layers 1-4 require valid motion for five consecutive frames, then a cooldown.
A `motion_trigger` is not a person/object detection. AI stays disabled and no
model or Ultralytics/LiteRT installation is required in this mode.
Press Q/Esc in the OpenCV window, or Ctrl+C in the terminal, to stop.

Without a graphical desktop, add `--headless`. Alternatively use `--browser`
for preview at http://127.0.0.1:8765 on Pi; the server binds only to localhost.

Packaged entry point (same behavior):

```bash
PYTHONPATH=src python -m edge_orchestrator.Edge.pipeline_edge_ai --source usb --device /dev/video0 --motion-only --seconds 30
```

For owned USB capture through the Python API, use
`EdgeAICamera(camera_index='/dev/video0', v4l2=True, model_path=...)`.
This API still requires a real model or an explicitly supplied detector.
The default webcam behavior on Mac remains unchanged.

To run actual inference later, provide a compatible model with `--model` and
omit `--motion-only`, after installing a runtime verified for this Pi.
This CLI is separate from the dashboard, whose existing controller and
DecisionEngine integration issues are not repaired by this camera change.
