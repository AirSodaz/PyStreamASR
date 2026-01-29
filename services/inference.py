import asyncio
import torch
from funasr import AutoModel
from typing import Dict, Any, Tuple, Optional

def load_model() -> AutoModel:
    """
    Loads the FunASR model with VAD and Punctuation support.
    Uses CUDA if available.
    """
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[Inference] Loading FunASR AutoModel on {device}...")
    
    # Load paraformer-zh-streaming with fsmn-vad and ct-punc
    model = AutoModel(
        model="paraformer-zh-streaming",
        vad_model="fsmn-vad",
        punc_model="ct-punc",
        device=device,
    )
    
    print("[Inference] Model loaded successfully.")
    return model

class ASRInferenceService:
    def __init__(self, model: AutoModel):
        self.model = model

    async def infer(self, audio_input: Any, cache: Dict[str, Any]) -> Tuple[str, bool, Dict[str, Any]]:
        """
        Runs inference on the provided audio chunk.
        Executes in a thread executor to prevent blocking the asyncio event loop.
        
        Args:
            audio_input: The prepared audio tensor or bytes.
            cache: Dictionary containing model state/context.
            
        Returns:
            Tuple containing:
            - text (str): The transcribed text (partial or final).
            - is_final (bool): Whether the segment is considered complete (e.g. by VAD).
            - cache (dict): Updated cache/state.
        """
        loop = asyncio.get_running_loop()

        def _blocking_infer():
            # AutoModel.generate handles the plumbing for streaming if cache is provided.
            # Note: For streaming, we typically pass the audio chunk.
            # The exact return structure depends on the model, but usually it's a list of results.
            # We assume inputs are properly formatted by the caller (AudioProcessor).
            
            # Using defaults for other params (chunk_size etc from model config)
            res = self.model.generate(
                input=audio_input,
                cache=cache,
                is_final=False,  # This tells the model valid audio is coming; force finish with True if stream ends
            )
            return res

        # Run CPU-bound generation in a separate thread
        res = await loop.run_in_executor(None, _blocking_infer)
        
        text = ""
        is_final = False

        # Parse FunASR result
        # Expected format example: [{'text': '...', 'mode': '2pass-online', 'is_final': True/False, ...}]
        if res and isinstance(res, list) and len(res) > 0:
            result_item = res[0]
            text = result_item.get("text", "")
            # Use 'is_final' from result if present
            is_final = result_item.get("is_final", False)
            
            # Sometimes 'text' is just the partial text.
        
        return text, is_final, cache
