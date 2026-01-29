import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock, AsyncMock
import sys
import os

# Add project root to path
sys.path.append(os.getcwd())

# Mock modules before importing main
# We need to mock 'funasr' because it might not be installed in the test env or we want to avoid loading it.
sys.modules["funasr"] = MagicMock()

from main import app

client = TestClient(app)

@patch("services.inference.load_model")
@patch("api.endpoints.StorageManager")
@patch("api.endpoints.redis_client") # Mock the global redis client in endpoints
def test_websocket_flow(mock_redis, mock_storage_cls, mock_load_model):
    # Setup Mocks
    mock_model = MagicMock()
    # Mock model.generate to return a list of results (FunASR format)
    # Return a result that triggers 'partial' logic
    mock_model.generate.return_value = [{"text": "Hello world", "is_final": False}]
    mock_load_model.return_value = mock_model
    
    # Mock StorageManager instance
    mock_storage_instance = mock_storage_cls.return_value
    mock_storage_instance.save_partial = AsyncMock()
    mock_storage_instance.save_final = AsyncMock()
    
    # Mock Redis get for sequence
    # AsyncMock for awaitable methods, but redis_client.get is awaitable
    mock_redis.get = AsyncMock(return_value="5")
    
    # Override app.state.model manually since lifespan might not run perfectly in this patched env 
    # or we want to ensure it's set.
    # Actually, TestClient(app) runs lifespan. mock_load_model should be hit.
    
    with client.websocket_connect("/ws/transcribe/test_session_1") as websocket:
        # Check connection
        # Receive nothing initially?
        
        # Send dummy bytes
        websocket.send_bytes(b'\x00' * 1000)
        
        # Expect partial response
        data = websocket.receive_json()
        assert data["type"] == "partial"
        assert data["text"] == "Hello world"
        # Seq should be 5 (from redis) + 1 = 6
        assert data["seq"] == 6
        
        # Verify calls
        mock_storage_instance.save_partial.assert_called_once()
        mock_model.generate.assert_called()

@patch("services.inference.load_model")
@patch("api.endpoints.StorageManager")
@patch("api.endpoints.redis_client") 
def test_websocket_final_flow(mock_redis, mock_storage_cls, mock_load_model):
    # Setup for FINAL response
    mock_model = MagicMock()
    mock_model.generate.return_value = [{"text": "Hello world.", "is_final": True}]
    mock_load_model.return_value = mock_model
    
    mock_storage_instance = mock_storage_cls.return_value
    mock_storage_instance.save_final = AsyncMock()
    # Mock save_final returning a segment object with .segment_seq
    mock_segment = MagicMock()
    mock_segment.segment_seq = 10
    mock_storage_instance.save_final.return_value = mock_segment
    
    mock_redis.get = AsyncMock(return_value="9")

    with client.websocket_connect("/ws/transcribe/test_session_2") as websocket:
        websocket.send_bytes(b'\x00' * 1000)
        
        data = websocket.receive_json()
        assert data["type"] == "final"
        assert data["text"] == "Hello world."
        assert data["seq"] == 10
        
        mock_storage_instance.save_final.assert_called_once()
