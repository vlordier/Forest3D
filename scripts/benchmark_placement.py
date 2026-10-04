"""Compare linear reference placement with the spatially indexed implementation.

Run with ``uv run python scripts/benchmark_placement.py`` from the repository.
The checked-in report is specific to the machine on which it was collected.
"""

from __future__ import annotations

import json
import hashlib
import os
import platform
import resource
import statistics
import subprocess
import sys
import time
import types
from pathlib import Path

import numpy as np
from stl import mesh

from forest3d.core.forest import WorldPopulator


def linear_sample(self, terrain_mesh, x, y):
    triangles = terrain_mesh.vectors.astype(np.float64, copy=False)
    v0, v1, v2 = triangles[:, 0], triangles[:, 1], triangles[:, 2]
    denom = (v1[:, 1] - v2[:, 1]) * (v0[:, 0] - v2[:, 0]) + (v2[:, 0] - v1[:, 0]) * (v0[:, 1] - v2[:, 1])
    good = np.abs(denom) > 1e-12
    denom = np.where(good, denom, 1.0)
    w0 = ((v1[:, 1] - v2[:, 1]) * (x-v2[:, 0]) + (v2[:, 0]-v1[:, 0]) * (y-v2[:, 1])) / denom
    w1 = ((v2[:, 1]-v0[:, 1]) * (x-v2[:, 0]) + (v0[:, 0]-v2[:, 0]) * (y-v2[:, 1])) / denom
    w2 = 1-w0-w1
    indices = np.flatnonzero(good & (w0 >= -1e-8) & (w1 >= -1e-8) & (w2 >= -1e-8))
    if not len(indices):
        return None
    i=indices[0]; tri=triangles[i]
    z=w0[i]*tri[0,2]+w1[i]*tri[1,2]+w2[i]*tri[2,2]
    normal=np.cross(tri[1]-tri[0],tri[2]-tri[0]); length=np.linalg.norm(normal)
    if length <= 1e-12: return None
    return float(z), float(np.degrees(np.arccos(np.clip(abs(normal[2])/length,0,1))))


def linear_distance(self, x, y, category, scale=1.0):
    for other, positions in self.placed_models.items():
        base=self._get_cross_distance(category, other)
        for px,py,_,other_scale in positions:
            required=base*(max(scale,.5)+max(other_scale,.5))/2
            if np.sqrt((x-px)**2+(y-py)**2) < required:
                return False
    return True


def worker(mode: str, total: int, project: Path) -> dict:
    populator=WorldPopulator(project, seed=20261004)
    if mode == "linear":
        populator._sample_terrain_properties=types.MethodType(linear_sample,populator)
        populator._check_distance_to_placed=types.MethodType(linear_distance,populator)
    terrain_path=project/"models/ground/mesh/terrain.stl"
    face_count=len(mesh.Mesh.from_file(str(terrain_path)).vectors)
    # A deterministic mixed workload scales every category to the requested total.
    ratios={"tree":.25,"bush":.14,"rock":.10,"grass":.45,"sand":.06}
    density={key:int(total*ratio) for key,ratio in ratios.items()}
    density["tree"] += total-sum(density.values())
    started=time.perf_counter()
    world=populator.create_forest_world(density, output_path=project/"worlds"/f"bench-{mode}-{total}.world")
    elapsed=time.perf_counter()-started
    placement_hash=hashlib.sha256("\n".join(
        f"{node.findtext('uri')}|{node.findtext('pose')}"
        for node in __import__("xml.etree.ElementTree",fromlist=["parse"]).parse(world).findall(".//include")
    ).encode()).hexdigest()
    return {
        "mode":mode,"requested":sum(density.values()),
        "placed":populator.get_model_statistics()["total_models"],
        "terrain_faces":face_count,"elapsed_seconds":round(elapsed,4),
        "peak_rss_bytes":(
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            if sys.platform == "darwin"
            else resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
        ),
        "world_bytes":world.stat().st_size,
        "placement_sha256":placement_hash,
    }


def main():
    if len(sys.argv)>1 and sys.argv[1]=="--worker":
        print(json.dumps(worker(sys.argv[2],int(sys.argv[3]),Path(sys.argv[4]).resolve())))
        return
    project=Path.cwd()
    cases=[]
    for label,count in (("small",80),("large",640)):
        for repetition in range(7):
            for mode in (("linear","optimized") if repetition % 2 == 0 else ("optimized","linear")):
                completed=subprocess.run(
                    [sys.executable,__file__,"--worker",mode,str(count),str(project)],
                    check=True,capture_output=True,text=True,
                )
                cases.append({"case":label,"repetition":repetition+1,**json.loads(completed.stdout.strip().splitlines()[-1])})
    summaries=[]
    for label,count in (("small",80),("large",640)):
        for mode in ("linear","optimized"):
            samples=[row for row in cases if row["case"]==label and row["mode"]==mode]
            summaries.append({
                "case":label,"mode":mode,"requested":count,
                "placed":samples[-1]["placed"],"terrain_faces":samples[-1]["terrain_faces"],
                "median_elapsed_seconds":round(statistics.median(s["elapsed_seconds"] for s in samples),4),
                "median_peak_rss_bytes":int(statistics.median(s["peak_rss_bytes"] for s in samples)),
                "placement_hashes_match":len({s["placement_sha256"] for s in samples})==1,
                "placement_sha256":samples[-1]["placement_sha256"],
            })
    report={
        "hardware":platform.platform(),"machine":platform.machine(),
        "cpu":subprocess.run(["sysctl","-n","machdep.cpu.brand_string"],capture_output=True,text=True).stdout.strip() or platform.processor(),
        "python":platform.python_version(),
        "numpy":np.__version__,"seed":20261004,
        "note":"Seven isolated workers per case/mode; report uses medians. Linear mode uses full face and placement scans. Peak RSS is per worker.",
        "summary":summaries,"runs":cases,
    }
    output=project/"benchmarks/placement-macos-2026-10-04.json"
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,indent=2)+"\n")
    print(output)
    print(json.dumps(report,indent=2))


if __name__=="__main__":
    main()
