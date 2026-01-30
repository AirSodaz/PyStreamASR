import os
import urllib.request
import tarfile
import shutil

MODEL_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-streaming-paraformer-bilingual-zh-en.tar.bz2"
MODEL_FILENAME = "sherpa-onnx-streaming-paraformer-bilingual-zh-en.tar.bz2"
EXTRACTED_DIR_NAME = "sherpa-onnx-streaming-paraformer-bilingual-zh-en"
DEST_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")

def download_file(url, filename):
    print(f"Downloading {url}...")
    with urllib.request.urlopen(url) as response, open(filename, 'wb') as out_file:
        shutil.copyfileobj(response, out_file)
    print("Download complete.")

def extract_tar_bz2(filename, dest_path):
    print(f"Extracting {filename} to {dest_path}...")
    with tarfile.open(filename, "r:bz2") as tar:
        tar.extractall(path=dest_path)
    print("Extraction complete.")

def main():
    if not os.path.exists(DEST_DIR):
        os.makedirs(DEST_DIR)
    
    # Check if model already exists
    model_path = os.path.join(DEST_DIR, EXTRACTED_DIR_NAME)
    if os.path.exists(model_path):
        print(f"Model already exists at {model_path}. Skipping download.")
        return

    # Download
    temp_file = os.path.join(DEST_DIR, MODEL_FILENAME)
    if not os.path.exists(temp_file):
        download_file(MODEL_URL, temp_file)
    
    # Extract
    extract_tar_bz2(temp_file, DEST_DIR)
    
    # Cleanup
    if os.path.exists(temp_file):
        os.remove(temp_file)
        print(f"Removed temporary file {temp_file}")
    
    print("Model setup finished successfully.")

if __name__ == "__main__":
    main()
