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
import os
import struct
import redis.asyncio as redis
from dotenv import load_dotenv

load_dotenv()

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

def parse_wav_header(file_path):
    """
    Parses WAV header to determine if it's G.711 A-law (Format=6) or PCM (Format=1).
    Returns (is_alaw, data_start_offset, data_length)
    is_alaw is True if Format=6 (A-law), Channels=1, SampleRate=8000, Bits=8.
    """
    try:
        with open(file_path, 'rb') as f:
            # RIFF header
            chunk_id, size, format_tag = struct.unpack('<4sI4s', f.read(12))
            if chunk_id != b'RIFF' or format_tag != b'WAVE':
                return False, 0, 0
            
            # Find chunks
            while True:
                chunk_header = f.read(8)
                if len(chunk_header) < 8:
                    break
                subchunk_id, subchunk_size = struct.unpack('<4sI', chunk_header)
                
                if subchunk_id == b'fmt ':
                    # Parse fmt chunk
                    fmt_data = f.read(subchunk_size)
                    audio_format, num_channels, sample_rate, byte_rate, block_align, bits_per_sample = struct.unpack('<HHIIHH', fmt_data[:16])
                    
                    # Check for G.711 A-law: Format 6, 1 channel, 8000Hz, 8 bits
                    if audio_format == 6 and num_channels == 1 and sample_rate == 8000 and bits_per_sample == 8:
                        # Found A-law, but need to find data chunk next
                        pass
                    elif audio_format == 7: # Mu-law
                         pass
                    elif audio_format == 1: # PCM
                         pass
                    
                    # Store params for return if needed, but we just want boolean "is_alaw_ready"
                    is_alaw_ready = (audio_format == 6 and num_channels == 1 and sample_rate == 8000 and bits_per_sample == 8)
                    
                    if subchunk_size > 16:
                        # Skip extra bytes if any (though we read subchunk_size above? No we read subchunk_size bytes? 
                        # Wait, f.read(subchunk_size) reads ALL content. correct.)
                        pass

                elif subchunk_id == b'data':
                    # Found data
                    data_start = f.tell()
                    return (locals().get('is_alaw_ready', False), data_start, subchunk_size)
                else:
                    # Skip other chunks
                    f.seek(subchunk_size, 1)
                    
    except Exception as e:
        print(f"Header parse error: {e}")
    
    return False, 0, 0

async def get_audio_generator(audio_file, chunk_duration):
    """
    Yields chunks of G.711 A-law bytes.
    Handles raw files, G.711 WAVs, and standard PCM WAVs (via conversion).
    """
    chunk_size = int(8000 * chunk_duration)
    
    # 1. Check extension for raw
    ext = os.path.splitext(audio_file)[1].lower()
    if ext in ['.alaw', '.pcma', '.g711']:
        print(f"Detected raw G.711 file: {audio_file}")
        with open(audio_file, 'rb') as f:
            while True:
                data = f.read(chunk_size)
                if not data:
                    break
                yield data
        return

    # 2. Check WAV header
    is_alaw, data_offset, data_len = parse_wav_header(audio_file)
    if is_alaw:
        print(f"Detected G.711 A-law WAV: {audio_file} (passing through)")
        with open(audio_file, 'rb') as f:
            f.seek(data_offset)
            read_bytes = 0
            while read_bytes < data_len:
                # Determine read size
                bytes_to_read = min(chunk_size, data_len - read_bytes)
                if bytes_to_read == 0:
                    break
                data = f.read(bytes_to_read)
                if not data:
                    break
                yield data
                read_bytes += len(data)
        return

    # 3. Fallback to Librosa/Conversion (Standard PCM)
    print(f"Processing as PCM WAV/Audio: {audio_file}...")
    try:
        # Load and Resample
        y, sr = librosa.load(audio_file, sr=8000)
    except Exception as e:
        print(f"Failed to load audio: {e}")
        return

    # Convert to Int16
    pcm_data = (y * 32767).astype(np.int16)
    
    # Encode
    raw_bytes = convert_to_alaw(pcm_data)
    
    total_len = len(raw_bytes)
    offset = 0
    while offset < total_len:
        end = min(offset + chunk_size, total_len)
        yield raw_bytes[offset:end]
        offset = end

async def send_audio(websocket, audio_file, chunk_duration):
    """
    Streams audio chunks to WebSocket.
    """
    print(f"Preparing stream for {audio_file}...")
    
    chunk_generator = get_audio_generator(audio_file, chunk_duration)
    
    start_time = time.time()
    chunk_count = 0
    
    try:
        async for chunk in chunk_generator:
            await websocket.send(chunk)
            chunk_count += 1
            
            # Real-time simulation sleep
            await asyncio.sleep(chunk_duration)
            
        print(f"\nFinished sending audio. Chunks sent: {chunk_count}")
        
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

async def monitor_redis(session_id: str, redis_url: str):
    """
    Polls Redis for real-time partial transcription updates.
    """
    print(f"Connecting to Redis at {redis_url} for session {session_id}...")
    try:
        client = redis.from_url(redis_url, decode_responses=True)
        key = f"asr:sess:{session_id}:current"
        
        last_seq = -1
        
        while True:
            # Poll every 0.1s
            data = await client.hgetall(key)
            if data:
                seq = int(data.get("seq", -1))
                content = data.get("content", "")
                
                if seq > last_seq:
                    print(f"[Redis] Partial (Seq: {seq}): {content}")
                    last_seq = seq
            
            await asyncio.sleep(0.1)
            
    except asyncio.CancelledError:
        print("Redis monitoring stopped.")
        await client.aclose()
    except Exception as e:
        print(f"Redis monitor error: {e}")

async def main():
    parser = argparse.ArgumentParser(description="Simulate WebSocket Audio Stream")
    parser.add_argument("--file", default="test_audio.wav", help="Path to input audio file")
    parser.add_argument("--host", default="ws://localhost:8000/ws/transcribe/test-session-1", help="WebSocket URL")
    parser.add_argument("--chunk_duration", type=float, default=0.6, help="Chunk duration in seconds")
    parser.add_argument("--redis", action="store_true", help="Enable Redis monitoring")
    parser.add_argument("--redis_url", default=os.getenv("REDIS_URL", "redis://localhost:6379/0"), help="Redis URL")
    
    args = parser.parse_args()
    
    print(f"Connecting to {args.host}...")
    
    try:
        async with websockets.connect(args.host) as websocket:
            print("Connected.")
            
            # Extract session_id from URL for Redis monitoring
            # URL format: .../ws/transcribe/{session_id}
            session_id = args.host.split("/")[-1]

            # Run send and receive in parallel
            tasks = [
                asyncio.create_task(send_audio(websocket, args.file, args.chunk_duration)),
                asyncio.create_task(receive_results(websocket))
            ]

            if args.redis:
                redis_task = asyncio.create_task(monitor_redis(session_id, args.redis_url))
                tasks.append(redis_task)
            
            # Wait for sender to finish (first task)
            await tasks[0]
            
            # Allow some time for final results to return
            print("Waiting for final results...")
            await asyncio.sleep(2.0)
            
            # Cancel utility tasks
            for task in tasks[1:]:
                task.cancel()
            
    except Exception as e:
        print(f"Connection failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())
