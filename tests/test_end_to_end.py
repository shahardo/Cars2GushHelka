"""Offline, no-network end-to-end tests.

Two kinds of coverage:

1. `FixtureCrosswalkTest` -- a small, hand-built 7-row file + CSV crosswalk
   exercising every tier (A-E) and the secondary-address fallback, with an
   exact expected output table asserted cell-by-cell.

2. `FullSampleTest` -- runs the real pipeline (reader -> matcher -> cache ->
   aggregator -> report) over the actual `data/sample_200.txt` file, using a
   resolver that's given synthetic-but-consistent answers for every address
   candidate the matcher can possibly generate from that file (so every
   tier decision is real, only the gush/helka values are synthetic). This
   is what validates the plan's core invariant: nothing is dropped.
"""

from __future__ import annotations

import csv
import os
import tempfile
import unittest
from pathlib import Path

from cars2gushhelka import report
from cars2gushhelka.cache import AddressCache
from cars2gushhelka.cli import run_aggregate, run_resolve
from cars2gushhelka.matcher import primary_candidate
from cars2gushhelka.reader import EXPECTED_HEADER, iter_rows
from cars2gushhelka.resolvers.base import MatchMethod, ResolvedParcel, ResolveQuery
from cars2gushhelka.resolvers.crosswalk import CrosswalkResolver
from cars2gushhelka.resolvers.fake import FakeResolver

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_PATH = REPO_ROOT / "data" / "sample_200.txt"


