"""Render a fast, camera-style preview of a generated Forest3D SDF world.

This is a presentation preview, not a Gazebo renderer. It reads the real terrain
mesh and world placements and draws the demo primitives with Pillow.
"""

from __future__ import annotations

import argparse
import math
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def read_obj(path: Path) -> tuple[list[tuple[float, float, float]], list[tuple[int, int, int]]]:
    vertices, faces = [], []
    with path.open() as source:
        for line in source:
            if line.startswith("v "):
                vertices.append(tuple(map(float, line.split()[1:4])))
            elif line.startswith("f "):
                faces.append(tuple(int(part.split("/")[0]) - 1 for part in line.split()[1:4]))
    return vertices, faces


def project(x: float, y: float, z: float, center: tuple[float, float], scale: float):
    # Isometric view: east-west and north-south axes recede in opposite directions.
    return center[0] + (x - y) * scale * 0.72, center[1] + (x + y) * scale * 0.36 - z * scale * 0.9


def draw_tree(draw: ImageDraw.ImageDraw, x, y, z, size, project_point):
    base = project_point(x, y, z)
    trunk_top = project_point(x, y, z + 2.2 * size)
    draw.line((base, trunk_top), fill=(86, 57, 36), width=max(2, round(size * 1.2)))
    for height, radius, color in (
        (2.25, 0.9, (48, 111, 62)),
        (3.05, 0.65, (66, 139, 76)),
    ):
        top = project_point(x, y, z + (height + 0.85) * size)
        left = project_point(x - radius * size, y, z + height * size)
        right = project_point(x + radius * size, y, z + height * size)
        back = project_point(x, y - radius * size, z + height * size)
        front = project_point(x, y + radius * size, z + height * size)
        draw.polygon((top, left, back), fill=tuple(min(255, c + 17) for c in color))
        draw.polygon((top, back, right), fill=color)
        draw.polygon((top, right, front), fill=tuple(max(0, c - 19) for c in color))
        draw.polygon((top, front, left), fill=tuple(max(0, c - 8) for c in color))


def render(world: Path, output: Path, width: int, height: int) -> None:
    root = ET.parse(world).getroot()
    world_node = root.find("world")
    assert world_node is not None
    includes = []
    for item in world_node.findall("include"):
        uri, pose = item.findtext("uri", ""), item.findtext("pose", "0 0 0 0 0 0")
        category = uri.removeprefix("model://").split("/")[0]
        values = list(map(float, pose.split()))
        match = __import__("re").search(r"_s(\d+)$", uri)
        factor = int(match.group(1)) / 1000 if match else 1.0
        includes.append((category, values, factor))

    trees = [(p[0], p[1], p[2], s) for category, p, s in includes if category == "tree"]
    # Frame the forest cluster while retaining surrounding slopes.
    cx = sum(t[0] for t in trees) / max(1, len(trees))
    cy = sum(t[1] for t in trees) / max(1, len(trees))
    mesh, faces = read_obj(world.parent.parent / "models/ground/mesh/terrain.obj")
    zvals = [v[2] for v in mesh]
    project_scale = min(width / 180, height / 145)
    center = (width * 0.5, height * 0.59)
    project_point = lambda x, y, z: project(x - cx, y - cy, z - min(zvals), center, project_scale)

    image = Image.new("RGB", (width, height), (226, 237, 233))
    draw = ImageDraw.Draw(image)
    # Draw actual OBJ faces back-to-front so occlusion and the relief stay coherent.
    triangles = [(sum(mesh[i][0] + mesh[i][1] for i in face), [mesh[i] for i in face]) for face in faces]
    for _, tri in sorted(triangles, reverse=True, key=lambda entry: entry[0]):
        projected = [project_point(*v) for v in tri]
        avgz = sum(v[2] for v in tri) / 3
        shade = int(97 + 57 * (avgz - min(zvals)) / max(1, max(zvals) - min(zvals)))
        draw.polygon(projected, fill=(max(40, shade - 41), shade, max(43, shade - 65)))

    # Props use real world positions/scales. Back-to-front painter order.
    for category, pose, size in sorted(includes, key=lambda item: item[1][0] + item[1][1], reverse=True):
        x, y, z = pose[:3]
        if category == "tree":
            draw_tree(draw, x - cx, y - cy, z - min(zvals), size, lambda a,b,c: project(a,b,c,center,project_scale))
        elif category == "rock":
            px, py = project_point(x, y, z + 0.4 * size)
            r = max(3, 4 * size)
            draw.ellipse((px-r, py-r*0.55, px+r, py+r*0.55), fill=(113, 119, 105), outline=(75, 83, 75))
        elif category == "bush":
            px, py = project_point(x, y, z + .45 * size)
            r = max(3, 5 * size)
            draw.ellipse((px-r, py-r*.6, px+r, py+r*.6), fill=(64, 129, 71))
        elif category == "grass":
            px, py = project_point(x, y, z + .25 * size)
            draw.line((px, py, px-2, py-5*size), fill=(90, 145, 59), width=2)
        elif category == "sand":
            px, py = project_point(x, y, z + .2 * size)
            r = max(3, 4 * size)
            draw.ellipse((px-r, py-r*.35, px+r, py+r*.35), fill=(199, 169, 105))

    # Subtle presentation frame and honest renderer label.
    draw.rounded_rectangle((26, 24, width-26, height-24), radius=18, outline=(255,255,255), width=2)
    title_font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 23)
    small_font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 13)
    draw.text((48, 44), "FOREST3D  /  FOREST PREVIEW", font=title_font, fill=(32, 51, 43))
    draw.text((49, 76), f"{len(trees)} trees  ·  terrain + placements from {world.name}", font=small_font, fill=(65, 83, 73))
    draw.text((49, height-55), "LOCAL PREVIEW  ·  Gazebo materials and physics are not rendered", font=small_font, fill=(65, 83, 73))
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world", type=Path, default=Path("worlds/forest_world.world"))
    parser.add_argument("--output", type=Path, default=Path("worlds/forest_preview_local.png"))
    parser.add_argument("--width", type=int, default=1600)
    parser.add_argument("--height", type=int, default=1000)
    args = parser.parse_args()
    render(args.world.resolve(), args.output.resolve(), args.width, args.height)
    print(f"Preview written to {args.output}")


if __name__ == "__main__":
    main()
