"""Regression tests for DEM-to-mesh generation and terrain sampling."""

import numpy as np
import pytest
import json
from stl import mesh as stl_mesh
from xml.etree import ElementTree as ET

from forest3d.config.schema import TerrainConfig
from forest3d.core.forest import WorldPopulator
from forest3d.core.terrain import TerrainGenerator


def test_calculate_normals_returns_unit_up_normals_for_flat_triangle():
    vertices = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32)
    faces = np.array([[0, 1, 2]], dtype=np.int32)

    normals = TerrainGenerator._calculate_normals(None, vertices, faces)

    np.testing.assert_allclose(normals, [[0, 0, 1]] * 3)


def test_sample_terrain_height_interpolates_inside_triangle():
    terrain = stl_mesh.Mesh(np.zeros(1, dtype=stl_mesh.Mesh.dtype))
    terrain.vectors[0] = np.array(
        [[0.0, 0.0, 0.0], [1.0, 0.0, 1.0], [0.0, 1.0, 1.0]], dtype=np.float32
    )
    populator = WorldPopulator.__new__(WorldPopulator)

    height = populator._sample_terrain_height(terrain, 0.25, 0.25)

    assert height == pytest.approx(0.5)


def test_sample_terrain_properties_returns_local_height_and_slope():
    terrain = stl_mesh.Mesh(np.zeros(1, dtype=stl_mesh.Mesh.dtype))
    terrain.vectors[0] = np.array(
        [[0.0, 0.0, 0.0], [1.0, 0.0, 1.0], [0.0, 1.0, 0.0]], dtype=np.float32
    )
    populator = WorldPopulator.__new__(WorldPopulator)

    height, slope = populator._sample_terrain_properties(terrain, 0.25, 0.25)

    assert height == pytest.approx(0.25)
    assert slope == pytest.approx(45.0)


def test_process_terrain_writes_expected_meshes_and_model_files(tmp_path):
    gdal, osr = _gdal_modules()
    dem_path = tmp_path / "small_dem.tif"
    driver = gdal.GetDriverByName("GTiff")
    dataset = driver.Create(str(dem_path), 4, 3, 1, gdal.GDT_Float32)
    dataset.SetGeoTransform((100.0, 2.0, 0.0, 200.0, 0.0, -3.0))
    _set_projected_crs(dataset, osr, 32631)
    dataset.GetRasterBand(1).WriteArray(
        np.array([[10, 11, 12, 13], [11, 12, 13, 14], [12, 13, 14, 15]], dtype=np.float32)
    )
    dataset.FlushCache()
    dataset = None

    output_path = tmp_path / "models" / "ground"
    generator = TerrainGenerator(
        dem_path,
        output_path=output_path,
        config=TerrainConfig(scale_factor=1.0, smooth_sigma=0.0),
    )
    result_path = generator.process_terrain()

    assert result_path == output_path
    assert (output_path / "mesh" / "terrain.obj").is_file()
    assert (output_path / "mesh" / "terrain.stl").is_file()
    assert (output_path / "model.config").is_file()
    assert (output_path / "test.world").is_file()
    terrain_metadata = json.loads((output_path / "terrain_metadata.json").read_text())
    assert terrain_metadata["source_dem"] == dem_path.name
    assert terrain_metadata["width"] == 4
    assert terrain_metadata["height"] == 3
    assert terrain_metadata["world_center_xy"] == pytest.approx([103.0, 197.0])
    assert terrain_metadata["source_linear_unit_factor"] == pytest.approx(1.0)
    model = ET.parse(output_path / "model.sdf").getroot()
    assert model.findtext(".//collision/geometry/mesh/uri") == "model://ground/mesh/terrain.stl"
    assert model.findtext(".//visual/geometry/mesh/uri") == "model://ground/mesh/terrain.obj"

    terrain_mesh = stl_mesh.Mesh.from_file(str(output_path / "mesh" / "terrain.stl"))
    assert terrain_mesh.vectors.shape == (12, 3, 3)
    _, stats = generator.create_terrain_mesh(smooth_sigma=0.0)
    assert stats["num_vertices"] == 12
    assert stats["num_faces"] == 12
    assert stats["x_extent"] == pytest.approx(6.0)
    assert stats["y_extent"] == pytest.approx(6.0)


def _gdal_modules():
    gdal = pytest.importorskip("osgeo.gdal")
    osr = pytest.importorskip("osgeo.osr")
    return gdal, osr


def _set_projected_crs(dataset, osr, epsg, unit_name=None, unit_to_meters=None):
    spatial_ref = osr.SpatialReference()
    spatial_ref.ImportFromEPSG(epsg)
    if unit_name is not None:
        spatial_ref.SetLinearUnits(unit_name, unit_to_meters)
    dataset.SetProjection(spatial_ref.ExportToWkt())


def _create_dem(path, values, geotransform, epsg=32631, nodata=None, scale=None, offset=None):
    gdal, osr = _gdal_modules()
    values = np.asarray(values, dtype=np.float32)
    dataset = gdal.GetDriverByName("GTiff").Create(
        str(path), values.shape[1], values.shape[0], 1, gdal.GDT_Float32
    )
    dataset.SetGeoTransform(geotransform)
    _set_projected_crs(dataset, osr, epsg)
    band = dataset.GetRasterBand(1)
    band.WriteArray(values)
    if nodata is not None:
        band.SetNoDataValue(nodata)
    if scale is not None:
        band.SetScale(scale)
    if offset is not None:
        band.SetOffset(offset)
    dataset.FlushCache()
    dataset = None


