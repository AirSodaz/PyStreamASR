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

1.  **Clone the repository**:
    ```bash
    git clone https://github.com/AirSodaz/PyStreamASR.git
    cd PyStreamASR
    ```
    
2.  **Install dependencies**:
    
    ```bash
    pip install -r requirements.txt
    ```

3.  **Download Models**:
    Ensure the Sherpa-onnx models are placed in the `models/` directory.
    *   Example: `models/sherpa-onnx-streaming-paraformer-bilingual-zh-en/`

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
```

### .env Variables (Required/Optional + Options)

| Variable | Required | Default | Options / Notes |
| --- | --- | --- | --- |
| `MYSQL_DATABASE_URL` | Yes | None | SQLAlchemy DSN. Example: `mysql+aiomysql://user:password@host/dbname`. |
| `MODEL_PATH` | Yes | None | Model directory path. Can be absolute or project-relative. |
| `PROJECT_NAME` | No | `PyStreamASR` | Any string. Used in app title and `/health`. |
| `LOG_LEVEL` | No | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. |
| `LOG_DIR` | No | `logs` | Directory for log files. |
| `RETURN_TRANSCRIPTION` | No | `true` | `true` or `false`. When `false`, server will still process but will not return transcription messages on WebSocket. |
| `AUDIO_INPUT_FORMAT` | No | `alaw` | `alaw`, `ulaw`, `pcm16le`. Must match the stream format sent by clients. |
| `AUDIO_SOURCE_RATE` | No | `8000` | `8000` or `16000`. Must match the stream sample rate sent by clients. |

## Usage

### Running the Server

**Development mode:**

```bash
uvicorn main:app --reload
```

**Production mode (Uvicorn):**

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

**Production mode (Gunicorn - Linux/macOS only):**

```bash
gunicorn main:app -c gunicorn.conf.py
```

> **Note:** Gunicorn does not support Windows. On Windows, use Uvicorn directly or deploy via Docker/WSL.

The server will start at `http://localhost:8000`.

### Checking Service Status

To quickly verify that the service is running and the model is loaded, you can check the `/health` endpoint:

```bash
curl http://localhost:8000/health
```

**Expected Output:**

```json
{"status":"ok","config":"loaded","project_name":"PyStreamASR","model_status":"loaded"}
```

### Running the Simulation Script

A test script is provided to simulate a client streaming audio to the server and verify real-time transcription.

```bash
python scripts/simulate_stream.py --file path/to/audio.wav --host ws://127.0.0.1:8000/ws/transcribe/test-session-1
```

**Arguments:**
*   `--file`: Input audio file path. Supported:
    *   Raw G.711: `.alaw`, `.pcma`, `.g711`, `.ulaw`, `.pcmu`, `.mulaw` (must match `--format`).
    *   Raw PCM16LE: `.pcm`, `.raw` (requires `--format pcm16le`).
    *   WAV:
        *   G.711 A-law/μ-law WAV is passed through (format must match `--format`).
        *   PCM WAV/other audio is loaded and converted to the stream format using `librosa`.
*   `--host`: WebSocket URL (default: `ws://localhost:8000/ws/transcribe/test-session-1`).
*   `--chunk_duration`: Duration per chunk in seconds (default: `0.6`). Controls chunk size and sleep interval to simulate real-time streaming.
*   `--format`: Stream encoding format: `alaw`, `ulaw`, or `pcm16le` (default: `alaw`).
*   `--sample_rate`: Stream sample rate: `8000` or `16000` (default: `8000`). G.711 is typically `8000`.

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
