"""Configuration validation and serialization contracts."""

import pytest
from pydantic import ValidationError

from forest3d.config.loader import load_config, save_config
from forest3d.config.schema import DensityConfig, Forest3DConfig
from forest3d.cli.config import resolve_project_paths


@pytest.mark.parametrize("value", [-1, 1001, 1.5, True])
def test_density_rejects_invalid_counts(value):
    with pytest.raises(ValidationError):
        DensityConfig(tree=value)


def test_config_rejects_unknown_fields(tmp_path):
    config_path = tmp_path / "forest3d.yaml"
    config_path.write_text("densitty:\n  tree: 12\n")

    with pytest.raises(ValidationError, match="densitty"):
        load_config(config_path)


def test_config_round_trip_preserves_nested_validated_settings(tmp_path):
    config = Forest3DConfig.model_validate(
        {
            "density": {"tree": 12},
            "paths": {"base_path": str(tmp_path)},
            "suitability": {"tree": {"min_elevation": 3, "max_slope_degrees": 24}},
        }
    )
    config_path = tmp_path / "forest3d.yaml"

    save_config(config, config_path)
    loaded = load_config(config_path)

    assert loaded.density.tree == 12
    assert loaded.paths.base_path == tmp_path.resolve()
    assert loaded.suitability.tree.min_elevation == 3
    assert loaded.suitability.tree.max_slope_degrees == 24


def test_config_models_validate_assignment():
    config = Forest3DConfig()

    with pytest.raises(ValidationError):
        config.terrain.scale_factor = 0


def test_path_resolver_obeys_cli_environment_config_and_cwd_precedence(tmp_path, monkeypatch):
    config_file = tmp_path / "forest3d.yaml"
    config_file.write_text(
        f"paths:\n  base_path: {tmp_path / 'configured'}\n"
        f"  models_path: {tmp_path / 'configured-models'}\n"
        f"  worlds_path: {tmp_path / 'configured-worlds'}\n"
    )
    monkeypatch.setenv("FOREST3D_BASE_PATH", str(tmp_path / "environment"))
    monkeypatch.setenv("FOREST3D_MODELS_PATH", str(tmp_path / "environment-models"))
    monkeypatch.setenv("FOREST3D_WORLDS_PATH", str(tmp_path / "environment-worlds"))
    config = load_config(config_file)
    cwd = tmp_path / "cwd"

    paths = resolve_project_paths(config, cwd=cwd)
    assert paths.base == (tmp_path / "environment").resolve()
    assert paths.models == (tmp_path / "environment-models").resolve()
    assert paths.worlds == (tmp_path / "environment-worlds").resolve()

    paths = resolve_project_paths(
        config,
        base_path=tmp_path / "cli",
        models_path=tmp_path / "cli-models",
        worlds_path=tmp_path / "cli-worlds",
        cwd=cwd,
    )
    assert paths.base == (tmp_path / "cli").resolve()
    assert paths.models == (tmp_path / "cli-models").resolve()
    assert paths.worlds == (tmp_path / "cli-worlds").resolve()
