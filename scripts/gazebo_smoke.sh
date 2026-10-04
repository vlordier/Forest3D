#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

uv run forest3d demo \
  --dem dem/terrain.tif \
  --density '{"tree":12,"bush":4,"rock":3,"grass":8,"sand":1}'

gz sim --version | tee /tmp/forest3d-gazebo-version.txt
grep -q 'Gazebo Sim, version 8\.' /tmp/forest3d-gazebo-version.txt

export GZ_SIM_RESOURCE_PATH="$(pwd)/models${GZ_SIM_RESOURCE_PATH:+:$GZ_SIM_RESOURCE_PATH}"
log_file="$(mktemp)"
trap 'rm -f "$log_file"' EXIT

timeout 120s gz sim -s -r --iterations 3 worlds/forest_world.world >"$log_file" 2>&1 || {
  cat "$log_file"
  exit 1
}

if grep -Eiq 'Unable to find uri|Error Code|Failed to load (resource|mesh|material)|Unable to find file' "$log_file"; then
  cat "$log_file"
  echo "Gazebo reported an unresolved model, mesh, or material resource." >&2
  exit 1
fi

echo "Gazebo headless world smoke passed."
