# SPDX-License-Identifier: MIT
import tempfile
import unittest
from pathlib import Path

from tab_companion.zram import (
    DEFAULT_SIZE_DECI_GB,
    MAX_SIZE_DECI_GB,
    MIN_SIZE_DECI_GB,
    parse_configured_size,
    render_config,
    validate_size,
)


class ZramSizingTests(unittest.TestCase):
    def test_tenth_gb_range_and_rejects_arbitrary_input(self):
        for size in (MIN_SIZE_DECI_GB, 43, MAX_SIZE_DECI_GB):
            self.assertEqual(validate_size(str(size)), size)
            self.assertIn(f"configured size: {size / 10:.1f} GB", render_config(size))
        self.assertIn("zram-size = 4300000000 / 1048576", render_config(43))
        for bad in ("0", "121", "4.3", "4G", "-1", "43; id"):
            with self.assertRaises(ValueError):
                validate_size(bad)

    def test_later_dropin_overrides_main_file(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / "zram-generator.conf"
            override = Path(temp) / "90-tab-companion.conf"
            base.write_text("[zram0]\nzram-size = 4096\n", encoding="utf-8")
            override.write_text(render_config(65), encoding="utf-8")
            self.assertEqual(parse_configured_size((base, override)), 65)

    def test_legacy_numeric_mib_config_is_shown_as_decimal_gb(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "zram-generator.conf"
            path.write_text("[zram0]\nzram-size = 4096\n", encoding="utf-8")
            self.assertEqual(parse_configured_size((path,)), 43)

    def test_defaults_to_existing_fedora_size_when_unset_or_unrecognized(self):
        self.assertEqual(parse_configured_size(()), DEFAULT_SIZE_DECI_GB)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "zram-generator.conf"
            path.write_text("[zram0]\nzram-size = ram / 2\n", encoding="utf-8")
            self.assertEqual(parse_configured_size((path,)), DEFAULT_SIZE_DECI_GB)


if __name__ == "__main__":
    unittest.main()
