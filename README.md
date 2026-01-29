# PyStreamASR

PyStreamASR is a real-time Automatic Speech Recognition (ASR) streaming service built with Python 3.12, FastAPI, and FunASR (SenseVoice/Paraformer). It is designed to handle audio streams via WebSockets, process them efficiently, and store transcripts.

## Features

*   **Real-time Streaming**: Efficient WebSocket-based audio streaming.
*   **Modern ASR**: Powered by Alibaba's FunASR (SenseVoice/Paraformer) for high-accuracy transcription.
*   **Audio Processing**: Supports G.711 A-law encoding (8000Hz) and automatic resampling to 16000Hz PCM.
*   **Non-blocking Architecture**: Built on FastAPI with asynchronous I/O for network and DB operations, and thread pools for CPU-intensive inference.
*   **Data Persistence**:
    *   **Redis**: For managing ephemeral session state (hot data).
    *   **MySQL**: For persistent storage of transcription segments (cold data).

## Prerequisites

*   **Python 3.12** (Strict requirement)
*   **Redis Server**
*   **MySQL Server** (asyncio-compatible driver used: `aiomysql` or `asyncmy`)

## Installation

1.  **Clone the repository**:
    ```bash
    git clone <repository-url>
    cd PyStreamASR
    ```
    
2.  **Install dependencies**:
    
    ```bash
    pip install -r requirements.txt
    ```

## Configuration

Create a `.env` file in the root directory. You can copy `.env.example` if it exists.

```ini
PROJECT_NAME="PyStreamASR"
MYSQL_DATABASE_URL="mysql+aiomysql://user:password@localhost/dbname"
REDIS_URL="redis://localhost:6379/0"
MODEL_PATH="/path/to/funasr/model"
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

A test script is provided to simulate a client streaming audio to the server.

```bash
python scripts/simulate_stream.py --file path/to/audio.wav --host ws://127.0.0.1:8000/ws/transcribe/test-session-1
```

**Arguments:**
*   `--file`: Path to the input audio file (WAV, etc.).
*   `--host`: WebSocket URL (default: `ws://localhost:8000/ws/transcribe/test-session-1`).
*   `--chunk_duration`: Duration of audio chunks in seconds (default: `0.1`).

## Project Structure

```text
PyStreamASR/
├── api/             # API endpoints and routers
├── core/            # Configuration and global settings
├── models/          # (Deprecated)
├── services/        # Business logic (Audio, Inference, Storage, Schemas)
├── scripts/         # Utility and test scripts
├── main.py          # Application entry point
└── requirements.txt # Project dependencies
```

## License

[License Name]
