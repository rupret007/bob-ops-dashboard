import json
import unittest
from unittest.mock import MagicMock, patch

from cluster.metrics_collect import collect_metrics, post_node_v1, to_node_v1


class TestBridge(unittest.TestCase):
    @patch("urllib.request.urlopen")
    def test_post_node_v1(self, mock_urlopen):
        m = collect_metrics("mini", 0, light=True)
        payload = to_node_v1(m)
        self.assertEqual(payload["v"], "node/v1")
        mock_resp = MagicMock()
        mock_urlopen.return_value.__enter__.return_value = mock_resp
        post_node_v1("http://127.0.0.1:8787/api/node", payload)
        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        body = json.loads(req.data.decode())
        self.assertEqual(body["node_id"], "mini")


if __name__ == "__main__":
    unittest.main()
