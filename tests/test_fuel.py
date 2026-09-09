import unittest
from collections import Counter

from cars2gushhelka.fuel import OTHER, categorize, ordered_categories


class CategorizeTests(unittest.TestCase):
    def test_gasoline_by_code(self):
        self.assertEqual(categorize("1", "בנזין"), "gasoline")

    def test_diesel_by_code(self):
        self.assertEqual(categorize("2", "דיזל"), "diesel")

    def test_kerosene_by_code(self):
        self.assertEqual(categorize("3", "נפט"), "kerosene")

    def test_electric_by_code(self):
        self.assertEqual(categorize("4", "חשמל"), "electric")

    def test_name_fallback_when_code_unrecognized(self):
        self.assertEqual(categorize("99", "בנזין                 "), "gasoline")

    def test_unknown_code_and_name_falls_to_other(self):
        self.assertEqual(categorize("77", "משהו מוזר"), OTHER)

    def test_unmapped_tracker_records_unknowns(self):
        tracker = Counter()
        categorize("77", "משהו מוזר", unmapped_tracker=tracker)
        self.assertEqual(tracker[("77", "משהו מוזר")], 1)

    def test_unmapped_tracker_not_incremented_for_known_fuel(self):
        tracker = Counter()
        categorize("1", "בנזין", unmapped_tracker=tracker)
        self.assertEqual(sum(tracker.values()), 0)

    def test_overrides_take_precedence_by_code(self):
        self.assertEqual(categorize("1", "בנזין", overrides={"1": "custom"}), "custom")

    def test_overrides_take_precedence_by_name(self):
        self.assertEqual(categorize("99", "בנזין", overrides={"בנזין": "custom"}), "custom")

    def test_padded_whitespace_name_still_matches(self):
        self.assertEqual(categorize("", "בנזין                                             "), "gasoline")


class OrderedCategoriesTests(unittest.TestCase):
    def test_canonical_order_preserved(self):
        seen = {"diesel", "gasoline", "other"}
        self.assertEqual(ordered_categories(seen), ["gasoline", "diesel", "other"])

    def test_extra_categories_from_overrides_appended_sorted(self):
        seen = {"gasoline", "zzz_custom", "aaa_custom"}
        self.assertEqual(ordered_categories(seen), ["gasoline", "aaa_custom", "zzz_custom"])

    def test_other_always_last_among_canonical(self):
        seen = set(["other", "hydrogen", "gasoline"])
        result = ordered_categories(seen)
        self.assertEqual(result[-1], "other")


if __name__ == "__main__":
    unittest.main()
