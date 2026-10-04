"""Terrain generation CLI subcommand."""

import click
from pathlib import Path
from rich.progress import Progress, SpinnerColumn, TextColumn
from pydantic import ValidationError

from forest3d.cli.config import load_cli_config, resolve_project_paths
from forest3d.config.schema import TerrainConfig

# Default output location
DEFAULT_OUTPUT = "./models/ground"


@click.command()
@click.option(
    "--dem",
    "-d",
    "dem_path",
    type=click.Path(exists=True),
    required=True,
    help="Path to DEM file (GeoTIFF), typically in ./DEM/ folder",
)
@click.option(
    "--output",
    "-o",
    "output_path",
    type=click.Path(),
    default=DEFAULT_OUTPUT,
    help=f"Output directory for generated terrain (default: {DEFAULT_OUTPUT})",
)
@click.option(
    "--scale", "-s", type=float, default=None, help="Scale factor for terrain (default: 1.0)"
)
@click.option("--smooth", type=float, default=None, help="Gaussian smoothing sigma (default: 1.0)")
@click.option("--enhance/--no-enhance", default=None, help="Enable DEM resolution enhancement")
@click.option(
    "--texture",
    "-t",
    "texture_path",
    type=click.Path(exists=True),
    help="Path to Blender file (.blend) for terrain texture, typically in ./Blender-Assets/soil/",
)
@click.option(
    "--blender",
    "blender_path",
    type=click.Path(exists=True),
    help="Path to Blender executable (auto-detected if not specified)",
)

# Advanced options (uncomment to enable):
# @click.option("--z-scale", type=float, default=None, help="Z scale factor for elevation")
# @click.option("--uv-tile", "-u", type=float, default=10.0, help="UV tile scale - texture repetition")


@click.pass_context
def terrain(ctx, dem_path, output_path, scale, smooth, enhance, texture_path, blender_path):
    """Generate terrain mesh from DEM data.

    Processes a Digital Elevation Model (GeoTIFF) file and creates:

    \b
    - STL mesh file for Gazebo Sim
    - SDF model definition with PBR materials
    - model.config file
    - Test world file

    \b
        Advanced options (edit CLI to enable):
        --z-scale    Separate Z elevation scaling
        --uv-tile    Texture tiling repetition (default: 10.0)
    \b
    Recommended folder structure:
        Forest3D/
        ├── DEM/                      <- DEM files (geographic data)
        │   └── terrain.tif
        ├── Blender-Assets/
        │   ├── tree/, rock/, ...     <- 3D model assets
        │   └── soil/                 <- Terrain textures
        │       └── soil.blend
        └── models/                   <- Output
            └── ground/               <- Terrain output

    \b
    Examples:
        # Basic terrain from DEM
        forest3d terrain --dem ./DEM/terrain.tif

        # With soil texture
        forest3d terrain --dem ./DEM/terrain.tif --texture ./Blender-Assets/soil/soil.blend

        # Custom options
        forest3d terrain -d ./DEM/terrain.tif -t ./Blender-Assets/soil/soil.blend --scale 2.0 --smooth 1.5

    \b
    Note: This command requires GDAL to be installed. Use Docker for
    easiest setup, or install GDAL manually.
    """
    console = ctx.obj["console"]
    logger = ctx.obj["logger"]
    config = load_cli_config(ctx.obj.get("config_path"))
    paths = resolve_project_paths(config)
    output_path = Path(output_path).expanduser().resolve() if output_path != DEFAULT_OUTPUT else paths.models / "ground"
    dem_path = Path(dem_path).resolve()

    # Override config with CLI options
    try:
        terrain_values = config.terrain.model_dump()
        if scale is not None:
            terrain_values["scale_factor"] = scale
        if smooth is not None:
            terrain_values["smooth_sigma"] = smooth
        if enhance is not None:
            terrain_values["enhance"] = enhance
        if texture_path is not None:
            terrain_values["texture_blend"] = Path(texture_path)
        config.terrain = TerrainConfig.model_validate(terrain_values)

        if blender_path is not None:
            config.blender.path = Path(blender_path)
    except ValidationError as exc:
        raise click.ClickException(f"Invalid terrain configuration:\n{exc}") from exc

    # Show configuration
    console.print(f"[bold]Terrain Generation[/bold]")
    console.print(f"  DEM: [cyan]{dem_path}[/cyan]")
    console.print(f"  Output: [cyan]{output_path}[/cyan]")
    if texture_path:
        console.print(f"  Texture: [cyan]{texture_path}[/cyan]")
    console.print()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Initializing terrain generator...", total=None)

        try:
            # Import here to give helpful error if GDAL missing
            from forest3d.core.terrain import TerrainGenerator, GDAL_AVAILABLE

            if not GDAL_AVAILABLE:
                raise click.ClickException(
                    "GDAL is required for terrain generation.\n\n"
                    "Install options:\n"
                    "  1. Use Docker: docker run -v $(pwd):/workspace forest3d terrain ...\n"
                    "  2. Ubuntu/Debian: sudo apt install python3-gdal gdal-bin\n"
                    "  3. See documentation for other platforms"
                )

            generator = TerrainGenerator(
                tif_path=Path(dem_path),
                output_path=output_path,
                config=config.terrain,
                blender_path=config.blender.path,
            )

            progress.update(task, description="Processing DEM data...")
            result_path = generator.process_terrain()

            # Extract textures from Blender file if provided
            if config.terrain.texture_blend:
                progress.update(task, description="Extracting textures from Blender file...")
                generator.extract_terrain_texture(config.terrain.texture_blend)

            progress.update(task, description="Complete!")

        except ImportError as e:
            raise click.ClickException(str(e))
        except Exception as e:
            logger.error(f"Terrain generation failed: {e}")
            raise click.ClickException(str(e))

    console.print(f"\n[green]Success![/green] Terrain created at: {result_path}")
    console.print(f"\n[dim]To view in Gazebo Sim:[/dim]")
    console.print(f"  export GZ_SIM_RESOURCE_PATH=$GZ_SIM_RESOURCE_PATH:{result_path.parent}")
    console.print(f"  gz sim {result_path}/test.world")
