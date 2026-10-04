"""Configuration schema using Pydantic for validation."""

from pathlib import Path
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ConfigModel(BaseModel):
    """Shared strict config behavior for validated application settings."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class BlenderConfig(ConfigModel):
    """Blender configuration for asset conversion."""

    path: Optional[Path] = Field(
        default=None, description="Path to Blender executable (auto-detected if None)"
    )
    visual_decimation: float = Field(
        default=0.1, ge=0.01, le=1.0, description="Decimation ratio for visual mesh"
    )
    collision_decimation: float = Field(
        default=0.01, ge=0.001, le=0.5, description="Decimation ratio for collision mesh"
    )

    @field_validator("path", mode="before")
    @classmethod
    def expand_path(cls, v):
        if v is None:
            return v
        return Path(v).expanduser().resolve()


class TerrainConfig(ConfigModel):
    """Terrain generation configuration."""

    scale_factor: float = Field(default=1.0, ge=0.1, le=100.0, description="Scale factor")
    smooth_sigma: float = Field(default=1.0, ge=0.0, le=10.0, description="Gaussian smoothing")
    enhance: bool = Field(default=False, description="Enable DEM enhancement")
    enhance_scale: float = Field(default=6.0, ge=1.0, le=20.0, description="Enhancement scale")
    texture_blend: Optional[Path] = Field(
        default=None, description="Path to Blender file for terrain texture extraction"
    )
    material_name: str = Field(
        default="Terrain/Ground", description="Name for the generated material"
    )

    @field_validator("texture_blend", mode="before")
    @classmethod
    def expand_texture_path(cls, v):
        if v is None:
            return v
        return Path(v).expanduser().resolve()


class DensityConfig(ConfigModel):
    """Forest population density configuration."""

    tree: int = Field(default=50, ge=0, le=1000, strict=True, description="Number of trees")
    bush: int = Field(default=10, ge=0, le=500, strict=True, description="Number of bushes")
    rock: int = Field(default=5, ge=0, le=200, strict=True, description="Number of rocks")
    grass: int = Field(
        default=50, ge=0, le=2000, strict=True, description="Number of grass patches"
    )
    sand: int = Field(default=5, ge=0, le=100, strict=True, description="Number of sand patches")


class EcologyConstraint(ConfigModel):
    """Acceptable values for one named optional ecology raster."""

    minimum: Optional[float] = None
    maximum: Optional[float] = None
    values: Optional[list[float]] = None
    nodata: Literal["reject"] = "reject"

    @model_validator(mode="after")
    def validate_constraint(self):
        if self.minimum is None and self.maximum is None and not self.values:
            raise ValueError("an ecology rule needs minimum/maximum or allowed values")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("ecology minimum must be less than or equal to maximum")
        return self


class EcologyLayerConfig(ConfigModel):
    """Georeferenced single-band raster and point resampling rule."""

    path: Path
    resampling: Literal["nearest", "bilinear"] = "nearest"

    @field_validator("path", mode="before")
    @classmethod
    def resolve_path(cls, value):
        return Path(value).expanduser().resolve()


class CategorySuitability(ConfigModel):
    """Terrain elevation and slope limits for one model category."""

    min_elevation: float = Field(
        default=0.0, description="Minimum normalized terrain height in meters"
    )
    max_elevation: float = Field(
        default=100000.0, description="Maximum normalized terrain height in meters"
    )
    max_slope_degrees: float = Field(default=90.0, ge=0.0, le=90.0)
    ecology: dict[str, EcologyConstraint] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_elevation_range(self):
        if self.min_elevation > self.max_elevation:
            raise ValueError("min_elevation must be less than or equal to max_elevation")
        return self


class SuitabilityConfig(ConfigModel):
    """Per-category terrain suitability limits."""

    tree: CategorySuitability = Field(
        default_factory=lambda: CategorySuitability(max_slope_degrees=35.0)
    )
    bush: CategorySuitability = Field(
        default_factory=lambda: CategorySuitability(max_slope_degrees=40.0)
    )
    rock: CategorySuitability = Field(
        default_factory=lambda: CategorySuitability(max_slope_degrees=60.0)
    )
    grass: CategorySuitability = Field(
        default_factory=lambda: CategorySuitability(max_slope_degrees=25.0)
    )
    sand: CategorySuitability = Field(
        default_factory=lambda: CategorySuitability(max_slope_degrees=30.0)
    )
    layers: dict[str, EcologyLayerConfig] = Field(
        default_factory=dict, description="Optional named single-band ecology rasters"
    )

    @model_validator(mode="after")
    def validate_layer_references(self):
        for category in ("tree", "bush", "rock", "grass", "sand"):
            rules = getattr(self, category).ecology
            missing = set(rules) - set(self.layers)
            if missing:
                raise ValueError(
                    f"{category} suitability references undefined ecology layers: {sorted(missing)}"
                )
        return self


class PathsConfig(ConfigModel):
    """Project paths configuration."""

    base_path: Optional[Path] = Field(
        default=None, description="Project base path (auto-detected if None)"
    )
    models_path: Optional[Path] = Field(default=None, description="Models directory")
    worlds_path: Optional[Path] = Field(default=None, description="Worlds directory")

    @field_validator("base_path", "models_path", "worlds_path", mode="before")
    @classmethod
    def expand_path(cls, v):
        if v is None:
            return v
        return Path(v).expanduser().resolve()


class Forest3DConfig(ConfigModel):
    """Main configuration schema for Forest3D."""

    blender: BlenderConfig = Field(default_factory=BlenderConfig)
    terrain: TerrainConfig = Field(default_factory=TerrainConfig)
    density: DensityConfig = Field(default_factory=DensityConfig)
    suitability: SuitabilityConfig = Field(default_factory=SuitabilityConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
