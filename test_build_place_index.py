import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("build_place_index.py")


class PlaceIdentityTests(unittest.TestCase):
    def build(self, opl: str) -> list[tuple]:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "places.sqlite"
            subprocess.run(
                [sys.executable, str(SCRIPT), str(database)],
                input=opl,
                text=True,
                check=True,
            )
            connection = sqlite3.connect(database)
            try:
                return connection.execute(
                    "SELECT id, osm_type, osm_id, name, category FROM places ORDER BY id"
                ).fetchall()
            finally:
                connection.close()

    def test_named_retail_landuse_way_keeps_identity(self):
        rows = self.build(
            "n1 v1 dV c0 t0 i0 u T x126.8466 y37.5330\n"
            "n2 v1 dV c0 t0 i0 u T x126.8467 y37.5331\n"
            "w452394017 v1 dV c0 t0 i0 u Tlanduse=retail,name=%ae4c%%ce58%%c0b0%%c2dc%%c7a5% Nn1,n2,n1\n"
        )
        self.assertEqual(
            rows,
            [("w452394017", "way", 452394017, "까치산시장", "retail")],
        )

    def test_named_relation_keeps_relation_identity(self):
        rows = self.build(
            "n1 v1 dV c0 t0 i0 u T x127.0 y37.0\n"
            "n2 v1 dV c0 t0 i0 u T x127.1 y37.1\n"
            "w7 v1 dV c0 t0 i0 u T Nn1,n2,n1\n"
            "r9 v1 dV c0 t0 i0 u Tlanduse=retail,name=Market Mw7@outer\n"
        )
        self.assertEqual(rows, [("r9", "relation", 9, "Market", "retail")])

    def test_bicycle_repair_and_sales_tags_use_bicycle_category(self):
        rows = self.build(
            "n11 v1 dV c0 t0 i0 u Tamenity=bicycle_repair_station x127.0 y37.0\n"
            "n12 v1 dV c0 t0 i0 u Tname=BikeService,shop=outdoor,service:bicycle:repair=yes x127.1 y37.1\n"
            "n13 v1 dV c0 t0 i0 u Tname=BikeSales,shop=sports,service:bicycle:retail=yes x127.2 y37.2\n"
        )
        self.assertEqual(
            rows,
            [
                ("n11", "node", 11, "자전거 수리·판매점", "bicycle"),
                ("n12", "node", 12, "BikeService", "bicycle"),
                ("n13", "node", 13, "BikeSales", "bicycle"),
            ],
        )

    def test_food_and_drink_shops_use_grocery_category(self):
        rows = self.build(
            "n21 v1 dV c0 t0 i0 u Tshop=beverages x127.0 y37.0\n"
            "n22 v1 dV c0 t0 i0 u Tname=SnackShop,shop=confectionery x127.1 y37.1\n"
            "n23 v1 dV c0 t0 i0 u Tname=MiniMart,shop=variety_store x127.2 y37.2\n"
            "n24 v1 dV c0 t0 i0 u Tname=VegShop,shop=greengrocer x127.3 y37.3\n"
        )
        self.assertEqual(
            rows,
            [
                ("n21", "node", 21, "식료품점", "grocery"),
                ("n22", "node", 22, "SnackShop", "grocery"),
                ("n23", "node", 23, "MiniMart", "grocery"),
                ("n24", "node", 24, "VegShop", "greengrocer"),
            ],
        )

    def test_coffee_shop_tags_and_names_use_cafe_category(self):
        rows = self.build(
            "n31 v1 dV c0 t0 i0 u Tamenity=cafe x127.0 y37.0\n"
            "n32 v1 dV c0 t0 i0 u Tname=메가MGC커피,amenity=restaurant x127.1 y37.1\n"
            "n33 v1 dV c0 t0 i0 u Tname=보드카페,amenity=restaurant x127.2 y37.2\n"
            "n34 v1 dV c0 t0 i0 u Tname=CoffeeShop,shop=coffee x127.3 y37.3\n"
        )
        self.assertEqual(
            rows,
            [
                ("n31", "node", 31, "카페", "cafe"),
                ("n32", "node", 32, "메가MGC커피", "cafe"),
                ("n33", "node", 33, "보드카페", "restaurant"),
                ("n34", "node", 34, "CoffeeShop", "cafe"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
