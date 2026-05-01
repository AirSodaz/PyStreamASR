**English** | [中文](README_zh.md)

# PyStreamASR

PyStreamASR is a FastAPI-based real-time ASR service for streaming speech-to-text workloads. It accepts audio over WebSocket, converts incoming G.711 or PCM audio into 16 kHz PCM for Sherpa-onnx streaming inference, returns partial and final transcription events, keeps partial state in memory, and persists finalized segments to MySQL.

## At a Glance

- Health endpoint: `GET /health`
- Streaming endpoint: `WebSocket /ws/transcribe/{session_id}`
- Input audio: `alaw`, `ulaw`, `pcm16le`
- Internal inference audio: mono 16 kHz float32 PCM
- Partial results: in-memory cache per session
- Final results: MySQL `segments` table
- Runtime model: Sherpa-onnx Paraformer Streaming

## Quick Start

1. Clone the repository.

   ```bash
   git clone https://github.com/AirSodaz/PyStreamASR.git
   cd PyStreamASR
   ```

2. Create and activate a Python 3.12 virtual environment.

   ```powershell
   py -3.12 -m venv venv
   .\venv\Scripts\activate
   ```

3. Install dependencies.

   ```powershell
   pip install -r requirements.txt
   ```

4. Create your local environment file.

   ```powershell
   Copy-Item .env.example .env
   ```

5. Update `.env` with at least:
   - `MYSQL_DATABASE_URL`
   - `MODEL_PATH`

6. Set `MODEL_PATH` to the Sherpa-onnx Paraformer Streaming model directory. Relative paths are resolved from the project root, for example:

   ```text
   models/sherpa-onnx-streaming-paraformer-bilingual-zh-en/
   ```

   The model loader reads this configured directory and expects files such as `encoder.int8.onnx`, `decoder.int8.onnx`, and `tokens.txt` inside it.

7. Start the development server.

   ```bash
   uvicorn main:app --reload
   ```

### Uvicorn Development Parameters

For local development, these are the most useful `uvicorn` options:

| Parameter | Example | What it does |
| --- | --- | --- |
| `--reload` | `uvicorn main:app --reload` | Restarts the server automatically when Python source files change. Use this only in development. |
| `--host` | `--host 0.0.0.0` | Controls the bind address. Use `127.0.0.1` for local-only access or `0.0.0.0` when other devices need to connect. |
| `--port` | `--port 8000` | Controls which port exposes `/health` and `/ws/transcribe/{session_id}`. |
| `--workers` | `--workers 4` | Starts multiple worker processes. This is usually unnecessary during development and should not be combined with `--reload`. |

Recommended development command:

```bash
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

If you prefer to use values from `.env`, `python main.py` starts Uvicorn with `APP_HOST` and `APP_PORT`, and enables reload mode.

## Quick Verification

### 1. Verify the service is up

Open:

```text
http://localhost:8000/health
```

Expected response shape:

```json
{
  "status": "ok",
  "config": "loaded",
  "project_name": "PyStreamASR",
  "model_status": "loaded"
}
```

### 2. Verify the WebSocket transcription flow

With the virtual environment activated, run:

```powershell
python scripts/simulate_stream.py --file .\path\to\your_audio.wav --host ws://localhost:8000/ws/transcribe/demo-session
```

Replace `.\path\to\your_audio.wav` with an actual audio file. You should then see streamed `partial` and `final` JSON messages in the client output. The exact transcript depends on the input audio and model.

## How Streaming Works

PyStreamASR processes each connection as a non-blocking streaming pipeline:

1. The client sends binary audio chunks to `WebSocket /ws/transcribe/{session_id}`.
2. `AudioProcessor` decodes `alaw`, `ulaw`, or `pcm16le`, normalizes samples, and resamples to 16 kHz when needed.
3. Audio processing and model inference run through `loop.run_in_executor` so the event loop stays available for WebSocket and database I/O.
4. Interim transcription is tracked as a session-scoped in-memory partial result.
5. Finalized segments are stored in MySQL and sent back to the client as `final` events.
6. Reconnecting with the same `session_id` continues sequence numbering for that session.

## Supported Input Formats

| Format | Expected Source Rate | Notes |
| --- | --- | --- |
| `alaw` | Usually `8000` Hz | G.711 A-law input. Recommended for telephony-style streams. |
| `ulaw` | Usually `8000` Hz | G.711 mu-law input. Recommended for telephony-style streams. |
| `pcm16le` | `8000` or `16000` Hz | Raw little-endian 16-bit PCM. `8000` Hz input is resampled server-side. |

All inputs are normalized and converted to mono 16 kHz PCM before inference. For G.711 streams, keep the client format and sample rate aligned with `AUDIO_INPUT_FORMAT` and `AUDIO_SOURCE_RATE`.

## Configuration Highlights

Create a `.env` file in the project root. A typical setup looks like this:

```ini
PROJECT_NAME=PyStreamASR
MYSQL_DATABASE_URL=mysql+aiomysql://root:password@localhost/pystreamasr
MODEL_PATH=models/sherpa-onnx-streaming-paraformer-bilingual-zh-en
LOG_LEVEL=INFO
LOG_DIR=logs
RETURN_TRANSCRIPTION=true
AUDIO_INPUT_FORMAT=alaw
AUDIO_SOURCE_RATE=8000
APP_HOST=0.0.0.0
APP_PORT=8000
APP_WORKERS=1
```

| Variable | Required | Default | Notes |
| --- | --- | --- | --- |
| `MYSQL_DATABASE_URL` | Yes | None | Async SQLAlchemy DSN. Example: `mysql+aiomysql://user:password@host/dbname`. |
| `MODEL_PATH` | Yes | None | Model directory used by the runtime loader. Relative paths are resolved from the project root. |
| `PROJECT_NAME` | No | `PyStreamASR` | Used in the FastAPI app title and `/health` response. |
| `LOG_LEVEL` | No | `INFO` | Set to `DEBUG` to capture processed audio into WAV files for troubleshooting. |
| `LOG_DIR` | No | `logs` | Base directory for runtime logs and debug artifacts. |
| `RETURN_TRANSCRIPTION` | No | `true` | When `false`, audio is still processed and stored, but no transcription messages are sent over WebSocket. |
| `AUDIO_INPUT_FORMAT` | No | `alaw` | One of `alaw`, `ulaw`, `pcm16le`. Must match the client stream. |
| `AUDIO_SOURCE_RATE` | No | `8000` | One of `8000`, `16000`. Must match the client stream. |
| `APP_HOST` | No | `0.0.0.0` | Bind host for local runs and service wrappers. |
| `APP_PORT` | No | `8000` | Bind port for local runs and service wrappers. |
| `APP_WORKERS` | No | `1` | Worker count used by the service wrapper. On Windows this is used with Uvicorn; on Linux/macOS it is used with Gunicorn. |

