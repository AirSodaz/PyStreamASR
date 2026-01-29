import json
import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from services.audio import AudioProcessor
from services.storage import StorageManager
from services.inference import ASRInferenceService
from services.storage import redis_client
from core.config import settings

router = APIRouter()


@router.websocket("/ws/transcribe/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """WebSocket endpoint for real-time speech transcription.

    Handles audio streaming, processing, inference, and result broadcasting.

    Args:
        websocket (WebSocket): The WebSocket connection instance.
        session_id (str): Unique identifier for the transcription session.
    """
    await websocket.accept()

    # Initialize components
    processor = AudioProcessor()
    storage = StorageManager(session_id)
    # Param dict for model cache (streaming context)
    param_dict = {}

    # Get global model from app state
    model = websocket.app.state.model
    inference_service = ASRInferenceService(model)

    try:
        # Determine current sequence number to handle reconnections or continuations
        seq_key = f"asr:sess:{session_id}:seq"
        current_seq_raw = await redis_client.get(seq_key)
        current_seq = int(current_seq_raw) if current_seq_raw else 0
        next_seq = current_seq + 1

        print(f"[WebSocket] Client connected: {session_id}. Start Seq: {next_seq}")

        while True:
            # 1. Receive Audio Bytes
            data = await websocket.receive_bytes()

            # 2. Process Audio (G.711 -> PCM -> Tensor)
            try:
                loop = asyncio.get_running_loop()
                tensor = await loop.run_in_executor(None, processor.process, data)
            except Exception as e:
                print(f"[WebSocket] Audio processing error: {e}")
                continue

            # 3. Inference
            text, is_final, param_dict = await inference_service.infer(tensor, param_dict)

            if not text:
                continue

            if is_final:
                # 4. Save Final
                saved_segment = await storage.save_final(text)

                if saved_segment:
                    next_seq = saved_segment.segment_seq + 1
                    response_seq = saved_segment.segment_seq
                else:
                    response_seq = next_seq
                    next_seq += 1

                # 5. Feedback (Final)
                await websocket.send_json({
                    "type": "final",
                    "text": text,
                    "seq": response_seq
                })

            else:
                # 4. Save Partial
                await storage.save_partial(text, next_seq)

                # 5. Feedback (Partial)
                await websocket.send_json({
                    "type": "partial",
                    "text": text,
                    "seq": next_seq
                })

    except WebSocketDisconnect:
        print(f"[WebSocket] Client disconnected: {session_id}")
    except Exception as e:
        print(f"[WebSocket] Unexpected error: {e}")
    finally:
        pass
