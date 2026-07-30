from __future__ import annotations

import unittest

from app import google_drive


class DriveFolderDiscoveryTests(unittest.TestCase):
    def test_folder_name_case_and_whitespace_are_normalized(self) -> None:
        children = [
            {
                "id": "folder-1",
                "name": "  TARGETS   DATA  ",
                "mimeType": google_drive.FOLDER_MIME_TYPE,
                "modifiedTime": "2026-07-01T00:00:00Z",
            }
        ]
        item = google_drive._find_folder_item_from_children(children, ("targets data",))
        self.assertIsNotNone(item)
        self.assertEqual("folder-1", item["id"])

    def test_targets_legacy_alias_can_be_found(self) -> None:
        children = [
            {
                "id": "folder-1",
                "name": "targets",
                "mimeType": google_drive.FOLDER_MIME_TYPE,
                "modifiedTime": "2026-07-01T00:00:00Z",
            }
        ]
        item = google_drive._find_folder_item_from_children(children, google_drive.TARGETS_FOLDER_NAMES)
        self.assertIsNotNone(item)
        self.assertEqual("targets", item["name"])

    def test_google_drive_folder_shortcut_can_be_found(self) -> None:
        children = [
            {
                "id": "shortcut-1",
                "name": "Cost Data",
                "mimeType": google_drive.SHORTCUT_MIME_TYPE,
                "modifiedTime": "2026-07-01T00:00:00Z",
                "shortcutDetails": {
                    "targetId": "real-folder-id",
                    "targetMimeType": google_drive.FOLDER_MIME_TYPE,
                },
            }
        ]
        item = google_drive._find_folder_item_from_children(children, google_drive.COST_FOLDER_NAMES)
        self.assertIsNotNone(item)
        metadata = google_drive._metadata_from_drive_item(item, "Cost Data")
        self.assertEqual("real-folder-id", metadata.file_id)
        self.assertEqual(google_drive.FOLDER_MIME_TYPE, metadata.mime_type)

    def test_non_folder_shortcut_is_ignored(self) -> None:
        children = [
            {
                "id": "shortcut-1",
                "name": "Cost Data",
                "mimeType": google_drive.SHORTCUT_MIME_TYPE,
                "modifiedTime": "2026-07-01T00:00:00Z",
                "shortcutDetails": {
                    "targetId": "file-id",
                    "targetMimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                },
            }
        ]
        item = google_drive._find_folder_item_from_children(children, google_drive.COST_FOLDER_NAMES)
        self.assertIsNone(item)


if __name__ == "__main__":
    unittest.main()
