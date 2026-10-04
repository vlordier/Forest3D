"""Create a small, self-contained Forest3D demo project."""

import json
from pathlib import Path
from xml.etree import ElementTree as ET

import click

from forest3d.cli.config import load_cli_config, resolve_project_paths
from forest3d.core.forest import WorldPopulator

DEMO_MODELS = {
    "tree": [
        (
            "trunk",
            "0 0 1.1 0 0 0",
            "cylinder",
            {"radius": "0.18", "length": "2.2"},
            "0.34 0.19 0.08 1",
        ),
        (
            "crown_lower",
            "0 0 2.25 0 0 0",
            "cone",
            {"radius": "0.9", "length": "1.7"},
            "0.12 0.34 0.14 1",
        ),
        (
            "crown_upper",
            "0 0 3.05 0 0 0",
            "cone",
            {"radius": "0.65", "length": "1.5"},
            "0.16 0.42 0.18 1",
        ),
    ],
    "bush": [
        ("center", "0 0 0.55 0 0 0", "sphere", {"radius": "0.65"}, "0.16 0.42 0.17 1"),
        ("left", "-0.42 0.04 0.38 0 0 0", "sphere", {"radius": "0.43"}, "0.19 0.48 0.2 1"),
        ("right", "0.4 -0.03 0.4 0 0 0", "sphere", {"radius": "0.44"}, "0.13 0.36 0.14 1"),
    ],
    "rock": [
        ("body", "0 0 0.48 0.12 0.18 0.2", "sphere", {"radius": "0.62"}, "0.42 0.43 0.4 1"),
    ],
    "grass": [
        ("blade_1", "0 0 0.32 0 0.28 0", "box", {"size": "0.08 0.08 0.68"}, "0.28 0.55 0.17 1"),
        ("blade_2", "0.08 0 0.29 0 -0.3 1.1", "box", {"size": "0.08 0.08 0.58"}, "0.36 0.62 0.2 1"),
        (
            "blade_3",
            "-0.07 0.03 0.26 0.25 0.12 -0.9",
            "box",
            {"size": "0.08 0.08 0.52"},
            "0.22 0.48 0.14 1",
        ),
    ],
    "sand": [
        (
            "mound",
            "0 0 0.25 0 0 0",
            "cylinder",
            {"radius": "0.9", "length": "0.5"},
            "0.72 0.61 0.36 1",
        ),
    ],
}


def _add_shape(
    link: ET.Element, name: str, pose: str, geometry: str, attributes: dict, color: str
) -> None:
    """Add matching visual and collision geometry to a model link."""
    for kind in ("collision", "visual"):
        element = ET.SubElement(link, kind, name=name + "_" + kind)
        ET.SubElement(element, "pose").text = pose
        shape = ET.SubElement(ET.SubElement(element, "geometry"), geometry)
        for key, value in attributes.items():
            ET.SubElement(shape, key).text = value
        if kind == "visual":
            material = ET.SubElement(element, "material")
            ET.SubElement(material, "ambient").text = color
            ET.SubElement(material, "diffuse").text = color


def create_demo_models(models_path: Path, scales: set[float] | None = None) -> int:
    """Write compact primitive SDF assets for categories and scale variants."""
    created = 0
    scales = scales or {1.0}
    for category, components in DEMO_MODELS.items():
        for scale in sorted(scales):
            model_name = f"forest3d_demo_s{round(scale * 1000):04d}"
            model_dir = models_path / category / model_name
            model_dir.mkdir(parents=True, exist_ok=True)

            sdf = ET.Element("sdf", version="1.8")
            model = ET.SubElement(sdf, "model", name=model_name)
            ET.SubElement(model, "static").text = "true"
            for component, pose, geometry, attributes, color in components:
                link = ET.SubElement(model, "link", name=component)
                pose_values = list(map(float, pose.split()))
                pose_values[:3] = [value * scale for value in pose_values[:3]]
                scaled_pose = " ".join(f"{value:.6f}" for value in pose_values)
                scaled_attributes = {
                    key: " ".join(f"{float(value) * scale:.6f}" for value in raw.split())
                    for key, raw in attributes.items()
                }
                _add_shape(link, component, scaled_pose, geometry, scaled_attributes, color)

            ET.indent(sdf, space="    ")
            ET.ElementTree(sdf).write(model_dir / "model.sdf", encoding="utf-8", xml_declaration=True)
            (model_dir / "model.config").write_text(
                '<?xml version="1.0"?>\n'
                "<model>\n"
                f"    <name>{model_name}</name>\n"
                "    <version>1.0</version>\n"
                '    <sdf version="1.8">model.sdf</sdf>\n'
                f"    <description>Procedural Forest3D {category} demo asset at scale {scale:.3f}</description>\n"
                "</model>\n",
                encoding="utf-8",
            )
            created += 1
    return created


@click.command()
@click.option(
    "--base-path",
    "base_path",
    type=click.Path(file_okay=False),
    default=".",
    help="Project directory for generated models and world",
)
@click.option(
    "--dem",
    "dem_path",
    type=click.Path(exists=True, dir_okay=False),
    help="DEM GeoTIFF (defaults to dem/terrain.tif under the project directory)",
)
@click.option(
    "--density",
    "density_json",
    type=str,
    help='JSON density config, such as \'{"tree": 30, "rock": 5}\'',
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(),
    help="Output world file (default: <base-path>/worlds/forest_world.world)",
)
@click.pass_context
def demo(ctx, base_path, dem_path, density_json, output_path):
    """Build demo assets, terrain, and a generated forest world.

    Requires GDAL. Run with `uv run --extra terrain forest3d demo`.
    """
    console = ctx.obj["console"]
    config = load_cli_config(ctx.obj.get("config_path"))
    paths = resolve_project_paths(config, base_path=base_path)
    base = paths.base
    dem = Path(dem_path).resolve() if dem_path else base / "dem" / "terrain.tif"
    if not dem.is_file():
        raise click.ClickException(
            f"DEM not found: {dem}. Pass --dem PATH or run from a project with dem/terrain.tif."
        )

    try:
        density = json.loads(density_json) if density_json else None
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"Invalid JSON for density: {exc}") from exc
    if density is not None and not isinstance(density, dict):
        raise click.ClickException("Density must be a JSON object of category counts.")

    try:
        from forest3d.core.terrain import GDAL_AVAILABLE, TerrainGenerator
    except ImportError as exc:
        raise click.ClickException(
            "Terrain generation requires GDAL. Install it with `uv sync --extra dev --extra terrain`."
        ) from exc
    if not GDAL_AVAILABLE:
        raise click.ClickException(
            "Terrain generation requires GDAL. Install it with `uv sync --extra dev --extra terrain`."
        )

    ground_path = paths.models / "ground"
    try:
        TerrainGenerator(dem, output_path=ground_path, config=config.terrain).process_terrain()
        scales = {
            round(minimum + (maximum - minimum) * step / 20, 3)
            for minimum, maximum in WorldPopulator.SCALE_RANGES.values()
            for step in range(21)
        }
        count = create_demo_models(paths.models, scales)
        populator = WorldPopulator(
            base,
            models_path=paths.models,
            worlds_path=paths.worlds,
            source_dem_path=dem,
        )
        world = populator.create_forest_world(
            density, output_path=Path(output_path).resolve() if output_path else paths.worlds / "forest_world.world"
        )
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    console.print(f"[green]Demo ready![/green] Created {count} category models.")
    console.print(f"World created at: {world}")
    console.print(f"Launch with: forest3d launch --base-path {base} --world {world}")
