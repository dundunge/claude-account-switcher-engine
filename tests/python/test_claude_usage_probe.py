"""Offline probes use synthetic accounts and a fake CLI, never installed logins."""
import concurrent.futures
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from zoneinfo import ZoneInfo


SCRIPT = Path(__file__).resolve().parents[2] / 'tools/claude_usage_probe.py'
spec = importlib.util.spec_from_file_location('case_usage_probe', SCRIPT)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

PANEL = '''Settings: Usage
Current session
  ███░░ 21% used
  Resets in 2 hr 4 min
Current week (all models)
  ███░░ 67% used
  Resets Oct 28 at 8am (America/Los_Angeles)
Current week (Sonnet only)
  99% used
Extra usage
  87% used
What's contributing to your limits usage?
  Read 100% used
'''

FAKE_CLI = r'''#!/usr/bin/python3
import fcntl, json, os, pathlib, struct, sys, termios, time
root = pathlib.Path(os.environ['CLAUDE_CONFIG_DIR'])
account = json.loads((root / '.claude.json').read_text())['oauthAccount']
assert not any(k.startswith(('ANTHROPIC_', 'CLAUDE_CODE_USE_')) for k in os.environ)
assert 'CLAUDE_CODE_OAUTH_TOKEN' not in os.environ and 'NODE_OPTIONS' not in os.environ
assert 'CLAUDE_CODE_NO_FLICKER' not in os.environ
assert 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC' not in os.environ
assert os.environ['DISABLE_TELEMETRY'] == '1'
assert os.environ['DISABLE_ERROR_REPORTING'] == '1'
assert os.environ['DO_NOT_TRACK'] == '1'
assert os.environ['DISABLE_AUTOUPDATER'] == '1'
if sys.argv[1:3] == ['auth', 'status']:
    print(json.dumps({'loggedIn': True, 'authMethod': 'claude.ai', 'apiProvider': 'firstParty',
                      'orgId': account['organizationUuid'], 'configDirectory': str(root)}))
else:
    assert sys.stdin.isatty()
    rows, cols, _, _ = struct.unpack('HHHH', fcntl.ioctl(0, termios.TIOCGWINSZ, b'\0' * 8))
    assert (rows, cols) == (200, 160)
    assert '--strict-mcp-config' in sys.argv and '--allowed-tools' in sys.argv
    assert 'disableAllHooks' in sys.argv[sys.argv.index('--settings') + 1]
    assert os.environ['CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN'] == '1'
    assert sys.argv[-1] == '/usage', sys.argv
    # New official CLI layouts can omit the shortcuts/help footer entirely.
    (root / 'query-count').write_text('1')
    mode = (root / 'query-mode').read_text() if (root / 'query-mode').exists() else 'fresh'
    debug = pathlib.Path(sys.argv[sys.argv.index('--debug-file') + 1])
    assert debug.is_fifo()
    stamp = '2026-10-26T03:00:00.000Z [DEBUG] '
    with debug.open('a') as log:
        if mode == 'snapshot':
            log.write(stamp + 'Usage read answered from a snapshot 30s old; endpoint not asked\n')
        elif mode != 'no-evidence':
            log.write(stamp + 'fetchUtilization: GET /api/oauth/usage (attempt 1)\n')
            if mode != 'fallback':
                log.write(stamp + 'fetchUtilization: 200 after 1 attempt(s)\n')
            if mode == 'invalid-body':
                log.write(stamp + 'Usage fetch returned a fieldless or non-object body (in-band error)\n')
    percent = int(account['accountUuid'][-1]) * 10
    print('\x1b[2J\x1b[HCurrent session\n  ' + str(percent) + '% used\nResets in 2 hr\n'
          'Current week (all models)\n  45% used\nResets Oct 28 at 8am', flush=True)
    time.sleep(20)
'''