When `LOG_LEVEL=DEBUG`, each WebSocket session writes a 16 kHz mono WAV file under `logs/debug_audio/` so you can inspect decoded and resampled audio.

## Deployment Options

Use one of the following depending on your environment:

| Scenario | Command | Notes |
| --- | --- | --- |
| Cross-platform direct run | `uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4` | Simple production-style launch. |
| Windows background service | `powershell.exe -ExecutionPolicy Bypass -File .\install.ps1` | Registers the `PyStreamASR` scheduled task and installs the `pystreamasr` command. |
| Linux persistent service | `sudo ./install.sh` | Installs a `pystreamasr.service` systemd unit and the `pystreamasr` command. |
| Linux/macOS Gunicorn | `gunicorn main:app -c gunicorn.conf.py` | Gunicorn is not supported on Windows. |

After either installer completes, use:

```bash
pystreamasr
```

On Linux, use `sudo pystreamasr` if service control requires elevated privileges.

### What `pystreamasr` Does

`pystreamasr` is the console entrypoint defined in `pyproject.toml`. It launches a layered terminal menu instead of starting the ASR server directly.

The menu provides:

- Main-menu navigation only (no direct info rendering on the main page): service operations, status viewer, configuration manager, log viewer, and diagnostics
- Service controls: `Start` / `Stop` / `Restart`
- Runtime setting updates: `APP_HOST`, `APP_PORT`, and `APP_WORKERS` in `.env`
- Status viewer with explicit refresh option
- Log viewer with source selection and configurable tail line count
- Diagnostics output with pass/warn/fail summaries and remediation details
- Screen is cleared and redrawn automatically when entering submenus and when returning to the main menu
- Exit behavior: `0` exits from the main menu, while `0` in submenus returns to the main menu

Behavior depends on how the service was installed:

- On Windows, it manages the `PyStreamASR` scheduled task and expects a Uvicorn-based install.
- On Linux, it manages the `pystreamasr.service` systemd unit and expects a Gunicorn-based install.

Typical usage flow:

1. Run `install.ps1` or `install.sh`.
2. Launch `pystreamasr`.
3. Use submenu navigation for status checks, runtime setting updates, and service controls.
4. Use the Logs and Diagnostics submenus during incidents, then restart if configuration changes require it.

If the service has not been installed yet, `pystreamasr` can still open, but service actions will report that the managed service is not installed.

## Project Layout

The current repository includes more than the minimal runtime tree. The main areas are:

```text
PyStreamASR/
├── api/               # WebSocket routes and connection lifecycle
├── core/              # Settings, logging, and request context helpers
├── docs/              # API reference in English and Chinese
├── models/            # Sherpa-onnx model assets
├── scripts/           # Stream simulators, installers, and service manager
├── services/          # Audio, inference, storage, and database schema logic
├── main.py            # FastAPI app entrypoint and lifespan setup
├── install.ps1        # Windows installer / scheduled-task setup
├── install.sh         # Linux installer / systemd setup
├── pyproject.toml     # Package metadata and console entrypoint
└── requirements.txt   # Python dependencies
```

## Docs

- Full API reference: [docs/API.md](docs/API.md)
- Chinese API reference: [docs/API_zh.md](docs/API_zh.md)

Use the README for setup and validation. Use the API docs for message shapes, examples, and interface details.

## Troubleshooting

- `FileNotFoundError` during startup usually means the Sherpa-onnx model files are not present under `models/sherpa-onnx-streaming-paraformer-bilingual-zh-en/`.
- Database connection failures usually mean `MYSQL_DATABASE_URL` is invalid, MySQL is unavailable, or the target database does not exist.
- If WebSocket messages never arrive, verify `RETURN_TRANSCRIPTION=true`.
- If transcription quality is poor or errors are logged during processing, check that `AUDIO_INPUT_FORMAT` and `AUDIO_SOURCE_RATE` match what the client is actually sending.
- If you need to inspect decoded audio, set `LOG_LEVEL=DEBUG` and review the WAV artifacts under `logs/debug_audio/`.
