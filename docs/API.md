# PyStreamASR API Documentation

## Real-Time Speech Transcription

### Method & URL

```
WebSocket /ws/transcribe/{session_id}
```

### Description

Establishes a WebSocket connection for real-time speech-to-text transcription. The client streams audio data encoded in G.711 format (8kHz), and the server returns transcription results as either partial (interim) or final segments.

This endpoint supports session continuity—reconnecting with the same `session_id` will resume from the last known sequence number.

---

### Path Parameters

| Name         | Type   | Required | Description                                      |
|--------------|--------|----------|--------------------------------------------------|
| `session_id` | string | Yes      | Unique identifier for the transcription session. |

---

### Request Headers

WebSocket connections follow the standard HTTP upgrade handshake ([RFC 6455](https://datatracker.ietf.org/doc/html/rfc6455)). No custom headers are required.

---

### Client → Server Messages

**Format:** Binary (raw bytes)

| Field       | Type   | Description                                                  |
|-------------|--------|--------------------------------------------------------------|
| Audio Data  | bytes  | G.711 encoded audio at 8kHz sample rate (μ-law or A-law).   |

---

### Server → Client Messages

**Format:** JSON

#### Partial Result

Sent when interim transcription is available (not yet finalized).

```json
{
  "type": "partial",
  "text": "hello wor",
  "seq": 5
}
```

| Field  | Type    | Description                                    |
|--------|---------|------------------------------------------------|
| `type` | string  | Always `"partial"` for interim results.        |
| `text` | string  | Current transcription hypothesis.              |
| `seq`  | integer | Sequence number for ordering.                  |

#### Final Result

Sent when a segment is finalized and persisted to the database.

```json
{
  "type": "final",
  "text": "hello world",
  "seq": 5
}
```

| Field  | Type    | Description                                    |
|--------|---------|------------------------------------------------|
| `type` | string  | Always `"final"` for confirmed transcriptions. |
| `text` | string  | Finalized transcription text.                  |
| `seq`  | integer | Segment sequence number (persisted).           |

---

### Example Request

**JavaScript (Browser)**

```javascript
const sessionId = "user-123-session-456";
const ws = new WebSocket(`ws://localhost:8000/ws/transcribe/${sessionId}`);

ws.onopen = () => {
  console.log("Connected");
  // Stream audio data as binary
  navigator.mediaDevices.getUserMedia({ audio: true })
    .then(stream => {
      // Process and send G.711 encoded audio chunks
      // ws.send(audioChunk);
    });
};

ws.onmessage = (event) => {
  const result = JSON.parse(event.data);
  if (result.type === "final") {
    console.log(`Final [${result.seq}]: ${result.text}`);
  } else {
    console.log(`Partial [${result.seq}]: ${result.text}`);
  }
};

ws.onclose = () => console.log("Disconnected");
```

**Python (websockets library)**

```python
import asyncio
import websockets

async def stream_audio():
    uri = "ws://localhost:8000/ws/transcribe/user-123-session-456"
    async with websockets.connect(uri) as ws:
        # Send G.711 audio bytes
        with open("audio.g711", "rb") as f:
            while chunk := f.read(320):  # 20ms frames
                await ws.send(chunk)

                # Receive transcription
                response = await ws.recv()
                print(response)

asyncio.run(stream_audio())
```

---

### Example Responses

#### Success — Partial Transcription

```json
{
  "type": "partial",
  "text": "the quick brown",
  "seq": 3
}
```

#### Success — Final Transcription

```json
{
  "type": "final",
  "text": "the quick brown fox jumps over the lazy dog",
  "seq": 3
}
```

#### Connection Closed (Normal)

The server closes the WebSocket gracefully when the client disconnects. No error payload is sent.

---

### Error Handling

| Scenario                  | Behavior                                                |
|---------------------------|---------------------------------------------------------|
| Invalid audio format      | Connection remains open; error logged server-side.      |
| Processing error          | Skipped frame; next audio chunk processed normally.     |
| Database error            | Logged; partial results may still be returned.          |
| Unexpected server error   | Connection closed; error logged with stack trace.       |

---

### Notes

- **Audio Format:** Input must be G.711 encoded at 8kHz. The server resamples to 16kHz internally for the ASR model.
- **Session Persistence:** Final transcriptions are stored in MySQL; partial results are cached in Redis.
- **Reconnection:** Using the same `session_id` resumes from the last sequence number.
- **Concurrency:** Audio processing and inference run in a thread pool to avoid blocking the event loop.
