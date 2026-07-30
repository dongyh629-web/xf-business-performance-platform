from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import auth


class AuthCacheResilienceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.oauth_path = self.root / "oauth_state.json"
        self.sessions_path = self.root / "auth_sessions.json"
        self.path_patches = [
            patch.object(auth, "OAUTH_STATE_CACHE_PATH", self.oauth_path),
            patch.object(auth, "APP_SESSION_STORE_PATH", self.sessions_path),
        ]
        for path_patch in self.path_patches:
            path_patch.start()

    def tearDown(self) -> None:
        for path_patch in reversed(self.path_patches):
            path_patch.stop()
        self.temp_dir.cleanup()

    def test_missing_cache_files_return_empty_dicts(self) -> None:
        self.assertEqual({}, auth._read_oauth_state_cache())
        self.assertEqual({}, auth._read_app_sessions())

    def test_valid_oauth_cache_json_is_read(self) -> None:
        self.oauth_path.write_text(json.dumps({"state": {"redirect_uri": "https://example.com"}}), encoding="utf-8")
        self.assertEqual({"state": {"redirect_uri": "https://example.com"}}, auth._read_oauth_state_cache())

    def test_valid_app_session_json_is_read(self) -> None:
        self.sessions_path.write_text(json.dumps({"hash": {"user_email": "user@example.com"}}), encoding="utf-8")
        self.assertEqual({"hash": {"user_email": "user@example.com"}}, auth._read_app_sessions())

    def test_empty_oauth_cache_is_removed_and_returns_empty(self) -> None:
        self.oauth_path.write_text("", encoding="utf-8")
        self.assertEqual({}, auth._read_oauth_state_cache())
        self.assertFalse(self.oauth_path.exists())

    def test_truncated_oauth_cache_is_removed_and_returns_empty(self) -> None:
        self.oauth_path.write_text('{"state": {"redirect_uri": "https://example.com"', encoding="utf-8")
        self.assertEqual({}, auth._read_oauth_state_cache())
        self.assertFalse(self.oauth_path.exists())

    def test_concatenated_oauth_cache_is_removed_and_returns_empty(self) -> None:
        self.oauth_path.write_text('{"a": {}}{"b": {}}', encoding="utf-8")
        self.assertEqual({}, auth._read_oauth_state_cache())
        self.assertFalse(self.oauth_path.exists())

    def test_non_utf8_oauth_cache_is_removed_and_returns_empty(self) -> None:
        self.oauth_path.write_bytes(b"\xff\xfe\x00")
        self.assertEqual({}, auth._read_oauth_state_cache())
        self.assertFalse(self.oauth_path.exists())

    def test_unreadable_oauth_cache_returns_empty(self) -> None:
        self.oauth_path.write_text("{}", encoding="utf-8")
        with patch.object(Path, "read_text", side_effect=OSError("unreadable")):
            self.assertEqual({}, auth._read_oauth_state_cache())

    def test_corrupted_app_session_cache_is_removed_and_returns_empty(self) -> None:
        self.sessions_path.write_text('{"a": {}}{"b": {}}', encoding="utf-8")
        self.assertEqual({}, auth._read_app_sessions())
        self.assertFalse(self.sessions_path.exists())

    def test_oauth_cache_can_be_rewritten_after_corruption(self) -> None:
        self.oauth_path.write_text('{"a": {}}{"b": {}}', encoding="utf-8")
        self.assertEqual({}, auth._read_oauth_state_cache())
        auth._write_oauth_state_cache({"fresh": {"state": "fresh"}})
        self.assertEqual({"fresh": {"state": "fresh"}}, auth._read_oauth_state_cache())

    def test_app_session_cache_can_be_rewritten_after_corruption(self) -> None:
        self.sessions_path.write_text("", encoding="utf-8")
        self.assertEqual({}, auth._read_app_sessions())
        auth._write_app_sessions({"hash": {"user_email": "user@example.com"}})
        self.assertEqual({"hash": {"user_email": "user@example.com"}}, auth._read_app_sessions())

    def test_atomic_write_creates_valid_oauth_json(self) -> None:
        auth._write_oauth_state_cache({"state": {"redirect_uri": "https://example.com"}})
        with self.oauth_path.open("r", encoding="utf-8") as handle:
            parsed = json.load(handle)
        self.assertEqual({"state": {"redirect_uri": "https://example.com"}}, parsed)

    def test_atomic_write_creates_valid_app_session_json(self) -> None:
        auth._write_app_sessions({"hash": {"user_email": "user@example.com"}})
        with self.sessions_path.open("r", encoding="utf-8") as handle:
            parsed = json.load(handle)
        self.assertEqual({"hash": {"user_email": "user@example.com"}}, parsed)


if __name__ == "__main__":
    unittest.main()
