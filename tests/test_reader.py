import io
import unittest

from cars2gushhelka.reader import EXPECTED_HEADER, iter_rows


HEADER = "|".join(EXPECTED_HEADER)


def make_line(**overrides):
    fields = {
        "rc_mispar_rechev": "00021690",
        "rc_sug_rechev": "140",
        "rc_sug_rechev_nm": "פרטי רכב שדה",
        "rc_semel_tozar": "360",
        "rc_kod_degem": "99998",
        "rc_sug_delek": "1",
        "sug_delek_nm": "בנזין",
        "moed_aliya": "1950",
        "sug_degem": "",
        "sug_baal": "תז",
        "semel_yishuv": "249",
        "shem_yishuv": "כפר סירקין",
        "rechov": "דרך אפק",
        "mispar_bait": "5",
        "mikud": "4993500",
        "semel_yishuv_nosaf": "",
        "rechov_nosaf": "",
        "mispar_bait_nosaf": "",
        "mikud_nosaf": "",
    }
    fields.update(overrides)
    return "|".join(fields[k] for k in EXPECTED_HEADER)


class IterRowsTests(unittest.TestCase):
    def test_parses_a_well_formed_row(self):
        text = HEADER + "\r\n" + make_line() + "\r\n"
        rows = list(iter_rows(io.StringIO(text)))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.mispar_rechev, "00021690")
        self.assertEqual(row.shem_yishuv, "כפר סירקין")
        self.assertEqual(row.rechov, "דרך אפק")
        self.assertEqual(row.mispar_bait, "5")
        self.assertEqual(row.mikud, "4993500")

    def test_strips_padding_from_every_field(self):
        text = HEADER + "\r\n" + make_line(rechov="דרך אפק" + " " * 20) + "\r\n"
        rows = list(iter_rows(io.StringIO(text)))
        self.assertEqual(rows[0].rechov, "דרך אפק")

    def test_handles_empty_trailing_fields(self):
        text = HEADER + "\r\n" + make_line(mikud="", mispar_bait="") + "\r\n"
        rows = list(iter_rows(io.StringIO(text)))
        self.assertEqual(rows[0].mikud, "")
        self.assertEqual(rows[0].mispar_bait, "")

    def test_line_numbers_are_1_indexed_and_skip_header(self):
        text = HEADER + "\r\n" + make_line() + "\r\n" + make_line() + "\r\n"
        rows = list(iter_rows(io.StringIO(text)))
        self.assertEqual([r.line_no for r in rows], [2, 3])

    def test_skips_blank_lines(self):
        text = HEADER + "\r\n" + make_line() + "\r\n\r\n" + make_line() + "\r\n"
        rows = list(iter_rows(io.StringIO(text)))
        self.assertEqual(len(rows), 2)

    def test_malformed_row_skipped_by_default_and_reported(self):
        bad_line = "00021690|140|only|three"
        text = HEADER + "\r\n" + bad_line + "\r\n" + make_line() + "\r\n"
        errors = []
        rows = list(iter_rows(io.StringIO(text), on_error=lambda ln, raw, exc: errors.append((ln, exc))))
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0][0], 2)

    def test_malformed_row_raises_when_not_skipping(self):
        bad_line = "00021690|140|only|three"
        text = HEADER + "\r\n" + bad_line + "\r\n"
        with self.assertRaises(Exception):
            list(iter_rows(io.StringIO(text), skip_malformed=False))

    def test_empty_file_yields_nothing(self):
        self.assertEqual(list(iter_rows(io.StringIO(""))), [])

    def test_unexpected_header_is_reported_but_not_fatal(self):
        bad_header = HEADER.replace("rc_mispar_rechev", "something_else")
        text = bad_header + "\r\n" + make_line() + "\r\n"
        errors = []
        rows = list(iter_rows(io.StringIO(text), on_error=lambda ln, raw, exc: errors.append((ln, exc))))
        self.assertEqual(len(rows), 1)
        self.assertTrue(any(ln == 1 for ln, _ in errors))


if __name__ == "__main__":
    unittest.main()
