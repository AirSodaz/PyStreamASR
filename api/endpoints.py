import asyncio
import contextvars
import logging
import time
import uuid

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
from services.inference import (
    ASRInferenceService,
    INFERENCE_OVERLOAD_CLOSE_CODE,
    INFERENCE_OVERLOAD_CLOSE_REASON,
    INFERENCE_OVERLOAD_ERROR_CODE,
    INFERENCE_OVERLOAD_MESSAGE,
    InferenceBackpressureError,
)
from core.config import settings
from core.context import connection_id_ctx, session_id_ctx

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


async def _send_inference_overload_error(websocket: WebSocket) -> None:
    """Send an inference overload event and close the WebSocket."""
    await websocket.send_json({
        "type": "error",
        "code": INFERENCE_OVERLOAD_ERROR_CODE,
        "message": INFERENCE_OVERLOAD_MESSAGE,
        "retryable": True,
    })
    await websocket.close(
        code=INFERENCE_OVERLOAD_CLOSE_CODE,
        reason=INFERENCE_OVERLOAD_CLOSE_REASON,
    )


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
    connection_id = uuid.uuid4().hex[:12]
    connection_token = connection_id_ctx.set(connection_id)
    connection_start = time.perf_counter()
    connection_opened = False
    connection_disconnected = False
    connection_had_error = False
    runtime_metrics = getattr(websocket.app.state, "runtime_metrics", None)

    try:
        await websocket.accept()
    except Exception:
        connection_id_ctx.reset(connection_token)
        session_id_ctx.reset(token)
        raise

    if runtime_metrics is not None:
        runtime_metrics.record_connection_opened()
        connection_opened = True

    logging.info(
        f"[WebSocket] Connection accepted for session: {session_id}, "
        f"connection_id={connection_id}"
    )

    # Initialize components
    processor = AudioProcessor()
    storage = StorageManager(session_id)

    # Get global model from app state
    model = websocket.app.state.model
    inference_executor = websocket.app.state.inference_executor
    inference_service = ASRInferenceService(model, inference_executor)
    last_text: str = ""
    last_is_final: bool = True
    skip_auto_finalize = False
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
                connection_had_error = True
                if runtime_metrics is not None:
                    runtime_metrics.record_receive_error()
                logging.error(f"[WebSocket] Receive error: {e}")
                break

            if runtime_metrics is not None:
                runtime_metrics.record_websocket_chunk(len(data))

            # 2. Process Audio (G.711 -> PCM -> Samples)
            audio_start = time.perf_counter()
            try:
                ctx = contextvars.copy_context()
                samples = await loop.run_in_executor(None, ctx.run, processor.process, data)
            except Exception as e:
                if runtime_metrics is not None:
                    runtime_metrics.record_audio_processing_error()
                logging.error(f"[WebSocket] Audio processing error: {e}")
                continue
            else:
                if runtime_metrics is not None:
                    runtime_metrics.record_audio_processed(time.perf_counter() - audio_start)

            if debug_audio_enabled:
                debug_audio_writer = await _append_debug_audio(
                    loop=loop,
                    writer=debug_audio_writer,
                    session_id=session_id,
                    samples=samples,
                )

            # 3. Inference
            try:
                text, is_final = await inference_service.infer(samples)
            except InferenceBackpressureError as exc:
                skip_auto_finalize = True
                if runtime_metrics is not None:
                    runtime_metrics.record_overload_close()
                logging.warning(f"[WebSocket] Inference overloaded for session {session_id}: {exc}")
                try:
                    await _send_inference_overload_error(websocket)
                except Exception as send_exc:
                    logging.error(
                        f"[WebSocket] Failed to send inference overload error: {send_exc}"
                    )
                break

            # Update tracking state
            if text:
                last_text = text
                last_is_final = is_final

            if not text:
                if runtime_metrics is not None:
                    runtime_metrics.record_empty_result()
                continue

            if is_final:
                # 4. Save Final
                storage_start = time.perf_counter()
                try:
                    saved_segment = await storage.save_final(text)
                except Exception:
                    connection_had_error = True
                    if runtime_metrics is not None:
                        runtime_metrics.record_storage_error()
                    raise
                else:
                    if runtime_metrics is not None:
                        runtime_metrics.record_final_save(
                            time.perf_counter() - storage_start
                        )

                if saved_segment:
                    next_seq = saved_segment.segment_seq + 1
                    response_seq = saved_segment.segment_seq
                else:
                    response_seq = next_seq
                    next_seq += 1

                if runtime_metrics is not None:
                    runtime_metrics.record_final()

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
                storage_start = time.perf_counter()
                try:
                    await storage.save_partial(text, next_seq)
                except Exception:
                    if runtime_metrics is not None:
                        runtime_metrics.record_storage_error()
                    logging.error("[WebSocket] Failed to save partial", exc_info=True)
                    continue
                else:
                    if runtime_metrics is not None:
                        runtime_metrics.record_partial_save(
                            time.perf_counter() - storage_start
                        )
                        runtime_metrics.record_partial()

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
        connection_disconnected = True
        logging.info(f"[WebSocket] Client disconnected: {session_id}")
    except Exception as e:
        connection_had_error = True
        logging.error(f"[WebSocket] Unexpected error: {e}", exc_info=True)
    finally:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        # Check if we have a pending partial result that needs to be finalized
        if last_text and not last_is_final and not skip_auto_finalize:
            logging.info(
                f"[WebSocket] Connection closed with pending partial. Finalizing: '{last_text}'"
            )
            try:
                # Save as final
                storage_start = time.perf_counter()
                saved_segment = await storage.save_final(last_text)
                if runtime_metrics is not None:
                    runtime_metrics.record_final_save(
                        time.perf_counter() - storage_start
                    )
                    runtime_metrics.record_final()
                    runtime_metrics.record_auto_finalized()
                if saved_segment:
                    logging.info(f"[WebSocket] Auto-finalized segment seq: {saved_segment.segment_seq}")
            except Exception as e:
                connection_had_error = True
                if runtime_metrics is not None:
                    runtime_metrics.record_storage_error()
                logging.error(f"[WebSocket] Failed to auto-finalize pending text: {e}")

        if debug_audio_enabled and loop is not None:
            await _close_debug_audio_writer(loop, debug_audio_writer)

        if connection_opened and runtime_metrics is not None:
            runtime_metrics.record_connection_closed(
                time.perf_counter() - connection_start,
                disconnected=connection_disconnected,
                error=connection_had_error,
            )

        logging.info(f"[WebSocket] Connection closed: {session_id}")
        connection_id_ctx.reset(connection_token)
        session_id_ctx.reset(token)
