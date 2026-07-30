from __future__ import annotations

import ssl
import unittest
from unittest.mock import Mock, patch

from app import google_drive
from app.google_transport import GoogleHttpStatusError


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, content: bytes | None = None):
        self.status_code = status_code
        self._payload = payload or {}
        self.content = content if content is not None else b"{}"

    def json(self) -> dict:
        return self._payload


class _FakeSession:
    def __init__(self, responses: list[object]):
        self.responses = list(responses)
        self.requests: list[dict] = []
        self.closed = False

    def request(self, method: str, url: str, timeout=None, **kwargs):
        self.requests.append({"method": method, "url": url, "timeout": timeout, **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    def close(self) -> None:
        self.closed = True


class DriveRestTransportTests(unittest.TestCase):
    def test_rest_file_list_paginates(self) -> None:
        session = _FakeSession(
            [
                _FakeResponse(200, {"files": [{"id": "1", "name": "A"}], "nextPageToken": "next"}),
                _FakeResponse(200, {"files": [{"id": "2", "name": "B"}]}),
            ]
        )
        client = google_drive.DriveRestClient(session)
        items = google_drive._list_drive_children(client, "folder")
        self.assertEqual(["A", "B"], [item["name"] for item in items])
        self.assertEqual("next", session.requests[1]["params"]["pageToken"])

    def test_folder_alias_discovery_uses_rest_children(self) -> None:
        session = _FakeSession(
            [
                _FakeResponse(
                    200,
                    {
                        "files": [
                            {
                                "id": "folder-1",
                                "name": "  TARGETS   DATA ",
                                "mimeType": google_drive.FOLDER_MIME_TYPE,
                                "modifiedTime": "2026-07-01T00:00:00Z",
                            }
                        ]
                    },
                )
            ]
        )
        folder = google_drive.find_drive_folder_from_aliases(
            google_drive.DriveRestClient(session),
            "root",
            "Targets",
            google_drive.TARGETS_FOLDER_NAMES,
        )
        self.assertEqual("folder-1", folder.file_id)

    def test_shortcut_folder_is_resolved_from_rest_response(self) -> None:
        session = _FakeSession(
            [
                _FakeResponse(
                    200,
                    {
                        "files": [
                            {
                                "id": "shortcut-1",
                                "name": "Cost Data",
                                "mimeType": google_drive.SHORTCUT_MIME_TYPE,
                                "modifiedTime": "2026-07-01T00:00:00Z",
                                "shortcutDetails": {
                                    "targetId": "real-folder",
                                    "targetMimeType": google_drive.FOLDER_MIME_TYPE,
                                },
                            }
                        ]
                    },
                )
            ]
        )
        folder = google_drive.find_drive_folder_from_aliases(
            google_drive.DriveRestClient(session),
            "root",
            "Cost Data",
            google_drive.COST_FOLDER_NAMES,
        )
        self.assertEqual("real-folder", folder.file_id)

    def test_excel_download_uses_alt_media(self) -> None:
        session = _FakeSession([_FakeResponse(200, content=b"excel-bytes")])
        content = google_drive.download_drive_file(google_drive.DriveRestClient(session), "file-id")
        self.assertEqual(b"excel-bytes", content.getvalue())
        self.assertEqual({"alt": "media", "supportsAllDrives": "true"}, session.requests[0]["params"])

    def test_429_retry_succeeds(self) -> None:
        session = _FakeSession([_FakeResponse(429), _FakeResponse(200, {"files": []})])
        with patch("app.google_transport.time.sleep"):
            response = google_drive.DriveRestClient(session).list_files(q="x")
        self.assertEqual({"files": []}, response)
        self.assertEqual(2, len(session.requests))

    def test_503_retry_succeeds(self) -> None:
        session = _FakeSession([_FakeResponse(503), _FakeResponse(200, {"files": []})])
        with patch("app.google_transport.time.sleep"):
            response = google_drive.DriveRestClient(session).list_files(q="x")
        self.assertEqual({"files": []}, response)
        self.assertEqual(2, len(session.requests))

    def test_ssl_retry_succeeds(self) -> None:
        session = _FakeSession([ssl.SSLError("[SSL] record layer failure"), _FakeResponse(200, {"files": []})])
        with patch("app.google_transport.time.sleep"):
            response = google_drive.DriveRestClient(session).list_files(q="x")
        self.assertEqual({"files": []}, response)
        self.assertEqual(2, len(session.requests))

    def test_403_is_not_retried(self) -> None:
        session = _FakeSession([_FakeResponse(403), _FakeResponse(200, {"files": []})])
        with self.assertRaises(GoogleHttpStatusError):
            google_drive.DriveRestClient(session).list_files(q="x")
        self.assertEqual(1, len(session.requests))

    def test_drive_business_load_closes_short_lived_transport(self) -> None:
        service = Mock()
        service.close = Mock()
        status = google_drive.DriveLoadItemStatus("loaded", "ok")
        with patch.object(google_drive, "get_drive_config", return_value=Mock()), patch.object(
            google_drive, "get_drive_service", return_value=service
        ), patch.object(google_drive, "_load_sales_file", return_value=status), patch.object(
            google_drive, "_load_target_file", return_value=status
        ), patch.object(google_drive, "_get_streamlit") as fake_st:
            fake_st.return_value.session_state = {}
            google_drive.load_drive_business_files(force=True)
        service.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
