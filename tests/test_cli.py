import csv
import os
import tempfile
import unittest

from cars2gushhelka.cache import AddressCache
from cars2gushhelka.cli import build_arg_parser, main
from cars2gushhelka.matcher import RowResolution


class SecondaryAddressFlagTests(unittest.TestCase):
    def test_defaults_to_enabled(self):
        args = build_arg_parser().parse_args(["--input", "file.txt"])
        self.assertTrue(args.use_secondary_address)

    def test_no_secondary_address_disables_it(self):
        args = build_arg_parser().parse_args(["--input", "file.txt", "--no-secondary-address"])
        self.assertFalse(args.use_secondary_address)


class ExportCacheTests(unittest.TestCase):
    def _resolution(self, gush, helka, method="street_house", secondary=False):
        return RowResolution(
            gush=gush, helka=helka, sub_helka=None, match_method=method,
            confidence=0.8, is_aggregated=False, used_secondary_address=secondary,
            settlement_code="249", settlement_name="כפר סירקין",
        )

    def test_export_cache_standalone(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = os.path.join(tmp, "cache.sqlite")
            out_path = os.path.join(tmp, "mappings.csv")
            with AddressCache(cache_path) as cache:
                cache.set_row("sec|249|דרך אפק|5|4993500", self._resolution("4049", "104"))
                cache.set_row(
                    "sec|249|דרך אפק|5|4993500|249|הגפן|2|4993501",
                    self._resolution("4050", "7", secondary=True),
                )
                cache.set_row("sec|738|דור||3082000", self._resolution("FAILED", "738", "failed"))

            args = ["--export-cache", out_path, "--cache", cache_path]
            self.assertEqual(main(args), 0)

            with open(out_path, encoding="utf-8-sig", newline="") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(len(rows), 2)  # the FAILED row is skipped
            plain, with_secondary = rows
            self.assertEqual((plain["street"], plain["house_number"], plain["mikud"]), ("דרך אפק", "5", "4993500"))
            self.assertEqual((plain["gush"], plain["helka"]), ("4049", "104"))
            self.assertEqual(plain["secondary_street"], "")
            self.assertEqual(plain["used_secondary_address"], "false")
            self.assertEqual(with_secondary["secondary_street"], "הגפן")
            self.assertEqual(with_secondary["used_secondary_address"], "true")


if __name__ == "__main__":
    unittest.main()