def _write_lines(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("|".join(EXPECTED_HEADER) + "\r\n")
        for row in rows:
            fh.write("|".join(row) + "\r\n")


BLANK = {h: "" for h in EXPECTED_HEADER}


def row(**overrides):
    fields = dict(BLANK)
    fields.update(
        rc_mispar_rechev="00000001",
        rc_sug_rechev="110",
        rc_sug_rechev_nm="פרטי רגיל",
        rc_semel_tozar="360",
        rc_kod_degem="99998",
        sug_baal="תז",
    )
    fields.update(overrides)
    return [fields[h] for h in EXPECTED_HEADER]


class FixtureCrosswalkTest(unittest.TestCase):
    """7 synthetic rows, one per tier plus a secondary-address case and an
    unmapped fuel code, matched against a hand-written CSV crosswalk."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.input_path = os.path.join(self.tmpdir.name, "vehicles.txt")
        self.crosswalk_path = os.path.join(self.tmpdir.name, "crosswalk.csv")
        self.cache_path = os.path.join(self.tmpdir.name, "cache.sqlite")

        rows = [
            # 1: Tier A (street_house), gasoline
            row(semel_yishuv="100", shem_yishuv="עיר א", rechov="הרצל",
                mispar_bait="1", mikud="1234500", rc_sug_delek="1", sug_delek_nm="בנזין"),
            # 2: same address as row 1, different fuel -> same parcel, diesel
            row(semel_yishuv="100", shem_yishuv="עיר א", rechov="הרצל",
                mispar_bait="1", mikud="1234500", rc_sug_delek="2", sug_delek_nm="דיזל"),
            # 3: Tier B (mikud_specific) -- street present but crosswalk has
            # no street_house entry for it, only a mikud entry.
            row(semel_yishuv="100", shem_yishuv="עיר א", rechov="לא ידוע",
                mispar_bait="9", mikud="1234511", rc_sug_delek="4", sug_delek_nm="חשמל"),
            # 4: Tier C (street_only) -- no house number, mikud not specific.
            row(semel_yishuv="200", shem_yishuv="עיר ב", rechov="ויצמן",
                mispar_bait="", mikud="2000000", rc_sug_delek="5", sug_delek_nm="היברידי"),
            # 5: Tier D (settlement_centroid) -- rural, street == settlement.
            row(semel_yishuv="300", shem_yishuv="מושב ג", rechov="מושב ג",
                mispar_bait="", mikud="3000000", rc_sug_delek="1", sug_delek_nm="בנזין"),
            # 6: Tier E (failed) -- settlement with no crosswalk entry at all.
            row(semel_yishuv="400", shem_yishuv="עיר לא ידועה", rechov="רחוב לא ידוע",
                mispar_bait="", mikud="", rc_sug_delek="1", sug_delek_nm="בנזין"),
            # 7: primary address unresolvable, secondary address resolves.
            row(semel_yishuv="500", shem_yishuv="עיר לא ידועה 2", rechov="רחוב לא ידוע",
                mispar_bait="", mikud="",
                rechov_nosaf="שדרות הנשיא", mispar_bait_nosaf="4",
                rc_sug_delek="99", sug_delek_nm="דלק מוזר"),  # unmapped fuel code
        ]
        _write_lines(self.input_path, rows)

        crosswalk_rows = [
            {"match_method": "street_house", "semel_yishuv": "100", "street": "הרצל",
             "house_number": "1", "mikud": "", "gush": "6001", "helka": "10", "sub_helka": ""},
            {"match_method": "mikud_specific", "semel_yishuv": "", "street": "",
             "house_number": "", "mikud": "1234511", "gush": "6002", "helka": "20", "sub_helka": ""},
            {"match_method": "street_only", "semel_yishuv": "200", "street": "ויצמן",
             "house_number": "", "mikud": "", "gush": "6003", "helka": "30", "sub_helka": ""},
            {"match_method": "settlement_centroid", "semel_yishuv": "300", "street": "",
             "house_number": "", "mikud": "", "gush": "6004", "helka": "1", "sub_helka": ""},
            {"match_method": "street_house", "semel_yishuv": "500", "street": "שדרות הנשיא",
             "house_number": "4", "mikud": "", "gush": "6005", "helka": "50", "sub_helka": ""},
        ]
        with open(self.crosswalk_path, "w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(
                fh, fieldnames=["match_method", "semel_yishuv", "street", "house_number", "mikud", "gush", "helka", "sub_helka"]
            )
            writer.writeheader()
            writer.writerows(crosswalk_rows)

    def _run(self, *, use_secondary_address):
        resolver = CrosswalkResolver.from_csv(self.crosswalk_path)
        with AddressCache(self.cache_path) as cache:
            run_resolve(self.input_path, resolver, cache, use_secondary_address=use_secondary_address)
            return run_aggregate(self.input_path, cache, use_secondary_address=use_secondary_address)

    def test_all_seven_rows_reconcile(self):
        agg = self._run(use_secondary_address=True)
        self.assertEqual(agg.total_rows, 7)
        self.assertEqual(sum(s.total for s in agg.parcels.values()), 7)

    def test_tier_a_groups_two_fuel_types_on_one_parcel(self):
        agg = self._run(use_secondary_address=True)
        stats = agg.parcels[("6001", "10", "", False)]
        self.assertEqual(stats.engine_counts["gasoline"], 1)
        self.assertEqual(stats.engine_counts["diesel"], 1)
        self.assertEqual(stats.total, 2)
        self.assertEqual(stats.dominant_match_method, "street_house")

    def test_tier_b_mikud_specific_resolved(self):
        agg = self._run(use_secondary_address=True)
        stats = agg.parcels[("6002", "20", "", False)]
        self.assertEqual(stats.engine_counts["electric"], 1)
        self.assertEqual(stats.dominant_match_method, "mikud_specific")

    def test_tier_c_street_only_resolved(self):
        agg = self._run(use_secondary_address=True)
        stats = agg.parcels[("6003", "30", "", False)]
        self.assertEqual(stats.engine_counts["hybrid"], 1)

    def test_tier_d_settlement_centroid_flagged_aggregated(self):
        agg = self._run(use_secondary_address=True)
        stats = agg.parcels[("6004", "1", "", True)]
        self.assertEqual(stats.total, 1)

    def test_tier_e_failed_bucketed_by_settlement(self):
        agg = self._run(use_secondary_address=True)
        stats = agg.parcels[("FAILED", "עיר לא ידועה", "", True)]
        self.assertEqual(stats.total, 1)

    def test_secondary_address_resolves_and_is_flagged(self):
        agg = self._run(use_secondary_address=True)
        stats = agg.parcels[("6005", "50", "", False)]
        self.assertEqual(stats.total, 1)
        self.assertEqual(stats.via_secondary_address, 1)
        self.assertEqual(agg.secondary_address_rows, 1)

    def test_without_secondary_flag_that_row_fails_instead(self):
        agg = self._run(use_secondary_address=False)
        self.assertNotIn(("6005", "50", "", False), agg.parcels)
        self.assertEqual(agg.secondary_address_rows, 0)
        failed = agg.parcels[("FAILED", "עיר לא ידועה 2", "", True)]
        self.assertEqual(failed.total, 1)

    def test_unmapped_fuel_code_counted_as_other_and_tracked(self):
        agg = self._run(use_secondary_address=True)
        stats = agg.parcels[("6005", "50", "", False)]
        self.assertEqual(stats.engine_counts["other"], 1)
        self.assertEqual(agg.unmapped_fuel_codes[("99", "דלק מוזר")], 1)

    def test_report_csv_column_sums_match_reconciliation(self):
        agg = self._run(use_secondary_address=True)
        out_path = os.path.join(self.tmpdir.name, "out.csv")
        categories = report.write_report(agg, out_path)
        with open(out_path, encoding="utf-8-sig", newline="") as fh:
            reader_ = csv.DictReader(fh)
            data_rows = list(reader_)
        self.assertEqual(len(data_rows), len(agg.parcels))
        total_from_csv = sum(int(r["total"]) for r in data_rows)
        self.assertEqual(total_from_csv, 7)
        secondary_from_csv = sum(int(r["via_secondary_address"]) for r in data_rows)
        self.assertEqual(secondary_from_csv, 1)
        for r in data_rows:
            cell_sum = sum(int(r[c]) for c in categories)
            self.assertEqual(cell_sum, int(r["total"]))

    def test_resolve_is_resumable(self):
        # Run resolve once, then again with a resolver that raises on any
        # call -- if the cache weren't hit, this would error out.
        resolver = CrosswalkResolver.from_csv(self.crosswalk_path)
        with AddressCache(self.cache_path) as cache:
            run_resolve(self.input_path, resolver, cache, use_secondary_address=True)

        class ExplodingResolver:
            def resolve(self, query):
                raise AssertionError("resolver should not be called on a fully-cached run")

        with AddressCache(self.cache_path) as cache:
            resolved = run_resolve(self.input_path, ExplodingResolver(), cache, use_secondary_address=True)
        self.assertEqual(resolved, 0)


def _all_candidate_queries(cand):
    """Independently mirrors the tier-A/B/C query shapes a full-coverage
    resolver needs to answer for `resolve_row` to succeed at the expected
    tier -- written from AddressCandidate's public properties, not by
    importing matcher's private query builder."""
    queries = []
    if cand.has_street and cand.has_house_number:
        queries.append(ResolveQuery(
            method=MatchMethod.STREET_HOUSE, semel_yishuv=cand.semel_yishuv,
            settlement_name=cand.settlement_name, street=cand.street, house_number=cand.house_number,
        ))
    if cand.has_specific_mikud:
        queries.append(ResolveQuery(
            method=MatchMethod.MIKUD_SPECIFIC, semel_yishuv=cand.semel_yishuv,
            settlement_name=cand.settlement_name, mikud=cand.mikud,
        ))
    if cand.has_street:
        queries.append(ResolveQuery(
            method=MatchMethod.STREET_ONLY, semel_yishuv=cand.semel_yishuv,
            settlement_name=cand.settlement_name, street=cand.street,
        ))
    return queries


def _expected_tier(cand):
    if cand.has_street and cand.has_house_number:
        return MatchMethod.STREET_HOUSE.value
    if cand.has_specific_mikud:
        return MatchMethod.MIKUD_SPECIFIC.value
    if cand.has_street:
        return MatchMethod.STREET_ONLY.value
    return MatchMethod.SETTLEMENT_CENTROID.value


@unittest.skipUnless(SAMPLE_PATH.exists(), "sample data file not present")
class FullSampleTest(unittest.TestCase):
    """Runs the real pipeline over data/sample_200.txt with a resolver that
    can answer every query the matcher can generate from that file, so
    every row resolves at its natural tier and nothing falls to Tier E."""

    @classmethod
    def setUpClass(cls):
        cls.rows = list(iter_rows(str(SAMPLE_PATH)))
        answers = {}
        next_id = 1
        expected_tiers = {}
        for r in cls.rows:
            cand = primary_candidate(r)
            for q in _all_candidate_queries(cand):
                if q.cache_key not in answers:
                    answers[q.cache_key] = ResolvedParcel(gush=str(1000 + next_id), helka=str(next_id))
                    next_id += 1
            centroid_key = ResolveQuery(
                method=MatchMethod.SETTLEMENT_CENTROID, semel_yishuv=cand.semel_yishuv,
                settlement_name=cand.settlement_name,
            ).cache_key
            if centroid_key not in answers:
                answers[centroid_key] = ResolvedParcel(gush=f"CENTROID-{cand.semel_yishuv}", helka="1")
            expected_tiers[r.line_no] = _expected_tier(cand)

        cls.expected_tiers = expected_tiers
        cls.resolver = FakeResolver(answers)

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.cache_path = os.path.join(self.tmpdir.name, "cache.sqlite")

    def _run(self):
        with AddressCache(self.cache_path) as cache:
            run_resolve(str(SAMPLE_PATH), self.resolver, cache, use_secondary_address=False)
            return run_aggregate(str(SAMPLE_PATH), cache, use_secondary_address=False)

    def test_row_count_matches_file(self):
        self.assertEqual(len(self.rows), 199)

    def test_reconciliation_invariant_holds(self):
        agg = self._run()
        self.assertEqual(agg.total_rows, 199)
        self.assertEqual(sum(s.total for s in agg.parcels.values()), 199)

    def test_nothing_falls_to_failed_tier_with_full_coverage(self):
        agg = self._run()
        for (gush, _helka, _sub, _is_agg), stats in agg.parcels.items():
            self.assertNotEqual(gush, "FAILED", f"unexpected unresolved bucket with total={stats.total}")

    def test_tier_distribution_matches_per_row_expectation(self):
        from collections import Counter

        agg = self._run()
        actual = agg.tier_distribution()
        expected_counts = Counter(self.expected_tiers.values())
        self.assertEqual(dict(actual), dict(expected_counts))

    def test_settlement_centroid_rows_are_flagged_aggregated(self):
        agg = self._run()
        for (gush, helka, sub, is_aggregated), stats in agg.parcels.items():
            if stats.dominant_match_method == "settlement_centroid":
                self.assertTrue(is_aggregated)

    def test_no_vehicle_dropped_across_fuel_categories(self):
        agg = self._run()
        cell_total = sum(sum(s.engine_counts.values()) for s in agg.parcels.values())
        self.assertEqual(cell_total, 199)


if __name__ == "__main__":
    unittest.main()
