import asyncio
import torch
from funasr import AutoModel
from typing import Dict, Any, Tuple

def load_model() -> AutoModel:
    """Loads the FunASR model with VAD and Punctuation support.

    Uses CUDA or MPS if available, otherwise falls back to CPU.

    Returns:
        AutoModel: The loaded FunASR model instance.
    """
    device = "cuda" if torch.cuda.is_available() else \
             "mps" if torch.backends.mps.is_available() else \
             "cpu"
    print(f"[Inference] Loading FunASR AutoModel on {device}...")
    
    # Load paraformer-zh-streaming with fsmn-vad and ct-punc
    model = AutoModel(
        model="paraformer-zh-streaming",
        vad_model="fsmn-vad",
        punc_model="ct-punc",
        device=device,
        disable_update=True,
    )
    
    print("[Inference] Model loaded successfully.")
    return model

class ASRInferenceService:
    def __init__(self, model: AutoModel):
        self.model = model

    async def infer(self, audio_input: Any, cache: Dict[str, Any]) -> Tuple[str, bool, Dict[str, Any]]:
        """Runs inference on the provided audio chunk.

        Executes in a thread executor to prevent blocking the asyncio event loop.

        Args:
            audio_input (Any): The prepared audio tensor or bytes.
            cache (Dict[str, Any]): Dictionary containing model state/context.

        Returns:
            Tuple[str, bool, Dict[str, Any]]: A tuple containing:
                - text (str): The transcribed text (partial or final).
                - is_final (bool): Whether the segment is considered complete (e.g., by VAD).
                - cache (Dict[str, Any]): Updated cache/state.
        """
        loop = asyncio.get_running_loop()

        def _blocking_infer():
            # AutoModel.generate handles the plumbing for streaming if cache is provided.
            res = self.model.generate(
                input=audio_input,
                cache=cache,
                is_final=False,
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
            is_final = result_item.get("is_final", False)
        
        return text, is_final, cache
