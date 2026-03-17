import asyncio
import contextvars
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import numpy as np

from services.audio import (
    AudioProcessor,
    DebugAudioWriter,
    append_debug_audio_samples,
    close_debug_audio_writer,
    is_debug_audio_enabled,
)
from services.storage import StorageManager
from services.inference import ASRInferenceService
from core.config import settings
from core.context import session_id_ctx

router = APIRouter()


async def _append_debug_audio(
    loop: asyncio.AbstractEventLoop,
    writer: DebugAudioWriter | None,
    session_id: str,
    samples: np.ndarray,
) -> DebugAudioWriter | None:
    """Persist processed audio samples without blocking the event loop."""
    append_ctx = contextvars.copy_context()
    return await loop.run_in_executor(
        None,
        append_ctx.run,
        append_debug_audio_samples,
        writer,
        session_id,
        samples,
    )


async def _close_debug_audio_writer(
    loop: asyncio.AbstractEventLoop,
    writer: DebugAudioWriter | None,
) -> None:
    """Close a debug-audio writer without blocking the event loop."""
    if writer is None:
        return

    try:
        close_ctx = contextvars.copy_context()
        await loop.run_in_executor(None, close_ctx.run, close_debug_audio_writer, writer)
    except Exception as exc:
        logging.error(f"[WebSocket] Failed to close debug audio writer: {exc}")


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
    last_text: str = ""
    last_is_final: bool = True
    debug_audio_writer: DebugAudioWriter | None = None
    debug_audio_enabled = is_debug_audio_enabled()

    try:
        # Determine current sequence number to handle reconnections or continuations
        current_seq = await storage.get_current_sequence()
        next_seq = current_seq + 1
        loop = asyncio.get_running_loop()

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
                ctx = contextvars.copy_context()
                samples = await loop.run_in_executor(None, ctx.run, processor.process, data)
            except Exception as e:
                logging.error(f"[WebSocket] Audio processing error: {e}")
                continue

            if debug_audio_enabled:
                debug_audio_writer = await _append_debug_audio(
                    loop=loop,
                    writer=debug_audio_writer,
                    session_id=session_id,
                    samples=samples,
                )

            # 3. Inference
            text, is_final = await inference_service.infer(samples)

            # Update tracking state
            if text:
                last_text = text
                last_is_final = is_final

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
                if settings.RETURN_TRANSCRIPTION:
                    await websocket.send_json({
                        "type": "final",
                        "text": text,
                        "seq": response_seq
                    })
                    logging.info(f"[WebSocket] Sent FINAL: {text} (Seq: {response_seq})")
                else:
                    logging.info(f"[WebSocket] Tracking FINAL: {text} (Seq: {response_seq}) (Response Disabled)")

            else:
                # 4. Save Partial
                await storage.save_partial(text, next_seq)

                # 5. Feedback (Partial)
                if settings.RETURN_TRANSCRIPTION:
                    await websocket.send_json({
                        "type": "partial",
                        "text": text,
                        "seq": next_seq
                    })
                    logging.debug(f"[WebSocket] Sent PARTIAL: {text} (Seq: {next_seq})")
                else:
                    logging.debug(f"[WebSocket] Tracking PARTIAL: {text} (Seq: {next_seq}) (Response Disabled)")

    except WebSocketDisconnect:
        logging.info(f"[WebSocket] Client disconnected: {session_id}")
    except Exception as e:
        logging.error(f"[WebSocket] Unexpected error: {e}", exc_info=True)
    finally:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        # Check if we have a pending partial result that needs to be finalized
        if last_text and not last_is_final:
            logging.info(
                f"[WebSocket] Connection closed with pending partial. Finalizing: '{last_text}'"
            )
            try:
                # Save as final
                saved_segment = await storage.save_final(last_text)
                if saved_segment:
                    logging.info(f"[WebSocket] Auto-finalized segment seq: {saved_segment.segment_seq}")
            except Exception as e:
                logging.error(f"[WebSocket] Failed to auto-finalize pending text: {e}")

        if debug_audio_enabled and loop is not None:
            await _close_debug_audio_writer(loop, debug_audio_writer)

        logging.info(f"[WebSocket] Connection closed: {session_id}")
        session_id_ctx.reset(token)
