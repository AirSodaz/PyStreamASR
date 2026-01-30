# PyStreamASR

PyStreamASR is a real-time Automatic Speech Recognition (ASR) streaming service built with Python 3.12 and FastAPI. It leverages **Sherpa-onnx** (Paraformer Streaming) for high-performance, low-latency speech-to-text transcription suitable for real-time applications.

## Features

*   **Real-time Streaming**: Efficient WebSocket-based audio streaming (Pseudo-streaming supported).
*   **High Performance ASR**: Powered by **Sherpa-onnx** (Streaming Paraformer) for robust bilingual (Chinese/English) transcription.
*   **Audio Processing**: Native support for G.711 A-law encoding (8000Hz) with optimized decoding and resampling to 16000Hz PCM.
*   **Non-blocking Architecture**: Built on FastAPI with asynchronous I/O for network and DB operations, using `loop.run_in_executor` for CPU-bound inference.
*   **Data Persistence**:
    *   **Redis**: For managing ephemeral session state and real-time partial results (hot data).
    *   **MySQL**: For persistent storage of finalized transcription segments (cold data).

## Prerequisites

*   **Python 3.12** (Strict requirement)
*   **Redis Server**
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
REDIS_URL="redis://localhost:6379/0"
# Path relative to the project root
MODEL_PATH="models/sherpa-onnx-streaming-paraformer-bilingual-zh-en"
LOG_LEVEL="INFO"
LOG_DIR="logs"
```

## Usage

### Running the Server

Start the FastAPI server with Uvicorn (development mode):

```bash
uvicorn main:app --reload
```
The server will start at `http://127.0.0.1:8000`.

### Running the Simulation Script

A test script is provided to simulate a client streaming audio to the server and verify real-time transcription.

```bash
python scripts/simulate_stream.py --file path/to/audio.wav --host ws://127.0.0.1:8000/ws/transcribe/test-session-1 --redis
```

**Arguments:**
*   `--file`: Path to the input audio file (WAV, G.711 A-law, etc.).
*   `--host`: WebSocket URL (default: `ws://localhost:8000/ws/transcribe/test-session-1`).
*   `--chunk_duration`: Duration of audio chunks in seconds (default: `0.6`).
*   `--redis`: (Optional) Enable monitoring of real-time partials from Redis.

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