class ParserTests(unittest.TestCase):
    def test_usage_evidence_accepts_split_official_markers_only(self):
        evidence = probe.UsageEvidence()
        data = (b'2026-10-26T03:00:00.000Z [DEBUG] fetchUtilization: GET /api/oauth/usage (attempt 1)\n'
                b'2026-10-26T03:00:00.100Z [DEBUG] fetchUtilization: 200 after 1 attempt(s)\n')
        for byte in data:
            evidence.feed(bytes([byte]))
        self.assertTrue(evidence.request)
        self.assertTrue(evidence.fresh)
        unrelated = probe.UsageEvidence()
        unrelated.feed(b'HTTP 200\n2026-10-26T03:00:00.000Z [DEBUG] unrelated: fetchUtilization: 200 after 1 attempt(s)\n')
        self.assertFalse(unrelated.fresh)

    def test_invalid_usage_body_overrides_successful_http_transport(self):
        evidence = probe.UsageEvidence()
        evidence.feed(b'2026-10-26T03:00:00.000Z [DEBUG] fetchUtilization: 200 after 1 attempt(s)\n'
                      b'2026-10-26T03:00:00.000Z [ERROR] Usage fetch returned a fieldless or non-object body (in-band error)\n')
        self.assertTrue(evidence.fresh)
        self.assertTrue(evidence.failed)

    def test_subscription_windows_exclude_model_and_insights_percentages(self):
        value = probe.usage(PANEL, 123)
        self.assertEqual((value['fh'], value['sd']), (21, 67))
        self.assertEqual(value['at'], 123)
        self.assertEqual(value['source'], 'claude-cli')
        self.assertEqual(value['fhResetText'], 'Resets in 2 hr 4 min')
        self.assertEqual(value['fhResetAt'], 123 + (2 * 3600 + 4 * 60) * 1000)
        self.assertTrue(value['fhResetEstimated'])

    def test_loading_or_context_only_is_not_a_current_reading(self):
        for text in ('Context 0% used', PANEL + '\nLoading usage data…', 'Current week (Sonnet only)\n90% used'):
            self.assertIsNone(probe.usage(text, 123))

    def test_background_activity_does_not_hide_a_complete_usage_panel(self):
        for activity in ('Downloading update…', 'Uploading session…', 'Reloading plugins',
                         'Loading plugins…', '正在加载插件', '加载历史会话'):
            with self.subTest(activity=activity):
                for text in (activity + '\n' + PANEL, PANEL + '\n' + activity):
                    value = probe.usage(text, 123)
                    self.assertEqual((value['fh'], value['sd']), (21, 67))

    def test_loading_inside_a_quota_block_rejects_stale_numbers(self):
        for loading in ('Loading…', 'Loading...', '加载中…', '正在加载中…'):
            text = PANEL.replace('  ███░░ 21% used', '  ███░░ 21% used\n  ' + loading)
            with self.subTest(loading=loading):
                self.assertIsNone(probe.usage(text, 123))
        self.assertIsNone(probe.usage(PANEL + '\n正在加载额度数据中…', 123))

    def test_errors_override_previously_rendered_percentages(self):
        for text, code in [('Failed to load usage data', 'CLI_USAGE_UNAVAILABLE'),
                           ('rate_limit_error', 'CLI_RATE_LIMITED'),
                           ('authentication_error', 'CLI_AUTH_REQUIRED'),
                           ('Do you trust the files in this folder?', 'CLI_WORKSPACE_TRUST_REQUIRED'),
                           ('Choose the text style', 'CLI_SETUP_REQUIRED')]:
            with self.subTest(code=code), self.assertRaisesRegex(probe.ProbeError, code):
                probe.usage(PANEL + '\n' + text, 123)

    def test_conflicting_or_out_of_range_numbers_are_refused(self):
        for bad in (PANEL.replace('21% used', '101% used'), PANEL.replace('21% used', '21% used\n22% used')):
            with self.assertRaisesRegex(probe.ProbeError, 'CLI_USAGE_UNRECOGNIZED'):
                probe.usage(bad, 123)

    def test_terminal_redraw_erases_old_percentages_and_replays_split_utf8(self):
        screen = probe.Screen()
        screen.feed(PANEL.encode())
        screen.feed(b'\x1b[2J\x1b[HCurrent session\r\n  8% used\r\nCurrent week (all models)\r\n  9% used')
        value = probe.usage(screen.text(), 123)
        self.assertEqual((value['fh'], value['sd']), (8, 9))
        screen.feed(b'\x1b[2J\x1b[H')
        data = '当前会话\r\n20% 已用'.encode()
        for byte in data: screen.feed(bytes([byte]))
        self.assertEqual(probe.usage(screen.text(), 123)['fh'], 20)

    def test_terminal_cursor_erase_and_osc_do_not_accumulate_history(self):
        screen = probe.Screen()
        screen.feed(b'Current session\r\n99% used\x1b[1G\x1b[2K3% used\x1b]8;;https://unused.invalid\a\x1b]8;;\x1b\\')
        self.assertEqual(probe.usage(screen.text(), 123)['fh'], 3)
        self.assertNotIn('https://', screen.text())

    def test_private_keyboard_queries_preserve_cursor_for_relative_usage_redraw(self):
        screen = probe.Screen()
        screen.feed(b'\x1b[11;1HCurrent session\r\n21% used\r\n'
                    b'Current week (all models)\r\n67% used')
        # The official CLI negotiates Kitty/terminal capabilities after the
        # first render, then updates the existing rows with relative moves.
        screen.feed(b'\x1b[>0q\x1b[?u\x1b[<u\x1b[?25h'
                    b'\x1b[2A\r\x1b[2K22% used')
        value = probe.usage(screen.text(), 123)
        self.assertEqual((value['fh'], value['sd']), (22, 67))

    def test_standard_save_restore_still_applies_to_terminal_cursor(self):
        screen = probe.Screen()
        screen.feed(b'Current session\r\n21% used\r\n\x1b[s'
                    b'Current week (all models)\r\n67% used')
        screen.feed(b'\x1b[u\x1b[1A\r\x1b[2K22% used')
        value = probe.usage(screen.text(), 123)
        self.assertEqual((value['fh'], value['sd']), (22, 67))


