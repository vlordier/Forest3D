"""Forest world generation with procedural model placement."""

import logging
import json
import hashlib
import platform
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET

import numpy as np
from stl import mesh

from forest3d.config.schema import DensityConfig, SuitabilityConfig, TerrainConfig

logger = logging.getLogger("forest3d.forest")


class WorldPopulator:
    """Procedurally generate forest worlds with intelligent model placement.

    Places models on terrain using zone weighting, distance constraints,
    and natural clustering patterns. Handles cross-category collision
    avoidance and scale-aware spacing.
    """

    # Scale ranges for each model category
    SCALE_RANGES = {
        "tree": (0.8, 1.5),
        "rock": (0.5, 2.0),
        "bush": (0.3, 1.0),
        "grass": (0.2, 0.6),
        "sand": (1.0, 2.5),
    }

    # Minimum distances between models of same category
    MIN_DISTANCES = {
        "tree": 8.0,
        "bush": 2.0,
        "rock": 4.0,
        "grass": 0.5,
        "sand": 3.0,
    }

    # Cross-category minimum distances
    # Keys are tuples of (category1, category2), order doesn't matter
    CROSS_CATEGORY_DISTANCES = {
        ("tree", "tree"): 8.0,
        ("tree", "bush"): 1.5,
        ("tree", "rock"): 2.0,
        ("tree", "grass"): 0.5,
        ("tree", "sand"): 4.0,
        ("bush", "bush"): 2.0,
        ("bush", "rock"): 1.5,
        ("bush", "grass"): 0.3,
        ("bush", "sand"): 2.0,
        ("rock", "rock"): 4.0,
        ("rock", "grass"): 0.5,
        ("rock", "sand"): 2.0,
        ("grass", "grass"): 0.5,
        ("grass", "sand"): 0.5,
        ("sand", "sand"): 3.0,
    }

    # Zone weights (edge vs center preference)
    ZONE_WEIGHTS = {
        "tree": {"edge": 0.2, "center": 0.8},
        "rock": {"edge": 0.8, "center": 0.2},
        "bush": {"edge": 0.4, "center": 0.6},
        "grass": {"edge": 0.5, "center": 0.5},
        "sand": {"edge": 0.7, "center": 0.3},
    }

    def __init__(
        self,
        base_path: Path,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        seed: Optional[int] = None,
        suitability_config: Optional[SuitabilityConfig | dict[str, Any]] = None,
        models_path: Optional[Path] = None,
        worlds_path: Optional[Path] = None,
        source_dem_path: Optional[Path] = None,
    ):
        """Initialize the world populator.

        Args:
            base_path: Project base path containing models/ and worlds/.
            progress_callback: Optional callback for progress updates (percent, message).

        Raises:
            FileNotFoundError: If required paths don't exist.
        """
        self.base_path = Path(base_path)
        self.models_path = Path(models_path).resolve() if models_path else self.base_path / "models"
        self.worlds_path = Path(worlds_path).resolve() if worlds_path else self.base_path / "worlds"
        self.source_dem_path = Path(source_dem_path).resolve() if source_dem_path else None
        self.progress_callback = progress_callback
        self.seed = seed
        self.rng = np.random.default_rng(seed)
        self.suitability_config = SuitabilityConfig.model_validate(suitability_config or {})
        self.placement_rejections: Dict[str, Dict[str, int]] = {}
        self._terrain_index_mesh_id: int | None = None
        self._terrain_index: dict[tuple[int, int], list[int]] = {}
        self._terrain_index_size = 10.0
        self._distance_cell_size = 10.0
        self._distance_grid: dict[tuple[int, int], list[tuple[str, int]]] = {}
        self._distance_grid_count = -1
        self._use_spatial_index = True

        # Store (x, y, z, scale) for each placed model
        self.placed_models: Dict[str, List[Tuple[float, float, float, float]]] = {
            "tree": [],
            "bush": [],
            "rock": [],
            "grass": [],
            "sand": [],
        }

        self._verify_paths()
        self.model_variants = self._get_model_variants()

    def _verify_paths(self) -> None:
        """Verify all required paths exist."""
        required_paths = [
            self.models_path / "ground",
            self.worlds_path,
        ]

        # Check for at least one model category
        categories = ["tree", "rock", "bush", "grass", "sand"]
        has_category = False
        for cat in categories:
            if (self.models_path / cat).exists():
                has_category = True
                required_paths.append(self.models_path / cat)

        if not has_category:
            logger.warning("No model categories found in models directory")

        missing_paths = []
        for path in required_paths:
            if not path.exists():
                missing_paths.append(str(path))

        if self.models_path / "ground" not in [Path(p) for p in missing_paths]:
            # Ground is required
            pass
        elif missing_paths:
            missing_str = "\n  - ".join(missing_paths)
            raise FileNotFoundError(f"Required paths not found:\n  - {missing_str}")

        # Create worlds directory if it doesn't exist
        self.worlds_path.mkdir(parents=True, exist_ok=True)

    def _get_model_variants(self) -> Dict[str, List[str]]:
        """Get available variants for each model category."""
        variants = {}
        categories = ["tree", "bush", "rock", "grass", "sand"]

        for category in categories:
            category_path = self.models_path / category
            if category_path.exists():
                variants[category] = []
                for d in category_path.iterdir():
                    if d.is_dir() and not d.name.startswith("."):
                        variants[category].append(d.name)
                if variants[category]:
                    logger.info(f"Found {len(variants[category])} variants for {category}")

        return variants

    def _get_terrain_mesh(self) -> mesh.Mesh:
        """Get terrain mesh for height sampling."""
        mesh_path = self.models_path / "ground" / "mesh" / "terrain.stl"
        if not mesh_path.exists():
            raise FileNotFoundError(f"Terrain mesh not found at: {mesh_path}")
        return mesh.Mesh.from_file(str(mesh_path))

    def _get_random_variant(self, category: str) -> Optional[str]:
        """Get random variant for category."""
        variants = self.model_variants.get(category, [])
        if not variants:
            return None
        return self.rng.choice(variants)

    def _get_cross_distance(self, cat1: str, cat2: str) -> float:
        """Get minimum distance between two categories.

        Args:
            cat1: First category name.
            cat2: Second category name.

        Returns:
            Minimum required distance between the two categories.
        """
        # Try both orderings since we store only one direction
        if (cat1, cat2) in self.CROSS_CATEGORY_DISTANCES:
            return self.CROSS_CATEGORY_DISTANCES[(cat1, cat2)]
        elif (cat2, cat1) in self.CROSS_CATEGORY_DISTANCES:
            return self.CROSS_CATEGORY_DISTANCES[(cat2, cat1)]
        else:
            # Fallback to average of individual minimum distances
            return (self.MIN_DISTANCES.get(cat1, 1.0) + self.MIN_DISTANCES.get(cat2, 1.0)) / 2

    def _check_distance_to_placed(
        self, x: float, y: float, category: str, scale: float = 1.0
    ) -> bool:
        """Check if position is far enough from ALL placed models.

        Args:
            x: X coordinate of proposed position.
            y: Y coordinate of proposed position.
            category: Category of model being placed.
            scale: Scale factor of model being placed.

        Returns:
            True if position is valid (far enough from all models), False otherwise.
        """
        placed_count = sum(map(len, self.placed_models.values()))
        if not getattr(self, "_use_spatial_index", True):
            for other_category, positions in self.placed_models.items():
                base_distance = self._get_cross_distance(category, other_category)
                for px, py, _, other_scale in positions:
                    required = base_distance * (max(scale, 0.5) + max(other_scale, 0.5)) / 2
                    if np.sqrt((x - px) ** 2 + (y - py) ** 2) < required:
                        return False
            return True
        if placed_count != getattr(self, "_distance_grid_count", -1):
            self._rebuild_distance_grid()
        max_distance = max(
            self._get_cross_distance(category, other) * (max(scale, 0.5) + 2.5) / 2
            for other in self.placed_models
        )
        cx = int(np.floor(x / self._distance_cell_size))
        cy = int(np.floor(y / self._distance_cell_size))
        cell_radius = int(np.ceil(max_distance / self._distance_cell_size))
        candidates = []
        for gx in range(cx - cell_radius, cx + cell_radius + 1):
            for gy in range(cy - cell_radius, cy + cell_radius + 1):
                candidates.extend(self._distance_grid.get((gx, gy), ()))

        for other_category, index in candidates:
            px, py, _, p_scale = self.placed_models[other_category][index]
            dist = np.sqrt((x - px) ** 2 + (y - py) ** 2)
            base_distance = self._get_cross_distance(category, other_category)
            scale_factor = (max(scale, 0.5) + max(p_scale, 0.5)) / 2
            if dist < base_distance * scale_factor:
                return False

        return True

    def _rebuild_distance_grid(self) -> None:
        if not hasattr(self, "_distance_grid"):
            self._distance_grid = {}
            self._distance_cell_size = 10.0
        self._distance_grid.clear()
        count = 0
        for category, positions in self.placed_models.items():
            for index, (x, y, _, _) in enumerate(positions):
                cell = (int(np.floor(x / self._distance_cell_size)), int(np.floor(y / self._distance_cell_size)))
                self._distance_grid.setdefault(cell, []).append((category, index))
                count += 1
        self._distance_grid_count = count

    def _add_distance_grid_placement(self, category: str, index: int, x: float, y: float) -> None:
        total = sum(map(len, self.placed_models.values()))
        if self._distance_grid_count != total - 1:
            self._rebuild_distance_grid()
            return
        cell = (
            int(np.floor(x / self._distance_cell_size)),
            int(np.floor(y / self._distance_cell_size)),
        )
        self._distance_grid.setdefault(cell, []).append((category, index))
        self._distance_grid_count = total

    def _index_terrain(self, terrain_mesh: mesh.Mesh) -> None:
        if getattr(self, "_terrain_index_mesh_id", None) == id(terrain_mesh):
            return
        if not hasattr(self, "_terrain_index"):
            self._terrain_index = {}
            self._terrain_index_size = 10.0
        self._terrain_index.clear()
        cell = self._terrain_index_size
        for index, triangle in enumerate(terrain_mesh.vectors):
            min_x, min_y = np.floor(triangle[:, :2].min(axis=0) / cell).astype(int)
            max_x, max_y = np.floor(triangle[:, :2].max(axis=0) / cell).astype(int)
            for gx in range(min_x, max_x + 1):
                for gy in range(min_y, max_y + 1):
                    self._terrain_index.setdefault((gx, gy), []).append(index)
        self._terrain_index_mesh_id = id(terrain_mesh)

    def _sample_terrain_properties(
        self, terrain_mesh: mesh.Mesh, x: float, y: float
    ) -> Optional[Tuple[float, float]]:
        """Return interpolated height and local slope degrees at a point.

        Args:
            terrain_mesh: The terrain mesh to sample from.
            x: X coordinate.
            y: Y coordinate.

        Returns:
            Height and slope for the containing triangle, or None outside the mesh.
        """
        triangles = terrain_mesh.vectors.astype(np.float64, copy=False)
        if not getattr(self, "_use_spatial_index", True):
            return self._sample_terrain_properties_linear(triangles, x, y)
        self._index_terrain(terrain_mesh)
        cell = self._terrain_index_size
        candidates = self._terrain_index.get(
            (int(np.floor(x / cell)), int(np.floor(y / cell))), ()
        )
        if not candidates:
            return None
        triangles = triangles[np.asarray(candidates, dtype=np.intp)]
        v0, v1, v2 = triangles[:, 0], triangles[:, 1], triangles[:, 2]
        denom = (v1[:, 1] - v2[:, 1]) * (v0[:, 0] - v2[:, 0]) + (v2[:, 0] - v1[:, 0]) * (
            v0[:, 1] - v2[:, 1]
        )
        nondegenerate = np.abs(denom) > 1e-12
        safe_denom = np.where(nondegenerate, denom, 1.0)
        w0 = (
            (v1[:, 1] - v2[:, 1]) * (x - v2[:, 0]) + (v2[:, 0] - v1[:, 0]) * (y - v2[:, 1])
        ) / safe_denom
        w1 = (
            (v2[:, 1] - v0[:, 1]) * (x - v2[:, 0]) + (v0[:, 0] - v2[:, 0]) * (y - v2[:, 1])
        ) / safe_denom
        w2 = 1.0 - w0 - w1
        contained = nondegenerate & (w0 >= -1e-8) & (w1 >= -1e-8) & (w2 >= -1e-8)
        indices = np.flatnonzero(contained)
        if indices.size == 0:
            return None

        index = indices[0]
        triangle = triangles[index]
        height = (
            w0[index] * triangle[0, 2] + w1[index] * triangle[1, 2] + w2[index] * triangle[2, 2]
        )
        normal = np.cross(triangle[1] - triangle[0], triangle[2] - triangle[0])
        normal_length = np.linalg.norm(normal)
        if normal_length <= 1e-12:
            return None
        slope = np.degrees(np.arccos(np.clip(abs(normal[2]) / normal_length, 0.0, 1.0)))
        return float(height), float(slope)

    @staticmethod
    def _sample_terrain_properties_linear(triangles, x: float, y: float):
        """Vectorized reference path used for small placement batches."""
        v0, v1, v2 = triangles[:, 0], triangles[:, 1], triangles[:, 2]
        denom = (v1[:, 1] - v2[:, 1]) * (v0[:, 0] - v2[:, 0]) + (v2[:, 0] - v1[:, 0]) * (v0[:, 1] - v2[:, 1])
        nondegenerate = np.abs(denom) > 1e-12
        safe_denom = np.where(nondegenerate, denom, 1.0)
        w0 = ((v1[:, 1] - v2[:, 1]) * (x-v2[:, 0]) + (v2[:, 0]-v1[:, 0]) * (y-v2[:, 1])) / safe_denom
        w1 = ((v2[:, 1]-v0[:, 1]) * (x-v2[:, 0]) + (v0[:, 0]-v2[:, 0]) * (y-v2[:, 1])) / safe_denom
        w2=1-w0-w1
        indices=np.flatnonzero(nondegenerate & (w0>=-1e-8) & (w1>=-1e-8) & (w2>=-1e-8))
        if not indices.size:
            return None
        i=indices[0]; triangle=triangles[i]
        height=w0[i]*triangle[0,2]+w1[i]*triangle[1,2]+w2[i]*triangle[2,2]
        normal=np.cross(triangle[1]-triangle[0],triangle[2]-triangle[0]); length=np.linalg.norm(normal)
        if length<=1e-12:
            return None
        slope=np.degrees(np.arccos(np.clip(abs(normal[2])/length,0.0,1.0)))
        return float(height),float(slope)

    def _sample_terrain_height(self, terrain_mesh: mesh.Mesh, x: float, y: float) -> float:
        """Sample terrain height; return NaN for points outside valid mesh faces."""
        sample = self._sample_terrain_properties(terrain_mesh, x, y)
        return sample[0] if sample is not None else float("nan")

    def _get_random_position(
        self,
        terrain_mesh: mesh.Mesh,
        category: str,
        scale: float = 1.0,
        margin: float = 2.0,
        ecology_samplers: Optional[dict] = None,
    ) -> Optional[Tuple[float, float, float]]:
        """Get random position with intelligent placement.

        Args:
            terrain_mesh: Terrain mesh for bounds and height sampling.
            category: Model category being placed.
            scale: Scale of the model (affects distance requirements).
            margin: Minimum distance from terrain edges.

        Returns:
            Tuple of (x, y, z) if valid position found, None otherwise.
        """
        bounds = terrain_mesh.vectors.reshape(-1, 3)
        min_x, max_x = np.min(bounds[:, 0]) + margin, np.max(bounds[:, 0]) - margin
        min_y, max_y = np.min(bounds[:, 1]) + margin, np.max(bounds[:, 1]) - margin

        max_attempts = 100  # Increased attempts for better placement

        for _ in range(max_attempts):
            x, y = None, None
            is_edge = self.rng.random() < self.ZONE_WEIGHTS[category]["edge"]

            if category == "sand":
                if is_edge:
                    edge = self.rng.choice(["top", "bottom", "left", "right"])
                    if edge in ["top", "bottom"]:
                        x = self.rng.uniform(min_x + margin, max_x - margin)
                        y = max_y - margin if edge == "top" else min_y + margin
                        y += self.rng.uniform(-1, 1)
                    else:
                        x = max_x - margin if edge == "right" else min_x + margin
                        x += self.rng.uniform(-1, 1)
                        y = self.rng.uniform(min_y + margin, max_y - margin)
                else:
                    x = self.rng.uniform(min_x + margin, max_x - margin)
                    y = self.rng.uniform(min_y + margin, max_y - margin)

            elif category == "tree":
                if self.placed_models["tree"] and self.rng.random() < 0.7:
                    # Cluster near existing trees
                    base_idx = self.rng.integers(len(self.placed_models["tree"]))
                    base_tree = self.placed_models["tree"][base_idx]
                    base_scale = base_tree[3]

                    # Adjust cluster radius based on scales
                    min_cluster_dist = self.MIN_DISTANCES["tree"] * max(scale, base_scale)
                    max_cluster_dist = min_cluster_dist * 2

                    radius = self.rng.uniform(min_cluster_dist, max_cluster_dist)
                    angle = self.rng.uniform(0, 2 * np.pi)
                    x = base_tree[0] + radius * np.cos(angle)
                    y = base_tree[1] + radius * np.sin(angle)
                else:
                    # Place in open area, avoiding sand
                    for _ in range(10):
                        x = self.rng.uniform(min_x + margin, max_x - margin)
                        y = self.rng.uniform(min_y + margin, max_y - margin)

                        # Check distance from sand areas
                        sand_clear = all(
                            np.sqrt((x - sx) ** 2 + (y - sy) ** 2)
                            > self._get_cross_distance("tree", "sand") * max(scale, s_scale)
                            for sx, sy, _, s_scale in self.placed_models["sand"]
                        )
                        if sand_clear:
                            break
                    else:
                        continue

            elif category == "rock":
                if is_edge:
                    edge = self.rng.choice(["top", "bottom", "left", "right"])
                    edge_variance = self.rng.uniform(-2, 2)
                    if edge in ["top", "bottom"]:
                        x = self.rng.uniform(min_x + margin, max_x - margin)
                        y = (max_y - margin if edge == "top" else min_y + margin) + edge_variance
                    else:
                        x = (max_x - margin if edge == "right" else min_x + margin) + edge_variance
                        y = self.rng.uniform(min_y + margin, max_y - margin)
                else:
                    x = self.rng.uniform(min_x + margin, max_x - margin)
                    y = self.rng.uniform(min_y + margin, max_y - margin)

            elif category == "bush":
                if self.rng.random() < 0.6 and self.placed_models["tree"]:
                    # Place near trees
                    base_idx = self.rng.integers(len(self.placed_models["tree"]))
                    base_tree = self.placed_models["tree"][base_idx]
                    base_scale = base_tree[3]

                    # Bushes cluster closer to trees but not too close
                    min_dist = self._get_cross_distance("bush", "tree") * max(scale, base_scale)
                    radius = self.rng.uniform(min_dist, min_dist + 3.0)
                    angle = self.rng.uniform(0, 2 * np.pi)
                    x = base_tree[0] + radius * np.cos(angle)
                    y = base_tree[1] + radius * np.sin(angle)
                else:
                    x = self.rng.uniform(min_x + margin, max_x - margin)
                    y = self.rng.uniform(min_y + margin, max_y - margin)

            elif category == "grass":
                # Grass can go almost anywhere but prefers areas with trees/bushes
                if self.rng.random() < 0.5 and (
                    self.placed_models["tree"] or self.placed_models["bush"]
                ):
                    # Place near vegetation
                    all_vegetation = self.placed_models["tree"] + self.placed_models["bush"]
                    base = all_vegetation[self.rng.integers(len(all_vegetation))]
                    radius = self.rng.uniform(1.0, 5.0)
                    angle = self.rng.uniform(0, 2 * np.pi)
                    x = base[0] + radius * np.cos(angle)
                    y = base[1] + radius * np.sin(angle)
                else:
                    x = self.rng.uniform(min_x + margin, max_x - margin)
                    y = self.rng.uniform(min_y + margin, max_y - margin)

            else:
                x = self.rng.uniform(min_x + margin, max_x - margin)
                y = self.rng.uniform(min_y + margin, max_y - margin)

            # Validate position
            if x is None or y is None:
                continue

            if not (min_x <= x <= max_x and min_y <= y <= max_y):
                continue

            if not self._check_distance_to_placed(x, y, category, scale):
                continue

            # Reject candidates outside terrain faces or outside category suitability.
            terrain_sample = self._sample_terrain_properties(terrain_mesh, x, y)
            if terrain_sample is None:
                continue
            z, slope_degrees = terrain_sample
            suitability = getattr(self.suitability_config, category)
            if not (
                suitability.min_elevation <= z <= suitability.max_elevation
                and slope_degrees <= suitability.max_slope_degrees
            ):
                continue
            ecology_valid = True
            for layer_name, rule in suitability.ecology.items():
                value = (ecology_samplers or {})[layer_name].sample(x, y)
                if value is None:
                    reason = "nodata"
                    ecology_valid = False
                elif rule.values is not None and value not in rule.values:
                    reason = "value"
                    ecology_valid = False
                elif (rule.minimum is not None and value < rule.minimum) or (
                    rule.maximum is not None and value > rule.maximum
                ):
                    reason = "range"
                    ecology_valid = False
                else:
                    continue
                rejected = self.placement_rejections.setdefault(category, {})
                rejected[reason] = rejected.get(reason, 0) + 1
                break
            if not ecology_valid:
                continue

            # Category-specific height adjustments
            if category == "sand":
                z += self.rng.uniform(-0.2, 0)
            elif category == "grass":
                z += self.rng.uniform(-0.05, 0.05)
            elif category == "rock":
                z += self.rng.uniform(-0.1, 0.1)
            else:
                z += self.rng.uniform(-0.08, 0.08)

            # Store position with scale
            self.placed_models[category].append((x, y, z, scale))
            if getattr(self, "_use_spatial_index", True):
                self._add_distance_grid_placement(
                    category, len(self.placed_models[category]) - 1, x, y
                )
            return x, y, z

        # No valid position found after max attempts
        return None

    def _add_scene_settings(self, world: ET.Element) -> None:
        """Add scene settings for proper PBR lighting.

        Args:
            world: World XML element to add settings to.
        """
        scene = ET.SubElement(world, "scene")
        ET.SubElement(scene, "ambient").text = "0.4 0.4 0.4 1"
        ET.SubElement(scene, "background").text = "0.7 0.8 0.9 1"

    def _add_extra_lighting(self, world: ET.Element) -> None:
        """Add extra lighting for forest scenes (ambient and point lights).

        Args:
            world: World XML element to add lights to.
        """
        # Add ambient directional light (softer fill light)
        ambient = ET.SubElement(world, "light", {"name": "ambient", "type": "directional"})
        ET.SubElement(ambient, "cast_shadows").text = "false"
        ET.SubElement(ambient, "pose").text = "0 0 10 0 0 0"
        ET.SubElement(ambient, "diffuse").text = "0.6 0.6 0.6 1"
        ET.SubElement(ambient, "specular").text = "0.1 0.1 0.1 1"
        ET.SubElement(ambient, "direction").text = "0.1 0.1 -0.9"

        # Add point light with proper attenuation structure
        point = ET.SubElement(world, "light", {"name": "point_light", "type": "point"})
        ET.SubElement(point, "cast_shadows").text = "false"
        ET.SubElement(point, "pose").text = "0 0 15 0 0 0"
        ET.SubElement(point, "diffuse").text = "0.3 0.3 0.3 1"
        ET.SubElement(point, "specular").text = "0.05 0.05 0.05 1"
        point_attenuation = ET.SubElement(point, "attenuation")
        ET.SubElement(point_attenuation, "range").text = "50"
        ET.SubElement(point_attenuation, "constant").text = "0.5"
        ET.SubElement(point_attenuation, "linear").text = "0.01"
        ET.SubElement(point_attenuation, "quadratic").text = "0.001"

    def create_forest_world(
        self,
        density_config: Optional[DensityConfig | dict[str, int]] = None,
        output_path: Optional[Path] = None,
        terrain_config: Optional[TerrainConfig | dict[str, Any]] = None,
        suitability_config: Optional[SuitabilityConfig | dict[str, Any]] = None,
    ) -> Path:
        """Create forest world with placed models.

        Args:
            density_config: Dict of category -> count. Uses defaults if None.

        Returns:
            Path to created world file.
        """
        from forest3d.utils.sdf import create_world_base, write_world_file

        # Reset placed models
        for category in self.placed_models:
            self.placed_models[category] = []
        self._distance_grid.clear()
        self._distance_grid_count = -1

        # Use provided config or defaults
        effective_density = (
            density_config
            if isinstance(density_config, DensityConfig)
            else DensityConfig.model_validate(density_config or {})
        )
        density_config = effective_density.model_dump()
        effective_terrain = (
            terrain_config
            if isinstance(terrain_config, TerrainConfig)
            else TerrainConfig.model_validate(terrain_config or {})
        )
        effective_suitability = (
            suitability_config
            if isinstance(suitability_config, SuitabilityConfig)
            else SuitabilityConfig.model_validate(
                suitability_config or self.suitability_config.model_dump()
            )
        )
        self.suitability_config = effective_suitability
        self.placement_rejections = {}

        # Create world with shared base (plugins, physics, gravity, sun)
        world_elem, world = create_world_base("forest_world")

        # Add scene settings for proper PBR lighting
        self._add_scene_settings(world)

        # Add extra lighting for forest scenes
        self._add_extra_lighting(world)

        # Record all inputs that affect this generated world.
        generation = ET.SubElement(world, "forest3d_generation")
        ET.SubElement(generation, "seed").text = str(self.seed) if self.seed is not None else "null"
        ET.SubElement(generation, "density").text = json.dumps(
            density_config, sort_keys=True, separators=(",", ":")
        )
        ET.SubElement(generation, "terrain").text = json.dumps(
            effective_terrain.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        ET.SubElement(generation, "suitability").text = json.dumps(
            effective_suitability.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )

        # Add terrain
        terrain = ET.SubElement(world, "include")
        ET.SubElement(terrain, "uri").text = "model://ground"
        ET.SubElement(terrain, "name").text = "terrain"
        ET.SubElement(terrain, "pose").text = "0 0 0 0 0 0"

        terrain_mesh = self._get_terrain_mesh()
        ecology_samplers = {}
        if effective_suitability.layers:
            import json as json_module

            from forest3d.core.ecology import EcologyRasterSampler

            metadata_path = self.models_path / "ground" / "terrain_metadata.json"
            if not metadata_path.is_file():
                raise ValueError(
                    "Ecology layers require terrain_metadata.json; regenerate terrain from its DEM."
                )
            terrain_metadata = json_module.loads(metadata_path.read_text())
            ecology_samplers = {
                name: EcologyRasterSampler(layer, terrain_metadata)
                for name, layer in effective_suitability.layers.items()
            }

        # Process categories in specific order (larger/important first)
        category_order = ["sand", "rock", "tree", "bush", "grass"]
        total_models = sum(density_config.get(c, 0) for c in category_order)
        # Index construction pays off only once batches are sufficiently large.
        self._use_spatial_index = total_models >= 160
        models_placed = 0
        models_failed = 0

        for category in category_order:
            if category not in density_config or category not in self.model_variants:
                continue

            count = density_config[category]
            if count == 0:
                continue

            logger.info(f"Adding {count} {category} models...")
            category_placed = 0
            category_failed = 0

            for i in range(count):
                try:
                    variant = self._get_random_variant(category)
                    if not variant:
                        logger.warning(f"No variants available for {category}")
                        continue

                    # Generate scale FIRST (needed for distance calculations)
                    scale = self.rng.uniform(*self.SCALE_RANGES[category])

                    # Get position considering scale
                    position = self._get_random_position(
                        terrain_mesh, category, scale, ecology_samplers=ecology_samplers
                    )

                    if position is None:
                        category_failed += 1
                        models_failed += 1
                        logger.debug(f"Could not find valid position for {category}_{i}")
                        continue

                    x, y, z = position

                    # Category-specific rotations
                    if category == "sand":
                        roll = pitch = 0
                        yaw = self.rng.uniform(0, 2 * np.pi)
                    elif category == "tree":
                        # Slight tilt for natural look
                        roll = self.rng.uniform(-0.05, 0.05)
                        pitch = self.rng.uniform(-0.05, 0.05)
                        yaw = self.rng.uniform(0, 2 * np.pi)
                    elif category == "rock":
                        # Rocks can have more tilt
                        roll = self.rng.uniform(-0.15, 0.15)
                        pitch = self.rng.uniform(-0.15, 0.15)
                        yaw = self.rng.uniform(0, 2 * np.pi)
                    elif category == "bush":
                        roll = self.rng.uniform(-0.03, 0.03)
                        pitch = self.rng.uniform(-0.03, 0.03)
                        yaw = self.rng.uniform(0, 2 * np.pi)
                    else:  # grass
                        roll = pitch = 0
                        yaw = self.rng.uniform(0, 2 * np.pi)

                    # Add model to world
                    include = ET.SubElement(world, "include")
                    if variant.startswith("forest3d_demo_s"):
                        scale_min, scale_max = self.SCALE_RANGES[category]
                        scale_bucket = min(
                            20,
                            max(0, round((scale - scale_min) / (scale_max - scale_min) * 20)),
                        )
                        scale = round(scale_min + (scale_max - scale_min) * scale_bucket / 20, 3)
                        variant = f"forest3d_demo_s{round(scale * 1000):04d}"
                        last = self.placed_models[category][-1]
                        self.placed_models[category][-1] = (*last[:3], scale)
                    ET.SubElement(include, "uri").text = f"model://{category}/{variant}"
                    ET.SubElement(include, "name").text = f"{category}_{i}"
                    ET.SubElement(include, "pose").text = (
                        f"{x:.4f} {y:.4f} {z:.4f} {roll:.4f} {pitch:.4f} {yaw:.4f}"
                    )

                    category_placed += 1
                    models_placed += 1

                    if self.progress_callback and total_models > 0:
                        progress = int((models_placed / total_models) * 100)
                        self.progress_callback(progress, f"Placing {category}...")

                except Exception as e:
                    logger.warning(f"Failed to add {category} model: {e}")
                    category_failed += 1
                    models_failed += 1
                    continue

            logger.info(
                f"  {category}: placed {category_placed}/{count} (failed: {category_failed})"
            )

        output_path = Path(output_path) if output_path else self.worlds_path / "forest_world.world"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info(f"World file created at: {output_path}")
        logger.info(
            f"Total models placed: {models_placed}/{total_models} (failed: {models_failed})"
        )
        logger.info("Models placed by category:")
        for category in category_order:
            if category in self.placed_models:
                logger.info(f"  - {category}: {len(self.placed_models[category])}")
                if self.placement_rejections.get(category):
                    logger.info(f"    ecology rejected: {self.placement_rejections[category]}")

        from forest3d.utils.provenance import build_provenance, provenance_json

        terrain_path = self.models_path / "ground" / "mesh" / "terrain.stl"
        model_uris = [
            item.findtext("uri", "") for item in world.findall("include")
        ]
        provenance = build_provenance(
            root=self.base_path,
            terrain_mesh=terrain_path,
            source_dem=self.source_dem_path,
            model_uris=model_uris,
            models_root=self.models_path,
            bit_generator=type(self.rng.bit_generator).__name__,
            ecology_layers=effective_suitability.layers,
        )
        ET.SubElement(generation, "provenance").text = provenance_json(provenance)
        write_world_file(world_elem, output_path)

        return output_path

    def get_model_statistics(self) -> Dict:
        """Get statistics about placed models.

        Returns:
            Dictionary containing placement statistics.
        """
        stats = {
            "total_models": sum(len(models) for models in self.placed_models.values()),
            "by_category": {
                category: len(models) for category, models in self.placed_models.items()
            },
            "variants_available": {
                category: len(variants) for category, variants in self.model_variants.items()
            },
            "placement_rejections": self.placement_rejections,
        }

        # Add scale statistics per category
        stats["scale_stats"] = {}
        for category, models in self.placed_models.items():
            if models:
                scales = [m[3] for m in models]
                stats["scale_stats"][category] = {
                    "min": float(np.min(scales)),
                    "max": float(np.max(scales)),
                    "mean": float(np.mean(scales)),
                }

        return stats

    def get_placement_density_map(self, resolution: int = 50) -> Dict[str, np.ndarray]:
        """Generate density maps for visualization/debugging.

        Args:
            resolution: Grid resolution for density calculation.

        Returns:
            Dictionary of category -> 2D density array.
        """
        density_maps = {}

        # Get bounds from placed models
        all_positions = []
        for models in self.placed_models.values():
            all_positions.extend([(m[0], m[1]) for m in models])

        if not all_positions:
            return density_maps

        all_positions = np.array(all_positions)
        min_x, max_x = np.min(all_positions[:, 0]), np.max(all_positions[:, 0])
        min_y, max_y = np.min(all_positions[:, 1]), np.max(all_positions[:, 1])

        for category, models in self.placed_models.items():
            if not models:
                density_maps[category] = np.zeros((resolution, resolution))
                continue

            density = np.zeros((resolution, resolution))
            for x, y, z, scale in models:
                # Convert to grid coordinates
                gx = int((x - min_x) / (max_x - min_x + 1e-6) * (resolution - 1))
                gy = int((y - min_y) / (max_y - min_y + 1e-6) * (resolution - 1))
                gx = np.clip(gx, 0, resolution - 1)
                gy = np.clip(gy, 0, resolution - 1)
                density[gy, gx] += 1

            density_maps[category] = density

        return density_maps
