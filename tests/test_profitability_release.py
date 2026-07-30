from __future__ import annotations

import unittest
import inspect

from app import google_drive
from app.auth import has_permission


class ProfitabilityReleaseTests(unittest.TestCase):
    def test_profitability_google_drive_exports_exist(self) -> None:
        for name in [
            "DriveUserError",
            "ensure_drive_data_loaded",
            "load_drive_cost_snapshots",
            "render_data_source_sidebar",
        ]:
            self.assertTrue(hasattr(google_drive, name), name)

    def test_drive_service_is_not_cached_as_long_lived_transport(self) -> None:
        source = inspect.getsource(google_drive.get_drive_service)
        self.assertNotIn("@st.cache_resource", source)

    def test_profitability_and_validation_permissions(self) -> None:
        self.assertTrue(has_permission("margin", "Admin"))
        self.assertTrue(has_permission("margin", "Finance"))
        self.assertTrue(has_permission("margin", "Executive"))
        self.assertFalse(has_permission("margin", "Sales"))
        self.assertTrue(has_permission("data_validation", "Admin"))
        self.assertTrue(has_permission("data_validation", "Finance"))
        self.assertFalse(has_permission("data_validation", "Executive"))
        self.assertFalse(has_permission("data_validation", "Sales"))
        self.assertTrue(has_permission("finance", "Executive"))
        self.assertFalse(has_permission("finance", "Sales"))


if __name__ == "__main__":
    unittest.main()
