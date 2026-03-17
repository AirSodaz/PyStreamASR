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
from dotenv import load_dotenv

load_dotenv()

try:
    import soundfile as sf
    import librosa
except ImportError:
    print("Error: Please install required libraries via 'pip install -r requirements.txt'")
    sys.exit(1)

# Try to import g711 (required for G.711 encoding).
try:
    import g711
except ImportError:
    print("Error: 'g711' library not found. Install it via 'pip install g711'.")
    sys.exit(1)

def encode_g711(pcm_data_int16, audio_format):
    """
    Convert 16-bit PCM (numpy int16 array) to G.711 bytes.
    """
    if audio_format == "ulaw":
        encoder_name = "encode_ulaw"
    else:
        encoder_name = "encode_alaw"

    encoder = getattr(g711, encoder_name, None)
    if encoder is None:
        raise RuntimeError(f"g711.{encoder_name} is not available. Install or update the 'g711' package.")

    # Some g711 libs take raw bytes, some take samples.
    # We'll assume it might take bytes of PCM.
    try:
        return encoder(pcm_data_int16.tobytes())
    except Exception:
        # If it expects an iterable of ints
        return encoder(pcm_data_int16.tolist())

def parse_wav_header(file_path):
    """
    Parses WAV header to determine if it's G.711 A-law (Format=6), μ-law (Format=7), or PCM (Format=1).
    Returns (format_str, sample_rate, data_start_offset, data_length)
    format_str: "alaw", "ulaw", "pcm16le", or "" if unknown.
    """
    try:
        with open(file_path, 'rb') as f:
            # RIFF header
            chunk_id, size, format_tag = struct.unpack('<4sI4s', f.read(12))
            if chunk_id != b'RIFF' or format_tag != b'WAVE':
                return "", 0, 0, 0
            
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
                    
                    if audio_format == 6 and num_channels == 1 and bits_per_sample == 8:
                        fmt = "alaw"
                    elif audio_format == 7 and num_channels == 1 and bits_per_sample == 8:
                        fmt = "ulaw"
                    elif audio_format == 1 and num_channels == 1 and bits_per_sample == 16:
                        fmt = "pcm16le"
                    else:
                        fmt = ""
                    
                    if subchunk_size > 16:
                        # Skip extra bytes if any (though we read subchunk_size above? No we read subchunk_size bytes? 
                        # Wait, f.read(subchunk_size) reads ALL content. correct.)
                        pass

                elif subchunk_id == b'data':
                    # Found data
                    data_start = f.tell()
                    return (locals().get('fmt', ""), locals().get('sample_rate', 0), data_start, subchunk_size)
                else:
                    # Skip other chunks
                    f.seek(subchunk_size, 1)
                    
    except Exception as e:
        print(f"Header parse error: {e}")
    
    return "", 0, 0, 0

async def get_audio_generator(audio_file, chunk_duration, audio_format, sample_rate):
    """
    Yields chunks of audio bytes.
    Handles raw files, G.711 WAVs, and standard PCM WAVs (via conversion).
    """
    bytes_per_sample = 1 if audio_format in ("alaw", "ulaw") else 2
    samples_per_chunk = int(sample_rate * chunk_duration)
    chunk_size = samples_per_chunk * bytes_per_sample
    
    # 1. Check extension for raw
    ext = os.path.splitext(audio_file)[1].lower()
    if ext in ['.alaw', '.pcma', '.g711', '.ulaw', '.pcmu', '.mulaw']:
        detected_format = "alaw" if ext in ['.alaw', '.pcma', '.g711'] else "ulaw"
        if audio_format != detected_format:
            print(f"Error: Raw G.711 file is {detected_format}, but --format is {audio_format}.")
            return
        print(f"Detected raw G.711 {detected_format}: {audio_file}")
        with open(audio_file, 'rb') as f:
            while True:
                data = f.read(chunk_size)
                if not data:
                    break
                yield data
        return
    if ext in ['.pcm', '.raw']:
        if audio_format != "pcm16le":
            print("Error: Raw PCM file requires --format pcm16le.")
            return
        print(f"Detected raw PCM16LE file: {audio_file}")
        with open(audio_file, 'rb') as f:
            while True:
                data = f.read(chunk_size)
                if not data:
                    break
                yield data
        return

    # 2. Check WAV header
    wav_format, wav_sr, data_offset, data_len = parse_wav_header(audio_file)
    if wav_format in ("alaw", "ulaw"):
        if audio_format != wav_format:
            print(f"Error: WAV is {wav_format}, but --format is {audio_format}.")
            return
        if wav_sr != sample_rate:
            print(f"Warning: WAV sample rate is {wav_sr}, but --sample_rate is {sample_rate}.")
        print(f"Detected G.711 {wav_format} WAV: {audio_file} (passing through)")
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
    if wav_format == "pcm16le" and audio_format == "pcm16le":
        if wav_sr != sample_rate:
            print(f"Warning: WAV sample rate is {wav_sr}, but --sample_rate is {sample_rate}.")

    # 3. Fallback to Librosa/Conversion (Standard PCM/Audio)
    print(f"Processing as PCM audio: {audio_file}...")
    try:
        # Load and Resample
        y, sr = librosa.load(audio_file, sr=sample_rate)
    except Exception as e:
        print(f"Failed to load audio: {e}")
        return

    # Convert to Int16
    pcm_data = (y * 32767).astype(np.int16)

    if audio_format in ("alaw", "ulaw"):
        # Encode
        raw_bytes = encode_g711(pcm_data, audio_format)
        total_len = len(raw_bytes)
        offset = 0
        while offset < total_len:
            end = min(offset + chunk_size, total_len)
            yield raw_bytes[offset:end]
            offset = end
    else:
        # PCM16LE bytes
        raw_bytes = pcm_data.tobytes()
        total_len = len(raw_bytes)
        offset = 0
        while offset < total_len:
            end = min(offset + chunk_size, total_len)
            yield raw_bytes[offset:end]
            offset = end

async def send_audio(websocket, audio_file, chunk_duration, audio_format, sample_rate):
    """
    Streams audio chunks to WebSocket.
    """
    print(f"Preparing stream for {audio_file}...")
    
    chunk_generator = get_audio_generator(audio_file, chunk_duration, audio_format, sample_rate)
    
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

async def main():
    parser = argparse.ArgumentParser(description="Simulate WebSocket Audio Stream")
    parser.add_argument("--file", default="test_audio.wav", help="Path to input audio file")
    parser.add_argument("--host", default="ws://localhost:8000/ws/transcribe/test-session-1", help="WebSocket URL")
    parser.add_argument("--chunk_duration", type=float, default=0.6, help="Chunk duration in seconds")
    parser.add_argument("--format", default="alaw", choices=["alaw", "ulaw", "pcm16le"], help="Audio format")
    parser.add_argument("--sample_rate", type=int, default=8000, choices=[8000, 16000], help="Sample rate")
    
    args = parser.parse_args()
    
    if args.format == "pcm16le" and args.sample_rate == 8000:
        print("Warning: pcm16le at 8000 Hz will be resampled on the server.")
    if args.format in ("alaw", "ulaw") and args.sample_rate != 8000:
        print("Warning: G.711 is typically 8000 Hz; ensure server config matches.")

    print(f"Connecting to {args.host}...")
    
    try:
        async with websockets.connect(args.host) as websocket:
            print("Connected.")
            
            # Run send and receive in parallel
            tasks = [
                asyncio.create_task(send_audio(websocket, args.file, args.chunk_duration, args.format, args.sample_rate)),
                asyncio.create_task(receive_results(websocket))
            ]

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
