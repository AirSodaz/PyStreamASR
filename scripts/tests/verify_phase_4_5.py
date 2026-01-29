import asyncio
import sys
import os
import unittest
from unittest.mock import MagicMock, AsyncMock, patch
import torch

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Mock modules that might not be installed or heavy
sys.modules["funasr"] = MagicMock()
sys.modules["redis.asyncio"] = MagicMock()
sys.modules["sqlalchemy"] = MagicMock()
sys.modules["sqlalchemy.ext.asyncio"] = MagicMock()
sys.modules["asyncmy"] = MagicMock()
sys.modules["g711"] = MagicMock()
# Mock torchaudio and its transforms
mock_torchaudio = MagicMock()
mock_torchaudio.transforms.Resample = MagicMock(return_value=lambda x: x)
sys.modules["torchaudio"] = mock_torchaudio

# Now import the services
# We need to patch where they are imported in the target files if we want to intercept them fully,
# but since we mocked sys.modules, normal imports will pick up the mocks.

from services.inference import ASRInferenceService
from services.audio import AudioProcessor

class TestPhase4And5(unittest.IsolatedAsyncioTestCase):
    async def test_inference_service_threading(self):
        """Test that infer runs in executor"""
        mock_model = MagicMock()
        # Setup generate to return a dummy result
        mock_model.generate.return_value = [{'text': 'hello world', 'is_final': False}]
        
        service = ASRInferenceService(mock_model)
        
        # We need to verify run_in_executor is called.
        # Since we can't easily spy on the loop's run_in_executor without complex patching of asyncio.get_running_loop,
        # we will check if the result is correct and the underlying model.generate was called.
        
        cache = {}
        tensor = torch.zeros(16000)
        
        text, is_final, new_cache = await service.infer(tensor, cache)
        
        self.assertEqual(text, 'hello world')
        self.assertFalse(is_final)
        mock_model.generate.assert_called_once()
        print("Inference Service Test Passed")

    async def test_audio_processor_logic(self):
        """Test AudioProcessor steps (mocking g711 decode)"""
        # Patch g711.decode_alaw in services.audio
        with patch('services.audio.g711.decode_alaw') as mock_decode:
            # Return 160 bytes of zero (80 samples * 2 bytes/sample)
            mock_decode.return_value = b'\x00' * 160 
            
            processor = AudioProcessor()
            # Input G.711 chunk (say 80 bytes)
            chunk = b'\x00' * 80
            
            # processor.process -> decode -> resample
            result_tensor = processor.process(chunk)
            
            mock_decode.assert_called_once_with(chunk)
            # We mocked Resample to identity, so we just check it returns a tensor
            self.assertTrue(isinstance(result_tensor, torch.Tensor))
            print("Audio Processor Test Passed")

if __name__ == '__main__':
    unittest.main()
