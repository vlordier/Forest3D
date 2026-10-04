"""Helpers for presenting typed configuration errors in Click commands."""

import os

import click
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from pydantic import ValidationError

from forest3d.config.loader import load_config
from forest3d.config.schema import Forest3DConfig


@dataclass(frozen=True)
class ProjectPaths:
    base: Path
    models: Path
    worlds: Path


def resolve_project_paths(
    config: Forest3DConfig,
    *,
    base_path: Path | str | None = None,
    models_path: Path | str | None = None,
    worlds_path: Path | str | None = None,
    cwd: Path | None = None,
) -> ProjectPaths:
    """Resolve paths using CLI > environment > config > cwd precedence."""
    root = (cwd or Path.cwd()).resolve()
    paths = config.paths
    env_base = os.environ.get("FOREST3D_BASE_PATH")
    base = (
        Path(base_path).expanduser()
        if base_path is not None
        else Path(env_base).expanduser()
        if env_base
        else paths.base_path or root
    )
    base = base.resolve() if base.is_absolute() else (root / base).resolve()
    models = Path(models_path).expanduser() if models_path is not None else paths.models_path
    worlds = Path(worlds_path).expanduser() if worlds_path is not None else paths.worlds_path
    env_models = os.environ.get("FOREST3D_MODELS_PATH")
    env_worlds = os.environ.get("FOREST3D_WORLDS_PATH")
    if models_path is None and env_models:
        models = Path(env_models).expanduser()
    if worlds_path is None and env_worlds:
        worlds = Path(env_worlds).expanduser()
    if paths.models_path is not None and models_path is None and not env_models:
        models = paths.models_path if paths.models_path.is_absolute() else base / paths.models_path
    if paths.worlds_path is not None and worlds_path is None and not env_worlds:
        worlds = paths.worlds_path if paths.worlds_path.is_absolute() else base / paths.worlds_path
    models = models if models is not None else base / "models"
    worlds = worlds if worlds is not None else base / "worlds"
    models = models.resolve() if models.is_absolute() else (root / models).resolve()
    worlds = worlds.resolve() if worlds.is_absolute() else (root / worlds).resolve()
    return ProjectPaths(base=base, models=models, worlds=worlds)


def load_cli_config(config_path: Optional[Path | str]) -> Forest3DConfig:
    """Load validated settings and convert schema errors to actionable CLI errors."""
    try:
        return load_config(config_path)
    except ValidationError as exc:
        raise click.ClickException(f"Invalid Forest3D configuration:\n{exc}") from exc
