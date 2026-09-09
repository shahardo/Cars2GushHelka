import unittest

from cars2gushhelka.cli import build_arg_parser


class SecondaryAddressFlagTests(unittest.TestCase):
    def test_defaults_to_enabled(self):
        args = build_arg_parser().parse_args(["--input", "file.txt"])
        self.assertTrue(args.use_secondary_address)

    def test_no_secondary_address_disables_it(self):
        args = build_arg_parser().parse_args(["--input", "file.txt", "--no-secondary-address"])
        self.assertFalse(args.use_secondary_address)


if __name__ == "__main__":
    unittest.main()
