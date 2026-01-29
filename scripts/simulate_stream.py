"""
QA/Test script to simulate real-time audio streaming via WebSocket.
Verifies PyStreamASR Phase 4/5 implementation.

Usage:
    python scripts/simulate_stream.py --file my_speech.wav
"""

import argparse
import asyncio
import sys
import numpy as np
import websockets
import json
import time

try:
    import soundfile as sf
    import librosa
except ImportError:
    print("Error: Please install required libraries via 'pip install -r requirements.txt'")
    sys.exit(1)

# Try to import g711, or fall back to audioop or lookup table if needed
# The user requirements specifically mentions `g711` library.
try:
    import g711
except ImportError:
    # If g711 is missing from env despite requirements, we might define a simple lookup or use audioop
    import audioop
    print("Warning: 'g711' library not found, using 'audioop' as fallback.")
    g711 = None

def convert_to_alaw(pcm_data_int16):
    """
    Convert 16-bit PCM (numpy int16 array) to G.711 A-law bytes.
    """
    # If g711 library is installed and has encode_alaw
    if g711 and hasattr(g711, "encode_alaw"):
        # Some g711 libs take raw bytes, some take samples.
        # We'll assume it might take bytes of PCM.
        # Check standard usage: often g711.encode_alaw(samples)
        try:
            return g711.encode_alaw(pcm_data_int16.tobytes())
        except:
             # If it expects an iterable of ints
             return g711.encode_alaw(pcm_data_int16.tolist())
    
    # Fallback to audioop (standard python < 3.13)
    import audioop
    # audioop.lin2alaw takes bytes, width (2 for 16-bit)
    return audioop.lin2alaw(pcm_data_int16.tobytes(), 2)

async def send_audio(websocket, audio_file, chunk_duration):
    """
    Reads audio, resamples to 8000Hz, encodes to G.711 A-law, and streams chunks.
    """
    print(f"Loading and processing {audio_file}...")
    
    # 1. Load and Resample to 8000 Hz
    # librosa.load can resample on the fly
    try:
        y, sr = librosa.load(audio_file, sr=8000)
    except Exception as e:
        print(f"Failed to load audio: {e}")
        return

    # 2. Convert Float32 to Int16 PCM
    # librosa output is -1 to 1 float
    pcm_data = (y * 32767).astype(np.int16)
    
    # 3. Encode to G.711 A-law
    # Total bytes expected
    raw_bytes = convert_to_alaw(pcm_data)
    
    # 4. Calculate Chunk Size
    # G.711 is 1 byte per sample. 8000 samples/sec.
    # Chunk size = 8000 * chunk_duration
    chunk_size = int(8000 * chunk_duration)
    
    total_len = len(raw_bytes)
    print(f"Audio ready. Duration: {len(y)/8000:.2f}s. Total Bytes: {total_len}. Chunk Size: {chunk_size}")
    
    # 5. Stream
    offset = 0
    start_time = time.time()
    
    try:
        while offset < total_len:
            end = min(offset + chunk_size, total_len)
            chunk = raw_bytes[offset:end]
            
            # Send binary frame
            await websocket.send(chunk)
            
            offset = end
            
            # Real-time simulation sleep
            # Adjust sleep to match real-time (rudimentary)
            await asyncio.sleep(chunk_duration)
            
            # Optional: print progress?
            # sys.stdout.write(f"\rSent {offset}/{total_len} bytes")
            # sys.stdout.flush()
            
        print("\nFinished sending audio.")
        # Send a special message or just ensure we keep listening for final results?
        # Usually client might send an EOF or just close, or wait for final silence?
        # For this test, we just wait a bit then close? 
        # But `receive_results` loop is running.
        
    except Exception as e:
        print(f"\nSend error: {e}")

async def receive_results(websocket):
    """
    Listens for messages (partials/finals) from the server.
    """
    try:
        while True:
            message = await websocket.recv()
            data = json.loads(message)
            
            msg_type = data.get("type", "unknown")
            text = data.get("text", "")
            seq = data.get("seq", -1)
            
            print(f"Received [{msg_type}]: {text} (Seq: {seq})")
            
    except websockets.exceptions.ConnectionClosed:
        print("Server disconnected.")
    except Exception as e:
        print(f"Receive error: {e}")

async def main():
    parser = argparse.ArgumentParser(description="Simulate WebSocket Audio Stream")
    parser.add_argument("--file", default="test_audio.wav", help="Path to input audio file")
    parser.add_argument("--host", default="ws://localhost:8000/ws/transcribe/test-session-1", help="WebSocket URL")
    parser.add_argument("--chunk_duration", type=float, default=0.1, help="Chunk duration in seconds")
    
    args = parser.parse_args()
    
    print(f"Connecting to {args.host}...")
    
    try:
        async with websockets.connect(args.host) as websocket:
            print("Connected.")
            
            # Run send and receive in parallel
            send_task = asyncio.create_task(send_audio(websocket, args.file, args.chunk_duration))
            recv_task = asyncio.create_task(receive_results(websocket))
            
            # Wait for sender to finish
            await send_task
            
            # Allow some time for final results to return
            print("Waiting for final results...")
            await asyncio.sleep(2.0)
            
            # Cancel receiver
            recv_task.cancel()
            
    except Exception as e:
        print(f"Connection failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())
