"""
Concurrent stream simulation script for PyStreamASR load testing.
Simulates multiple simultaneous WebSocket connections streaming audio.

Usage:
    python scripts/simulate_concurrent_streams.py --file test_audio.wav --num_streams 5
    python scripts/simulate_concurrent_streams.py --file test_audio.wav --num_streams 10 --stagger 0.5
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
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

try:
    import soundfile as sf
    import librosa
except ImportError:
    print("Error: Please install required libraries via 'pip install -r requirements.txt'")
    sys.exit(1)

try:
    import g711
except ImportError:
    import audioop
    print("Warning: 'g711' library not found, using 'audioop' as fallback.")
    g711 = None


@dataclass
class StreamStats:
    """Statistics for a single stream."""
    stream_id: str
    chunks_sent: int = 0
    messages_received: int = 0
    partials_received: int = 0
    finals_received: int = 0
    start_time: float = 0.0
    end_time: float = 0.0
    error: Optional[str] = None
    
    @property
    def duration(self) -> float:
        return self.end_time - self.start_time if self.end_time else 0.0


def convert_to_alaw(pcm_data_int16: np.ndarray) -> bytes:
    """Convert 16-bit PCM (numpy int16 array) to G.711 A-law bytes."""
    if g711 and hasattr(g711, "encode_alaw"):
        try:
            return g711.encode_alaw(pcm_data_int16.tobytes())
        except:
            return g711.encode_alaw(pcm_data_int16.tolist())
    
    import audioop
    return audioop.lin2alaw(pcm_data_int16.tobytes(), 2)


def parse_wav_header(file_path: str) -> tuple[bool, int, int]:
    """
    Parses WAV header to determine if it's G.711 A-law (Format=6) or PCM (Format=1).
    Returns (is_alaw, data_start_offset, data_length).
    """
    try:
        with open(file_path, 'rb') as f:
            chunk_id, size, format_tag = struct.unpack('<4sI4s', f.read(12))
            if chunk_id != b'RIFF' or format_tag != b'WAVE':
                return False, 0, 0
            
            is_alaw_ready = False
            while True:
                chunk_header = f.read(8)
                if len(chunk_header) < 8:
                    break
                subchunk_id, subchunk_size = struct.unpack('<4sI', chunk_header)
                
                if subchunk_id == b'fmt ':
                    fmt_data = f.read(subchunk_size)
                    audio_format, num_channels, sample_rate, byte_rate, block_align, bits_per_sample = struct.unpack('<HHIIHH', fmt_data[:16])
                    is_alaw_ready = (audio_format == 6 and num_channels == 1 and sample_rate == 8000 and bits_per_sample == 8)
                elif subchunk_id == b'data':
                    data_start = f.tell()
                    return is_alaw_ready, data_start, subchunk_size
                else:
                    f.seek(subchunk_size, 1)
                    
    except Exception as e:
        print(f"Header parse error: {e}")
    
    return False, 0, 0


def load_audio_data(audio_file: str) -> bytes:
    """
    Loads audio file and returns G.711 A-law bytes.
    Pre-loads entire file to avoid repeated I/O during concurrent streams.
    """
    ext = os.path.splitext(audio_file)[1].lower()
    
    # Raw G.711 files
    if ext in ['.alaw', '.pcma', '.g711']:
        with open(audio_file, 'rb') as f:
            return f.read()
    
    # Check WAV header for native A-law
    is_alaw, data_offset, data_len = parse_wav_header(audio_file)
    if is_alaw:
        with open(audio_file, 'rb') as f:
            f.seek(data_offset)
            return f.read(data_len)
    
    # Convert PCM to A-law
    y, sr = librosa.load(audio_file, sr=8000)
    pcm_data = (y * 32767).astype(np.int16)
    return convert_to_alaw(pcm_data)


async def stream_sender(
    websocket,
    audio_data: bytes,
    chunk_duration: float,
    stats: StreamStats
) -> None:
    """Streams audio chunks to WebSocket."""
    chunk_size = int(8000 * chunk_duration)
    total_len = len(audio_data)
    offset = 0
    
    try:
        while offset < total_len:
            end = min(offset + chunk_size, total_len)
            chunk = audio_data[offset:end]
            await websocket.send(chunk)
            stats.chunks_sent += 1
            offset = end
            await asyncio.sleep(chunk_duration)
    except Exception as e:
        stats.error = f"Send error: {e}"


async def stream_receiver(websocket, stats: StreamStats) -> None:
    """Receives messages from WebSocket."""
    try:
        while True:
            message = await websocket.recv()
            data = json.loads(message)
            
            msg_type = data.get("type", "unknown")
            stats.messages_received += 1
            
            if msg_type == "partial":
                stats.partials_received += 1
            elif msg_type == "final":
                stats.finals_received += 1
                
    except websockets.exceptions.ConnectionClosed:
        pass
    except Exception as e:
        if not stats.error:
            stats.error = f"Receive error: {e}"


async def run_single_stream(
    stream_id: str,
    host_base: str,
    audio_data: bytes,
    chunk_duration: float,
    verbose: bool = False
) -> StreamStats:
    """Runs a single WebSocket stream from start to finish."""
    stats = StreamStats(stream_id=stream_id)
    ws_url = f"{host_base}/{stream_id}"
    
    if verbose:
        print(f"[{stream_id}] Connecting to {ws_url}...")
    
    stats.start_time = time.time()
    
    try:
        async with websockets.connect(ws_url) as websocket:
            if verbose:
                print(f"[{stream_id}] Connected.")
            
            # Run sender and receiver concurrently
            sender_task = asyncio.create_task(
                stream_sender(websocket, audio_data, chunk_duration, stats)
            )
            receiver_task = asyncio.create_task(
                stream_receiver(websocket, stats)
            )
            
            # Wait for sender to finish
            await sender_task
            
            # Give receiver time to get final results
            await asyncio.sleep(2.0)
            receiver_task.cancel()
            
            try:
                await receiver_task
            except asyncio.CancelledError:
                pass
                
    except Exception as e:
        stats.error = f"Connection error: {e}"
        if verbose:
            print(f"[{stream_id}] Error: {e}")
    
    stats.end_time = time.time()
    
    if verbose:
        print(f"[{stream_id}] Completed. Chunks: {stats.chunks_sent}, Messages: {stats.messages_received}")
    
    return stats


async def run_concurrent_streams(
    host_base: str,
    audio_data: bytes,
    num_streams: int,
    chunk_duration: float,
    stagger_delay: float,
    verbose: bool = False
) -> list[StreamStats]:
    """
    Runs multiple concurrent streams with optional staggered start.
    
    Args:
        host_base: WebSocket URL base (without session_id)
        audio_data: Pre-loaded audio data
        num_streams: Number of concurrent streams
        chunk_duration: Duration per chunk in seconds
        stagger_delay: Delay between starting each stream (0 = simultaneous)
        verbose: Print per-stream progress
    
    Returns:
        List of StreamStats for each stream
    """
    tasks = []
    
    print(f"\n{'='*60}")
    print(f"Starting {num_streams} concurrent streams")
    print(f"Stagger delay: {stagger_delay}s | Chunk duration: {chunk_duration}s")
    print(f"{'='*60}\n")
    
    start_time = time.time()
    
    for i in range(num_streams):
        stream_id = f"concurrent-test-{i+1:03d}-{int(time.time()*1000)}"
        
        task = asyncio.create_task(
            run_single_stream(stream_id, host_base, audio_data, chunk_duration, verbose)
        )
        tasks.append(task)
        
        if stagger_delay > 0 and i < num_streams - 1:
            await asyncio.sleep(stagger_delay)
    
    # Wait for all streams to complete
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    total_time = time.time() - start_time
    
    # Process results
    stats_list = []
    for result in results:
        if isinstance(result, StreamStats):
            stats_list.append(result)
        else:
            # Exception occurred
            stats_list.append(StreamStats(
                stream_id="unknown",
                error=str(result)
            ))
    
    return stats_list, total_time


def print_summary(stats_list: list[StreamStats], total_time: float) -> None:
    """Prints a summary of all stream results."""
    print(f"\n{'='*60}")
    print("CONCURRENT STREAMS SUMMARY")
    print(f"{'='*60}")
    
    successful = [s for s in stats_list if s.error is None]
    failed = [s for s in stats_list if s.error is not None]
    
    print(f"\nTotal Streams:    {len(stats_list)}")
    print(f"Successful:       {len(successful)}")
    print(f"Failed:           {len(failed)}")
    print(f"Total Time:       {total_time:.2f}s")
    
    if successful:
        total_chunks = sum(s.chunks_sent for s in successful)
        total_messages = sum(s.messages_received for s in successful)
        total_partials = sum(s.partials_received for s in successful)
        total_finals = sum(s.finals_received for s in successful)
        avg_duration = sum(s.duration for s in successful) / len(successful)
        
        print(f"\n--- Successful Streams ---")
        print(f"Total Chunks Sent:      {total_chunks}")
        print(f"Total Messages Received: {total_messages}")
        print(f"  - Partials:           {total_partials}")
        print(f"  - Finals:             {total_finals}")
        print(f"Avg Stream Duration:    {avg_duration:.2f}s")
        print(f"Throughput:             {len(successful)/total_time:.2f} streams/sec")
    
    if failed:
        print(f"\n--- Failed Streams ---")
        for s in failed:
            print(f"  [{s.stream_id}] {s.error}")
    
    # Per-stream details
    print(f"\n--- Per-Stream Details ---")
    print(f"{'Stream ID':<40} {'Chunks':>8} {'Msgs':>8} {'Duration':>10} {'Status':>10}")
    print("-" * 80)
    for s in stats_list:
        status = "OK" if s.error is None else "FAILED"
        print(f"{s.stream_id:<40} {s.chunks_sent:>8} {s.messages_received:>8} {s.duration:>9.2f}s {status:>10}")


async def main():
    parser = argparse.ArgumentParser(
        description="Simulate Multiple Concurrent WebSocket Audio Streams",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run 5 concurrent streams
  python scripts/simulate_concurrent_streams.py --file test_audio.wav --num_streams 5
  
  # Run 10 streams with 0.5s stagger
  python scripts/simulate_concurrent_streams.py --file test_audio.wav --num_streams 10 --stagger 0.5
  
  # Run with verbose output
  python scripts/simulate_concurrent_streams.py --file test_audio.wav --num_streams 3 --verbose
        """
    )
    parser.add_argument("--file", default="test_audio.wav", help="Path to input audio file")
    parser.add_argument("--host", default="ws://localhost:8000/ws/transcribe", 
                        help="WebSocket URL base (without session_id)")
    parser.add_argument("--num_streams", type=int, default=5, 
                        help="Number of concurrent streams (default: 5)")
    parser.add_argument("--chunk_duration", type=float, default=0.6, 
                        help="Chunk duration in seconds (default: 0.6)")
    parser.add_argument("--stagger", type=float, default=0.0, 
                        help="Delay between starting each stream in seconds (default: 0, simultaneous)")
    parser.add_argument("--verbose", "-v", action="store_true", 
                        help="Print per-stream progress")
    
    args = parser.parse_args()
    
    # Validate audio file
    if not os.path.exists(args.file):
        print(f"Error: Audio file not found: {args.file}")
        sys.exit(1)
    
    # Pre-load audio data
    print(f"Loading audio file: {args.file}...")
    try:
        audio_data = load_audio_data(args.file)
        print(f"Audio loaded: {len(audio_data)} bytes ({len(audio_data)/8000:.2f}s at 8kHz)")
    except Exception as e:
        print(f"Error loading audio: {e}")
        sys.exit(1)
    
    # Run concurrent streams
    stats_list, total_time = await run_concurrent_streams(
        host_base=args.host,
        audio_data=audio_data,
        num_streams=args.num_streams,
        chunk_duration=args.chunk_duration,
        stagger_delay=args.stagger,
        verbose=args.verbose
    )
    
    # Print summary
    print_summary(stats_list, total_time)


if __name__ == "__main__":
    asyncio.run(main())
