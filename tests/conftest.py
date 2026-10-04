"""Small reusable terrain fixtures for Forest3D tests."""

import numpy as np
import pytest
from stl import mesh as stl_mesh


@pytest.fixture
def flat_terrain_stl(tmp_path):
    """Create a 100 m square flat terrain mesh split into two triangles."""
    vertices = np.array(
        [
            [-50.0, -50.0, 0.0],
            [50.0, -50.0, 0.0],
            [-50.0, 50.0, 0.0],
            [50.0, 50.0, 0.0],
        ],
        dtype=np.float32,
    )
    faces = ((0, 1, 2), (1, 3, 2))
    terrain = stl_mesh.Mesh(np.zeros(len(faces), dtype=stl_mesh.Mesh.dtype))
    for index, face in enumerate(faces):
        terrain.vectors[index] = vertices[list(face)]

    path = tmp_path / "terrain.stl"
    terrain.save(str(path))
    return path
