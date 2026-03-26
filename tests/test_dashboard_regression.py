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
        return (FIXTURES_DIR / name).read_text()

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



    def test_normalize_favorite_entry_keeps_search_metadata(self):
        fav = server.normalize_favorite_entry({
            'name': 'Ubuntu.iso',
            'link': 'ed2k://|search_result|12|Ubuntu.iso|/',
            'size': '700 MB',
            'sources': 12,
            'kind': 'search_result',
            'query': 'ubuntu',
            'search_type': 'kad',
        })
        self.assertEqual(fav['kind'], 'search_result')
        self.assertEqual(fav['query'], 'ubuntu')
        self.assertEqual(fav['search_type'], 'kad')
        self.assertTrue(fav['favorite_id'])

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

    def test_summarize_search_download_results_groups_codes(self):
        results = [
            {'id': 1, 'name': 'One', 'size': '700 MB', 'sources': 10, 'code': 'SUCCESS', 'message': 'ok', 'confirmed': True, 'ok': True},
            {'id': 2, 'name': 'Two', 'size': '700 MB', 'sources': 4, 'code': 'ALREADY_EXISTS', 'message': 'already', 'confirmed': True, 'ok': True},
            {'id': 3, 'name': 'Three', 'size': '700 MB', 'sources': 2, 'code': 'RESULT_NOT_FOUND', 'message': 'missing', 'confirmed': False, 'ok': False},
            {'id': 4, 'name': 'Four', 'size': '700 MB', 'sources': 1, 'code': 'STATE_NOT_CONFIRMED', 'message': 'bad', 'confirmed': False, 'ok': False},
        ]
        overview = server.summarize_search_download_results(results)
        self.assertEqual(overview['success'], 1)
        self.assertEqual(overview['already'], 1)
        self.assertEqual(overview['missing'], 1)
        self.assertEqual(overview['failed'], 1)
        self.assertIn(1, overview['confirmed_ids'])
        self.assertIn(3, overview['missing_ids'])
        self.assertIn(4, overview['failed_ids'])

    def test_bulk_download_from_cached_search_summarizes_success_already_and_missing(self):
        server.set_last_search_context('ubuntu', 'kad', [
            {'id': 1, 'name': 'Alpha.iso', 'size': '700 MB', 'sources': 20},
            {'id': 2, 'name': 'Beta.iso', 'size': '800 MB', 'sources': 8},
        ])
        before_raw = '> BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB Beta.iso\n  100.0/800.0 MB  12%\n  Sources: 3\n  0.0 KB/s paused\n'
        after_raw = before_raw + '> AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA Alpha.iso\n  0.0/700.0 MB  0%\n  Sources: 0\n  0.0 KB/s waiting\n'

        def fake_run_amulecmd(cmd, timeout=20):
            if cmd == 'show dl':
                if fake_run_amulecmd.calls == 0:
                    fake_run_amulecmd.calls += 1
                    return before_raw
                return after_raw
            raise AssertionError(f'unexpected command: {cmd}')
        fake_run_amulecmd.calls = 0

        with mock.patch.object(server, 'run_amulecmd', side_effect=fake_run_amulecmd), \
             mock.patch.object(server, 'run_amulecmd_interactive', return_value='OK'), \
             mock.patch.object(server.time, 'sleep', return_value=None):
            payload, status = server.bulk_download_from_cached_search([1, 2, 999, 1])

        self.assertEqual(status, 207)
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['code'], 'PARTIAL_SUCCESS')
        self.assertEqual(payload['data']['summary']['success'], 1)
        self.assertEqual(payload['data']['summary']['already'], 1)
        self.assertEqual(payload['data']['summary']['missing'], 1)
        self.assertEqual(payload['data']['summary']['total'], 3)
        self.assertIn(1, payload['data']['changed_result_ids'])


    def test_download_favorites_supports_search_result_favorites(self):
        fav = {
            'favorite_id': 'fav-search-1',
            'name': 'Alpha.iso',
            'link': 'ed2k://|search_result|1|Alpha.iso|/',
            'size': '700 MB',
            'sources': 20,
            'kind': 'search_result',
            'query': 'ubuntu',
            'search_type': 'kad',
        }
        before_raw = ''
        after_raw = '> AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA Alpha.iso\n  0.0/700.0 MB  0%\n  Sources: 0\n  0.0 KB/s waiting\n'
        search_output = '0.    Alpha.iso  700.0  12\n1.    Beta.iso  800.0  2\n'

        def fake_run_amulecmd(cmd, timeout=20):
            if cmd == 'show dl':
                if fake_run_amulecmd.calls == 0:
                    fake_run_amulecmd.calls += 1
                    return before_raw
                return after_raw
            raise AssertionError(f'unexpected command: {cmd}')
        fake_run_amulecmd.calls = 0

        with mock.patch.object(server, 'run_amulecmd', side_effect=fake_run_amulecmd),              mock.patch.object(server, 'run_amulecmd_interactive', side_effect=[search_output, 'OK']),              mock.patch.object(server.time, 'sleep', return_value=None):
            payload, status = server.download_favorites([fav])

        self.assertEqual(status, 200)
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['data']['summary']['success'], 1)
        self.assertIn('fav-search-1', payload['data']['changed_favorite_ids'])

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


    def test_remove_favorites_supports_bulk_ids(self):
        fav1 = server.add_favorite('One.iso', 'ed2k://|file|One.iso|1|AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA|/')
        fav2 = server.add_favorite('Two.iso', 'ed2k://|file|Two.iso|2|BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB|/')
        self.assertTrue(fav1)
        self.assertTrue(fav2)
        favorites = server.get_favorites()
        ids = [favorites[0]['favorite_id'], favorites[1]['favorite_id']]
        removed, removed_ids = server.remove_favorites(ids)
        self.assertEqual(removed, 2)
        self.assertEqual(set(removed_ids), set(ids))
        self.assertEqual(server.get_favorites(), [])

    def test_update_saved_search_can_rename_and_change_query(self):
        ok, _ = server.add_saved_search('ubuntu', 'kad', 'Ubuntu base')
        self.assertTrue(ok)
        item = server.get_saved_searches()[0]
        updated, message, changed = server.update_saved_search(item['id'], query='debian', search_type='global', label='Debian stable')
        self.assertTrue(updated)
        self.assertEqual(message, 'Recherche mise à jour')
        self.assertEqual(changed['query'], 'debian')
        self.assertEqual(changed['type'], 'global')
        self.assertEqual(changed['label'], 'Debian stable')
        reloaded = server.get_saved_searches()[0]
        self.assertEqual(reloaded['key'], 'global::debian')

    def test_update_saved_search_rejects_duplicate_target(self):
        self.assertTrue(server.add_saved_search('ubuntu', 'kad', 'Ubuntu')[0])
        self.assertTrue(server.add_saved_search('debian', 'global', 'Debian')[0])
        items = sorted(server.get_saved_searches(), key=lambda x: x['query'])
        debian = next(x for x in items if x['query'] == 'debian')
        updated, message, changed = server.update_saved_search(debian['id'], query='ubuntu', search_type='kad')
        self.assertFalse(updated)
        self.assertEqual(message, 'Une recherche sauvegardée identique existe déjà')
        self.assertIsNone(changed)

    def test_remove_saved_searches_supports_bulk_ids(self):
        self.assertTrue(server.add_saved_search('ubuntu', 'kad', 'Ubuntu')[0])
        self.assertTrue(server.add_saved_search('debian', 'global', 'Debian')[0])
        items = server.get_saved_searches()
        removed, removed_ids = server.remove_saved_searches([items[0]['id'], items[1]['id']])
        self.assertEqual(removed, 2)
        self.assertEqual(set(removed_ids), {items[0]['id'], items[1]['id']})
        self.assertEqual(server.get_saved_searches(), [])


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

    def test_parse_search_results_fixture_handles_decimal_commas_and_alt_lines(self):
        raw = self.fixture('search_results_table.txt')
        results = server.parse_search_results(raw)
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0]['id'], 0)
        self.assertEqual(results[0]['sources'], 33)
        self.assertEqual(results[0]['size'], '1.0 GB')
        alt = next(item for item in results if item['id'] == 2)
        self.assertEqual(alt['size'], '1.5 GiB')
        self.assertGreater(alt['size_mb'], 1500)

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
        server.set_last_search_context('ubuntu', 'kad', [
            {'id': 1, 'name': 'Alpha.iso', 'size': '700 MB', 'sources': 20},
            {'id': 2, 'name': 'Beta.iso', 'size': '800 MB', 'sources': 8},
        ])
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
            bulk_payload, bulk_status = server.bulk_download_from_cached_search([1])
            download_hash = bulk_payload['data']['changed_result_ids'] and bulk_payload['data']['results'][0].get('hash')
            self.assertEqual(bulk_status, 200)
            self.assertEqual(bulk_payload['data']['summary']['success'], 1)
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

        self.assertIn('pause AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA', command_log)
        self.assertIn('resume AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA', command_log)
        self.assertIn('cancel AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA', command_log)

if __name__ == '__main__':
    unittest.main()
