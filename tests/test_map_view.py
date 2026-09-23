"""Optional map configuration must not leak unrelated local settings."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from windpilot.api import create_app
from windpilot.map_view import map_key


class MapTests(unittest.TestCase):
    def test_only_named_map_key_is_read_with_environment_precedence(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '.env'
            path.write_text('OTHER_SECRET=not-a-map-key\nWINDY_MAP_API_KEY="test-key"\n', encoding='utf-8')
            with patch.dict('os.environ', {}, clear=True):
                self.assertEqual(map_key(path), 'test-key')
            with patch.dict('os.environ', {'WINDY_MAP_API_KEY': 'environment-key'}):
                self.assertEqual(map_key(path), 'environment-key')
            with patch.dict('os.environ', {'WINDY_MAP_API_KEY': ''}):
                self.assertEqual(map_key(path), '')

    def test_optional_map_missing_key_and_enabled_configuration(self):
        with TestClient(create_app()) as client:
            with patch('windpilot.map_view.map_key', return_value=''):
                response = client.get('/map/config')
                self.assertFalse(response.json()['enabled'])
                self.assertIsNone(response.json()['key'])
                self.assertEqual(response.headers['cache-control'], 'no-store')
                self.assertEqual(client.get('/health').status_code, 200)
            with patch('windpilot.map_view.map_key', return_value='test-only'):
                data = client.get('/map/config').json()
                self.assertTrue(data['enabled'])
                self.assertEqual(data['key'], 'test-only')
                self.assertEqual(len(data['turbines']), 2)
                self.assertEqual(data['product'], 'gfs')
                self.assertNotIn('weather', data)
            page = client.get('/map/windy')
            self.assertEqual(page.status_code, 200)
            self.assertEqual(page.headers['x-frame-options'], 'SAMEORIGIN')
            self.assertNotIn('test-only', page.text)
            self.assertIn('redrawFinished', page.text)


if __name__ == '__main__':
    unittest.main()