def test_geographic_dem_is_projected_to_local_metric_extent(tmp_path):
    gdal, osr = _gdal_modules()
    dem_path = tmp_path / "geographic.tif"
    dataset = gdal.GetDriverByName("GTiff").Create(str(dem_path), 3, 2, 1, gdal.GDT_Float32)
    dataset.SetGeoTransform((-123.0, 0.0001, 0.0, 49.0, 0.0, -0.0001))
    spatial_ref = osr.SpatialReference()
    spatial_ref.ImportFromEPSG(4326)
    dataset.SetProjection(spatial_ref.ExportToWkt())
    dataset.GetRasterBand(1).WriteArray(np.ones((2, 3), dtype=np.float32))
    dataset = None

    generator = TerrainGenerator(
        dem_path, output_path=tmp_path / "out", config=TerrainConfig(smooth_sigma=0)
    )
    _, stats = generator.create_terrain_mesh()

    assert stats["x_extent"] == pytest.approx(14.6, rel=0.02)
    assert stats["y_extent"] == pytest.approx(11.1, rel=0.02)
    mesh = stl_mesh.Mesh.from_file(str(tmp_path / "out" / "mesh" / "terrain.stl"))
    assert np.isfinite(mesh.vectors).all()


def test_projected_crs_units_are_converted_to_meters(tmp_path):
    gdal, osr = _gdal_modules()
    dem_path = tmp_path / "feet.tif"
    dataset = gdal.GetDriverByName("GTiff").Create(str(dem_path), 3, 2, 1, gdal.GDT_Float32)
    dataset.SetGeoTransform((0, 10, 0, 0, 0, -10))
    spatial_ref = osr.SpatialReference()
    spatial_ref.ImportFromEPSG(32631)
    spatial_ref.SetLinearUnits("US survey foot", 1200.0 / 3937.0)
    dataset.SetProjection(spatial_ref.ExportToWkt())
    dataset.GetRasterBand(1).WriteArray(np.ones((2, 3), dtype=np.float32))
    dataset = None

    generator = TerrainGenerator(
        dem_path, output_path=tmp_path / "out", config=TerrainConfig(smooth_sigma=0)
    )
    _, stats = generator.create_terrain_mesh()
    assert stats["x_extent"] == pytest.approx(20 * 1200 / 3937)
    assert stats["y_extent"] == pytest.approx(10 * 1200 / 3937)


@pytest.mark.parametrize("geotransform", [(0, 1, 0.1, 0, 0, -1), (0, 1, 0, 0, 0.1, -1)])
def test_rotated_or_skewed_affine_is_rejected(tmp_path, geotransform):
    dem_path = tmp_path / "skewed.tif"
    _create_dem(dem_path, [[1, 2], [3, 4]], geotransform)
    generator = TerrainGenerator(dem_path, output_path=tmp_path / "out")

    with pytest.raises(ValueError, match="Rotated or skewed"):
        generator.create_terrain_mesh()


def test_nodata_is_rejected_instead_of_written_as_a_terrain_spike(tmp_path):
    dem_path = tmp_path / "nodata.tif"
    _create_dem(dem_path, [[1, -9999], [3, 4]], (0, 1, 0, 0, 0, -1), nodata=-9999)
    generator = TerrainGenerator(dem_path, output_path=tmp_path / "out")

    with pytest.raises(ValueError, match="NoData or masked"):
        generator.create_terrain_mesh()


def test_masked_cells_are_rejected(tmp_path):
    gdal, _ = _gdal_modules()
    dem_path = tmp_path / "masked.tif"
    _create_dem(dem_path, [[1, 2], [3, 4]], (0, 1, 0, 0, 0, -1))
    dataset = gdal.Open(str(dem_path), gdal.GA_Update)
    band = dataset.GetRasterBand(1)
    band.CreateMaskBand(gdal.GMF_PER_DATASET)
    band.GetMaskBand().WriteArray(np.array([[255, 0], [255, 255]], dtype=np.uint8))
    dataset = None
    generator = TerrainGenerator(dem_path, output_path=tmp_path / "out")

    with pytest.raises(ValueError, match="NoData or masked"):
        generator.create_terrain_mesh()


def test_raster_scale_and_offset_are_applied_to_elevation(tmp_path):
    dem_path = tmp_path / "scaled.tif"
    _create_dem(
        dem_path,
        [[10, 11], [12, 13]],
        (0, 1, 0, 0, 0, -1),
        scale=2.0,
        offset=5.0,
    )
    generator = TerrainGenerator(
        dem_path, output_path=tmp_path / "out", config=TerrainConfig(smooth_sigma=0)
    )

    _, stats = generator.create_terrain_mesh()

    # The scale doubles the raw elevation range; the offset shifts the origin and remains finite.
    assert stats["z_extent"] == pytest.approx(6.0)
    vertices = np.array(
        [
            [float(value) for value in line.split()[1:]]
            for line in (tmp_path / "out" / "mesh" / "terrain.obj").read_text().splitlines()
            if line.startswith("v ")
        ]
    )
    assert np.isfinite(vertices).all()


def test_geographic_crs_unit_conversion_uses_projected_known_extent(tmp_path):
    """A lon/lat input is rejected; the equivalent projected DEM retains metric extent."""
    dem_path = tmp_path / "projected.tif"
    _create_dem(dem_path, [[0, 1, 2], [1, 2, 3]], (500000, 30, 0, 5500000, 0, -20))
    generator = TerrainGenerator(
        dem_path, output_path=tmp_path / "out", config=TerrainConfig(smooth_sigma=0)
    )

    _, stats = generator.create_terrain_mesh()

    assert stats["x_extent"] == pytest.approx(60.0)
    assert stats["y_extent"] == pytest.approx(20.0)
    mesh = stl_mesh.Mesh.from_file(str(tmp_path / "out" / "mesh" / "terrain.stl"))
    assert np.isfinite(mesh.vectors).all()
