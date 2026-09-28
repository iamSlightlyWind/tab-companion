# SPDX-License-Identifier: MIT
import importlib.machinery
import importlib.util
from pathlib import Path
import unittest


CORE = Path(__file__).resolve().parents[1] / "ports/fedora-x810/tools/x810-boot-switch-core"
LOADER = importlib.machinery.SourceFileLoader("x810_identity_core", str(CORE))
SPEC = importlib.util.spec_from_loader("x810_identity_core", LOADER)
core = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(core)


class X810DeviceIdentityTests(unittest.TestCase):
    def test_accepts_live_x810_human_model_with_board_compatible(self):
        self.assertTrue(core.is_x810_device(
            "Samsung Galaxy Tab S9+ Wi-Fi",
            "qcom,kalama-mtp\0qcom,kalama\0samsung,gts9pwifi\0samsung,gts9wifi\0qcom,sm8550\0",
        ))

    def test_accepts_legacy_board_codename_model(self):
        self.assertTrue(core.is_x810_device(
            "Samsung GTS9PWIFI PROJECT (board-id,04)", "qcom,sm8550\0"))

    def test_rejects_other_s9_boards_even_with_similar_marketing_name(self):
        self.assertFalse(core.is_x810_device(
            "Samsung Galaxy Tab S9+ Wi-Fi",
            "qcom,kalama\0samsung,gts9uwifi\0qcom,sm8550\0",
        ))

    def test_rejects_unknown_or_missing_device_tree_identity(self):
        self.assertFalse(core.is_x810_device("unknown", "qcom,sm8550\0"))


if __name__ == "__main__":
    unittest.main()
