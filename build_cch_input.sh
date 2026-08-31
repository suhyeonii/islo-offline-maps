#!/bin/zsh
set -euo pipefail

source_pbf="${SOURCE_PBF:-build/south-korea-latest.osm.pbf}"
version="${CCH_INPUT_VERSION:-v1}"
output="build/korea.cch-input.${version}.sqlite"
partial="${output}.partial"
filtered="build/korea.cch-input.${version}.filtered.osm.pbf"
dem_dir="${DEM_DIR:-dem}"
dem_args=()
[[ -d "$dem_dir" ]] && dem_args=(--dem-dir "$dem_dir")

if [[ -f "$output" ]]; then
  echo "CCH input already exists: ${output}"
  exit 0
fi

rm -f "$partial" "$filtered"
osmium tags-filter --overwrite "$source_pbf" \
  w/highway w/route=ferry n/highway=elevator n/elevator=yes \
  -o "$filtered"
osmium cat "$filtered" -f opl | python3 ./build_routing_graph.py \
  --cch-input --official-csv ./official_cycle_routes.csv "${dem_args[@]}" "$partial"
mv "$partial" "$output"
rm -f "$filtered"

echo "Built ${output}"
