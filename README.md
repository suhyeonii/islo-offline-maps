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

The initial `v0.1.0` release publishes the package contract. Region entries stay
unavailable until their generated assets have been uploaded and checksums added.
