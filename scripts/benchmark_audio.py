import time
import numpy as np
import g711
from services.audio import AudioProcessor
from core.config import settings

def benchmark_getattr():
    # Simulate the current implementation
    data = bytes([0] * 160) # 20ms of G.711 at 8kHz
    iterations = 100000

    start = time.perf_counter()
    for _ in range(iterations):
        decoder = getattr(g711, "decode_alaw", None)
        _ = decoder(data)
    end = time.perf_counter()
    print(f"getattr lookup + decode: {end - start:.6f}s for {iterations} iterations")

def benchmark_direct():
    # Simulate the optimized implementation
    data = bytes([0] * 160)
    iterations = 100000
    decoder = g711.decode_alaw

    start = time.perf_counter()
    for _ in range(iterations):
        _ = decoder(data)
    end = time.perf_counter()
    print(f"Direct call + decode: {end - start:.6f}s for {iterations} iterations")

if __name__ == "__main__":
    benchmark_getattr()
    benchmark_direct()
