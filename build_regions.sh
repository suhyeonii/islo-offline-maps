#!/bin/zsh
set -euo pipefail

repo="suhyeonii/islo-offline-maps"
# The app resolves maps, routing and search assets from one release base URL.
# Replace only PMTiles in the current release, then publish the new manifest
# checksums after every asset upload has completed.
release="${ISLO_MAP_RELEASE:-v0.1.0}"
manifest_release="${ISLO_MAP_MANIFEST_RELEASE:-v0.1.2-individual-poi-points}"
snapshot_version="${ISLO_SNAPSHOT_VERSION:-20260827}"
publish="${ISLO_PUBLISH:-1}"
region_ids=",${ISLO_REGION_IDS:-},"
changed_bbox="${ISLO_CHANGED_BBOX:-}"
# 새 스냅샷은 기존 마지막 정상 원본을 덮어쓰지 않고 명시적으로 지정해
# 생성·검증합니다. manifest 교체 전 실패해도 기존 배포를 재현할 수 있습니다.
source_pbf="${SOURCE_PBF:-build/south-korea-latest.osm.pbf}"
tilemaker_config="/workspace/tilemaker-islo-config.json"
tilemaker_process="/workspace/tilemaker-islo-process.lua"

regions=(
  "seoul|126.76,37.41,127.20,37.72"
  "busan|128.75,34.95,129.32,35.39"
  "daegu|128.35,35.55,129.02,36.02"
  # 서해 5도(백령도 124.6E 포함)까지 인천 행정구역 전체를 보존합니다.
  "incheon|124.30,36.85,126.82,38.20"
  "gwangju|126.64,35.00,127.02,35.30"
  "daejeon|127.18,36.17,127.56,36.50"
  "ulsan|129.00,35.30,129.49,35.73"
  "sejong|127.12,36.42,127.38,36.73"
  "gyeonggi|126.36,36.88,127.86,38.30"
  "gangwon|127.05,37.02,129.60,38.70"
  "chungbuk|127.25,36.00,128.65,37.25"
  "chungnam|125.30,35.80,127.65,37.15"
  "jeonbuk|125.65,35.20,127.88,36.22"
  # 가거도·홍도 등 전남 외곽 도서를 포함합니다.
  "jeonnam|124.85,33.65,127.95,35.55"
  # 울릉도와 독도까지 경북 지도 한 권역에 포함합니다.
  "gyeongbuk|127.80,35.55,132.05,37.65"
  "gyeongnam|127.45,34.35,129.35,35.95"
  # 추자도를 포함하도록 제주 북쪽 범위를 전남 해역까지 확장합니다.
  "jeju|125.95,33.00,127.10,34.15"
)

# 부분 배포는 변경된 OSM 객체의 bbox와 겹치는 모든 권역을 같은 원본으로
# 함께 갱신해야 합니다. 요청 목록에 빠진 연관 권역은 자동으로 추가합니다.
# 예: 서울 서부 변경은 서울과 경기 PMTiles를 한 배포 단위로 묶습니다.
if [[ "$publish" == "1" && "$region_ids" != ",," && -z "$changed_bbox" ]]; then
  echo "Refusing unscoped partial map publication: set ISLO_CHANGED_BBOX=west,south,east,north." >&2
  exit 2
fi
if [[ -n "$changed_bbox" ]]; then
  changed=("${(@s:,:)changed_bbox}")
  if (( ${#changed[@]} != 4 )); then
    echo "Invalid ISLO_CHANGED_BBOX: expected west,south,east,north." >&2
    exit 2
  fi
  for entry in "${regions[@]}"; do
    id="${entry%%|*}"
    bounds="${entry#*|}"
    b=("${(@s:,:)bounds}")
    if awk -v aw="${changed[1]}" -v as="${changed[2]}" \
      -v ae="${changed[3]}" -v an="${changed[4]}" \
      -v bw="${b[1]}" -v bs="${b[2]}" -v be="${b[3]}" -v bn="${b[4]}" \
      'BEGIN { exit !((aw <= be) && (ae >= bw) && (as <= bn) && (an >= bs)) }'; then
      if [[ "$region_ids" != *",${id},"* ]]; then
        region_ids="${region_ids%,},${id},"
        echo "Including overlapping region ${id}"
      fi
    fi
  done
fi
for entry in "${regions[@]}"; do
  id="${entry%%|*}"
  if [[ "$region_ids" != ",," && "$region_ids" != *",${id},"* ]]; then
    continue
  fi
  bbox="${entry#*|}"
  pbf="build/${id}.osm.pbf"
  # Immutable filename: never replace the bytes referenced by the currently
  # published manifest. The manifest switches to this snapshot only after all
  # regions have uploaded and passed checksum verification.
  tiles="build/${id}.map.${snapshot_version}.pmtiles"
  echo "Building ${id}"
  # POIs must remain individual Point features. Always rebuild and replace the
  # PMTiles asset; stale local or release assets may still contain MultiPoint.
  rm -f "$tiles"
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
      docker run --rm -v "$PWD/build:/data" -v "$PWD:/workspace:ro" ghcr.io/systemed/tilemaker:master \
        "/data/${id}-${part_number}.osm.pbf" --output "/data/${id}.mbtiles" \
        --bbox "$quadrant" --config "$tilemaker_config" --process "$tilemaker_process" \
        "${merge[@]}" --quiet
      rm -f "$part_pbf"
    done
    pmtiles convert "$mbtiles" "$tiles"
    rm -f "$mbtiles"
  fi
  if [[ "$publish" == "1" ]]; then
    gh release upload "$release" "$tiles" --repo "$repo" --clobber
  fi
  rm -f "$pbf"
done

# Publish the manifest only after all replacement assets are uploaded, so an
# app never receives a checksum for an asset that is not available yet.
if [[ "$publish" == "1" ]]; then
  if [[ "$region_ids" == ",," ]]; then
    python3 refresh_map_manifest.py --release "$manifest_release" --asset-release "$release" \
      --filename-version "$snapshot_version"
  else
    python3 refresh_partial_map_manifest.py --release "$manifest_release" \
      --asset-release "$release" --filename-version "$snapshot_version" \
      --regions "${region_ids#,}"
  fi
  gh release upload "$release" manifest.json --repo "$repo" --clobber
fi
