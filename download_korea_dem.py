#!/usr/bin/env python3
"""Download SRTM 30m (1-arcsecond) DEM tiles for South Korea from AWS Open Data."""

import gzip
import os
import urllib.error
import urllib.request

DEM_DIR = os.path.join(os.path.dirname(__file__), "dem")

# South Korea covers approximately Lat 33N-39N, Lon 125E-131E
TILES = [
    (lat, lon)
    for lat in range(33, 39)
    for lon in range(125, 131)
]


def download_tile(lat: int, lon: int) -> bool:
    tile_name = f"N{lat:02d}E{lon:03d}"
    hgt_path = os.path.join(DEM_DIR, f"{tile_name}.hgt")
    if os.path.isfile(hgt_path) and os.path.getsize(hgt_path) == 3601 * 3601 * 2:
        print(f"✓ Already downloaded {tile_name}.hgt")
        return True

    url = f"https://elevation-tiles-prod.s3.amazonaws.com/skadi/N{lat:02d}/{tile_name}.hgt.gz"
    req = urllib.request.Request(url, headers={"User-Agent": "IsloElevation/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            compressed = response.read()
            data = gzip.decompress(compressed)
            with open(hgt_path, "wb") as f:
                f.write(data)
            print(f"✓ Downloaded {tile_name}.hgt ({len(data) // 1024 // 1024}MB)")
            return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"- {tile_name} not found (likely ocean)")
        else:
            print(f"✗ HTTP error {e.code} for {tile_name}")
        return False
    except Exception as e:
        print(f"✗ Error downloading {tile_name}: {e}")
        return False


def main() -> None:
    os.makedirs(DEM_DIR, exist_ok=True)
    print(f"Downloading SRTM DEM tiles for South Korea to {DEM_DIR}...")
    success_count = 0
    for lat, lon in TILES:
        if download_tile(lat, lon):
            success_count += 1
    print(f"Finished downloading {success_count} DEM tiles.")


if __name__ == "__main__":
    main()
