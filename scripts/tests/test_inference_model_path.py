"""Unit tests for configured model path loading."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services import inference


class FakeOnlineRecognizer:
    """Test double for sherpa_onnx.OnlineRecognizer."""

    calls: list[dict[str, object]] = []

    @classmethod
    def from_paraformer(cls, **kwargs: object) -> str:
        """Capture recognizer options and return a sentinel recognizer."""
        cls.calls.append(kwargs)
        return "recognizer"


class InferenceModelPathTests(unittest.TestCase):
    """Test configured model path behavior."""

    def setUp(self) -> None:
        """Create an isolated temporary directory for model files."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.model_dir = Path(self.temp_dir.name) / "model"
        self.model_dir.mkdir(parents=True, exist_ok=True)

    def override_attr(self, obj: object, name: str, value: object) -> None:
        """Temporarily replace an attribute."""
        original_value = getattr(obj, name)
        self.addCleanup(setattr, obj, name, original_value)
        setattr(obj, name, value)

    def write_required_model_files(self) -> None:
        """Create the minimum model files required by load_model."""
        for filename in inference.MODEL_REQUIRED_FILES:
            (self.model_dir / filename).write_text("placeholder", encoding="utf-8")

    def test_relative_model_path_resolves_from_project_root(self) -> None:
        """Relative MODEL_PATH values should be project-root-relative."""
        resolved = inference.resolve_model_dir("models/test-model")

        self.assertEqual(resolved, inference.PROJECT_ROOT / "models" / "test-model")

    def test_absolute_model_path_is_preserved(self) -> None:
        """Absolute MODEL_PATH values should not be rewritten."""
        resolved = inference.resolve_model_dir(self.model_dir)

        self.assertEqual(resolved, self.model_dir)

    def test_load_model_passes_resolved_paths_to_sherpa(self) -> None:
        """load_model should pass string paths from the configured model directory."""
        self.write_required_model_files()
        FakeOnlineRecognizer.calls = []
        self.override_attr(inference.sherpa_onnx, "OnlineRecognizer", FakeOnlineRecognizer)

        recognizer = inference.load_model(self.model_dir)

        self.assertEqual(recognizer, "recognizer")
        self.assertEqual(len(FakeOnlineRecognizer.calls), 1)
        call = FakeOnlineRecognizer.calls[0]
        self.assertEqual(call["encoder"], str(self.model_dir / "encoder.int8.onnx"))
        self.assertEqual(call["decoder"], str(self.model_dir / "decoder.int8.onnx"))
        self.assertEqual(call["tokens"], str(self.model_dir / "tokens.txt"))

    def test_load_model_reports_all_missing_required_files(self) -> None:
        """Missing model files should fail before creating the recognizer."""
        with self.assertRaises(FileNotFoundError) as raised:
            inference.load_model(self.model_dir)

        message = str(raised.exception)
        self.assertIn(str(self.model_dir), message)
        for filename in inference.MODEL_REQUIRED_FILES:
            self.assertIn(filename, message)


if __name__ == "__main__":
    unittest.main()
