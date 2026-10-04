"""Regression tests for generated demo models and forest worlds."""

from itertools import combinations
import json
import hashlib
from xml.etree import ElementTree as ET

import numpy as np
import pytest
from pydantic import ValidationError

from forest3d.cli.demo import create_demo_models
from forest3d.config.schema import SuitabilityConfig
from forest3d.core.forest import WorldPopulator
from stl import mesh as stl_mesh


def test_suitability_config_validates_per_category_ranges():
    suitability = SuitabilityConfig.model_validate(
        {"tree": {"min_elevation": 5, "max_elevation": 30, "max_slope_degrees": 20}}
    )

    assert suitability.tree.min_elevation == 5
    assert suitability.tree.max_elevation == 30
    assert suitability.tree.max_slope_degrees == 20
    assert suitability.rock.max_slope_degrees > suitability.tree.max_slope_degrees
    with pytest.raises(ValidationError, match="min_elevation"):
        SuitabilityConfig.model_validate({"tree": {"min_elevation": 20, "max_elevation": 10}})


def test_unsuitable_slope_and_elevation_reject_placements(tmp_path):
    models_path = tmp_path / "models"
    ground_mesh_path = models_path / "ground" / "mesh" / "terrain.stl"
    ground_mesh_path.parent.mkdir(parents=True)
    vertices = np.array(
        [[-50, -50, 0], [50, -50, 100], [-50, 50, 0], [50, 50, 100]],
        dtype=np.float32,
    )
    terrain = stl_mesh.Mesh(np.zeros(2, dtype=stl_mesh.Mesh.dtype))
    terrain.vectors[0] = vertices[[0, 1, 2]]
    terrain.vectors[1] = vertices[[1, 3, 2]]
    terrain.save(str(ground_mesh_path))
    scales = {
        round(minimum + (maximum - minimum) * step / 20, 3)
        for minimum, maximum in WorldPopulator.SCALE_RANGES.values()
        for step in range(21)
    }
    create_demo_models(models_path, scales)
    strict_slope = WorldPopulator(
        tmp_path, seed=8, suitability_config={"tree": {"max_slope_degrees": 10}}
    )
    assert strict_slope._get_random_position(strict_slope._get_terrain_mesh(), "tree") is None

    strict_elevation = WorldPopulator(
        tmp_path, seed=8, suitability_config={"tree": {"min_elevation": 200}}
    )
    assert (
        strict_elevation._get_random_position(strict_elevation._get_terrain_mesh(), "tree") is None
    )


def test_demo_models_have_matching_visual_collision_geometry(tmp_path):
    models_path = tmp_path / "models"

    count = create_demo_models(models_path)

    assert count == 5
    categories = {"tree", "bush", "rock", "grass", "sand"}
    assert {path.name for path in models_path.iterdir()} == categories
    for category in categories:
        model_dir = models_path / category / "forest3d_demo_s1000"
        model_config = ET.parse(model_dir / "model.config").getroot()
        assert model_config.findtext("sdf") == "model.sdf"
        model = ET.parse(model_dir / "model.sdf").getroot().find("model")
        assert model is not None
        assert model.find("link") is not None
        for link in model.findall("link"):
            visual_geometry = link.find("visual/geometry")
            collision_geometry = link.find("collision/geometry")
            assert visual_geometry is not None
            assert collision_geometry is not None
            assert ET.tostring(visual_geometry).strip() == ET.tostring(collision_geometry).strip()


def test_placement_stays_in_bounds_and_respects_tree_spacing(
    tmp_path, flat_terrain_stl, monkeypatch
):
    models_path = tmp_path / "models"
    ground_mesh_path = models_path / "ground" / "mesh" / "terrain.stl"
    ground_mesh_path.parent.mkdir(parents=True)
    flat_terrain_stl.replace(ground_mesh_path)
    create_demo_models(models_path)
    (models_path / "ground" / "model.config").write_text(
        "<model><name>ground</name><sdf>model.sdf</sdf></model>"
    )
    (models_path / "ground" / "model.sdf").write_text(
        '<sdf version="1.8"><model name="ground"/></sdf>'
    )
    populator = WorldPopulator(
        tmp_path,
        seed=2718,
        suitability_config={
            "tree": {"min_elevation": 0, "max_elevation": 0, "max_slope_degrees": 0}
        },
    )

    terrain = populator._get_terrain_mesh()
    for _ in range(4):
        position = populator._get_random_position(terrain, "tree", scale=1.0)
        assert position is not None
        x, y, _ = position
        assert -48.0 <= x <= 48.0
        assert -48.0 <= y <= 48.0
        height, slope = populator._sample_terrain_properties(terrain, x, y)
        assert 0.0 <= height <= 0.0
        assert slope <= 0.0

    for first, second in combinations(populator.placed_models["tree"], 2):
        distance = np.hypot(first[0] - second[0], first[1] - second[1])
        assert distance >= populator.MIN_DISTANCES["tree"]


