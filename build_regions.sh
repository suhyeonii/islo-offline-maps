#!/bin/zsh
set -euo pipefail

repo="suhyeonii/islo-offline-maps"
release="v0.1.0"
source_pbf="build/south-korea-latest.osm.pbf"

regions=(
  "seoul|126.76,37.41,127.20,37.72"
  "busan|128.75,34.95,129.32,35.39"
  "daegu|128.35,35.55,129.02,36.02"
  "incheon|126.20,37.30,126.82,37.72"
  "gwangju|126.64,35.00,127.02,35.30"
  "daejeon|127.18,36.17,127.56,36.50"
  "ulsan|129.00,35.30,129.49,35.73"
  "sejong|127.12,36.42,127.38,36.73"
  "gyeonggi|126.36,36.88,127.86,38.30"
  "gangwon|127.05,37.02,129.37,38.62"
  "chungbuk|127.25,36.00,128.65,37.25"
  "chungnam|125.95,35.95,127.65,37.10"
  "jeonbuk|126.30,35.28,127.88,36.18"
  "jeonnam|125.85,33.90,127.88,35.50"
  "gyeongbuk|127.80,35.55,130.95,37.58"
  "gyeongnam|127.55,34.55,129.25,35.95"
  "jeju|126.05,33.05,126.98,33.62"
)
uploaded="$(gh release view "$release" --repo "$repo" --json assets --jq '.assets[].name')"

for entry in "${regions[@]}"; do
  id="${entry%%|*}"
  bbox="${entry#*|}"
  pbf="build/${id}.osm.pbf"
  tiles="build/${id}.pmtiles"
  if print -r -- "$uploaded" | grep -qx "${id}.pmtiles"; then
    echo "Skipping uploaded ${id}"
    continue
  fi
  echo "Building ${id}"
  if [[ ! -f "$tiles" ]]; then
    parts=("${(@s:,:)bbox}")
    west="$parts[1]"; south="$parts[2]"; east="$parts[3]"; north="$parts[4]"
    mid_lon="$(awk -v a="$west" -v b="$east" 'BEGIN { printf "%.6f", (a+b)/2 }')"
    mid_lat="$(awk -v a="$south" -v b="$north" 'BEGIN { printf "%.6f", (a+b)/2 }')"
    quadrants=(
      "$west,$south,$mid_lon,$mid_lat"
      "$mid_lon,$south,$east,$mid_lat"
      "$west,$mid_lat,$mid_lon,$north"
      "$mid_lon,$mid_lat,$east,$north"
    )
    mbtiles="build/${id}.mbtiles"
    rm -f "$mbtiles" "$pbf"
    part_number=0
    for quadrant in "${quadrants[@]}"; do
      part_number=$((part_number + 1))
      part_pbf="build/${id}-${part_number}.osm.pbf"
      osmium extract --overwrite --bbox "$quadrant" --strategy complete_ways \
        -o "$part_pbf" "$source_pbf"
      merge=()
      [[ -f "$mbtiles" ]] && merge=(--merge)
      docker run --rm -v "$PWD/build:/data" ghcr.io/systemed/tilemaker:master \
        "/data/${id}-${part_number}.osm.pbf" --output "/data/${id}.mbtiles" \
        --bbox "$quadrant" "${merge[@]}" --quiet
      rm -f "$part_pbf"
    done
    pmtiles convert "$mbtiles" "$tiles"
    rm -f "$mbtiles"
  fi
  gh release upload "$release" "$tiles" --repo "$repo" --clobber
  rm -f "$pbf" "$tiles"
done
