from __future__ import annotations

import ssl
import unittest
from unittest.mock import Mock, patch

from app import auth
from app.google_transport import close_google_service, is_retryable_google_transport_error, with_google_transport_retry


class GoogleTransportResilienceTest(unittest.TestCase):
    def test_ssl_record_layer_failure_is_retryable(self) -> None:
        error = ssl.SSLError("[SSL] record layer failure")
        self.assertTrue(is_retryable_google_transport_error(error))

    def test_invalid_grant_is_not_retryable(self) -> None:
        self.assertFalse(is_retryable_google_transport_error(ValueError("invalid_grant")))

    def test_retry_succeeds_after_transient_ssl_failure(self) -> None:
        calls = {"count": 0}

        def flaky_operation() -> str:
            calls["count"] += 1
            if calls["count"] == 1:
                raise ssl.SSLError("[SSL] record layer failure")
            return "ok"

        with patch("app.google_transport.time.sleep"):
            self.assertEqual("ok", with_google_transport_retry("unit_test", flaky_operation))
        self.assertEqual(2, calls["count"])

    def test_retry_stops_after_attempt_limit(self) -> None:
        with patch("app.google_transport.time.sleep"):
            with self.assertRaises(ssl.SSLError):
                with_google_transport_retry("unit_test", lambda: (_ for _ in ()).throw(ssl.SSLError("connection reset")), attempts=2)

    def test_close_google_service_uses_close_when_available(self) -> None:
        service = Mock()
        close_google_service(service)
        service.close.assert_called_once()

    def test_close_google_service_ignores_missing_close(self) -> None:
        close_google_service(object())

    def test_oauth_token_exchange_retries_transient_ssl_failure(self) -> None:
        flow = Mock()
        flow.credentials.id_token = "id-token"
        flow.fetch_token.side_effect = [ssl.SSLError("[SSL] record layer failure"), None]
        context = {
            "state": "state",
            "code_verifier": "verifier",
            "redirect_uri": "https://example.com/",
            "return_to": "",
        }
        with patch.object(auth, "_oauth_context", return_value=context), patch.object(auth, "_auth_flow", return_value=flow), patch.object(
            auth, "_oauth_client_config", return_value={"web": {"client_id": "client"}}
        ), patch("google.oauth2.id_token.verify_oauth2_token", return_value={"email": "user@toporiental.co.uk"}), patch(
            "app.google_transport.time.sleep"
        ):
            verified = auth._verify_callback_code("code", "state")
        self.assertEqual("user@toporiental.co.uk", verified["email"])
        self.assertEqual(2, flow.fetch_token.call_count)


if __name__ == "__main__":
    unittest.main()
