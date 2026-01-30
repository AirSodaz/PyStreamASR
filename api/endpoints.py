import json
import logging
import asyncio
import time
import contextvars
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from services.audio import AudioProcessor
from services.storage import StorageManager
from services.inference import ASRInferenceService
from services.storage import redis_client
from core.config import settings
from core.context import session_id_ctx

router = APIRouter()


@router.websocket("/ws/transcribe/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """WebSocket endpoint for real-time speech transcription.

    Handles audio streaming, processing, inference, and result broadcasting.

    Args:
        websocket (WebSocket): The WebSocket connection instance.
        session_id (str): Unique identifier for the transcription session.
    """
    # Set correlation ID context
    token = session_id_ctx.set(session_id)

    await websocket.accept()
    logging.info(f"[WebSocket] Connection accepted for session: {session_id}")

    # Initialize components
    processor = AudioProcessor()
    storage = StorageManager(session_id)

    # Get global model from app state
    model = websocket.app.state.model
    inference_service = ASRInferenceService(model)

    try:
        # Determine current sequence number to handle reconnections or continuations
        seq_key = f"asr:sess:{session_id}:seq"
        current_seq_raw = await redis_client.get(seq_key)
        current_seq = int(current_seq_raw) if current_seq_raw else 0
        next_seq = current_seq + 1

        logging.info(f"[WebSocket] Client connected: {session_id}. Start Seq: {next_seq}")

        # Ensure session exists in DB to satisfy foreign key constraints
        await storage.ensure_session_exists(user_id="websocket_client")

        while True:
            # 1. Receive Audio Bytes
            try:
                data = await websocket.receive_bytes()
            except WebSocketDisconnect:
                raise
            except Exception as e:
                logging.error(f"[WebSocket] Receive error: {e}")
                break

            # 2. Process Audio (G.711 -> PCM -> Samples)
            try:
                loop = asyncio.get_running_loop()
                ctx = contextvars.copy_context()
                samples = await loop.run_in_executor(None, ctx.run, processor.process, data)
            except Exception as e:
                logging.error(f"[WebSocket] Audio processing error: {e}")
                continue

            # 3. Inference
            text, is_final = await inference_service.infer(samples)

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
                logging.info(f"[WebSocket] Sent FINAL: {text} (Seq: {response_seq})")

            else:
                # 4. Save Partial
                await storage.save_partial(text, next_seq)

                # 5. Feedback (Partial)
                await websocket.send_json({
                    "type": "partial",
                    "text": text,
                    "seq": next_seq
                })
                logging.debug(f"[WebSocket] Sent PARTIAL: {text} (Seq: {next_seq})")

    except WebSocketDisconnect:
        logging.info(f"[WebSocket] Client disconnected: {session_id}")
    except Exception as e:
        logging.error(f"[WebSocket] Unexpected error: {e}", exc_info=True)
    finally:
        logging.info(f"[WebSocket] Connection closed: {session_id}")
        session_id_ctx.reset(token)
