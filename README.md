# Islo Offline Maps

Public distribution repository for Islo offline bicycle-navigation data.

Each region release contains three immutable assets:

- `<region>.maplibre.db` — MapLibre offline-pack database
- `<region>.places.json` — searchable OSM place index
- `<region>.routing.bin` — bicycle routing graph

The iOS app reads `manifest.json`, verifies SHA-256 checksums, downloads the
selected region, then imports all three files atomically. A nationwide download
is the ordered download of every available region.

## Compressed release assets

Routing and search SQLite databases can be distributed as LZFSE streams while
remaining ordinary SQLite files after installation. This reduces network
transfer without changing route or search quality and keeps the manifest
backward compatible with older app versions.

```sh
python3 prepare_compressed_release.py
```

The command writes compressed assets and a staged manifest to
`build/compressed-release`. Upload every `.lzfse` asset first, then publish the
staged `manifest.json` last. New clients use `downloadFile`, `downloadSize`,
`downloadSHA256`, and `compression`; older clients continue to use the original
`file`, `size`, and `sha256` fields. Keep the uncompressed release assets until
older app versions are no longer supported.

Map data is © OpenStreetMap contributors and distributed under ODbL. Generated
packages must retain the attribution included in `LICENSE-DATA.md`.

## Routing storage

Nationwide routing is distributed only as the memory-mapped CCH package.
Regional SQLite graphs are optional detailed graphs for short-distance routing;
they contain nodes, edges, amenities, official-route metadata and the indexes
needed for bidirectional search. They do not contain portal or hierarchy
shortcut tables. `build_cch_input.sh` creates the temporary full graph used by
the CCH extraction pipeline, while `build_routing_graphs.sh` never creates a
second nationwide SQLite graph.

## Seoul official bridge-access supplements

`seoul_official_bridge_access.json` is a versioned inventory extracted from the
Seoul Metropolitan Government Han River facility table. Refresh it from a saved
official HTML response, then conflate it into an OSM-derived routing database:

```sh
python3 refresh_seoul_bridge_access_inventory.py official.html \
  seoul_official_bridge_access.json --checked-at YYYY-MM-DD
python3 apply_official_bridge_access.py \
  build/seoul.routing.v19.sqlite build/seoul.routing.v20.sqlite \
  seoul_official_bridge_access.json
```

The conflation step labels only an existing, uniquely matched OSM interruption
node. It records unresolved and conflicting official facilities in separate
SQLite tables and never invents coordinates or silently merges two facilities.
When a later OSM snapshot contains a uniquely matching facility, rebuilding and
rerunning conflation promotes it to `osm_confirmed` without creating a duplicate.

The initial `v0.1.0` release publishes the package contract. Region entries stay
unavailable until their generated assets have been uploaded and checksums added.
