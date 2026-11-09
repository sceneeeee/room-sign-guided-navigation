"""Local file hashes and runtime inventory without importing vision engines."""
from hashlib import sha256
from importlib import metadata
from pathlib import Path
import subprocess
import sys


def file_digest(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _asset(path: Path) -> dict:
    return {"name": path.name, "sha256": file_digest(path)}


def runtime_manifest() -> dict:
    """Inventory provisioned assets, not a claim that every file was loaded.

    This never downloads models or initializes OCR. Missing packages/assets
    remain explicit. Basenames prevent local environment paths leaking into
    logs; the package-default OCR config is identified by hash.
    """
    versions = {}
    for name in ("torch", "ultralytics", "rapidocr", "onnxruntime", "numpy", "pillow"):
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = None
    try:
        ocr_root = Path(metadata.distribution("rapidocr").locate_file("rapidocr"))
    except metadata.PackageNotFoundError:
        ocr_root = None
    config = ocr_root / "config.yaml" if ocr_root else None
    models = sorted((ocr_root / "models").glob("*.onnx")) if ocr_root else []
    # These are existing local text-encoder provisioning locations used by
    # the detector adapter. Inventory is distinct from active model identity.
    auxiliary = [Path("mobileclip_blt.ts"), Path("weights/clip/ViT-B-32.pt")]
    torch = sys.modules.get("torch")
    ffmpeg = subprocess.run(["ffmpeg", "-version"], check=True, capture_output=True, text=True)
    sources = Path(__file__).parent
    return {
        "versions": versions,
        "ffmpeg_version": ffmpeg.stdout.splitlines()[0],
        "torch_threads": torch.get_num_threads() if torch is not None else None,
        "ocr": {
            "entrypoint": "roomnav.ocr.get_ocr_engine: RapidOCR() package defaults",
            "config": _asset(config) if config is not None and config.is_file() else None,
            "local_model_inventory": [_asset(p) for p in models if p.is_file()],
        },
        "auxiliary_model_inventory": [_asset(p) for p in auxiliary if p.is_file()],
        "inventory_semantics": "available local files before inference; not active-model verification",
        "source_sha256": {p.name: file_digest(p) for p in sorted(sources.glob("*.py"))},
    }
