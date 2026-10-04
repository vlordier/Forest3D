"""Forest generation CLI subcommand."""

import json
import click
from pathlib import Path
from rich.progress import Progress, BarColumn, TaskProgressColumn, TextColumn
from rich.table import Table
from pydantic import ValidationError

from forest3d.cli.config import load_cli_config, resolve_project_paths
from forest3d.config.schema import DensityConfig
from forest3d.core.forest import WorldPopulator


@click.command()
@click.option(
    "--base-path",
    "-b",
    type=click.Path(exists=True),
    help="Project base path containing models/ directory",
)
@click.option("--density", "-d", type=str, help='JSON density config: \'{"tree": 50, "rock": 5}\'')
@click.option("--seed", type=int, help="Seed for reproducible forest placement")
@click.option("--output", "-o", type=click.Path(), help="Output world file path")
@click.option("--verbose", "-v", is_flag=True, help="Show detailed statistics including scale info")
@click.pass_context
def generate(ctx, base_path, density, seed, output, verbose):
    """Generate a forest world from existing models.

    Procedurally places models on terrain using intelligent positioning
    algorithms that consider terrain slope, model spacing, and natural
    distribution patterns.

    \b
    Examples:
        forest3d generate
        forest3d generate --density '{"tree": 100, "rock": 20}'
        forest3d generate --seed 42  # Reproduce placement from this seed
        forest3d generate -b ./my-project -o ./worlds/custom.world
        forest3d generate -v  # Show detailed stats

    \b
    Default density:
        tree: 50, bush: 10, rock: 5, grass: 50, sand: 5

    \b
    Placement behavior:
        - Trees cluster together and avoid sand areas
        - Rocks prefer terrain edges
        - Bushes often appear near trees
        - Sand dunes are placed at edges
        - Cross-category collision avoidance
        - Scale-aware spacing
    """
    console = ctx.obj["console"]
    logger = ctx.obj["logger"]
    config = load_cli_config(ctx.obj.get("config_path"))

    # Parse density JSON if provided
    effective_density = config.density
    if density:
        try:
            density_dict = json.loads(density)
            if not isinstance(density_dict, dict):
                raise click.ClickException("Density JSON must be an object of category counts")
            updates = {}
            for key, value in density_dict.items():
                if key in DensityConfig.model_fields:
                    updates[key] = value
                else:
                    console.print(f"[yellow]Warning:[/yellow] Unknown category '{key}'")
            effective_density = DensityConfig.model_validate(
                {**effective_density.model_dump(), **updates}
            )
        except json.JSONDecodeError as e:
            raise click.ClickException(f"Invalid JSON for density: {e}")
        except ValidationError as e:
            raise click.ClickException(f"Invalid density configuration: {e}")

    # Determine base path
    paths = resolve_project_paths(config, base_path=base_path)
    project_base = paths.base
    if not paths.models.exists():
        raise click.ClickException(
            f"Models directory not found: {paths.models}\n\n"
            "Make sure you're in a Forest3D project directory with:\n"
            "  - models/ground/  (terrain)\n"
            "  - models/tree/    (trees)\n"
            "  - etc."
        )

    # Display configuration
    table = Table(title="Density Configuration", show_header=True)
    table.add_column("Category", style="cyan")
    table.add_column("Count", justify="right")

    density_config = effective_density.model_dump()
    for cat in ["tree", "bush", "rock", "grass", "sand"]:
        count = density_config[cat]
        table.add_row(cat, str(count))

    console.print(table)
    console.print()

    # Track progress
    progress_state = {"progress": None, "task": None}

    def progress_callback(percent, description):
        if progress_state["progress"] and progress_state["task"]:
            progress_state["progress"].update(
                progress_state["task"],
                completed=percent,
                description=description,
            )

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        progress_state["progress"] = progress
        progress_state["task"] = progress.add_task("Generating forest...", total=100)

        try:
            populator = WorldPopulator(
                base_path=project_base,
                progress_callback=progress_callback,
                seed=seed,
                suitability_config=config.suitability,
                models_path=paths.models,
                worlds_path=paths.worlds,
            )

            world_path = populator.create_forest_world(
                effective_density,
                output_path=Path(output).resolve() if output else paths.worlds / "forest_world.world",
                terrain_config=config.terrain,
            )

            # Get statistics
            stats = populator.get_model_statistics()

        except FileNotFoundError as e:
            raise click.ClickException(str(e))
        except Exception as e:
            logger.error(f"Forest generation failed: {e}")
            raise click.ClickException(str(e))

    # Display results
    console.print()
    console.print(f"[green]Success![/green] World created at: {world_path}")
    console.print(f"\nTotal models placed: [bold]{stats['total_models']}[/bold]")

    # Show placed counts with success rate
    results_table = Table(show_header=True)
    results_table.add_column("Category", style="cyan")
    results_table.add_column("Requested", justify="right")
    results_table.add_column("Placed", justify="right")
    results_table.add_column("Success", justify="right")

    total_requested = 0
    total_placed = 0
    for cat in ["tree", "bush", "rock", "grass", "sand"]:
        requested = density_config.get(cat, 0)
        placed = stats["by_category"].get(cat, 0)
        total_requested += requested
        total_placed += placed

        if requested > 0:
            success_rate = f"{(placed / requested * 100):.0f}%"
            color = "green" if placed == requested else "yellow" if placed > 0 else "red"
            results_table.add_row(
                cat, str(requested), str(placed), f"[{color}]{success_rate}[/{color}]"
            )
        else:
            results_table.add_row(cat, str(requested), str(placed), "-")

    console.print(results_table)

    # Show warning if many placements failed
    failed = total_requested - total_placed
    if failed > 0:
        console.print(
            f"\n[yellow]Note:[/yellow] {failed} models couldn't be placed (area too crowded)"
        )

    ecology_rejections = stats.get("placement_rejections", {})
    if ecology_rejections:
        console.print("\n[bold]Ecology suitability rejections[/bold]")
        for category, reasons in ecology_rejections.items():
            console.print(f"  {category}: {reasons}")

    # Show scale statistics if verbose
    if verbose and stats.get("scale_stats"):
        console.print()
        scale_table = Table(title="Scale Statistics", show_header=True)
        scale_table.add_column("Category", style="cyan")
        scale_table.add_column("Min", justify="right")
        scale_table.add_column("Max", justify="right")
        scale_table.add_column("Mean", justify="right")

        for cat, scale_info in stats["scale_stats"].items():
            scale_table.add_row(
                cat,
                f"{scale_info['min']:.2f}",
                f"{scale_info['max']:.2f}",
                f"{scale_info['mean']:.2f}",
            )

        console.print(scale_table)

    console.print(f"\n[dim]To launch in Gazebo:[/dim]")
    console.print(f"  export GZ_SIM_RESOURCE_PATH=$GZ_SIM_RESOURCE_PATH:{project_base}/models")
    console.print(f"  gz sim {world_path}")
