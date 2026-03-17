**English** | [中文](README_zh.md)

# PyStreamASR

PyStreamASR is a real-time Automatic Speech Recognition (ASR) streaming service built with Python 3.12 and FastAPI. It leverages **Sherpa-onnx** (Paraformer Streaming) for high-performance, low-latency speech-to-text transcription suitable for real-time applications.

## Features

*   **Real-time Streaming**: Efficient WebSocket-based audio streaming (Pseudo-streaming supported).
*   **High Performance ASR**: Powered by **Sherpa-onnx** (Streaming Paraformer) for robust bilingual (Chinese/English) transcription.
*   **Audio Processing**: Native support for G.711 A-law/μ-law and PCM16LE (8k/16k) with optimized decoding and resampling to 16000Hz PCM.
*   **Non-blocking Architecture**: Built on FastAPI with asynchronous I/O for network and DB operations, using `loop.run_in_executor` for CPU-bound inference.
*   **Data Persistence**:
    *   **In-memory Hash Table**: For managing ephemeral session state and real-time partial results (hot data).
    *   **MySQL**: For persistent storage of finalized transcription segments (cold data).

## Prerequisites

*   **Python 3.12** (Strict requirement)
*   **MySQL Server**
*   **Sherpa-onnx** (`pip install sherpa-onnx`)
*   **g711** (`pip install g711`) - Recommended for efficient G.711 decoding.

## Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/AirSodaz/PyStreamASR.git
   cd PyStreamASR
   ```

2. **Create a virtual environment and install dependencies**
   ```powershell
   py -3.12 -m venv venv
   .\venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. **Create `.env` and set the required values**
   Copy `.env.example` to `.env`, then set at least:
   - `MYSQL_DATABASE_URL`
   - `MODEL_PATH`

4. **Download the Sherpa-onnx model**
   Place the model directory under `models/`, for example:
   - `models/sherpa-onnx-streaming-paraformer-bilingual-zh-en/`

## Configuration

Create a `.env` file in the root directory. You can copy `.env.example` if it exists.

```ini
PROJECT_NAME="PyStreamASR"
MYSQL_DATABASE_URL="mysql+aiomysql://user:password@localhost/dbname"
# Path relative to the project root
MODEL_PATH="models/sherpa-onnx-streaming-paraformer-bilingual-zh-en"
LOG_LEVEL="INFO"
LOG_DIR="logs"
RETURN_TRANSCRIPTION=true
AUDIO_INPUT_FORMAT="alaw"  # alaw | ulaw | pcm16le
AUDIO_SOURCE_RATE=8000     # 8000 | 16000
APP_HOST="0.0.0.0"
APP_PORT=8000
APP_WORKERS=1
```

### .env Variables (Required/Optional + Options)

| Variable | Required | Default | Options / Notes |
| --- | --- | --- | --- |
| `MYSQL_DATABASE_URL` | Yes | None | SQLAlchemy DSN. Example: `mysql+aiomysql://user:password@host/dbname`. |
| `MODEL_PATH` | Yes | None | Model directory path. Can be absolute or project-relative. |
| `PROJECT_NAME` | No | `PyStreamASR` | Any string. Used in app title and `/health`. |
| `LOG_LEVEL` | No | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. When set to `DEBUG`, processed connection audio is also saved as WAV files under `logs/debug_audio/`. |
| `LOG_DIR` | No | `logs` | Directory for log files. |
| `RETURN_TRANSCRIPTION` | No | `true` | `true` or `false`. When `false`, server will still process but will not return transcription messages on WebSocket. |
| `AUDIO_INPUT_FORMAT` | No | `alaw` | `alaw`, `ulaw`, `pcm16le`. Must match the stream format sent by clients. |
| `AUDIO_SOURCE_RATE` | No | `8000` | `8000` or `16000`. Must match the stream sample rate sent by clients. |
| `APP_HOST` | No | `0.0.0.0` | Bind host used by the terminal service manager. |
| `APP_PORT` | No | `8000` | Bind port used by the terminal service manager. |
| `APP_WORKERS` | No | `1` | Worker count used by the terminal service manager. On Windows this applies to Uvicorn; on macOS/Linux it applies to Gunicorn. |

## Usage

### Quick Start

Start the server in development mode:

```bash
uvicorn main:app --reload
```

Check readiness:

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{"status":"ok","config":"loaded","project_name":"PyStreamASR","model_status":"loaded"}
```

Run the streaming simulator:

```bash
python scripts/simulate_stream.py --file path/to/audio.wav --host ws://127.0.0.1:8000/ws/transcribe/test-session-1
```

Common simulator options:
- `--format`: `alaw`, `ulaw`, or `pcm16le`
- `--sample_rate`: `8000` or `16000`
- `--chunk_duration`: chunk size and send interval in seconds

### Service and Deployment Options

| Scenario | Command | Notes |
| --- | --- | --- |
| Local service manager (Windows) | `.\scripts\manage_service.bat` | Opens a TUI and updates `APP_HOST`, `APP_PORT`, and `APP_WORKERS` in `.env`. |
| Local service manager (macOS/Linux) | `./scripts/manage_service.sh` | Same TUI for Unix-like environments. |
| Persistent Linux service | `sudo ./install.sh` | Creates or reuses `venv`, installs dependencies, registers `pystreamasr.service`, and installs the `pystreamasr` helper command. |
| Persistent Windows service | `powershell.exe -ExecutionPolicy Bypass -File .\install.ps1` | Creates or reuses `venv`, registers the `PyStreamASR` scheduled task, and installs the `pystreamasr` helper command. |
| Direct production run | `uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4` | Cross-platform production entry point. |
| Gunicorn (Linux/macOS only) | `gunicorn main:app -c gunicorn.conf.py` | Gunicorn is not supported on Windows. |

After running either installer, use:

```bash
pystreamasr
```

Use `sudo pystreamasr` on Linux if `systemctl` requires elevated privileges.

When `LOG_LEVEL=DEBUG`, each WebSocket connection also writes a mono 16 kHz PCM WAV file to `logs/debug_audio/` for audio inspection.

## Project Structure

```text
PyStreamASR/
├── api/             # API endpoints (WebSocket logic)
├── core/            # Configuration and global settings
├── models/          # Pre-trained Sherpa-onnx models
├── services/        # Business logic (Audio, Inference, Storage, Schemas)
├── scripts/         # Utility and test scripts (e.g., simulate_stream.py)
├── main.py          # Application entry point
└── requirements.txt # Project dependencies
```
