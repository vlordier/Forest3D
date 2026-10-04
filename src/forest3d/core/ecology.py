"""Point sampling for optional, georeferenced ecology rasters."""

from __future__ import annotations

import numpy as np
from pathlib import Path

from forest3d.config.schema import EcologyLayerConfig


class EcologyRasterSampler:
    """Sample one numeric raster at Forest3D terrain-local XY positions."""

    def __init__(self, config: EcologyLayerConfig, terrain_metadata: dict):
        try:
            from osgeo import gdal, osr
        except ImportError as exc:
            raise RuntimeError("Ecology rasters require GDAL (install the terrain extra)") from exc

        self.path = Path(config.path)
        self.resampling = config.resampling
        self.dataset = gdal.Open(str(self.path), gdal.GA_ReadOnly)
        if self.dataset is None:
            raise ValueError(f"Cannot open ecology raster: {self.path}")
        if self.dataset.RasterCount < 1:
            raise ValueError(f"Ecology raster has no bands: {self.path}")
        target_srs = self.dataset.GetSpatialRef()
        source_srs = osr.SpatialReference()
        if target_srs is None:
            raise ValueError(f"Ecology raster is missing its CRS: {self.path}")
        if not terrain_metadata.get("world_crs_wkt"):
            raise ValueError("Terrain metadata is required to sample ecology rasters")
        source_srs.ImportFromWkt(terrain_metadata["world_crs_wkt"])
        source_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        target_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        self.transform = osr.CoordinateTransformation(source_srs, target_srs)
        self.geotransform = self.dataset.GetGeoTransform()
        a, b, c, d, e, f = self.geotransform
        determinant = b * f - c * e
        if abs(determinant) < 1e-15:
            raise ValueError(f"Ecology raster has a singular geotransform: {self.path}")
        self.inverse = (f / determinant, -c / determinant, -e / determinant, b / determinant)
        self.width, self.height = self.dataset.RasterXSize, self.dataset.RasterYSize
        self.band = self.dataset.GetRasterBand(1)
        self.mask = self.band.GetMaskBand()
        self.nodata = self.band.GetNoDataValue()
        self.value_scale = self.band.GetScale() or 1.0
        self.value_offset = self.band.GetOffset() or 0.0
        self.center_x, self.center_y = terrain_metadata["world_center_xy"]
        self.horizontal_scale = terrain_metadata["horizontal_scale_factor"]
        self.unit_factor = terrain_metadata["source_linear_unit_factor"]

    def _read(self, col: int, row: int) -> float | None:
        if not (0 <= col < self.width and 0 <= row < self.height):
            return None
        values = self.band.ReadAsArray(col, row, 1, 1)
        valid = self.mask.ReadAsArray(col, row, 1, 1)
        value = float(values[0, 0])
        if valid[0, 0] == 0 or not np.isfinite(value):
            return None
        if self.nodata is not None and value == self.nodata:
            return None
        return value * self.value_scale + self.value_offset

    def sample(self, x: float, y: float) -> float | None:
        """Sample with pixel-center semantics; NoData/outside returns None."""
        world_x = (x + self.center_x) / self.horizontal_scale
        world_y = (y + self.center_y) / self.horizontal_scale
        world_x /= self.unit_factor
        world_y /= self.unit_factor
        map_x, map_y, _ = self.transform.TransformPoint(world_x, world_y)
        origin_x, pixel_x, rotate_x, origin_y, rotate_y, pixel_y = self.geotransform
        dx, dy = map_x - origin_x, map_y - origin_y
        inv_a, inv_b, inv_c, inv_d = self.inverse
        col = inv_a * dx + inv_b * dy - 0.5
        row = inv_c * dx + inv_d * dy - 0.5

        if self.resampling == "nearest":
            return self._read(int(np.floor(col + 0.5)), int(np.floor(row + 0.5)))

        col0, row0 = int(np.floor(col)), int(np.floor(row))
        fx, fy = col - col0, row - row0
        samples = [
            (col0, row0, (1 - fx) * (1 - fy)),
            (col0 + 1, row0, fx * (1 - fy)),
            (col0, row0 + 1, (1 - fx) * fy),
            (col0 + 1, row0 + 1, fx * fy),
        ]
        weighted = []
        for col_i, row_i, weight in samples:
            if weight <= 1e-12:
                continue
            value = self._read(col_i, row_i)
            if value is None:
                return None
            weighted.append((value, weight))
        if not weighted:
            return None
        return float(sum(value * weight for value, weight in weighted))