def test_create_forest_world_includes_terrain_and_requested_models(
    tmp_path, flat_terrain_stl, monkeypatch
):
    models_path = tmp_path / "models"
    ground_mesh_path = models_path / "ground" / "mesh" / "terrain.stl"
    ground_mesh_path.parent.mkdir(parents=True)
    flat_terrain_stl.replace(ground_mesh_path)
    create_demo_models(models_path)
    populator = WorldPopulator(tmp_path, seed=42)
    output = tmp_path / "custom" / "forest.world"

    world_path = populator.create_forest_world(
        {"tree": 2, "bush": 0, "rock": 0, "grass": 0, "sand": 0},
        output_path=output,
        suitability_config={"tree": {"max_slope_degrees": 12}},
    )

    assert world_path == output
    world = ET.parse(world_path).getroot().find("world")
    assert world is not None
    uris = [include.findtext("uri") for include in world.findall("include")]
    assert uris.count("model://ground") == 1
    assert len([uri for uri in uris if uri.startswith("model://tree/forest3d_demo_s")]) == 2
    assert populator.get_model_statistics()["total_models"] == 2
    metadata = world.find("forest3d_generation")
    assert metadata is not None
    assert metadata.findtext("seed") == "42"
    assert metadata.findtext("density") == ('{"bush":0,"grass":0,"rock":0,"sand":0,"tree":2}')
    terrain_settings = json.loads(metadata.findtext("terrain"))
    assert terrain_settings["scale_factor"] == 1.0
    assert terrain_settings["smooth_sigma"] == 1.0
    suitability = json.loads(metadata.findtext("suitability"))
    assert suitability["tree"]["max_slope_degrees"] == 12.0


def test_generated_world_model_uris_resolve_to_local_model_configs(
    tmp_path, flat_terrain_stl, monkeypatch
):
    models_path = tmp_path / "models"
    ground_mesh_path = models_path / "ground" / "mesh" / "terrain.stl"
    ground_mesh_path.parent.mkdir(parents=True)
    flat_terrain_stl.replace(ground_mesh_path)
    scales = {
        round(minimum + (maximum - minimum) * step / 20, 3)
        for minimum, maximum in WorldPopulator.SCALE_RANGES.values()
        for step in range(21)
    }
    create_demo_models(models_path, scales)
    (models_path / "ground" / "model.config").write_text(
        "<model><name>ground</name><sdf>model.sdf</sdf></model>"
    )
    (models_path / "ground" / "model.sdf").write_text(
        '<sdf version="1.8"><model name="ground"/></sdf>'
    )
    populator = WorldPopulator(tmp_path, seed=19)
    world_path = populator.create_forest_world(
        {"tree": 1, "bush": 0, "rock": 0, "grass": 0, "sand": 0}
    )
    world = ET.parse(world_path).getroot().find("world")

    for include in world.findall("include"):
        uri = include.findtext("uri")
        assert uri is not None
        if uri == "model://ground":
            model_dir = models_path / "ground"
        else:
            model_dir = models_path / uri.removeprefix("model://")
        assert (model_dir / "model.config").is_file()
        assert (model_dir / "model.sdf").is_file()


def test_seed_reproduces_model_instances_and_poses(tmp_path, flat_terrain_stl):
    models_path = tmp_path / "models"
    ground_mesh_path = models_path / "ground" / "mesh" / "terrain.stl"
    ground_mesh_path.parent.mkdir(parents=True)
    flat_terrain_stl.replace(ground_mesh_path)
    create_demo_models(models_path)

    generated_worlds = []
    for seed in (51, 51, 52):
        populator = WorldPopulator(tmp_path, seed=seed)
        world_path = populator.create_forest_world(
            {"tree": 3, "bush": 0, "rock": 0, "grass": 0, "sand": 0},
            output_path=tmp_path / f"seed-{seed}-{len(generated_worlds)}.world",
        )
        world = ET.parse(world_path).getroot().find("world")
        generated_worlds.append(
            [
                (include.findtext("uri"), include.findtext("pose"))
                for include in world.findall("include")
            ]
        )
        provenance = json.loads(world.findtext("forest3d_generation/provenance"))
        assert provenance["numpy"] == np.__version__
        assert provenance["bit_generator"] == "PCG64"
        assert provenance["terrain_mesh_sha256"]
        assert "models/ground/mesh/terrain.stl" in provenance["files"]

    assert generated_worlds[0] == generated_worlds[1]
    assert generated_worlds[0] != generated_worlds[2]


