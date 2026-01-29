import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from services.audio import AudioProcessor
from services.storage import StorageManager
from services.inference import ASRInferenceService
from services.storage import redis_client
from core.config import settings

router = APIRouter()

@router.websocket("/ws/transcribe/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    await websocket.accept()
    
    # Initialize components
    processor = AudioProcessor()
    storage = StorageManager(session_id)
    # Param dict for model cache (streaming context)
    param_dict = {}
    
    # Get global model from app state
    # Note: We assume app.state.model is set in main.py lifespan
    model = websocket.app.state.model
    inference_service = ASRInferenceService(model)

    try:
        # Determine current sequence number to handle reconnections or continuations
        # We need the *next* sequence number for partials.
        # Key matches StorageManager's implementation: f"asr:sess:{self.session_id}:seq"
        seq_key = f"asr:sess:{session_id}:seq"
        current_seq_raw = await redis_client.get(seq_key)
        # If None, it means 0 (Redis behavior strictly or logic assumption), so next is 1.
        current_seq = int(current_seq_raw) if current_seq_raw else 0
        next_seq = current_seq + 1

        print(f"[WebSocket] Client connected: {session_id}. Start Seq: {next_seq}")

        while True:
            # 1. Receive Audio Bytes
            data = await websocket.receive_bytes()
            
            # 2. Process Audio (G.711 -> PCM -> Tensor)
            try:
                tensor = processor.process(data)
            except Exception as e:
                print(f"[WebSocket] Audio processing error: {e}")
                continue

            # 3. Inference
            # Returns text, is_final status, and updated cache
            text, is_final, param_dict = await inference_service.infer(tensor, param_dict)
            
            # 4. Logic Fork
            if not text:
                continue

            if is_final:
                # Save Final
                # save_final handles the sequence increment internally
                saved_segment = await storage.save_final(text)
                
                # Update our local next_seq to match the new reality
                # saved_segment.segment_seq should be the sequence number used
                if saved_segment:
                    next_seq = saved_segment.segment_seq + 1
                    response_seq = saved_segment.segment_seq
                else:
                    # Fallback if something weird happens
                    response_seq = next_seq
                    next_seq += 1

                # 5. Feedback (Final)
                await websocket.send_json({
                    "type": "final",
                    "text": text,
                    "seq": response_seq
                })
                
                # Reset param_dict? 
                # Usu. FunASR streaming maintains context unless we explicitly reset.
                # If 'is_final' implies end of sentence, we might keep cache for history 
                # or reset if it's a hard break. 
                # Paraformer-streaming continuous? I'll leave param_dict as is unless directed otherwise.
                # However, typically `is_final=True` output from model might reset internal state logic?
                # We'll just pass updated `param_dict` back.

            else:
                # Save Partial
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
        # Cleanup if needed. StorageManager sessions close contextually, 
        # but if we wanted to enforce anything else, do it here.
        pass
