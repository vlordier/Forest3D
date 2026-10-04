"""Compact, portable hashes for generated-world inputs."""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Iterable

import numpy as np


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def portable_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        token = hashlib.sha256(str(path.resolve().parent).encode()).hexdigest()[:12]
        return f"external/{token}/{path.name}"


def build_provenance(
    *, root: Path, terrain_mesh: Path, source_dem: Path | None, model_uris: Iterable[str],
    models_root: Path, bit_generator: str, ecology_layers: dict | None = None,
) -> dict:
    """Hash only the terrain and unique model assets referenced by this world."""
    root = root.resolve()
    files: dict[str, str] = {}

    def record(path: Path) -> None:
        if path.is_file():
            files[portable_path(path, root)] = sha256_file(path)

    record(terrain_mesh)
    record(models_root / "ground" / "terrain_metadata.json")
    if source_dem is not None:
        record(source_dem)
    assets = []
    for uri in sorted(set(model_uris)):
        if not uri.startswith("model://"):
            continue
        model_dir = models_root / uri.removeprefix("model://")
        candidates = [model_dir / "model.config", model_dir / "model.sdf"]
        for sdf_path in list(model_dir.glob("*.sdf")):
            candidates.append(sdf_path)
            try:
                import xml.etree.ElementTree as ET
                tree = ET.parse(sdf_path)
                for mesh_node in tree.findall(".//mesh/uri"):
                    mesh_uri = (mesh_node.text or "").strip()
                    if mesh_uri.startswith("model://"):
                        candidates.append(models_root / mesh_uri.removeprefix("model://"))
                    elif mesh_uri and not mesh_uri.startswith(("http://", "https://", "file://")):
                        candidates.append(sdf_path.parent / mesh_uri)
            except (OSError, ValueError):
                pass
        for candidate in candidates:
            record(candidate)
        assets.append(uri)

    ecology = []
    for name, layer in sorted((ecology_layers or {}).items()):
        path = Path(layer.path)
        record(path)
        ecology.append({
            "name": name,
            "path": portable_path(path, root),
            "sha256": sha256_file(path) if path.is_file() else None,
            "resampling": layer.resampling,
        })
    manifest = {key: files[key] for key in sorted(files)}
    try:
        forest3d_version = version("forest3d")
    except PackageNotFoundError:
        forest3d_version = "unknown"
    return {
        "forest3d": forest3d_version,
        "python": platform.python_version(),
        "numpy": np.__version__,
        "bit_generator": bit_generator,
        "terrain_mesh_sha256": sha256_file(terrain_mesh) if terrain_mesh.is_file() else None,
        "source_dem_sha256": sha256_file(source_dem) if source_dem and source_dem.is_file() else None,
        "assets": assets,
        "ecology_layers": ecology,
        "files": manifest,
    }


def provenance_json(data: dict) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))