def test_provenance_hash_changes_when_a_referenced_asset_changes(tmp_path, flat_terrain_stl):
    models_path = tmp_path / "models"
    ground_mesh_path = models_path / "ground" / "mesh" / "terrain.stl"
    ground_mesh_path.parent.mkdir(parents=True)
    flat_terrain_stl.replace(ground_mesh_path)
    scales = {
        round(minimum + (maximum - minimum) * step / 20, 3)
        for minimum, maximum in WorldPopulator.SCALE_RANGES.values()
        for step in range(21)
    }
    create_demo_models(models_path, scales)
    populator = WorldPopulator(tmp_path, seed=12)
    first_world = populator.create_forest_world(
        {"tree": 1, "bush": 0, "rock": 0, "grass": 0, "sand": 0},
        output_path=tmp_path / "first.world",
    )
    first = json.loads(ET.parse(first_world).findtext(".//provenance"))
    model_uri = json.loads(ET.parse(first_world).findtext(".//provenance"))["assets"]
    tree_uri = next(uri for uri in model_uri if uri.startswith("model://tree/"))
    tree_sdf = models_path / tree_uri.removeprefix("model://") / "model.sdf"
    tree_sdf.write_text(tree_sdf.read_text() + "\n<!-- changed -->\n")
    second_populator = WorldPopulator(tmp_path, seed=12)
    second_world = second_populator.create_forest_world(
        {"tree": 1, "bush": 0, "rock": 0, "grass": 0, "sand": 0},
        output_path=tmp_path / "second.world",
    )
    second = json.loads(ET.parse(second_world).findtext(".//provenance"))
    assert first["files"] != second["files"]


def test_spatial_queries_match_linear_reference(tmp_path, flat_terrain_stl):
    from forest3d.core.forest import WorldPopulator

    terrain = stl_mesh.Mesh.from_file(str(flat_terrain_stl))
    populator = WorldPopulator.__new__(WorldPopulator)
    populator._terrain_index = {}
    populator._terrain_index_size = 10.0
    populator._terrain_index_mesh_id = None
    for x, y in ((-25, -25), (0, 0), (20, 30), (49, 49), (51, 0)):
        populator._use_spatial_index = False
        reference = populator._sample_terrain_properties(terrain, x, y)
        populator._use_spatial_index = True
        indexed = populator._sample_terrain_properties(terrain, x, y)
        assert indexed == reference

    populator.placed_models = {
        "tree": [(0.0, 0.0, 0.0, 1.2), (30.0, 30.0, 0.0, 0.9)],
        "bush": [(3.0, 0.0, 0.0, 0.7)],
        "rock": [], "grass": [], "sand": [],
    }
    populator.CROSS_CATEGORY_DISTANCES = WorldPopulator.CROSS_CATEGORY_DISTANCES
    populator.MIN_DISTANCES = WorldPopulator.MIN_DISTANCES
    populator._distance_cell_size = 10.0
    populator._distance_grid = {}
    populator._distance_grid_count = -1
    for x, y, category, scale in ((8, 0, "tree", 1.0), (10, 0, "grass", .4), (45, 45, "rock", 1.1)):
        populator._use_spatial_index = False
        reference = populator._check_distance_to_placed(x, y, category, scale)
        populator._use_spatial_index = True
        populator._distance_grid_count = -1
        indexed = populator._check_distance_to_placed(x, y, category, scale)
        assert indexed is reference


def test_tree_distribution_has_local_clusters_and_open_gaps(tmp_path, flat_terrain_stl):
    models_path = tmp_path / "models"
    ground_mesh_path = models_path / "ground" / "mesh" / "terrain.stl"
    ground_mesh_path.parent.mkdir(parents=True)
    flat_terrain_stl.replace(ground_mesh_path)
    create_demo_models(models_path)
    populator = WorldPopulator(tmp_path, seed=721)
    populator.create_forest_world({"tree": 14, "bush": 0, "rock": 0, "grass": 0, "sand": 0})

    trees = populator.placed_models["tree"]
    distances = [
        np.hypot(first[0] - second[0], first[1] - second[1])
        for first, second in combinations(trees, 2)
    ]
    assert any(populator.MIN_DISTANCES["tree"] <= distance <= 18 for distance in distances)
    assert any(distance >= 25 for distance in distances)


def test_all_categories_obey_configured_terrain_suitability(tmp_path, flat_terrain_stl):
    models_path = tmp_path / "models"
    ground_mesh_path = models_path / "ground" / "mesh" / "terrain.stl"
    ground_mesh_path.parent.mkdir(parents=True)
    flat_terrain_stl.replace(ground_mesh_path)
    create_demo_models(models_path)
    limits = {
        category: {"min_elevation": 0, "max_elevation": 0, "max_slope_degrees": 0}
        for category in ("tree", "bush", "rock", "grass", "sand")
    }
    populator = WorldPopulator(tmp_path, seed=144, suitability_config=limits)
    populator.create_forest_world({category: 2 for category in limits})
    terrain = populator._get_terrain_mesh()

    for category, positions in populator.placed_models.items():
        assert len(positions) == 2
        for x, y, _, _ in positions:
            height, slope = populator._sample_terrain_properties(terrain, x, y)
            assert limits[category]["min_elevation"] <= height <= limits[category]["max_elevation"]
            assert slope <= limits[category]["max_slope_degrees"]
