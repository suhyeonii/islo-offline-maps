#!/bin/zsh
set -euo pipefail

# 지도 PMTiles와 동일한 검증 완료 스냅샷을 받아야 검색 결과와 지도 피처의
# OSM 식별자가 어긋나지 않습니다.
source_pbf="${SOURCE_PBF:-build/south-korea-latest.osm.pbf}"
place_version="${PLACE_VERSION:-v2}"
regions=(
  "seoul|126.76,37.41,127.20,37.72" "busan|128.75,34.95,129.32,35.39"
  "daegu|128.35,35.55,129.02,36.02" "incheon|124.30,36.85,126.82,38.20"
  "gwangju|126.64,35.00,127.02,35.30" "daejeon|127.18,36.17,127.56,36.50"
  "ulsan|129.00,35.30,129.49,35.73" "sejong|127.12,36.42,127.38,36.73"
  "gyeonggi|126.36,36.88,127.86,38.30" "gangwon|127.05,37.02,129.60,38.70"
  "chungbuk|127.25,36.00,128.65,37.25" "chungnam|125.30,35.80,127.65,37.15"
  "jeonbuk|125.65,35.20,127.88,36.22" "jeonnam|124.85,33.65,127.95,35.55"
  "gyeongbuk|127.80,35.55,132.05,37.65" "gyeongnam|127.45,34.35,129.35,35.95"
  "jeju|125.95,33.00,127.10,34.15"
)
for entry in "${regions[@]}"; do
  id="${entry%%|*}"
  bbox="${entry#*|}"
  extracted="build/${id}.places.osm.pbf"
  filtered="build/${id}.places.filtered.osm.pbf"
  osmium extract --overwrite --strategy complete_ways --bbox "$bbox" -o "$extracted" "$source_pbf"
  # 일반 장소는 이름이 있어야 검색할 수 있지만, 라이딩 편의시설은 OSM에
  # 이름 없이 등록된 경우가 대부분입니다. 이 시설들을 name 필터에서
  # 탈락시키면 앱의 강조 POI 색인에서도 영구히 사라집니다.
  osmium tags-filter --overwrite "$extracted" \
    nwr/name \
    nwr/amenity=toilets \
    nwr/amenity=drinking_water \
    nwr/amenity=cafe \
    nwr/amenity=bicycle_parking \
    nwr/amenity=bicycle_repair_station \
    nwr/shop=bicycle \
    nwr/shop=convenience \
    nwr/shop=supermarket \
    nwr/shop=grocery \
    nwr/shop=market \
    nwr/shop=greengrocer \
    nwr/shop=general \
    nwr/shop=kiosk \
    nwr/shop=beverages \
    nwr/shop=confectionery \
    nwr/shop=variety_store \
    nwr/shop=coffee \
    nwr/shop=tea \
    nwr/cuisine=coffee_shop \
    nwr/service:bicycle:repair=yes \
    nwr/service:bicycle:retail=yes \
    -o "$filtered"
  output="build/${id}.places.${place_version}.sqlite"
  rm -f "$output"
  osmium cat "$filtered" -f opl | python3 ./build_place_index.py "$output"
  rm -f "$extracted" "$filtered"
done
