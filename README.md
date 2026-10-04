# Forest3D - Terrain and Forest Generation for Gazebo

Forest3D eliminates the manual overhead of building realistic simulation environments. Using DEM terrain data and Blender assets, it automatically generates collision-accurate Gazebo worlds with procedurally placed vegetation, rocks, and trees—ensuring both visual realism and physical fidelity for simulation.

| Forest Environment – Soil 1 | Forest Environment – Soil 2 | Forest Environment – Soil 3 |
|-----------------|-------------|-------------------|
| ![](https://raw.githubusercontent.com/unitsSpaceLab/Forest3D/feature/terrain-texture/Screenshot%20from%202026-01-08%2023-56-51.png) | ![](https://raw.githubusercontent.com/unitsSpaceLab/Forest3D/feature/terrain-texture/Screenshot%20from%202026-01-09%2000-01-54.png) | ![](https://raw.githubusercontent.com/unitsSpaceLab/Forest3D/feature/terrain-texture/Screenshot%20from%202026-01-09%2000-04-26.png) |



[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

## Demo

LiDAR point cloud (RViz) accurately captures the 3D assets, confirming Forest3D outputs are ready for real sensor-based simulation workflows.
<video src="https://github.com/user-attachments/assets/952f6a1d-dbc8-47dd-bce7-383bfa85e7ca" autoplay loop muted playsinline width="100%"/>

> **Need wheel-soil terramechanics?** A terramechanics-aware version
> (real-time Bekker-Wong wheel-soil forces, used in the IFIT 2026 paper)
> lives on the [`IFIT-2026` branch](https://github.com/unitsSpaceLab/Forest3D/tree/IFIT-2026).
> This `main` branch is the lighter environment-generation pipeline (no terramechanics).

## Tutorial

[![Watch the tutorial](https://img.youtube.com/vi/fLvci8LoMeY/maxresdefault.jpg)](https://youtu.be/fLvci8LoMeY)

## Pipeline

Forest3D follows a 4-step pipeline to generate simulation environments:

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   1. TERRAIN    │     │   2. CONVERT    │     │   3. GENERATE   │     │   4. LAUNCH     │
│                 │     │                 │     │                 │     │                 │
│  DEM (GeoTIFF)  │────►│ Blender Assets  │────►│  Place Models   │────►│ Open Gazebo     │
│       ↓         │     │       ↓         │     │       ↓         │     │       ↓         │
│  models/ground/ │     │ models/{tree,   │     │ worlds/forest_  │     │  Simulation     │
│                 │     │  rock,bush,...} │     │    world.world  │     │                 │
└─────────────────┘     └─────────────────┘     └─────────────────┘     └─────────────────┘
```

| Step | Command | Input | Output |
|------|---------|-------|--------|
| 1 | `forest3d terrain` | DEM file (.tif) | Terrain mesh + SDF model |
| 2 | `forest3d convert` | Blender files (.blend) | Gazebo models (glTF + SDF) |
| 3 | `forest3d generate` | models/ directory | World file (.world) |
| 4 | `forest3d launch` | World file | Gazebo simulation |

**Example workflow:**
```bash
# Step 1: Generate terrain from DEM
forest3d terrain --dem ./DEM/terrain.tif

# Step 2: Convert Blender assets (auto-detects categories from subfolders)
forest3d convert -i ./Blender-Assets -o ./models

# Step 3: Generate forest world (places models on terrain)
forest3d generate --density '{"tree": 50, "rock": 10, "bush": 20}' --seed 42

# Step 4: Launch Gazebo to view the result
forest3d launch
```

## Features

- **Terrain Generation**: DEM processing with resolution enhancement and Gaussian smoothing
- **Asset Processing**: Automatic Blender to Gazebo conversion with optimized collision meshes
- **Forest Population**: Intelligent procedural placement with natural clustering patterns
- **Unified CLI**: Simple `forest3d` command with subcommands for each operation
- **Docker Support**: Pre-built images with GDAL for easy deployment

Forest generation reads optional per-category terrain suitability from
`forest3d.yaml`. Elevation limits are relative to the generated terrain's
zeroed elevation origin; slope is measured in degrees. For example:

```yaml
suitability:
  tree:
    min_elevation: 2
    max_elevation: 80
    max_slope_degrees: 28
  rock:
    min_elevation: 0
    max_elevation: 100000
    max_slope_degrees: 60
```

The defaults favor gentle slopes for vegetation, allow steeper rock placement,
cluster trees and undergrowth, and retain scattered placements that leave open
areas. The effective suitability settings are recorded in the generated world.

### DEM coordinate contract

Terrain generation accepts GeoTIFF DEMs with a known projected or geographic
CRS. Projected horizontal units are converted to meters; geographic rasters are
projected into a local azimuthal equidistant coordinate system. Raster scale
and offset are applied to elevations. Missing CRS metadata, rotated/skewed
affine transforms, and any NoData or masked cells are rejected with an
actionable error. Reproject/resample rotated rasters and fill or crop invalid
cells before running `forest3d terrain`.

## Quick Start

### Option 1: Docker (Recommended)

The Docker image includes everything you need: Python, GDAL, Blender 4.2, and Gazebo Harmonic.

```bash
# Build the image (downloads Blender + Gazebo, ~2GB)
cd Forest3D
docker build -t forest3d -f docker/Dockerfile .

# Generate a forest world
docker run -v $(pwd):/workspace forest3d generate

# Convert Blender assets to Gazebo models
docker run -v $(pwd):/workspace forest3d convert \
  -i /workspace/Blender-Assets -o /workspace/models -c tree

# Launch Gazebo to view the world (requires X11)
xhost +local:docker  # Allow Docker to access display
docker run -e DISPLAY=$DISPLAY \
           -v /tmp/.X11-unix:/tmp/.X11-unix \
           -v $(pwd):/workspace \
           --network host \
           forest3d launch
```

### Option 2: pip install

```bash
# Clone and install
git clone https://github.com/khalidbourr/Forest3D.git
cd Forest3D
pip install -e .

# For terrain generation, also install GDAL:
# Ubuntu/Debian:
sudo apt install python3-gdal gdal-bin libgdal-dev
pip install "pygdal==$(gdal-config --version).*"
```

## Usage

### Generate Forest World

```bash
# Use default settings
forest3d generate

# Custom density
forest3d generate --density '{"tree": 100, "rock": 20, "bush": 30}'

# Use a preset configuration
forest3d -c configs/examples/dense_forest.yaml generate
```

### Generate Terrain from DEM

```bash
forest3d terrain --dem ./DEM/terrain.tif

# With texture from Blender
forest3d terrain --dem ./DEM/terrain.tif --texture ./Blender-Assets/soil/soil.blend

# With options
forest3d terrain --dem ./DEM/terrain.tif --scale 2.0 --smooth 1.5 --enhance
```

### Convert Blender Assets

```bash
# Auto-detect categories from subfolders (tree/, rock/, bush/, etc.)
forest3d convert -i ./Blender-Assets -o ./models

# Or specify category manually
forest3d convert -i ./Blender-Assets/tree -o ./models -c tree
```

### Launch Gazebo

The automated integration target is **Ubuntu 24.04 Noble + Gazebo Sim 8
(Harmonic)**. Ubuntu 22.04, Gazebo Classic, Windows, and other Gazebo majors are
not covered by the current CI matrix. CI installs the current Harmonic package
from the OSRF apt repository and checks Gazebo Sim major version 8.

```bash
# Build the sample project
uv run forest3d demo --dem dem/terrain.tif

# Launch the GUI (macOS starts a separate server process)
uv run forest3d launch

# Or manually with Gazebo Sim (Harmonic)
export GZ_SIM_RESOURCE_PATH=$GZ_SIM_RESOURCE_PATH:$(pwd)/models
gz sim worlds/forest_world.world
```

On the supported Ubuntu desktop setup, visually inspect terrain, tree/rock
materials, and their placement. In Gazebo, enable **View → Visualize Collisions**
to check collision geometry, then disable it to compare with visible meshes.
Move the camera around a tree and rock to verify the collision shape follows
the model and does not use the terrain mesh as the prop collision. The automated
headless integration check is `bash scripts/gazebo_smoke.sh`.

macOS is not currently a supported GUI target. This machine's source-built
Gazebo Sim 8.14.0 server loads the generated world, but its GUI plugin was built
against Ogre 2.3.1 while Homebrew exposes Ogre 2.3.3; the local GUI visual check
therefore remains unverified. Use `uv run python scripts/render_preview.py` for
a lightweight placement preview that does not render Gazebo materials or
physics.

## CLI Reference

```
forest3d --help                    # Show all commands
forest3d terrain --help            # Terrain generation help
forest3d convert --help            # Asset conversion help
forest3d generate --help           # Forest generation help
forest3d launch --help             # Launch Gazebo help

# Global options
forest3d -v ...                    # Verbose output
forest3d -vv ...                   # Debug output
forest3d -c config.yaml ...        # Use config file
```

## Configuration

Create `forest3d.yaml` in your project directory:

```yaml
terrain:
  scale_factor: 1.0
  smooth_sigma: 1.0
  enhance: false

density:
  tree: 50
  bush: 10
  rock: 5
  grass: 50
  sand: 5

blender:
  visual_decimation: 0.1
  collision_decimation: 0.01

paths:
  base_path: .
  models_path: models
  worlds_path: worlds

suitability:
  layers:
    moisture:
      path: data/moisture.tif
      resampling: bilinear # nearest or bilinear
  tree:
    ecology:
      moisture:
        minimum: 0.25
        maximum: 0.8
```

Ecology rasters are optional single-band GeoTIFFs with a defined CRS. Forest3D
reprojects terrain-local placement coordinates into each layer CRS. Nearest and
bilinear sampling are supported; outside-raster, masked, and NoData samples are
rejected. Bilinear samples that touch NoData are rejected. The terrain must have
been generated by Forest3D so its local projection metadata is available.
Category rules can use a numeric `minimum`/`maximum` range or a list of exact
`values` (useful for categorical rasters). Rejection counts and raster hashes
are included in generation statistics and world provenance.

See `configs/examples/` for preset configurations.

### Environment Variables

| Variable | Description |
|----------|-------------|
| `FOREST3D_BLENDER_PATH` | Path to Blender executable |
| `FOREST3D_BASE_PATH` | Project base directory |
| `FOREST3D_MODELS_PATH` | Models directory |
| `FOREST3D_WORLDS_PATH` | Worlds output directory |

Path precedence is command-line options, then environment variables, then the
config file, then the current working directory defaults. Relative paths in the
config file are rooted at `base_path`; relative command-line paths use the
current working directory. `models_path` and `worlds_path` can point outside
the project base.

### Visual preview and placement benchmark

After generating a world, create a quick local PNG preview with:

```bash
uv run python scripts/render_preview.py
```

This reads the generated terrain mesh and world placements; it is a lightweight
preview rather than a Gazebo rendering. To reproduce the placement performance
comparison recorded for the current macOS development machine, run:

```bash
uv run python scripts/benchmark_placement.py
```

## Project Structure

```
Forest3D/
├── src/forest3d/          # Python package
│   ├── cli/               # Command-line interface
│   ├── core/              # Core modules (terrain, converter, forest)
│   ├── config/            # Configuration handling
│   └── utils/             # Shared utilities
├── DEM/                   # DEM files (GeoTIFF)
├── Blender-Assets/        # Source .blend files
│   ├── tree/              # Tree models
│   ├── rock/              # Rock models
│   ├── bush/              # Bush models
│   ├── grass/             # Grass models
│   └── soil/              # Terrain textures
├── models/                # Generated Gazebo models
│   ├── ground/            # Terrain model
│   └── tree/, rock/, etc. # Asset models
├── worlds/                # Generated world files
├── configs/               # Configuration presets
└── docker/                # Docker files
```

## Asset Categories

| Category | Description | Default Count |
|----------|-------------|---------------|
| tree | Large vegetation | 50 |
| bush | Small vegetation/shrubs | 10 |
| rock | Rock formations | 5 |
| grass | Ground cover | 50 |
| sand | Sand dunes/patches | 5 |

## Adding Custom Assets

1. Place `.blend` files in category subfolders:
   ```
   Blender-Assets/
   ├── tree/your_tree.blend
   ├── rock/your_rock.blend
   └── bush/your_bush.blend
   ```
2. Convert to Gazebo format:
   ```bash
   forest3d convert -i ./Blender-Assets -o ./models
   ```
3. Models will be available for forest generation

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Format code
black src/

# Lint
pylint src/forest3d/
```

## Docker Compose

```bash
# Development environment (with Blender + GDAL + Gazebo)
docker compose -f docker/docker-compose.yml run forest3d-dev

# Convert Blender assets
docker compose -f docker/docker-compose.yml run convert \
  -i /workspace/Blender-Assets -o /workspace/models -c tree

# Generate terrain from DEM
docker compose -f docker/docker-compose.yml run terrain --dem terrain.tif

# Generate forest world
docker compose -f docker/docker-compose.yml run generate

# Launch Gazebo to view world (requires X11)
xhost +local:docker
docker compose -f docker/docker-compose.yml run launch
```

## Troubleshooting

### GDAL Not Found
Use Docker or install GDAL system packages:
```bash
# Ubuntu/Debian
sudo apt install python3-gdal gdal-bin libgdal-dev
pip install "pygdal==$(gdal-config --version).*"
```

### Blender Not Found
Set the path explicitly:
```bash
export FOREST3D_BLENDER_PATH=/path/to/blender
# or
forest3d convert --blender /path/to/blender ...
```

### Model Path Issues
Ensure Gazebo Sim can find models:
```bash
export GZ_SIM_RESOURCE_PATH=$GZ_SIM_RESOURCE_PATH:$(pwd)/models
```

## License

This project is licensed under the AGPL-3.0 - see the LICENSE file for details.


> **Need Custom Environments & Commercial Use ?**
> Forest3D ships natural/forest environments out of the box. Need a different one: vineyard, orchard, agricultural row crops, urban, lunar, or a custom site? Custom environment builds are available.
> Contact the authors

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
