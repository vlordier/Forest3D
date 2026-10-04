"""Optional ecology raster configuration and sampling tests."""

import json
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from forest3d.cli.demo import create_demo_models
from forest3d.config.schema import EcologyLayerConfig, SuitabilityConfig
from forest3d.core.ecology import EcologyRasterSampler
from forest3d.core.forest import WorldPopulator
from osgeo import gdal, osr


def _write_raster(path, values, geotransform=(-50, 50, 0, 50, 0, -50), nodata=None):
    driver = gdal.GetDriverByName("GTiff")
    dataset = driver.Create(str(path), values.shape[1], values.shape[0], 1, gdal.GDT_Float32)
    dataset.SetGeoTransform(geotransform)
    spatial_ref = osr.SpatialReference()
    spatial_ref.ImportFromEPSG(32631)
    dataset.SetProjection(spatial_ref.ExportToWkt())
    band = dataset.GetRasterBand(1)
    band.WriteArray(values)
    if nodata is not None:
        band.SetNoDataValue(nodata)
    dataset = None


def _metadata():
    spatial_ref = osr.SpatialReference()
    spatial_ref.ImportFromEPSG(32631)
    return {
        "world_crs_wkt": spatial_ref.ExportToWkt(),
        "world_center_xy": [0.0, 0.0],
        "horizontal_scale_factor": 1.0,
        "source_linear_unit_factor": 1.0,
    }


def test_nearest_and_bilinear_sample_pixel_centers(tmp_path):
    raster_path = tmp_path / "ecology.tif"
    _write_raster(raster_path, np.array([[1, 2], [3, 4]], dtype=np.float32))
    nearest = EcologyRasterSampler(
        EcologyLayerConfig(path=raster_path, resampling="nearest"), _metadata()
    )
    bilinear = EcologyRasterSampler(
        EcologyLayerConfig(path=raster_path, resampling="bilinear"), _metadata()
    )

    assert nearest.sample(-25, 25) == 1
    assert bilinear.sample(-25, 25) == 1
    assert bilinear.sample(0, 0) == pytest.approx(2.5)


def test_nodata_and_outside_raster_are_rejected(tmp_path):
    raster_path = tmp_path / "ecology.tif"
    _write_raster(
        raster_path,
        np.array([[1, -9999], [3, 4]], dtype=np.float32),
        nodata=-9999,
    )
    sampler = EcologyRasterSampler(EcologyLayerConfig(path=raster_path), _metadata())

    assert sampler.sample(25, 25) is None
    assert sampler.sample(100, 100) is None


def test_ecology_rules_reject_undefined_layers_and_bad_ranges():
    with pytest.raises(ValueError, match="undefined ecology layers"):
        SuitabilityConfig.model_validate({"tree": {"ecology": {"moisture": {"minimum": 0.2}}}})
    with pytest.raises(ValueError, match="less than or equal"):
        SuitabilityConfig.model_validate(
            {
                "layers": {"moisture": {"path": "/tmp/moisture.tif"}},
                "tree": {"ecology": {"moisture": {"minimum": 0.8, "maximum": 0.2}}},
            }
        )


def test_ecology_constraints_explain_rejected_placements(tmp_path, flat_terrain_stl):
    models_path = tmp_path / "models"
    terrain_path = models_path / "ground"
    (terrain_path / "mesh").mkdir(parents=True)
    flat_terrain_stl.replace(terrain_path / "mesh" / "terrain.stl")
    (terrain_path / "terrain_metadata.json").write_text(json.dumps(_metadata()))
    create_demo_models(models_path)
    ecology_path = tmp_path / "habitat.tif"
    _write_raster(ecology_path, np.zeros((120, 120), dtype=np.float32), (-60, 1, 0, 60, 0, -1))

    populator = WorldPopulator(tmp_path, seed=4)
    world_path = populator.create_forest_world(
        {"tree": 1, "bush": 0, "rock": 0, "grass": 0, "sand": 0},
        suitability_config={
            "layers": {"habitat": {"path": ecology_path, "resampling": "nearest"}},
            "tree": {"ecology": {"habitat": {"values": [1]}}},
        },
    )

    assert populator.get_model_statistics()["total_models"] == 0
    assert populator.placement_rejections["tree"]["value"] == 100
    world = ET.parse(world_path).getroot()
    provenance = json.loads(world.findtext(".//provenance"))
    assert provenance["ecology_layers"][0]["name"] == "habitat"
    assert provenance["ecology_layers"][0]["sha256"]
