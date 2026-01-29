import sys
import os
from unittest.mock import MagicMock

sys.path.append(os.getcwd())
sys.modules["funasr"] = MagicMock()

print("Checking imports...")
try:
    import torch
    print("torch present")
    import torchaudio
    print("torchaudio present")
    import g711
    print("g711 present")
    
    from services.audio import AudioProcessor
    print("AudioProcessor imported")
    
    from services.inference import ASRInferenceService
    print("ASRInferenceService imported")
    
    from api.endpoints import router
    print("Router imported")
    
    from main import app
    print("App imported")
    
except ImportError as e:
    print(f"ImportError: {e}")
except Exception as e:
    print(f"Error: {e}")
