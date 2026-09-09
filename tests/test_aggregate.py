import unittest

from cars2gushhelka.aggregate import Aggregator
from cars2gushhelka.matcher import RowResolution


def res(gush, helka, method, is_aggregated=False, used_secondary=False, sub_helka=None, confidence=1.0):
    return RowResolution(
        gush=gush, helka=helka, sub_helka=sub_helka, match_method=method,
        confidence=confidence, is_aggregated=is_aggregated,
        used_secondary_address=used_secondary,
        settlement_code="249", settlement_name="כפר סירקין",
    )


class AggregatorTests(unittest.TestCase):
    def test_groups_by_parcel_and_counts_by_category(self):
        agg = Aggregator()
        agg.add_row(res("6355", "12", "street_house"), "gasoline")
        agg.add_row(res("6355", "12", "street_house"), "gasoline")
        agg.add_row(res("6355", "12", "street_house"), "diesel")
        key = ("6355", "12", "", False)
        self.assertEqual(agg.parcels[key].engine_counts["gasoline"], 2)
        self.assertEqual(agg.parcels[key].engine_counts["diesel"], 1)
        self.assertEqual(agg.parcels[key].total, 3)

    def test_aggregated_and_precise_parcels_kept_separate_even_if_same_gush_helka(self):
        agg = Aggregator()
        agg.add_row(res("6300", "1", "street_house", is_aggregated=False), "gasoline")
        agg.add_row(res("6300", "1", "settlement_centroid", is_aggregated=True), "gasoline")
        self.assertEqual(len(agg.parcels), 2)
        self.assertIn(("6300", "1", "", False), agg.parcels)
        self.assertIn(("6300", "1", "", True), agg.parcels)

    def test_total_rows_and_reconciliation(self):
        agg = Aggregator()
        for _ in range(5):
            agg.add_row(res("6355", "12", "street_house"), "gasoline")
        self.assertEqual(agg.total_rows, 5)
        self.assertEqual(sum(s.total for s in agg.parcels.values()), 5)

    def test_secondary_address_counted_per_parcel_and_globally(self):
        agg = Aggregator()
        agg.add_row(res("6355", "12", "street_house", used_secondary=True), "gasoline")
        agg.add_row(res("6355", "12", "street_house", used_secondary=False), "gasoline")
        key = ("6355", "12", "", False)
        self.assertEqual(agg.parcels[key].via_secondary_address, 1)
        self.assertEqual(agg.secondary_address_rows, 1)

    def test_dominant_match_method(self):
        agg = Aggregator()
        agg.add_row(res("6355", "12", "street_house"), "gasoline")
        agg.add_row(res("6355", "12", "street_house"), "gasoline")
        agg.add_row(res("6355", "12", "mikud_specific"), "gasoline")
        key = ("6355", "12", "", False)
        self.assertEqual(agg.parcels[key].dominant_match_method, "street_house")

    def test_all_categories_union(self):
        agg = Aggregator()
        agg.add_row(res("1", "1", "street_house"), "gasoline")
        agg.add_row(res("2", "2", "street_house"), "electric")
        self.assertEqual(agg.all_categories(), {"gasoline", "electric"})

    def test_failed_rows_bucketed_by_settlement(self):
        agg = Aggregator()
        agg.add_row(res("FAILED", "עומר", "failed", is_aggregated=True), "gasoline")
        agg.add_row(res("FAILED", "עומר", "failed", is_aggregated=True), "diesel")
        key = ("FAILED", "עומר", "", True)
        self.assertEqual(agg.parcels[key].total, 2)


if __name__ == "__main__":
    unittest.main()
