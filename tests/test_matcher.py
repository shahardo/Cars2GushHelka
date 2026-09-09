import unittest

from cars2gushhelka.cache import AddressCache
from cars2gushhelka.matcher import (
    resolve_row,
    row_identity_key,
    secondary_candidate,
)
from cars2gushhelka.reader import VehicleRow
from cars2gushhelka.resolvers.base import MatchMethod, ResolvedParcel, ResolveQuery
from cars2gushhelka.resolvers.fake import FakeResolver


def make_row(
    *,
    semel_yishuv="249",
    shem_yishuv="כפר סירקין",
    rechov="דרך אפק",
    mispar_bait="5",
    mikud="4993511",  # deliberately NOT ending in "00" -- see is_mikud_specific
    semel_yishuv_nosaf="",
    rechov_nosaf="",
    mispar_bait_nosaf="",
    mikud_nosaf="",
    sug_delek="1",
    sug_delek_nm="בנזין",
):
    return VehicleRow(
        line_no=2,
        mispar_rechev="00021690",
        sug_rechev="140",
        sug_rechev_nm="פרטי רכב שדה",
        semel_tozar="360",
        kod_degem="99998",
        sug_delek=sug_delek,
        sug_delek_nm=sug_delek_nm,
        moed_aliya="1950",
        sug_degem="",
        sug_baal="תז",
        semel_yishuv=semel_yishuv,
        shem_yishuv=shem_yishuv,
        rechov=rechov,
        mispar_bait=mispar_bait,
        mikud=mikud,
        semel_yishuv_nosaf=semel_yishuv_nosaf,
        rechov_nosaf=rechov_nosaf,
        mispar_bait_nosaf=mispar_bait_nosaf,
        mikud_nosaf=mikud_nosaf,
    )


def street_house_key(semel_yishuv, street, house):
    return ResolveQuery(
        method=MatchMethod.STREET_HOUSE, semel_yishuv=semel_yishuv,
        settlement_name="", street=street, house_number=house,
    ).cache_key


def mikud_key(mikud):
    return ResolveQuery(
        method=MatchMethod.MIKUD_SPECIFIC, semel_yishuv="", settlement_name="", mikud=mikud,
    ).cache_key


def street_only_key(semel_yishuv, street):
    return ResolveQuery(
        method=MatchMethod.STREET_ONLY, semel_yishuv=semel_yishuv, settlement_name="", street=street,
    ).cache_key


def settlement_key(semel_yishuv):
    return ResolveQuery(
        method=MatchMethod.SETTLEMENT_CENTROID, semel_yishuv=semel_yishuv, settlement_name="",
    ).cache_key


PARCEL_A = ResolvedParcel(gush="6355", helka="12")
PARCEL_B = ResolvedParcel(gush="6355", helka="13")
PARCEL_C = ResolvedParcel(gush="6355", helka="14")
PARCEL_D = ResolvedParcel(gush="6300", helka="1")


class TierOrderTests(unittest.TestCase):
    def test_tier_a_street_house_wins_when_available(self):
        row = make_row()
        resolver = FakeResolver({
            street_house_key("249", "דרך אפק", "5"): PARCEL_A,
            mikud_key("4993511"): PARCEL_B,
        })
        res = resolve_row(row, resolver)
        self.assertEqual(res.match_method, "street_house")
        self.assertEqual((res.gush, res.helka), ("6355", "12"))
        self.assertFalse(res.is_aggregated)

    def test_tier_b_mikud_tried_before_tier_c_street_only(self):
        # Street+house lookup fails (e.g. resolver doesn't know that street),
        # but the mikud is building-specific: mikud must win over a bare
        # street-only lookup, per the (swapped) B/C tier order.
        row = make_row()
        resolver = FakeResolver({
            mikud_key("4993511"): PARCEL_B,
            street_only_key("249", "דרך אפק"): PARCEL_C,
        })
        res = resolve_row(row, resolver)
        self.assertEqual(res.match_method, "mikud_specific")
        self.assertEqual((res.gush, res.helka), ("6355", "13"))

    def test_tier_c_used_when_mikud_not_specific(self):
        # mikud ends in "00" -> not building-specific -> mikud tier isn't
        # even attempted; falls straight to street-only.
        row = make_row(mikud="4990000")
        resolver = FakeResolver({
            street_only_key("249", "דרך אפק"): PARCEL_C,
        })
        res = resolve_row(row, resolver)
        self.assertEqual(res.match_method, "street_only")

    def test_tier_c_used_when_no_house_number_and_mikud_fails(self):
        row = make_row(mispar_bait="", mikud="4990000")
        resolver = FakeResolver({
            street_only_key("249", "דרך אפק"): PARCEL_C,
        })
        res = resolve_row(row, resolver)
        self.assertEqual(res.match_method, "street_only")

    def test_falls_to_settlement_centroid_when_all_else_fails(self):
        row = make_row()
        resolver = FakeResolver({settlement_key("249"): PARCEL_D})
        res = resolve_row(row, resolver)
        self.assertEqual(res.match_method, "settlement_centroid")
        self.assertTrue(res.is_aggregated)
        self.assertEqual((res.gush, res.helka), ("6300", "1"))

    def test_rural_address_street_equals_settlement_skips_to_centroid(self):
        # e.g. the sample's "דור" row: street == settlement name, no house #
        row = make_row(shem_yishuv="דור", rechov="דור", mispar_bait="", mikud="3082000")
        resolver = FakeResolver({settlement_key("249"): PARCEL_D})
        res = resolve_row(row, resolver)
        self.assertEqual(res.match_method, "settlement_centroid")
        self.assertTrue(res.is_aggregated)

    def test_totally_unresolved_row_is_tier_failed(self):
        row = make_row(shem_yishuv="עומר")
        resolver = FakeResolver({})
        res = resolve_row(row, resolver)
        self.assertEqual(res.match_method, "failed")
        self.assertEqual(res.gush, "FAILED")
        self.assertEqual(res.helka, "עומר")
        self.assertTrue(res.is_aggregated)


