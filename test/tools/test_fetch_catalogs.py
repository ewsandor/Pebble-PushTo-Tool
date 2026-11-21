import base64
import csv
import math
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import build_catalogs, fetch_catalogs
from tools.fetch_catalogs import group_close_stars, parse_votable, polygon_center


class FetchCatalogTests(unittest.TestCase):
    def test_binary_votable_handles_strings_numbers_and_nulls(self):
        data = (struct.pack(">i", 4) + b"star" +
                struct.pack(">dfh", 12.5, float("nan"), 42))
        document = f"""<VOTABLE><RESOURCE><INFO name="QUERY_STATUS" value="OK"/>
        <TABLE><FIELD name="name" datatype="char" arraysize="*"/>
        <FIELD name="ra" datatype="double"/><FIELD name="mag" datatype="float"/>
        <FIELD name="hr" datatype="short"/><DATA><BINARY>
        <STREAM encoding="base64">{base64.b64encode(data).decode()}</STREAM>
        </BINARY></DATA></TABLE></RESOURCE></VOTABLE>"""
        self.assertEqual(parse_votable(document.encode()),
                         [{"name": "star", "ra": 12.5, "mag": None, "hr": 42}])

    def test_merges_close_stars_across_ra_wrap_using_flux(self):
        stars = [
            {"ra": 359.999, "dec": 0.0, "vmag": 1.0, "hr": 1,
             "pmra": 0.1, "pmdec": 0.0},
            {"ra": 0.001, "dec": 0.0, "vmag": 1.0, "hr": 2,
             "pmra": 0.3, "pmdec": 0.0},
            {"ra": 1.0, "dec": 0.0, "vmag": 2.0, "hr": 3,
             "pmra": None, "pmdec": None},
        ]
        merged = group_close_stars(stars)
        self.assertEqual(len(merged), 2)
        self.assertAlmostEqual(merged[0]["vmag"], 1 - 2.5 * math.log10(2))
        self.assertAlmostEqual(merged[0]["ra"], 359.999)
        self.assertAlmostEqual(merged[0]["pmra"], 0.1)

    def test_spherical_centroid_crosses_ra_zero(self):
        vertices = [(23.8, -10), (0.2, -10), (0.2, 10), (23.8, 10)]
        for boundary in (vertices, list(reversed(vertices))):
            ra, dec = polygon_center(boundary)
            self.assertLess(min(ra, 360 - ra), 1e-8)
            self.assertAlmostEqual(dec, 0.0, places=8)

    def test_generated_csv_is_accepted_by_pack_builder(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(fetch_catalogs, "CATALOG_DIR", Path(directory)):
                row = fetch_catalogs.fixed_row("M1", "Crab", 83.6, 22,
                                               object_type="nebula")
                fetch_catalogs.write_catalog("test.csv", "Test", "Test",
                                             "Test source", "CC0", [row])
            provider, data = build_catalogs._compile_catalog(Path(directory) / "test.csv")
            self.assertEqual(provider, 0)
            self.assertGreater(len(data), 20)

    def test_rasc_catalog_has_110_unique_objects(self):
        catalog = fetch_catalogs.CATALOG_DIR / "rasc_finest_ngc.csv"
        with catalog.open(encoding="utf-8-sig", newline="") as source:
            rows = list(csv.DictReader(line for line in source if not line.startswith("#")))
        identifiers = [row["id"] for row in rows]
        self.assertEqual(len(identifiers), 110)
        self.assertEqual(len(set(identifiers)), 110)
        self.assertTrue(all(identifier.startswith("NGC ") for identifier in identifiers))


if __name__ == "__main__":
    unittest.main()
