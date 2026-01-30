# AI Agent Context & Rules - PyStreamASR

This file contains critical context, constraints, and operational commands for the **PyStreamASR** project. Always reference this file before generating code or executing commands.

## 1. Environment & Setup (Windows)
* **Virtual Environment:** The project runs in a local virtual environment.
* **Activation Command:** ALWAYS execute this before running python commands:
    ```powershell
    .\venv\Scripts\activate
    ```
* **Python Version:** **Python 3.12** (Strict).
    * **CRITICAL:** The `audioop` module is deprecated/removed. DO NOT use it. Use `g711` library or Lookup Tables for G.711 decoding.

## 2. Core Architecture Constraints
* **Concurrency Model:**
    * **I/O (Network, DB):** Must be `async/await` (FastAPI, aiomysql, redis-py).
    * **CPU (Inference):** Must run in `loop.run_in_executor`. **NEVER** block the main event loop with model inference or heavy audio processing.
* **Storage Strategy:**
    * **Hot Data (Partial):** Redis. Key: `asr:sess:{id}:current`.
    * **Cold Data (Final):** MySQL. Table: `segments`.
* **Audio Pipeline:**
    * Input: WebSocket -> G.711 (8k) -> PCM (16k) -> Sherpa-onnx (Paraformer-Streaming).

## 3. Project Structure & File Responsibilities

The project must strictly follow this directory structure. Do not create files outside this schema unless explicitly requested.

```text
PyStreamASR/
├── api/
│   ├── __init__.py
│   └── endpoints.py          # WebSocket routes, connection lifecycle, & debug logic.
├── core/
│   ├── __init__.py
│   └── config.py             # Pydantic Settings, Env loading, & Constants.
├── services/
│   ├── __init__.py
│   ├── audio.py              # AudioProcessor class (G.711 decoding, Resampling).
│   ├── inference.py          # ASRInferenceService class (Model loading, ThreadPool execution).
│   ├── schemas.py            # SQLAlchemy Async Models (Tables: 'sessions', 'segments').
│   └── storage.py            # StorageManager class (Redis buffering, MySQL persistence).
├── scripts/
│   ├── tests/                # (Placeholder for pytest)
│   └── simulate_stream.py    # Client simulation script for QA/Testing.
├── __init__.py
├── main.py                   # App Entry, Lifespan (Model Init), CORS, Exception Handlers.
├── .env                      # Local secrets (Gitignored).
└── requirements.txt          # Python dependencies.
```

## 4. Common Operational Commands
* **Run Server (Dev Mode):**
  
    ```bash
    uvicorn main:app --reload
    ```

## 5. Coding Standards
* **Type Hinting:** Use strict Python type hints (`def func(a: int) -> str:`).
* **Docstring Style:** Use **Google Style**.
* **Error Handling:**
    * Wrap WebSocket logic in `try/except WebSocketDisconnect`.
    * Ensure DB sessions are closed in `finally` blocks.
* **Libraries:**
    * Use `sqlalchemy` (Async) for MySQL.

## 6. Database Schema

* **sessions** (Table):
    * `id` (PK, String): Unique Session ID.
    * `user_id` (String): ID of the user.
    * `created_at` (DateTime): Creation timestamp.

* **segments** (Table):
    * `id` (PK, String): Unique Segment UUID.
    * `session_id` (FK, String): Reference to `sessions.id`.
    * `segment_seq` (Integer): Sequence number.
    * `content` (Text): Transcribed text.
    * `created_at` (DateTime): Creation timestamp.
    * **Indexes:** `idx_session_seq` (session_id, segment_seq).

---
**When analyzing issues or writing code, prioritize "Non-blocking I/O" and "Python 3.12 Compatibility".**