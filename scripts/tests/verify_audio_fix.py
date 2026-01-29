
import sys
import os
import numpy as np
import torch
import g711

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from services.audio import AudioProcessor

def verify_fix():
    print("Verifying Audio Fix...")

    processor = AudioProcessor()

    # 1. Generate Synthetic Audio (Sine wave at 440Hz, 8000Hz SR)
    sr = 8000
    duration = 1.0
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    # Sine wave amplitude 0.5 (float)
    y = 0.5 * np.sin(2 * np.pi * 440 * t)

    # 2. Encode to G.711 A-law
    pcm_data = (y * 32767).astype(np.int16)

    encoded = None
    # Force list encoding to ensure correct input to the encoder
    # (bytes encoding might treat input as 8-bit depending on library version)
    encoded = g711.encode_alaw(pcm_data.tolist())

    if not isinstance(encoded, (bytes, bytearray)):
        encoded = bytes(encoded)

    print(f"Encoded bytes length: {len(encoded)}")

    # 3. Process using AudioProcessor
    try:
        # Debug internal steps
        pcm_out = processor.decode_g711(encoded)
        print(f"Decoded type: {type(pcm_out)}")
        print(f"Decoded dtype: {pcm_out.dtype}")
        print(f"Decoded max: {np.max(np.abs(pcm_out))}")

        output_tensor = processor.process(encoded)

        print(f"Output Tensor Shape: {output_tensor.shape}")

        # Expected shape: 8000 samples -> 16000 samples
        assert output_tensor.shape[0] == 16000, f"Expected 16000 samples, got {output_tensor.shape[0]}"

        max_val = torch.max(torch.abs(output_tensor)).item()
        print(f"Output Tensor Max Amplitude: {max_val}")

        # Expected max amplitude ~0.5 (resampling might change it slightly)
        # Previously it was ~1.5e-05 (silence)

        if max_val < 0.1:
            print("FAILURE: Output is silent or near-silent.")
            sys.exit(1)

        if max_val > 1.0:
            print("WARNING: Output clipped?")

        print("SUCCESS: Signal preserved and processed correctly.")

    except Exception as e:
        print(f"Processing failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    verify_fix()
