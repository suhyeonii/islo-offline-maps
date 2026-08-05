# Islo Offline Maps

Public distribution repository for Islo offline bicycle-navigation data.

Each region release contains three immutable assets:

- `<region>.maplibre.db` — MapLibre offline-pack database
- `<region>.places.json` — searchable OSM place index
- `<region>.routing.bin` — bicycle routing graph

The iOS app reads `manifest.json`, verifies SHA-256 checksums, downloads the
selected region, then imports all three files atomically. A nationwide download
is the ordered download of every available region.

Map data is © OpenStreetMap contributors and distributed under ODbL. Generated
packages must retain the attribution included in `LICENSE-DATA.md`.

The initial `v0.1.0` release publishes the package contract. Region entries stay
unavailable until their generated assets have been uploaded and checksums added.
