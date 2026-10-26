import unittest

from cars2gushhelka.resolvers.base import MatchMethod, ResolveQuery
from cars2gushhelka.resolvers.nominatim import (
    NominatimGovMapResponseError,
    _address_text,
    _extract_latlon,
    _extract_parcel,
)


class AddressTextTests(unittest.TestCase):
    def test_street_house(self):
        query = ResolveQuery(
            method=MatchMethod.STREET_HOUSE, semel_yishuv="123",
            settlement_name="תל אביב", street="הרצל", house_number="5",
        )
        self.assertEqual(_address_text(query), "הרצל 5, תל אביב")

    def test_street_only(self):
        query = ResolveQuery(
            method=MatchMethod.STREET_ONLY, semel_yishuv="123",
            settlement_name="תל אביב", street="הרצל",
        )
        self.assertEqual(_address_text(query), "הרצל, תל אביב")

    def test_mikud_specific(self):
        query = ResolveQuery(
            method=MatchMethod.MIKUD_SPECIFIC, semel_yishuv="123",
            settlement_name="תל אביב", mikud="6800001",
        )
        self.assertEqual(_address_text(query), "6800001")

    def test_settlement_centroid(self):
        query = ResolveQuery(
            method=MatchMethod.SETTLEMENT_CENTROID, semel_yishuv="123",
            settlement_name="תל אביב",
        )
        self.assertEqual(_address_text(query), "תל אביב")


class ExtractLatLonTests(unittest.TestCase):
    def test_parses_top_result(self):
        payload = [{"lat": "31.7831614", "lon": "35.2012818"}, {"lat": "0", "lon": "0"}]
        self.assertEqual(_extract_latlon(payload), (35.2012818, 31.7831614))

    def test_empty_list_is_no_match(self):
        self.assertIsNone(_extract_latlon([]))

    def test_non_list_payload_raises(self):
        with self.assertRaises(NominatimGovMapResponseError):
            _extract_latlon({"lat": "1", "lon": "2"})

    def test_missing_lat_lon_raises(self):
        with self.assertRaises(NominatimGovMapResponseError):
            _extract_latlon([{"display_name": "somewhere"}])

    def test_non_numeric_lat_lon_raises(self):
        with self.assertRaises(NominatimGovMapResponseError):
            _extract_latlon([{"lat": "n/a", "lon": "n/a"}])


class ExtractParcelTests(unittest.TestCase):
    def test_parses_feature(self):
        payload = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {"GUSH_NUM": 10074, "GUSH_SUFFI": "0", "PARCEL": 77}},
            ],
        }
        parcel = _extract_parcel(payload)
        self.assertEqual(parcel.gush, "10074")
        self.assertEqual(parcel.helka, "77")
        self.assertEqual(parcel.source, "nominatim_govmap")

    def test_nonzero_suffix_is_appended(self):
        payload = {
            "features": [
                {"properties": {"GUSH_NUM": 10074, "GUSH_SUFFI": "1", "PARCEL": 77}},
            ],
        }
        parcel = _extract_parcel(payload)
        self.assertEqual(parcel.gush, "10074/1")

    def test_no_features_is_no_match(self):
        self.assertIsNone(_extract_parcel({"features": []}))

    def test_none_payload_is_no_match(self):
        self.assertIsNone(_extract_parcel(None))

    def test_missing_features_key_raises(self):
        with self.assertRaises(NominatimGovMapResponseError):
            _extract_parcel({"type": "FeatureCollection"})

    def test_feature_missing_properties_raises(self):
        with self.assertRaises(NominatimGovMapResponseError):
            _extract_parcel({"features": [{"type": "Feature"}]})

    def test_both_gush_and_helka_none_is_no_match(self):
        self.assertIsNone(_extract_parcel({"features": [{"properties": {"LEGAL_AREA": 100}}]}))


if __name__ == "__main__":
    unittest.main()
