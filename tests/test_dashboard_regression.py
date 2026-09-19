import importlib.util
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / 'dashboard' / 'server.py'
FIXTURES_DIR = Path(__file__).resolve().parent / 'fixtures'

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

    def fixture(self, name):
        return (FIXTURES_DIR / name).read_text(encoding='utf-8')

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



    def test_parse_downloads_exposes_size_metrics(self):
        raw = '''> AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA File One.mkv
  512.0/1024.0 MB  50%
  Sources: 12
  128.0 KB/s downloading
'''
        downloads = server.parse_downloads(raw)
        self.assertEqual(len(downloads), 1)
        self.assertEqual(downloads[0]['size'], '1024.0 MB')
        self.assertIsInstance(downloads[0]['size_bytes'], int)
        self.assertGreater(downloads[0]['size_bytes'], 0)
        self.assertAlmostEqual(downloads[0]['size_mb'], 1024.0, places=1)


    def test_detect_download_issues_flags_stalled_and_no_sources(self):
        raw = '''> AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA File One.mkv
  512.0/1024.0 MB  50%
  Sources: 0
  0.0 KB/s downloading
'''
        downloads = server.parse_downloads(raw)
        self.assertEqual(len(downloads), 1)
        self.assertIn('no_sources', downloads[0]['issues'])
        self.assertIn('stalled', downloads[0]['issues'])
        self.assertTrue(downloads[0]['problematic'])

    def test_build_downloads_payload_exposes_issue_summary(self):
        raw = '''> AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA File One.mkv
  512.0/1024.0 MB  50%
  Sources: 0
  0.0 KB/s downloading
> BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB File Two.iso
  100.0/700.0 MB  14%
  Sources: 8
  4.0 KB/s downloading
'''
        payload = server.build_downloads_payload(raw=raw, include_raw=False)
        self.assertEqual(payload['issues_summary']['problematic'], 2)
        self.assertEqual(payload['issues_summary']['counts_by_issue']['no_sources'], 1)
        self.assertEqual(payload['issues_summary']['counts_by_issue']['stalled'], 1)
        self.assertEqual(payload['issues_summary']['counts_by_issue']['slow'], 1)

    def test_summarize_transfer_action_results_groups_codes_and_hashes(self):
        results = [
            {'hash': 'A'*32, 'name': 'One', 'code': 'SUCCESS', 'message': 'ok', 'before_status': 'downloading', 'after_status': 'paused', 'confirmed': True, 'ok': True},
            {'hash': 'B'*32, 'name': 'Two', 'code': 'ALREADY_EXISTS', 'message': 'already', 'before_status': 'paused', 'after_status': 'paused', 'confirmed': True, 'ok': True},
            {'hash': 'C'*32, 'name': 'Three', 'code': 'STATE_NOT_CONFIRMED', 'message': 'bad', 'before_status': 'downloading', 'after_status': 'downloading', 'confirmed': False, 'ok': False},
            {'hash': 'D'*32, 'name': 'Four', 'code': 'TRANSFER_NOT_FOUND', 'message': 'missing', 'confirmed': False, 'ok': False},
        ]
        overview = server.summarize_transfer_action_results(results)
        self.assertEqual(overview['counts_by_code']['SUCCESS'], 1)
        self.assertEqual(overview['counts_by_code']['ALREADY_EXISTS'], 1)
        self.assertEqual(overview['counts_by_code']['STATE_NOT_CONFIRMED'], 1)
        self.assertIn('A'*32, overview['confirmed_hashes'])
        self.assertIn('C'*32, overview['failed_hashes'])
        self.assertIn('D'*32, overview['missing_hashes'])
        self.assertEqual(overview['status_before']['downloading'], 2)
        self.assertEqual(len(overview['failed_items']), 2)

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

    def test_payload_digest_stable_across_key_order(self):
        left = {'b': 2, 'a': {'x': 1, 'y': 2}}
        right = {'a': {'y': 2, 'x': 1}, 'b': 2}
        self.assertEqual(server.payload_digest(left), server.payload_digest(right))

    def test_build_downloads_payload_can_skip_raw_and_expose_digest(self):
        raw = "> AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA File One.mkv\n  512.0/1024.0 MB  50%\n  Sources: 12\n  128.0 KB/s downloading\n"
        payload = server.build_downloads_payload(raw=raw, include_raw=False)
        self.assertEqual(payload['count'], 1)
        self.assertIn('digest', payload)
        self.assertNotIn('raw', payload)
        self.assertEqual(payload['downloads'][0]['eta'], '1h 08m')

    def test_build_action_history_payload_contains_digest(self):
        server.record_action_event({'action': 'download', 'ok': True, 'confirmed': True, 'code': 'SUCCESS', 'message': 'ok'}, 200)
        payload = server.build_action_history_payload(limit=10)
        self.assertEqual(len(payload['actions']), 1)
        self.assertIn('digest', payload)

    def test_import_dashboard_bundle_merge_keeps_uniques(self):
        settings = server.normalize_settings(None)
        settings['dashboard']['refresh_interval_sec'] = 9
        self.assertTrue(server.save_settings(settings))
        event_a = {'ts': 1, 'action': 'pause', 'target': 'A', 'code': 'SUCCESS', 'ok': True}
        self.assertTrue(server._save_history({'action_history': [event_a]}))
        server.init_action_history_store()

        bundle = server.build_export_bundle(include_action_history=True, include_stats=False)
        bundle['settings']['dashboard']['refresh_interval_sec'] = 12
        # Same event again (must dedupe) + one new event (must be kept)
        bundle['history']['action_history'].append(dict(event_a))
        bundle['history']['action_history'].append({'ts': 2, 'action': 'resume', 'target': 'B', 'code': 'SUCCESS', 'ok': True})

        summary = server.import_dashboard_bundle(bundle, mode='merge')
        self.assertEqual(summary['history']['action_history'], 2)
        loaded = server._load_history()
        self.assertEqual(len(loaded['action_history']), 2)
        # Legacy keys from older bundles must not leak into the history file
        self.assertEqual(set(loaded.keys()), {'action_history'})
        self.assertEqual(server.load_settings()['dashboard']['refresh_interval_sec'], 12)

    def test_import_dashboard_bundle_replace_overwrites(self):
        self.assertTrue(server._save_history({'action_history': [{'ts': 1, 'action': 'old', 'code': 'SUCCESS'}]}))
        bundle = {
            'format': 'amule_dashboard_bundle',
            'version': 1,
            'settings': server.normalize_settings(None),
            'history': {
                # Legacy v1 bundle keys are ignored, only action_history is imported
                'searches': [{'query': 'new', 'type': 'global'}],
                'favorites': [{'name': 'f', 'link': 'ed2k://|file|f|1|AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA|/'}],
                'saved_searches': [],
                'action_history': [{'ts': 2, 'action': 'new', 'code': 'SUCCESS'}],
            },
        }
        summary = server.import_dashboard_bundle(bundle, mode='replace')
        self.assertEqual(summary['history']['action_history'], 1)
        loaded = server._load_history()
        self.assertEqual(loaded['action_history'][0]['action'], 'new')
        self.assertEqual(set(loaded.keys()), {'action_history'})


    def test_parse_downloads_fixture_handles_commas_unicode_and_errors(self):
        raw = self.fixture('show_dl_mixed.txt')
        downloads = server.parse_downloads(raw)
        self.assertEqual(len(downloads), 3)
        self.assertEqual(downloads[0]['status'], 'downloading')
        self.assertAlmostEqual(downloads[0]['speed'], 1536.0, places=1)
        self.assertEqual(downloads[1]['status'], 'waiting')
        self.assertIn('Série étrangère', downloads[1]['name'])
        self.assertGreater(downloads[1]['size_bytes'], 0)
        self.assertEqual(downloads[2]['status'], 'error')
        self.assertIn('insufficient disk space', downloads[2]['status_detail'].lower())

    def test_parse_status_does_not_confuse_not_connected_with_connected(self):
        raw = 'eD2k: Not connected\nKad: Not connected\nDownload:\t0 bytes/sec\nUpload:\t0 bytes/sec'
        info = server.parse_status(raw)
        self.assertFalse(info['connected_ed2k'])
        self.assertFalse(info['connected_kad'])
        self.assertEqual(info['ed2k_status'], 'disconnected')
        self.assertEqual(info['kad_status'], 'disconnected')

        raw = 'eD2k: Now connecting\nKad: Not running'
        info = server.parse_status(raw)
        self.assertEqual(info['ed2k_status'], 'connecting')
        self.assertEqual(info['kad_status'], 'disconnected')

        raw = 'eD2k: Connected to Srv 1.2.3.4 with LowID\nKad: Connected (firewalled)'
        info = server.parse_status(raw)
        self.assertEqual(info['ed2k_status'], 'low_id')
        self.assertEqual(info['kad_status'], 'firewalled')

    def test_bookmarklet_opens_dashboard_hash_without_token(self):
        code = server.get_bookmarklet_code('http://192.168.1.10:8078')
        self.assertTrue(code.startswith('javascript:'))
        self.assertIn("window.open('http://192.168.1.10:8078/#add='", code)
        self.assertNotIn('token', code)
        self.assertNotIn('/api/', code)  # no cross-origin fetch from third-party pages

    def test_build_health_payload_reports_ready_with_realistic_status_fixture(self):
        status_raw = self.fixture('status_connected.txt')
        with mock.patch.object(server, '_check_amuled_process', return_value={'ok': True, 'pid': '1234'}), \
             mock.patch.object(server, '_check_ec_port', return_value={'ok': True, 'errno': 0}):
            payload = server.build_health_payload(status_raw=status_raw)
        self.assertTrue(payload['ok'])
        self.assertTrue(payload['ready'])
        self.assertEqual(payload['status']['ed2k_status'], 'low_id')
        self.assertEqual(payload['status']['kad_status'], 'firewalled')
        self.assertIn('aMule prêt', payload['summary'])
        self.assertIn('digest', payload)

    def test_build_health_payload_reports_not_ready_when_status_unusable(self):
        with mock.patch.object(server, '_check_amuled_process', return_value={'ok': True, 'pid': '1234'}), \
             mock.patch.object(server, '_check_ec_port', return_value={'ok': True, 'errno': 0}):
            payload = server.build_health_payload(status_raw='ERROR: Unable to connect to aMule')
        self.assertTrue(payload['ok'])
        self.assertFalse(payload['ready'])
        self.assertIn('status aMule indisponible', payload['summary'])

    def test_end_to_end_transfer_flow_uses_fixture_outputs(self):
        alpha_link = 'ed2k://|file|Alpha.iso|734003200|AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA|/'
        before_raw = self.fixture('downloads_before.txt')
        after_add = self.fixture('downloads_after_add.txt')
        after_pause = self.fixture('downloads_after_pause.txt')
        after_resume = self.fixture('downloads_after_resume.txt')
        after_cancel = self.fixture('downloads_after_cancel.txt')

        outputs = [before_raw, after_add, after_add, after_pause, after_pause, after_resume, after_resume, after_cancel]
        command_log = []

        def fake_run_amulecmd(command, timeout=20):
            command_log.append(command)
            if command == 'show dl':
                return outputs.pop(0)
            if command.startswith('add ed2k://'):
                return 'OK'
            if command.startswith('pause '):
                return 'OK'
            if command.startswith('resume '):
                return 'OK'
            if command.startswith('cancel '):
                return 'OK'
            raise AssertionError(f'unexpected command: {command}')

        with mock.patch.object(server, 'run_amulecmd', side_effect=fake_run_amulecmd), \
             mock.patch.object(server, 'run_amulecmd_interactive', return_value='OK'), \
             mock.patch.object(server.time, 'sleep', return_value=None):
            add_payload, add_status = server.add_ed2k_confirmed(alpha_link)
            self.assertEqual(add_status, 200)
            self.assertTrue(add_payload['confirmed'])
            download_hash = add_payload['data']['download']['hash']
            self.assertEqual(download_hash, 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA')

            pause_payload, pause_status = server.change_transfer_state('pause', download_hash)
            self.assertEqual(pause_status, 200)
            self.assertTrue(pause_payload['confirmed'])
            self.assertEqual(pause_payload['data']['download']['status'], 'paused')

            resume_payload, resume_status = server.change_transfer_state('resume', download_hash)
            self.assertEqual(resume_status, 200)
            self.assertTrue(resume_payload['confirmed'])
            self.assertEqual(resume_payload['data']['download']['status'], 'waiting')

            cancel_payload, cancel_status = server.change_transfer_state('cancel', download_hash)
            self.assertEqual(cancel_status, 200)
            self.assertTrue(cancel_payload['confirmed'])
            self.assertEqual(cancel_payload['data']['removed_hashes'], [download_hash])

        self.assertIn(f'add {alpha_link}', command_log)
        self.assertIn('pause AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA', command_log)
        self.assertIn('resume AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA', command_log)
        self.assertIn('cancel AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA', command_log)

if __name__ == '__main__':
    unittest.main()
