"""Spec 069 T7.1 — JSON mode never degrades silently."""
import asyncio
import unittest
from unittest import mock

from src import llm_client


class JsonModeTest(unittest.TestCase):
    def test_rejected_response_format_raises(self):
        calls = []

        async def create(**kw):
            calls.append(kw)
            raise ValueError("400: unsupported parameter response_format")

        with mock.patch.object(llm_client._client.chat.completions, "create", create):
            with self.assertRaises(llm_client.JsonModeUnsupported):
                asyncio.run(llm_client.complete("s", "u", json_mode=True))
        self.assertEqual(len(calls), 1)
        self.assertIn("response_format", calls[0])


if __name__ == "__main__":
    unittest.main()
