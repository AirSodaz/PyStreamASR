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

@patch("services.inference.load_model")
@patch("api.endpoints.StorageManager")
@patch("api.endpoints.redis_client")
def test_websocket_flow(mock_redis, mock_storage_cls, mock_load_model):
    # Setup Mocks
    mock_model = MagicMock()
    mock_model.generate.return_value = [{"text": "Hello world", "is_final": False}]
    mock_load_model.return_value = mock_model
    
    # Mock StorageManager instance
    mock_storage_instance = mock_storage_cls.return_value
    mock_storage_instance.save_partial = AsyncMock()
    mock_storage_instance.save_final = AsyncMock()
    
    mock_redis.get = AsyncMock(return_value="5")
    
    # Manually set app.state.model
    app.state.model = mock_model

    client = TestClient(app)
    
    with client.websocket_connect("/ws/transcribe/test_session_1") as websocket:
        # Send dummy bytes
        websocket.send_bytes(b'\x00' * 1000)
        
        # Expect partial response
        data = websocket.receive_json()
        assert data["type"] == "partial"
        assert data["text"] == "Hello world"
        assert data["seq"] == 6
        
        mock_storage_instance.save_partial.assert_called_once()

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
    mock_segment = MagicMock()
    mock_segment.segment_seq = 10
    mock_storage_instance.save_final.return_value = mock_segment
    
    mock_redis.get = AsyncMock(return_value="9")

    app.state.model = mock_model

    client = TestClient(app)

    with client.websocket_connect("/ws/transcribe/test_session_2") as websocket:
        websocket.send_bytes(b'\x00' * 1000)
        
        data = websocket.receive_json()
        assert data["type"] == "final"
        assert data["text"] == "Hello world."
        assert data["seq"] == 10
        
        mock_storage_instance.save_final.assert_called_once()
