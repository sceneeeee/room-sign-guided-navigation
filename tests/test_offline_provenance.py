"""Metadata capture must not initialize a model or disclose private paths."""
from hashlib import sha256
from importlib import metadata
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from roomnav.offline_provenance import file_digest, runtime_manifest


class OfflineProvenanceTests(unittest.TestCase):
    def test_inventory_hashes_config_and_models_without_claiming_they_were_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "models").mkdir()
            config = root / "config.yaml"
            model = root / "models/test.onnx"
            config.write_text("Global: {text_score: 0.5}\n")
            model.write_bytes(b"synthetic model asset")
            distribution = SimpleNamespace(locate_file=lambda _: root)
            with patch("roomnav.offline_provenance.metadata.distribution", return_value=distribution), patch(
                "roomnav.offline_provenance.metadata.version", return_value="synthetic"
            ), patch("roomnav.offline_provenance.subprocess.run", return_value=SimpleNamespace(stdout="ffmpeg synthetic\n")):
                result = runtime_manifest()
            self.assertEqual(result["ocr"]["config"], {"name": "config.yaml", "sha256": file_digest(config)})
            self.assertEqual(result["ocr"]["local_model_inventory"],
                             [{"name": "test.onnx", "sha256": sha256(model.read_bytes()).hexdigest()}])
            self.assertNotIn(directory, str(result))
            self.assertIn("not active-model verification", result["inventory_semantics"])
            self.assertIn("offline_replay.py", result["source_sha256"])

    def test_unprovisioned_dependencies_remain_explicit_without_download(self):
        with patch("roomnav.offline_provenance.metadata.distribution", side_effect=metadata.PackageNotFoundError), patch(
            "roomnav.offline_provenance.metadata.version", side_effect=metadata.PackageNotFoundError
        ), patch("roomnav.offline_provenance.subprocess.run", return_value=SimpleNamespace(stdout="ffmpeg synthetic\n")):
            result = runtime_manifest()
        self.assertTrue(all(v is None for v in result["versions"].values()))
        self.assertIsNone(result["ocr"]["config"])
        self.assertEqual(result["ocr"]["local_model_inventory"], [])
