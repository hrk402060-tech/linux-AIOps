import json
from unittest.mock import Mock, patch
import unittest

from alert_relay import RelayError, sign_payload, split_alerts


class AlertRelayTests(unittest.TestCase):
    def test_splits_batches_without_dropping_alerts(self) -> None:
        payload = {
            "status": "firing",
            "receiver": "ops",
            "alerts": [
                {"status": "firing", "fingerprint": "one"},
                {"status": "resolved", "fingerprint": "two"},
            ],
        }
        result = split_alerts(payload)
        self.assertEqual([item["alerts"][0]["fingerprint"] for item in result], ["one", "two"])
        self.assertEqual([item["status"] for item in result], ["firing", "resolved"])

    def test_rejects_invalid_batch(self) -> None:
        with self.assertRaises(RelayError):
            split_alerts({"alerts": [{"status": "unknown"}]})
        with self.assertRaises(RelayError):
            split_alerts({"alerts": []})

    def test_signature_matches_platform_contract(self) -> None:
        raw, headers = sign_payload("secret", {"alerts": []}, timestamp=1_700_000_000)
        expected_body = json.dumps({"alerts": []}, separators=(",", ":"), ensure_ascii=False).encode()
        self.assertEqual(raw, expected_body)
        self.assertTrue(headers["X-Alert-Signature"].startswith("sha256="))
        self.assertEqual(len(headers["X-Alert-Nonce"]), 32)

    @patch("alert_relay.urlopen")
    def test_forward_uses_bounded_timeout_and_signed_headers(self, opener: Mock) -> None:
        response = Mock(status=202)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        opener.return_value = response
        from alert_relay import forward

        self.assertEqual(forward("http://relay.invalid/alerts", "secret", {"status": "firing"}, 3), 202)
        request = opener.call_args.args[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(opener.call_args.kwargs["timeout"], 3)
        self.assertTrue(request.get_header("X-alert-signature").startswith("sha256="))


if __name__ == "__main__":
    unittest.main()
