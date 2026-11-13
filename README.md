# AgentXR

English | [简体中文](README.zh-CN.md)

A camera-based assistant for hands-on tasks. AgentXR uses Codex to interpret your workspace, track progress, and show the next step with visual annotations.

## Features

- **Visual guidance:** analyze a photo or continuously sample camera frames, with streamed instructions, highlights, and arrows.
- **Task context:** track goals, steps, feedback, and observations across locally saved sessions.
- **Breadboard support:** calibrate a hole grid, review suggested endpoints, and check confirmed connections with deterministic circuit rules.

## Quick start

Requires Python 3.10+, a signed-in Codex CLI, and a camera-enabled browser such as Safari or Chrome. Basic observation uses the Python standard library; no npm install is required.

```sh
git clone git@github.com:SoniaSuns/AgentXR.git
cd AgentXR
python3 server.py
```

Open [localhost:8765](http://localhost:8765), enable the camera, enter a task, and select **拍照并分析** (analyze a photo) or **开始流式观察** (start continuous observation). On macOS, you can also double-click `start.command`.

## Optional breadboard tools

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-vision.txt
.venv/bin/python server.py
```

Freeze a clear frame, calibrate the board, and confirm endpoints before checking connections. Model suggestions require human review; this is not fully automatic photo-to-netlist conversion. See the [breadboard guide](docs/BREADBOARD.md) for calibration and measurement instructions.

## Documentation

- [Architecture](ARCHITECTURE.md)
- [Breadboard workflow](docs/BREADBOARD.md)
- Configuration: `settings.json` overrides defaults in `config.py`.

## Scope and data

AgentXR is a local research prototype with 2D annotations. It analyzes selected frames, not every video frame. Photos are sent to the model service used by Codex; sessions and images are saved locally by default. Circuit checks depend on the board profile and confirmed inputs, and require physical verification.
