import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / 'dashboard' / 'server.py'

spec = importlib.util.spec_from_file_location('dashboard_server', MODULE_PATH)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class DashboardRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        server.SETTINGS_FILE = str(base / 'settings.json')
        server.HISTORY_FILE = str(base / 'history.json')
        server.STATS_FILE = str(base / 'stats.json')
        server.SERVER_SOURCES = server.get_server_sources_from_settings()
        server.clear_action_history_store()

    def tearDown(self):
        self.tmp.cleanup()

    def test_normalize_dashboard_config_clamps_values(self):
        cfg = server.normalize_dashboard_config({
            'read_only': 1,
            'debug_mode': 0,
            'refresh_interval_sec': 999,
            'action_history_limit': 2,
            'write_rate_limit_per_minute': -4,
            'login_rate_limit_per_minute': 999,
        })
        self.assertTrue(cfg['read_only'])
        self.assertFalse(cfg['debug_mode'])
        self.assertEqual(cfg['refresh_interval_sec'], 60)
        self.assertEqual(cfg['action_history_limit'], 10)
        self.assertEqual(cfg['write_rate_limit_per_minute'], 5)
        self.assertEqual(cfg['login_rate_limit_per_minute'], 120)

    def test_extract_ed2k_links_deduplicates_and_ignores_noise(self):
        blob = '''hello\ned2k://|file|One.bin|123|AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA|/\ntrash\ned2k://|file|One.bin|123|AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA|/\ntext ed2k://|file|Two.bin|456|BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB|/ end'''
        links = server.extract_ed2k_links(blob)
        self.assertEqual(len(links), 2)
        self.assertTrue(all(link.startswith('ed2k://') for link in links))

    def test_filter_log_lines_applies_level_and_text(self):
        lines = [
            'INFO boot complete\n',
            'WARNING slow peer\n',
            'ERROR cannot connect\n',
            'random line\n',
        ]
        filtered = server.filter_log_lines(lines, level='error', contains='connect', limit=50)
        self.assertEqual(filtered['matched_total'], 1)
        self.assertEqual(filtered['counts']['error'], 1)
        self.assertIn('ERROR cannot connect', ''.join(filtered['lines']))

    def test_import_dashboard_bundle_merge_keeps_uniques(self):
        settings = server.normalize_settings(None)
        settings['dashboard']['refresh_interval_sec'] = 9
        self.assertTrue(server.save_settings(settings))
        history = {
            'searches': [{'query': 'ubuntu', 'type': 'kad'}],
            'favorites': [{'name': 'f1', 'link': 'ed2k://|file|A|1|AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA|/'}],
            'saved_searches': [{'id': 'abc', 'key': 'kad::ubuntu', 'query': 'ubuntu', 'type': 'kad', 'label': 'ubuntu'}],
            'action_history': [],
        }
        self.assertTrue(server._save_history(history))

        bundle = server.build_export_bundle(include_action_history=True, include_stats=False)
        bundle['settings']['dashboard']['refresh_interval_sec'] = 12
        bundle['history']['favorites'].append({'name': 'f2', 'link': 'ed2k://|file|B|2|BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB|/'})
        bundle['history']['saved_searches'].append({'id': 'def', 'key': 'kad::debian', 'query': 'debian', 'type': 'kad', 'label': 'debian'})

        summary = server.import_dashboard_bundle(bundle, mode='merge')
        self.assertEqual(summary['history']['favorites'], 2)
        self.assertEqual(summary['history']['saved_searches'], 2)
        loaded = server._load_history()
        self.assertEqual(len(loaded['favorites']), 2)
        self.assertEqual(len(loaded['saved_searches']), 2)
        self.assertEqual(server.load_settings()['dashboard']['refresh_interval_sec'], 12)

    def test_import_dashboard_bundle_replace_overwrites(self):
        self.assertTrue(server._save_history({'searches': [{'query': 'old'}], 'favorites': [], 'saved_searches': [], 'action_history': []}))
        bundle = {
            'format': 'amule_dashboard_bundle',
            'version': 1,
            'settings': server.normalize_settings(None),
            'history': {
                'searches': [{'query': 'new', 'type': 'global'}],
                'favorites': [],
                'saved_searches': [],
                'action_history': [],
            },
        }
        summary = server.import_dashboard_bundle(bundle, mode='replace')
        self.assertEqual(summary['history']['searches'], 1)
        loaded = server._load_history()
        self.assertEqual(loaded['searches'][0]['query'], 'new')


if __name__ == '__main__':
    unittest.main()
