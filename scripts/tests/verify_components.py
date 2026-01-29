import sys
import os

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import asyncio
import numpy as np
import torch
from services.audio import AudioProcessor
from services.storage import StorageManager
from unittest.mock import MagicMock, AsyncMock, patch

async def test_audio_processor():
    print("Testing AudioProcessor...")
    processor = AudioProcessor()
    
    # Generate dummy G.711 data (silence is 0xFF in A-law? Or something else. Let's just use random bytes)
    dummy_g711 = b'\xff' * 160  # 160 bytes = 20ms at 8000Hz
    
    # Test process
    output = processor.process(dummy_g711)
    
    # Check output type
    if not isinstance(output, torch.Tensor):
        print(f"FAILED: Output is not a Tensor. Type: {type(output)}")
        return
        
    # Check output shape
    # Input 160 samples (8000Hz). Output should be 320 samples (16000Hz).
    expected_samples = 320
    if output.shape[-1] != expected_samples:
        print(f"FAILED: Output shape mismatch. Expected {expected_samples}, got {output.shape[-1]}")
        return

    print("AudioProcessor Test PASSED")

async def test_storage_manager():
    print("Testing StorageManager (Mocked)...")
    
    # Mock Redis and Engine to avoid real connection errors during quick check
    with patch('services.storage.redis_client', new_callable=AsyncMock) as mock_redis, \
         patch('services.storage.AsyncSessionLocal', new_callable=MagicMock) as mock_session_cls:
        
        storage = StorageManager("test_session_123")
        
        # Setup mock pipeline to be an async context manager
        mock_pipeline = MagicMock()
        mock_pipeline.__aenter__ = AsyncMock(return_value=mock_pipeline)
        mock_pipeline.__aexit__ = AsyncMock(return_value=None)
        
        # pipeline method should NOT be async itself (it returns the pipeline object), 
        # BUT redis.asyncio.Redis.pipeline() is a synchronous method that returns a Pipeline object.
        # However, AsyncMock makes child attributes AsyncMocks by default if accessed? 
        # Let's force pipeline to be a MagicMock (synchronous function)
        mock_redis.pipeline = MagicMock(return_value=mock_pipeline)

        # Make execute awaitable
        mock_pipeline.execute = AsyncMock()

        # But wait, if mock_redis is AsyncMock, all its methods are async? 
        # Actually proper way to mock redis client:
        # redis_client.incr IS async.
        # redis_client.pipeline IS NOT async (usually).
        
        # Let's just patch the method on the instance if possible, or force it.
        
        # Configure incr (AsyncMock)
        mock_redis.incr.return_value = 1
        
        seq = await storage.get_next_sequence()
        if seq != 1:
            print(f"FAILED: get_next_sequence returned {seq}, expected 1")
            return
        mock_redis.incr.assert_called_with("asr:sess:test_session_123:seq")
        
        # Test save_partial
        await storage.save_partial("Hello", 1)
        # Check if pipeline was used
        mock_redis.pipeline.assert_called()
        mock_pipeline.hset.assert_called()
        mock_pipeline.expire.assert_called()
        mock_pipeline.execute.assert_called() # await pipe.execute()

        
        # Test save_final
        # Mock session context manager (AsyncSessionLocal)
        mock_session = AsyncMock()
        
        # Setup AsyncSessionLocal() to return an object that works with 'async with'
        mock_session_ctx = MagicMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_session_cls.return_value = mock_session_ctx

        # Setup session.begin() to return an object that works with 'async with'
        mock_transaction = MagicMock()
        mock_transaction.__aenter__ = AsyncMock()
        mock_transaction.__aexit__ = AsyncMock()
        # session.begin() is a sync method returning a CM, but mock_session is AsyncMock
        # so we must explicitly set .begin to be a MagicMock (sync function)
        mock_session.begin = MagicMock(return_value=mock_transaction)
        
        segment = await storage.save_final("Hello World")
        
        if segment.content != "Hello World":
             print(f"FAILED: save_final segment content mismatch")
             return
             
        mock_redis.delete.assert_called_with("asr:sess:test_session_123:current")
        mock_session.add.assert_called()

    print("StorageManager Test PASSED")

async def main():
    try:
        await test_audio_processor()
        await test_storage_manager()
    except Exception as e:
        print(f"An error occurred: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
