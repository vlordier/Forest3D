"""CLI contract tests that use isolated temporary project directories."""

import json
from xml.etree import ElementTree as ET

from click.testing import CliRunner

from forest3d.cli.main import main


def test_help_lists_pipeline_commands():
    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    for command in ("terrain", "convert", "generate", "launch", "demo"):
        assert command in result.output


def test_generate_rejects_invalid_density_json():
    result = CliRunner().invoke(main, ["generate", "--density", "{"])

    assert result.exit_code != 0
    assert "Invalid JSON for density" in result.output


def test_generate_rejects_invalid_density_values(tmp_path):
    result = CliRunner().invoke(
        main,
        ["generate", "--base-path", str(tmp_path), "--density", '{"tree": -1}'],
    )

    assert result.exit_code != 0
    assert "Invalid density configuration" in result.output


def test_cli_reports_invalid_config_fields_as_a_click_error(tmp_path):
    config_path = tmp_path / "forest3d.yaml"
    config_path.write_text("density:\n  trees: 10\n")
    result = CliRunner().invoke(
        main, ["--config", str(config_path), "generate", "--base-path", str(tmp_path)]
    )

    assert result.exit_code != 0
    assert "Invalid Forest3D configuration" in result.output
    assert "trees" in result.output


def test_terrain_rejects_out_of_range_cli_values_as_a_click_error(tmp_path):
    dem_path = tmp_path / "dem.tif"
    dem_path.touch()
    result = CliRunner().invoke(main, ["terrain", "--dem", str(dem_path), "--scale", "0"])

    assert result.exit_code != 0
    assert "Invalid terrain configuration" in result.output


def test_generate_reports_missing_models_directory(tmp_path):
    result = CliRunner().invoke(main, ["generate", "--base-path", str(tmp_path)])

    assert result.exit_code != 0
    assert "Models directory not found" in result.output


def test_generate_writes_world_to_requested_output(tmp_path, flat_terrain_stl):
    models_path = tmp_path / "models"
    ground_mesh_path = models_path / "ground" / "mesh" / "terrain.stl"
    ground_mesh_path.parent.mkdir(parents=True)
    flat_terrain_stl.replace(ground_mesh_path)
    output_path = tmp_path / "custom" / "world.world"
    density = json.dumps({"tree": 0, "bush": 0, "rock": 0, "grass": 0, "sand": 0})

    result = CliRunner().invoke(
        main,
        [
            "generate",
            "--base-path",
            str(tmp_path),
            "--density",
            density,
            "--seed",
            "123",
            "--output",
            str(output_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert output_path.is_file()
    metadata = ET.parse(output_path).getroot().find(".//forest3d_generation")
    assert metadata.findtext("seed") == "123"
