import json
import unittest
from unittest.mock import patch

from crous_watcher import SearchResultParser, accommodation_id, accommodation_label, read_env_value, save_seen, telegram_chat_id


class CrousWatcherTests(unittest.TestCase):
    def test_extracts_sveltekit_search_response(self):
        body = json.dumps({"results": {"items": [{"id": 123, "name": "Studio Cergy"}]}})
        page = (
            '<script type="application/json" data-sveltekit-fetched '
            'data-url="/api/fr/search/47">'
            + json.dumps({"body": body})
            + "</script>"
        )
        parser = SearchResultParser()
        parser.feed(page)
        self.assertEqual(len(parser.payloads), 1)
        payload = json.loads(parser.payloads[0])
        self.assertEqual(json.loads(payload["body"])["results"]["items"][0]["name"], "Studio Cergy")

    def test_uses_id_and_human_label(self):
        item = {"id": 123, "residence": {"name": "Résidence de Cergy"}}
        self.assertEqual(accommodation_id(item), "id:123")
        self.assertEqual(accommodation_label(item), "Résidence de Cergy")

    def test_environment_value_prefers_process_environment(self):
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "test-token"}, clear=True):
            self.assertEqual(read_env_value("TELEGRAM_BOT_TOKEN"), "test-token")

    @patch("crous_watcher.telegram_api")
    def test_finds_private_chat_id_from_updates(self, telegram_api):
        telegram_api.return_value = [{"message": {"chat": {"id": 42, "type": "private"}}}]
        self.assertEqual(telegram_chat_id(), 42)

    def test_state_is_not_rewritten_when_offers_are_unchanged(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            state = Path(directory) / "seen.json"
            self.assertTrue(save_seen(state, {"id:1"}))
            self.assertFalse(save_seen(state, {"id:1"}))


if __name__ == "__main__":
    unittest.main()