class ResetTests(unittest.TestCase):
    zone = ZoneInfo('America/Los_Angeles')
    now = int(datetime(2026, 10, 25, 6, 52, tzinfo=zone).timestamp() * 1000)

    def test_relative_duration_is_anchored_and_explicitly_estimated(self):
        for hint, seconds in [('Resets in 3 hr 38 min', 3 * 3600 + 38 * 60),
                              ('Resets in 2 days and 4 hours', 2 * 86400 + 4 * 3600),
                              ('Resets in 0 min', 0)]:
            with self.subTest(hint=hint):
                at, estimated = probe.reset_notice(hint, self.now, 'sd', self.zone)
                self.assertEqual(at, self.now + seconds * 1000)
                self.assertTrue(estimated)

    def test_five_hour_clock_and_explicit_time_zone(self):
        expected = int(datetime(2026, 10, 25, 10, 30, tzinfo=self.zone).timestamp() * 1000)
        self.assertEqual(probe.reset_notice('Resets 10:30am (America/Los_Angeles)', self.now, 'fh'),
                         (expected, False))
        self.assertEqual(probe.reset_notice('Resets 17:30 (UTC)', self.now, 'fh'), (expected, False))

    def test_five_hour_clock_can_cross_midnight(self):
        now = int(datetime(2026, 10, 25, 23, 30, tzinfo=self.zone).timestamp() * 1000)
        expected = int(datetime(2026, 10, 26, 1, tzinfo=self.zone).timestamp() * 1000)
        self.assertEqual(probe.reset_notice('Resets 1am', now, 'fh', self.zone), (expected, False))

    def test_weekly_month_date_uses_named_zone_or_local_zone(self):
        expected = int(datetime(2026, 10, 27, 20, tzinfo=self.zone).timestamp() * 1000)
        for hint in ('Resets Oct 27 at 8pm (America/Los_Angeles)', 'Resets October 27 at 20:00'):
            self.assertEqual(probe.reset_notice(hint, self.now, 'sd', self.zone), (expected, False))

    def test_weekday_notice_matches_claudes_compact_panel(self):
        expected = int(datetime(2026, 10, 27, 20, tzinfo=self.zone).timestamp() * 1000)
        for hint in ('Resets Tue 8:00 PM', 'Resets Tuesday at 8pm (America/Los_Angeles)'):
            self.assertEqual(probe.reset_notice(hint, self.now, 'sd', self.zone), (expected, False))

    def test_weekly_date_can_cross_year_boundary(self):
        now = int(datetime(2026, 12, 30, 12, tzinfo=self.zone).timestamp() * 1000)
        expected = int(datetime(2027, 1, 2, 8, tzinfo=self.zone).timestamp() * 1000)
        self.assertEqual(probe.reset_notice('Resets Jan 2 at 8am', now, 'sd', self.zone), (expected, False))

    def test_weekly_reset_uses_dst_offset_at_the_reset_date(self):
        now = int(datetime(2026, 10, 30, 9, tzinfo=self.zone).timestamp() * 1000)
        expected = int(datetime(2026, 11, 2, 20, tzinfo=self.zone).timestamp() * 1000)
        self.assertEqual(probe.reset_notice('Resets Nov 2 at 8pm', now, 'sd', self.zone), (expected, False))
        self.assertEqual(datetime.fromtimestamp(expected / 1000, timezone.utc).hour, 4)

    def test_nonexistent_and_ambiguous_dst_clock_times_are_not_guessed(self):
        for month, day, clock in [(3, 8, '2:30am'), (11, 1, '1:30am')]:
            now = int(datetime(2026, month, day, 0, 30, tzinfo=self.zone).timestamp() * 1000)
            self.assertEqual(probe.reset_notice('Resets ' + clock, now, 'fh', self.zone), (None, False))

    def test_complete_numeric_dates_and_iso_offsets(self):
        expected = int(datetime(2026, 10, 25, 10, 30, tzinfo=self.zone).timestamp() * 1000)
        for hint in ('Resets 2026/10/25 10:30:00', 'Resets 2026-10-25T17:30:00Z',
                     'Resets 2026-10-25T10:30:00-07:00'):
            self.assertEqual(probe.reset_notice(hint, self.now, 'fh', self.zone), (expected, False))

    def test_unknown_invalid_and_out_of_window_notices_remain_empty(self):
        for window, hint in [('fh', 'Resets in -2 hr'), ('fh', 'Resets in about 2 hr'),
                             ('fh', 'Resets in 6 hr'), ('sd', 'Resets in 8 days'),
                             ('fh', 'Resets 8pm'), ('sd', 'Resets 8pm'),
                             ('sd', 'Resets Oct 99 at 8am'), ('sd', 'Resets Feb 29 at 8am'),
                             ('sd', 'Resets Oct 27 at 8pm (Unknown/Zone)'),
                             ('sd', 'Server will reset eventually')]:
            with self.subTest(hint=hint):
                self.assertEqual(probe.reset_notice(hint, self.now, window, self.zone), (None, False))

    def test_both_windows_keep_their_own_reset_time(self):
        value = probe.usage(PANEL, self.now)
        self.assertEqual(value['fhResetAt'], self.now + (2 * 3600 + 4 * 60) * 1000)
        expected = int(datetime(2026, 10, 28, 8, tzinfo=self.zone).timestamp() * 1000)
        self.assertEqual(value['sdResetAt'], expected)
        self.assertTrue(value['fhResetEstimated'])
        self.assertFalse(value['sdResetEstimated'])


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='case-usage-offline-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.binary = self.root / 'fake-cli'
        self.binary.write_text(FAKE_CLI)
        self.binary.chmod(0o700)

    def account(self, index=1):
        config, workspace = self.root / str(index) / 'cli', self.root / str(index) / 'workspace'
        config.mkdir(parents=True); workspace.mkdir()
        identity = {'accountUuid': f'account-{index}', 'organizationUuid': f'org-{index}'}
        (config / '.claude.json').write_text(json.dumps({'oauthAccount': identity}))
        # Deliberately unreadable synthetic credential file: CASE must not open it.
        (config / '.credentials.json').write_text('not a credential')
        (config / '.credentials.json').chmod(0)
        return {'binary': str(self.binary), 'config': str(config), 'workspace': str(workspace),
                'accountHash': probe.digest(identity['accountUuid']),
                'organizationHash': probe.digest(identity['organizationUuid'])}

    def authorization(self, index=1):
        payload = self.account(index)
        base = Path(payload['config']).parent
        runtime = self.root / 'fake-electron'
        runtime.write_text('''#!/usr/bin/python3
import json, os, pathlib, sys, time
data = json.load(sys.stdin)
assert 'BROWSER' not in os.environ and 'CASE_AUTH_SOCKET' not in os.environ
assert 'NODE_OPTIONS' not in os.environ and 'ELECTRON_RUN_AS_NODE' not in os.environ
pathlib.Path(data['userData']).with_suffix('.marker').write_text(json.dumps({'pid': os.getpid(), 'userData': data['userData']}))
print('CASE_AUTH_READY', flush=True)
time.sleep(60)
''')
        runtime.chmod(0o700)
        helper, opener = base / 'auth-browser.cjs', base / 'browser.command'
        helper.write_text('// synthetic browser')
        opener.write_text('#!/bin/zsh\nexec /usr/bin/python3 -I ' + shlex.quote(str(SCRIPT)) + ' browser "$@"\n')
        opener.chmod(0o700)
        payload['browser'] = {'binary': str(runtime), 'script': str(helper), 'opener': str(opener),
                              'userData': str(base / 'browser'), 'label': f'Synthetic {index}'}
        self.binary.write_text('''#!/usr/bin/python3
import os, subprocess, sys
assert sys.argv[1:] == ['auth', 'login', '--claudeai']
assert 'BROWSER' in os.environ and 'CASE_AUTH_SOCKET' in os.environ
assert 'NODE_OPTIONS' not in os.environ
url = 'https://claude.com/cai/oauth/authorize?redirect_uri=http%3A%2F%2Flocalhost%3A12345%2Fcallback&state=synthetic'
sys.exit(subprocess.call([os.environ['BROWSER'], url]))
''')
        return payload

    def test_three_login_flows_route_to_distinct_electron_profiles_and_reap_only_their_children(self):
        accounts = [self.authorization(i) for i in range(1, 4)]
        with mock.patch.object(probe, 'setup', return_value=0) as setup, mock.patch('builtins.print'), \
                mock.patch.dict(os.environ, {'BROWSER': '/nonexistent/default-browser',
                                             'NODE_OPTIONS': 'synthetic', 'ELECTRON_RUN_AS_NODE': '1'}):
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                results = list(pool.map(probe.login, accounts))
        self.assertEqual(results, [0, 0, 0])
        self.assertEqual(setup.call_count, 3)
        profiles = []
        for payload in accounts:
            marker = json.loads(Path(payload['browser']['userData']).with_suffix('.marker').read_text())
            profiles.append(marker['userData'])
            with self.assertRaises(ProcessLookupError): os.kill(marker['pid'], 0)
            for item in Path(payload['config']).parent.iterdir():
                if item.is_file(): self.assertNotIn('state=synthetic', item.read_text())
        self.assertEqual(len(set(profiles)), 3)

    def test_wrong_account_login_still_fails_identity_check_after_isolated_browser(self):
        payload = self.authorization()
        payload['accountHash'] = probe.digest('different-account')
        with mock.patch('builtins.print'), self.assertRaisesRegex(probe.ProbeError, 'CLI_IDENTITY_MISMATCH'):
            probe.login(payload)

    def test_missing_electron_never_starts_login_or_falls_back_to_default_browser(self):
        payload = self.authorization()
        payload['browser']['binary'] = str(self.root / 'missing-electron')
        with mock.patch.object(probe.subprocess, 'Popen') as call, mock.patch('builtins.print'), \
                self.assertRaisesRegex(probe.ProbeError, 'AUTH_BROWSER_NOT_INSTALLED'):
            probe.login(payload)
        call.assert_not_called()

    def test_second_authorization_for_same_scope_is_refused(self):
        payload = self.authorization()
        with probe.AuthBrowser(payload), self.assertRaisesRegex(probe.ProbeError, 'AUTH_IN_PROGRESS'):
            with probe.AuthBrowser(payload): self.fail('concurrent login accepted')

    def test_authorization_cannot_redirect_to_another_accounts_browser_directory(self):
        first, second = self.authorization(1), self.authorization(2)
        for value in [second['browser']['userData'], str(self.root / '1' / 'browser-link')]:
            if value.endswith('browser-link'): Path(value).symlink_to(second['browser']['userData'])
            bad = dict(first, browser=dict(first['browser'], userData=value))
            with self.assertRaisesRegex(probe.ProbeError, 'CLI_SCOPE_INVALID'):
                with probe.AuthBrowser(bad): self.fail('redirected browser accepted')

    def test_authorization_url_rejects_external_origins_and_callbacks(self):
        valid = 'https://claude.com/cai/oauth/authorize?redirect_uri=http%3A%2F%2Flocalhost%3A12345%2Fcallback&state=synthetic'
        self.assertEqual(probe.authorization_url(valid), valid)
        for value in [valid.replace('claude.com', 'claude.com.example.org'),
                      valid.replace('https:', 'http:', 1), valid.replace('localhost', 'example.org'),
                      valid.replace('%2Fcallback', '%2Fother'), valid.replace('&state=synthetic', ''),
                      valid + '&redirect_uri=http%3A%2F%2Flocalhost%3A12345%2Fcallback',
                      valid.replace('claude.com', 'user@claude.com'), 'file:///tmp/login', valid + '\n']:
            with self.subTest(value=value), self.assertRaisesRegex(probe.ProbeError, 'AUTH_BROWSER_URL_INVALID'):
                probe.authorization_url(value)

    def test_browser_start_failure_is_explicit_and_does_not_open_system_browser(self):
        payload = self.authorization()
        Path(payload['browser']['binary']).write_text('#!/usr/bin/python3\nraise SystemExit(1)\n')
        with mock.patch('builtins.print'), self.assertRaisesRegex(probe.ProbeError, 'AUTH_BROWSER_FAILED'):
            probe.login(payload)

    def test_three_closed_accounts_use_distinct_official_cli_scopes(self):
        accounts = [self.account(i) for i in range(1, 4)]
        with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'synthetic', 'ANTHROPIC_BASE_URL': 'https://unused.invalid',
                                         'CLAUDE_CODE_OAUTH_TOKEN': 'synthetic', 'NODE_OPTIONS': 'synthetic'}):
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                values = list(pool.map(probe.probe, accounts))
        self.assertEqual([v['fh'] for v in values], [10, 20, 30])
        self.assertEqual([v['sd'] for v in values], [45, 45, 45])
        for account in accounts:
            self.assertEqual((Path(account['config']) / 'query-count').read_text(), '1')

    def test_query_without_a_shortcuts_footer_never_types_a_keyboard_command(self):
        payload = self.account()
        with mock.patch.object(probe.os, 'write', wraps=os.write) as write:
            value = probe.probe(payload)
        self.assertEqual((value['fh'], value['sd']), (10, 45))
        write.assert_not_called()

    def test_query_scope_overrides_ambient_fullscreen_and_auth_injection(self):
        payload = self.account()
        with mock.patch.dict(os.environ, {'CLAUDE_CODE_NO_FLICKER': '1',
                                         'CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN': '0',
                                         'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
                                         'DISABLE_TELEMETRY': '0', 'DISABLE_ERROR_REPORTING': '0',
                                         'DO_NOT_TRACK': '0',
                                         'CLAUDE_CODE_OAUTH_TOKEN': 'synthetic',
                                         'ANTHROPIC_BASE_URL': 'https://unused.invalid'}):
            value = probe.probe(payload)
        self.assertEqual((value['fh'], value['sd']), (10, 45))

    def test_failed_official_fetch_never_publishes_rendered_fallback_percentages(self):
        payload = self.account()
        config = Path(payload['config'])
        for mode in ('fallback', 'invalid-body', 'no-evidence'):
            (config / 'query-mode').write_text(mode)
            with self.subTest(mode=mode), self.assertRaisesRegex(probe.ProbeError, 'CLI_USAGE_UNAVAILABLE'):
                probe.probe(payload)

    def test_official_short_snapshot_keeps_its_original_observation_time(self):
        payload = self.account()
        config = Path(payload['config'])
        data = json.loads((config / '.claude.json').read_text())
        at = int(time.time() * 1000) - 30000
        data['cachedUsageUtilization'] = {'accountUuid': 'account-1', 'fetchedAtMs': at}
        (config / '.claude.json').write_text(json.dumps(data))
        (config / 'query-mode').write_text('snapshot')
        value = probe.probe(payload)
        self.assertEqual(value['at'], at)
        self.assertGreaterEqual(value['age'], 30000)
        self.assertEqual(value['fhResetAt'], at + 2 * 3600 * 1000)

    def test_missing_old_future_or_other_account_cache_cannot_validate_a_snapshot(self):
        payload = self.account()
        config = Path(payload['config'])
        data = json.loads((config / '.claude.json').read_text())
        now = int(time.time() * 1000)
        for account, at in [('account-1', now - 61000), ('account-1', now + 60000),
                            ('other-account', now - 1000), ('account-1', True)]:
            data['cachedUsageUtilization'] = {'accountUuid': account, 'fetchedAtMs': at}
            (config / '.claude.json').write_text(json.dumps(data))
            self.assertIsNone(probe.cached_observation_at(config, now))
        data.pop('cachedUsageUtilization')
        (config / '.claude.json').write_text(json.dumps(data))
        (config / 'query-mode').write_text('snapshot')
        with self.assertRaisesRegex(probe.ProbeError, 'CLI_USAGE_UNAVAILABLE'):
            probe.probe(payload)

    def test_diagnostic_fifo_is_private_and_removed_after_query(self):
        payload = self.account()
        spawned, real_popen = [], subprocess.Popen
        def start(args, **kwargs):
            spawned.append(args)
            return real_popen(args, **kwargs)
        with mock.patch.object(probe.subprocess, 'Popen', side_effect=start):
            probe.probe(payload)
        cli = next(args for args in spawned if '--debug-file' in args)
        pipe = Path(cli[cli.index('--debug-file') + 1])
        self.assertFalse(pipe.exists())
        self.assertFalse(pipe.parent.exists())

    def test_account_or_org_mismatch_never_starts_a_usage_probe(self):
        payload = self.account()
        for key in ('accountHash', 'organizationHash'):
            bad = dict(payload, **{key: probe.digest('different')})
            with mock.patch.object(probe.subprocess, 'run') as run:
                with self.assertRaisesRegex(probe.ProbeError, 'CLI_IDENTITY_MISMATCH'): probe.probe(bad)
                run.assert_not_called()

    def test_auth_status_rejects_global_scope_api_keys_and_other_orgs(self):
        payload = self.account()
        config = Path(payload['config'])
        expected = (payload['accountHash'], payload['organizationHash'])
        status = {'loggedIn': True, 'authMethod': 'claude.ai', 'apiProvider': 'firstParty', 'orgId': 'org-1'}
        for mutation in ({'authMethod': 'api_key'}, {'orgId': 'other'}, {'configDirectory': str(self.root)}, {'loggedIn': False}):
            with self.subTest(mutation=mutation), self.assertRaises(probe.ProbeError):
                probe.check_auth(json.dumps(dict(status, **mutation)), config, expected)

    def test_identity_change_during_capture_discards_result(self):
        payload = self.account()
        config = Path(payload['config'])
        status = {'loggedIn': True, 'authMethod': 'claude.ai', 'apiProvider': 'firstParty', 'orgId': 'org-1'}
        def change(*_):
            (config / '.claude.json').write_text('{"oauthAccount":{"accountUuid":"other","organizationUuid":"org-1"}}')
            return probe.usage(PANEL, 123)
        result = subprocess.CompletedProcess([], 0, json.dumps(status).encode())
        with mock.patch.object(probe.subprocess, 'run', return_value=result), mock.patch.object(probe, 'capture', side_effect=change):
            with self.assertRaisesRegex(probe.ProbeError, 'CLI_IDENTITY_MISMATCH'): probe.probe(payload)

    def test_auth_timeout_is_explicit_and_never_reads_a_durable_usage_cache(self):
        payload = self.account()
        with mock.patch.object(probe.subprocess, 'run', side_effect=subprocess.TimeoutExpired('fake-cli', 8)):
            with self.assertRaisesRegex(probe.ProbeError, 'REFRESH_TIMEOUT'): probe.probe(payload)

    def test_capture_timeout_terminates_our_new_child(self):
        payload = self.account()
        children, real_popen = [], subprocess.Popen
        def spawn(*args, **kwargs):
            child = real_popen(*args, **kwargs)
            children.append(child)
            return child
        with mock.patch.object(probe.subprocess, 'Popen', side_effect=spawn):
            with self.assertRaisesRegex(probe.ProbeError, 'REFRESH_TIMEOUT'):
                probe.capture(str(self.binary), Path(payload['config']), Path(payload['workspace']), time.monotonic() + .2)
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())

    def test_config_symlink_is_refused_before_auth(self):
        payload = self.account()
        link = self.root / 'redirected'; link.symlink_to(payload['config'])
        payload['config'] = str(link)
        with self.assertRaisesRegex(probe.ProbeError, 'CLI_SCOPE_INVALID'): probe.probe(payload)

    def test_visible_setup_reuses_login_without_rebinding_or_accepting_trust(self):
        payload = self.account()
        config = Path(payload['config'])
        metadata_before = (config / '.claude.json').read_bytes()
        binding = config.parent / 'binding.json'
        binding.write_text(json.dumps({'accountHash': payload['accountHash'],
                                       'organizationHash': payload['organizationHash'], 'revision': 'unchanged'}))
        binding_before = binding.read_bytes()
        status = {'loggedIn': True, 'authMethod': 'claude.ai', 'apiProvider': 'firstParty', 'orgId': 'org-1'}
        result = subprocess.CompletedProcess([], 0, json.dumps(status).encode())
        with mock.patch.object(probe.subprocess, 'run', return_value=result), \
                mock.patch.object(probe.subprocess, 'call', return_value=0) as call, \
                mock.patch('builtins.print'):
            self.assertEqual(probe.setup(payload), 0)
        call.assert_called_once()
        args, kwargs = call.call_args
        self.assertEqual(args[0], probe.cli_arguments(payload['binary']) + ['/usage'])
        self.assertEqual(kwargs['cwd'], Path(payload['workspace']))
        self.assertEqual(kwargs['env']['CLAUDE_CONFIG_DIR'], str(config))
        self.assertEqual(binding.read_bytes(), binding_before)
        self.assertEqual((config / '.claude.json').read_bytes(), metadata_before)

    def test_setup_refuses_other_account_before_opening_interactive_cli(self):
        payload = self.account()
        payload['accountHash'] = probe.digest('other-account')
        with mock.patch.object(probe.subprocess, 'run') as run, \
                mock.patch.object(probe.subprocess, 'call') as call:
            with self.assertRaisesRegex(probe.ProbeError, 'CLI_IDENTITY_MISMATCH'):
                probe.setup(payload)
        run.assert_not_called()
        call.assert_not_called()

    def test_reopening_three_account_clis_keeps_each_scope_and_binding(self):
        accounts = [self.account(i) for i in range(1, 4)]
        calls = []
        def auth(args, **kwargs):
            self.assertEqual(args[1:], ['auth', 'status', '--json'])
            config = Path(kwargs['env']['CLAUDE_CONFIG_DIR'])
            identity = json.loads((config / '.claude.json').read_text())['oauthAccount']
            return subprocess.CompletedProcess(args, 0, json.dumps({
                'loggedIn': True, 'authMethod': 'claude.ai', 'apiProvider': 'firstParty',
                'orgId': identity['organizationUuid'], 'configDirectory': str(config)}).encode())
        def interactive(args, **kwargs):
            self.assertEqual(args, probe.cli_arguments(str(self.binary)) + ['/usage'])
            self.assertNotIn('BROWSER', kwargs['env'])
            self.assertNotIn('CASE_AUTH_SOCKET', kwargs['env'])
            calls.append((kwargs['env']['CLAUDE_CONFIG_DIR'], str(kwargs['cwd'])))
            return 0
        before = {}
        for payload in accounts:
            base = Path(payload['config']).parent
            binding = base / 'binding.json'
            binding.write_text(json.dumps(dict(payload, revision='unchanged')))
            for path in (binding, Path(payload['config']) / '.claude.json'):
                before[path] = path.read_bytes()
        with mock.patch.object(probe.subprocess, 'run', side_effect=auth), \
                mock.patch.object(probe.subprocess, 'call', side_effect=interactive), \
                mock.patch.object(probe, 'login', side_effect=AssertionError('must not log in')), \
                mock.patch('builtins.print'):
            for payload in accounts * 2:
                self.assertEqual(probe.setup(payload), 0)
        self.assertEqual(calls, [(p['config'], p['workspace']) for p in accounts] * 2)
        self.assertTrue(all(path.read_bytes() == value for path, value in before.items()))

    def test_first_folder_trust_stops_probe_without_sending_a_command_or_confirmation(self):
        payload = self.account()
        self.binary.write_text('''#!/usr/bin/python3
import pathlib, sys, time
print("Quick safety check: Is this a project you created or one you trust?\\nYes, I trust this folder\\nEnter to confirm", flush=True)
line = sys.stdin.readline()
pathlib.Path(__file__).with_name('input-received').write_text(line)
time.sleep(20)
''')
        children, real_popen = [], subprocess.Popen
        def spawn(*args, **kwargs):
            child = real_popen(*args, **kwargs)
            children.append(child)
            return child
        with mock.patch.object(probe.subprocess, 'Popen', side_effect=spawn):
            with self.assertRaisesRegex(probe.ProbeError, 'CLI_WORKSPACE_TRUST_REQUIRED'):
                probe.capture(str(self.binary), Path(payload['config']), Path(payload['workspace']), time.monotonic() + 3)
        self.assertFalse((self.root / 'input-received').exists())
        self.assertIsNotNone(children[0].poll())


if __name__ == '__main__':
    unittest.main()
