#!/bin/zsh
set -euo pipefail

source_pbf="build/south-korea-latest.osm.pbf"
build_graph() {
  local id="$1"
  local bbox="$2"
  local mode="$3"
  local source="$source_pbf"
  local extracted="build/${id}.routing.osm.pbf"
  local filtered="build/${id}.routing.filtered.osm.pbf"
  local output="build/${id}.routing.sqlite"
  if [[ -f "$output" ]]; then
    echo "Skipping existing ${output}"
    return
  fi
  if [[ -n "$bbox" ]]; then
    osmium extract --overwrite --strategy complete_ways --bbox "$bbox" -o "$extracted" "$source_pbf"
    source="$extracted"
  fi
  if [[ "$mode" == "compact" ]]; then
    osmium tags-filter --overwrite "$source" \
      w/highway=cycleway w/highway=trunk w/highway=primary w/highway=secondary \
      w/highway=tertiary \
      w/bicycle=designated w/bicycle=official \
      n/amenity=drinking_water n/amenity=toilets -o "$filtered"
    osmium cat "$filtered" -f opl | python3 ./build_routing_graph.py \
      --compact --official-csv ./official_cycle_routes.csv "$output"
  else
    osmium tags-filter --overwrite "$source" w/highway \
      n/amenity=drinking_water n/amenity=toilets -o "$filtered"
    osmium cat "$filtered" -f opl | python3 ./build_routing_graph.py \
      --official-csv ./official_cycle_routes.csv "$output"
  fi
  rm -f "$extracted" "$filtered"
}

# Nationwide backbone plus the same detailed region boundaries used by PMTiles.
build_graph korea "" compact
regions=(
  "seoul|126.76,37.41,127.20,37.72" "busan|128.75,34.95,129.32,35.39"
  "daegu|128.35,35.55,129.02,36.02" "incheon|126.20,37.30,126.82,37.72"
  "gwangju|126.64,35.00,127.02,35.30" "daejeon|127.18,36.17,127.56,36.50"
  "ulsan|129.00,35.30,129.49,35.73" "sejong|127.12,36.42,127.38,36.73"
  "gyeonggi|126.36,36.88,127.86,38.30" "gangwon|127.05,37.02,129.37,38.62"
  "chungbuk|127.25,36.00,128.65,37.25" "chungnam|125.95,35.95,127.65,37.10"
  "jeonbuk|126.30,35.28,127.88,36.18" "jeonnam|125.85,33.90,127.88,35.50"
  "gyeongbuk|127.80,35.55,130.95,37.58" "gyeongnam|127.55,34.55,129.25,35.95"
  "jeju|126.05,33.05,126.98,33.62"
)
for entry in "${regions[@]}"; do
  build_graph "${entry%%|*}" "${entry#*|}" detail
done
