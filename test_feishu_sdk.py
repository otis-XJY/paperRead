import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from feishu_sdk import FeishuOpenAPIClient


class FeishuSDKTransportTests(unittest.TestCase):
    @staticmethod
    def response():
        return SimpleNamespace(
            raw=SimpleNamespace(
                content=b'{"code": 0, "data": {"ok": true}}',
                status_code=200,
            ),
            code=0,
            msg="",
        )

    def test_retries_transient_ssl_failure(self):
        client = FeishuOpenAPIClient.__new__(FeishuOpenAPIClient)
        client.client = SimpleNamespace(
            request=Mock(
                side_effect=[
                    requests.exceptions.SSLError("unexpected EOF"),
                    requests.exceptions.SSLError("unexpected EOF"),
                    self.response(),
                ]
            )
        )

        with patch("feishu_sdk.time.sleep") as sleep:
            result = client.request("GET", "/im/v1/messages")

        self.assertEqual(result, {"ok": True})
        self.assertEqual(client.client.request.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(sleep.call_args_list[0].args[0], 1.0)
        self.assertEqual(sleep.call_args_list[1].args[0], 2.0)

    def test_raises_after_transport_retries_are_exhausted(self):
        client = FeishuOpenAPIClient.__new__(FeishuOpenAPIClient)
        client.client = SimpleNamespace(
            request=Mock(
                side_effect=requests.exceptions.SSLError("unexpected EOF")
            )
        )

        with patch("feishu_sdk.time.sleep"):
            with self.assertRaises(requests.exceptions.SSLError):
                client.request("GET", "/im/v1/messages")

        self.assertEqual(
            client.client.request.call_count,
            FeishuOpenAPIClient._MAX_TRANSPORT_RETRIES + 1,
        )

    def test_send_interactive_card_uses_typed_im_sdk_request(self):
        client = FeishuOpenAPIClient.__new__(FeishuOpenAPIClient)
        create = Mock(return_value=self.response())
        client.client = SimpleNamespace(
            im=SimpleNamespace(v1=SimpleNamespace(message=SimpleNamespace(create=create)))
        )

        result = client.send_interactive_card(
            "oc_daily",
            {"header": {"title": {"tag": "plain_text", "content": "工作日报"}}},
        )

        self.assertEqual(result, {"ok": True})
        request = create.call_args.args[0]
        self.assertEqual(request.receive_id_type, "chat_id")
        self.assertEqual(request.request_body.receive_id, "oc_daily")
        self.assertEqual(request.request_body.msg_type, "interactive")
        self.assertIn("工作日报", request.request_body.content)


if __name__ == "__main__":
    unittest.main()