class SecondaryAddressTests(unittest.TestCase):
    def test_secondary_used_only_after_primary_fails(self):
        row = make_row(
            rechov_nosaf="שדרות ההדרים", mispar_bait_nosaf="7", mikud_nosaf="",
        )
        resolver = FakeResolver({
            street_house_key("249", "שדרות ההדרים", "7"): PARCEL_A,
        })
        res = resolve_row(row, resolver, use_secondary_address=True)
        self.assertEqual(res.match_method, "street_house")
        self.assertTrue(res.used_secondary_address)

    def test_secondary_not_tried_when_flag_off(self):
        row = make_row(
            rechov_nosaf="שדרות ההדרים", mispar_bait_nosaf="7", mikud_nosaf="",
        )
        resolver = FakeResolver({
            street_house_key("249", "שדרות ההדרים", "7"): PARCEL_A,
        })
        res = resolve_row(row, resolver, use_secondary_address=False)
        self.assertEqual(res.match_method, "failed")
        self.assertFalse(res.used_secondary_address)

    def test_secondary_ignored_when_primary_already_resolves(self):
        row = make_row(rechov_nosaf="שדרות ההדרים", mispar_bait_nosaf="7")
        resolver = FakeResolver({
            street_house_key("249", "דרך אפק", "5"): PARCEL_A,
            street_house_key("249", "שדרות ההדרים", "7"): PARCEL_B,
        })
        res = resolve_row(row, resolver, use_secondary_address=True)
        self.assertEqual((res.gush, res.helka), ("6355", "12"))
        self.assertFalse(res.used_secondary_address)

    def test_no_secondary_data_falls_through_to_centroid(self):
        row = make_row()  # no *_nosaf fields at all
        resolver = FakeResolver({settlement_key("249"): PARCEL_D})
        res = resolve_row(row, resolver, use_secondary_address=True)
        self.assertEqual(res.match_method, "settlement_centroid")
        self.assertFalse(res.used_secondary_address)

    def test_secondary_candidate_reuses_primary_settlement_code_when_blank(self):
        row = make_row(rechov_nosaf="שדרות ההדרים")
        cand = secondary_candidate(row)
        self.assertEqual(cand.semel_yishuv, "249")
        self.assertEqual(cand.settlement_name, "כפר סירקין")

    def test_secondary_candidate_blank_name_for_different_settlement_code(self):
        row = make_row(semel_yishuv_nosaf="6400", rechov_nosaf="גורדון")
        cand = secondary_candidate(row)
        self.assertEqual(cand.semel_yishuv, "6400")
        self.assertEqual(cand.settlement_name, "")

    def test_secondary_address_used_by_default_without_passing_the_flag(self):
        # use_secondary_address defaults to True -- callers must opt OUT.
        row = make_row(rechov_nosaf="שדרות ההדרים", mispar_bait_nosaf="7")
        resolver = FakeResolver({
            street_house_key("249", "שדרות ההדרים", "7"): PARCEL_A,
        })
        res = resolve_row(row, resolver)  # no use_secondary_address kwarg
        self.assertTrue(res.used_secondary_address)

    def test_secondary_address_disabled_with_explicit_false(self):
        row = make_row(rechov_nosaf="שדרות ההדרים", mispar_bait_nosaf="7")
        resolver = FakeResolver({
            street_house_key("249", "שדרות ההדרים", "7"): PARCEL_A,
        })
        res = resolve_row(row, resolver, use_secondary_address=False)
        self.assertEqual(res.match_method, "failed")
        self.assertFalse(res.used_secondary_address)


class CachingTests(unittest.TestCase):
    def test_repeat_query_hits_cache_not_resolver(self):
        with AddressCache(":memory:") as cache:
            resolver = FakeResolver({street_house_key("249", "דרך אפק", "5"): PARCEL_A})
            row1 = make_row()
            row2 = make_row()  # identical address, different vehicle
            resolve_row(row1, resolver, cache=cache)
            resolve_row(row2, resolver, cache=cache)
            self.assertEqual(len(resolver.calls), 1)

    def test_cached_failure_also_avoids_second_resolver_call(self):
        with AddressCache(":memory:") as cache:
            resolver = FakeResolver({})
            row = make_row()
            resolve_row(row, resolver, cache=cache)
            calls_after_first = len(resolver.calls)
            resolve_row(row, resolver, cache=cache)
            self.assertEqual(len(resolver.calls), calls_after_first)


class RowIdentityKeyTests(unittest.TestCase):
    def test_identical_rows_share_key(self):
        self.assertEqual(row_identity_key(make_row(), False), row_identity_key(make_row(), False))

    def test_different_address_differs(self):
        k1 = row_identity_key(make_row(), False)
        k2 = row_identity_key(make_row(mispar_bait="6"), False)
        self.assertNotEqual(k1, k2)

    def test_use_secondary_flag_changes_key(self):
        k1 = row_identity_key(make_row(), False)
        k2 = row_identity_key(make_row(), True)
        self.assertNotEqual(k1, k2)


if __name__ == "__main__":
    unittest.main()
