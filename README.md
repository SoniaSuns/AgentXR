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

Continuous observation waits for each analysis to finish, then captures again after the selected interval. Select **切换单次拍照** to stop automatic sampling and take one new photo; if an analysis is running, the new photo waits for it to finish.

Each photo uses one complete analysis request, including user feedback and selected historical evidence. Supplementary detail requests collect facts without replacing the diagnosis or hiding its panel. The automatic close-up/retry analysis chain has been removed; you can still select a detail region manually.

Guidance prioritizes one concrete move, using clearly visible relative positions without requiring a written plan. Object and destination annotations survive diagnostic processing, with a connecting arrow when both are located. Detailed analysis and effect records are collapsed by default. Open the annotated photo to zoom with the buttons, wheel or pinch, drag to pan, and double-click or select fit to reset. The photo and annotations scale together.

Networking defaults to `"proxy": "auto"` in `settings.json`. Before each analysis, AgentXR checks enabled macOS proxies, 0dcloud's configured local ports, then proxy environment variables. Unreachable ports are skipped; otherwise it uses the system route, including VPN/TUN connections. Detection checks port availability, not model-service health, and does not control VPN apps or nodes. A route change takes effect on the next analysis and appears in the session label. An explicit proxy URL pins that route without direct fallback; an empty string pins the system route. `AGENTXR_PROXY` or `--proxy` overrides the setting.

`codex.transport` requests HTTP/SSE by default, but the installed CLI may retain WebSockets for its built-in provider. Verify the actual transport in CLI connection logs; the health field `model_transport_requested` reports configuration intent only. Set it to `"auto"` to retain Codex's default selection. Restart the backend after changing settings; subsequent detectable proxy changes are picked up automatically.

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

Saved analysis records and photos are retained until manually deleted by default (`memory.saved_records: 0` disables count-based pruning). Select **作为历史参考** on a history card to include that photo in the next analysis regardless of its age. One selected photo is attached as historical evidence; cancel the selection or switch sessions to stop using it. Previously deleted photos cannot be recovered.

AgentXR is a local research prototype with 2D annotations. It analyzes selected frames, not every video frame. Photos are sent to the model service used by Codex; sessions and images are saved locally by default. Circuit checks depend on the board profile and confirmed inputs, and require physical verification.
