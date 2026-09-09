import unittest

from cars2gushhelka.normalize import (
    address_key,
    clean_text,
    is_mikud_specific,
    is_settlement_level,
    normalize_street,
    split_house_number,
)


class CleanTextTests(unittest.TestCase):
    def test_strips_padding_and_cr(self):
        self.assertEqual(clean_text("פרטי רגיל                    \r"), "פרטי רגיל")

    def test_collapses_internal_whitespace(self):
        self.assertEqual(clean_text("תל  אביב   - יפו"), "תל אביב - יפו")

    def test_empty_and_none(self):
        self.assertEqual(clean_text(""), "")
        self.assertEqual(clean_text(None), "")

    def test_fixes_flipped_parens_shrigim(self):
        # from the sample: "שריגים )לי-און(" should read "שריגים (לי-און)"
        self.assertEqual(clean_text("שריגים )לי-און("), "שריגים (לי-און)")

    def test_fixes_flipped_parens_gush_halav(self):
        self.assertEqual(clean_text("ג'ש )גוש חלב("), "ג'ש (גוש חלב)")

    def test_unifies_gershayim_variants(self):
        self.assertEqual(clean_text("הפלמ״ח"), 'הפלמ"ח')  # ״ -> "
        self.assertEqual(clean_text("הפלמ”ח"), 'הפלמ"ח')  # ” -> "

    def test_unifies_geresh_variants(self):
        self.assertEqual(clean_text("אז׳ר"), "אז'ר")  # ׳ -> '

    def test_leaves_plain_ascii_quotes_alone(self):
        self.assertEqual(clean_text('הרא"ה'), 'הרא"ה')


class NormalizeStreetTests(unittest.TestCase):
    def test_expands_shd_apostrophe(self):
        self.assertEqual(normalize_street("שד' המעפילים"), "שדרות המעפילים")

    def test_expands_sh_no_apostrophe(self):
        self.assertEqual(normalize_street("שד ניצה"), "שדרות ניצה")

    def test_expands_rechov_abbrev(self):
        self.assertEqual(normalize_street("רח שלום"), "רחוב שלום")

    def test_does_not_corrupt_numeric_street_name(self):
        # "רח 8805" is a real Nazareth street name in the sample -- the
        # digits after "רח" must not be treated as an abbreviation target.
        self.assertEqual(normalize_street("רח 8805"), "רח 8805")
        self.assertEqual(normalize_street("רח 5045"), "רח 5045")

    def test_expands_shchuna_abbrev(self):
        self.assertEqual(normalize_street("שכ יחד"), "שכונת יחד")

    def test_expands_smatah_abbrev(self):
        self.assertEqual(normalize_street("סמ ההדרים"), "סמטת ההדרים")

    def test_plain_street_unchanged(self):
        self.assertEqual(normalize_street("דרך אפק"), "דרך אפק")


class HouseNumberTests(unittest.TestCase):
    def test_plain_number(self):
        self.assertEqual(split_house_number("22"), (22, ""))

    def test_number_with_letter_suffix(self):
        self.assertEqual(split_house_number("12א"), (12, "א"))

    def test_empty(self):
        self.assertEqual(split_house_number(""), (None, ""))
        self.assertEqual(split_house_number("   "), (None, ""))

    def test_unparseable(self):
        self.assertEqual(split_house_number("abc"), (None, ""))


class MikudSpecificityTests(unittest.TestCase):
    def test_specific_mikud(self):
        # 5651665, from the sample (Savyon, האורנים 11) -- building-level
        self.assertTrue(is_mikud_specific("5651665"))

    def test_settlement_wide_mikud_ending_00(self):
        # 8496500 (Omer) spans 3 different streets in the sample -- not
        # trustworthy as a single-building match.
        self.assertFalse(is_mikud_specific("8496500"))

    def test_empty_mikud(self):
        self.assertFalse(is_mikud_specific(""))

    def test_malformed_short_mikud(self):
        # e.g. the malformed secondary mikud "48810" seen in the sample
        self.assertFalse(is_mikud_specific("48810"))

    def test_non_digit(self):
        self.assertFalse(is_mikud_specific("abcdefg"))


class SettlementLevelTests(unittest.TestCase):
    def test_empty_street_is_settlement_level(self):
        self.assertTrue(is_settlement_level("", "דור"))

    def test_street_equals_settlement(self):
        # from the sample: line for "דור" has rechov == shem_yishuv == "דור"
        self.assertTrue(is_settlement_level("דור", "דור"))

    def test_real_street_is_not_settlement_level(self):
        self.assertFalse(is_settlement_level("דרך אפק", "כפר סירקין"))


class AddressKeyTests(unittest.TestCase):
    def test_stable_across_padding_differences(self):
        k1 = address_key("249", "דרך אפק                ", "5")
        k2 = address_key("249", "דרך אפק", "5   ")
        self.assertEqual(k1, k2)

    def test_differs_by_house_number(self):
        k1 = address_key("249", "דרך אפק", "5")
        k2 = address_key("249", "דרך אפק", "6")
        self.assertNotEqual(k1, k2)


if __name__ == "__main__":
    unittest.main()
