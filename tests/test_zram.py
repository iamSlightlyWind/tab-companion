# SPDX-License-Identifier: MIT
import tempfile
import unittest
from pathlib import Path

from tab_companion.zram import (
    DEFAULT_SIZE_MIB,
    SIZES_MIB,
    parse_configured_size,
    render_config,
    validate_size,
)


class ZramSizingTests(unittest.TestCase):
    def test_allowlisted_sizes_and_rejects_arbitrary_input(self):
        for size in SIZES_MIB:
            self.assertEqual(validate_size(str(size)), size)
            self.assertIn(f"zram-size = {size}\n", render_config(size))
        for bad in ("0", "1025", "8193", "4G", "-1", "4096; id"):
            with self.assertRaises(ValueError):
                validate_size(bad)

    def test_later_dropin_overrides_main_file(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / "zram-generator.conf"
            override = Path(temp) / "90-tab-companion.conf"
            base.write_text("[zram0]\nzram-size = 4096\n", encoding="utf-8")
            override.write_text(render_config(6144), encoding="utf-8")
            self.assertEqual(parse_configured_size((base, override)), 6144)

    def test_defaults_to_fedora_port_default_when_unset_or_unrecognized(self):
        self.assertEqual(parse_configured_size(()), DEFAULT_SIZE_MIB)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "zram-generator.conf"
            path.write_text("[zram0]\nzram-size = ram / 2\n", encoding="utf-8")
            self.assertEqual(parse_configured_size((path,)), DEFAULT_SIZE_MIB)


if __name__ == "__main__":
    unittest.main()
