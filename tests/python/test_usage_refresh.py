import importlib.util
from io import BytesIO
from pathlib import Path
import unittest
from unittest import mock
import urllib.error
from email.message import Message

SCRIPT = Path(__file__).resolve().parents[2] / 'support/usage_refresh.py'
spec = importlib.util.spec_from_file_location('usage_refresh_test', SCRIPT)
usage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(usage)


class UsageTests(unittest.TestCase):
    def test_missing_limits_are_unknown_and_reset_times_require_a_timezone(self):
        value = usage.normalize({'five_hour': {'utilization': 17.4,
                                 'resets_at': '2026-10-23T10:00:00Z'}})
        self.assertEqual((value['fh'], value['sd']), (17, None))
        self.assertEqual(value['fhResetAt'], 1792749600000)
        self.assertIsNone(usage.reset_time('2026-10-23T10:00:00'))
        self.assertIsNone(usage.percent(True))
        with self.assertRaisesRegex(usage.UsageError, 'NO_QUOTA_DATA'):
            usage.normalize({'five_hour': {'utilization': float('nan')}})

    def query(self, opener):
        with mock.patch.object(usage.sys, 'platform', 'darwin'), mock.patch.object(
                usage, 'credentials', return_value=('synthetic-private-cookie',
                '11111111-1111-4111-8111-111111111111')):
            return usage.refresh(Path('/synthetic/profile'), opener)

    def test_errors_never_echo_the_response_or_credentials(self):
        headers = Message()
        for challenge in (False, True):
            if challenge:
                headers['cf-mitigated'] = 'challenge'
            error = urllib.error.HTTPError('https://claude.ai/private', 403,
                       'synthetic-private-cookie', headers, BytesIO(b'private response'))
            opener = mock.Mock()
            opener.open.side_effect = error
            code = 'BROWSER_VERIFICATION_REQUIRED' if challenge else 'HTTP_403'
            with self.assertRaises(usage.UsageError) as context:
                self.query(opener)
            self.assertEqual(str(context.exception), code)

    def test_redirects_cannot_forward_a_session_cookie_to_another_origin(self):
        with self.assertRaisesRegex(usage.UsageError, 'REDIRECT_REFUSED'):
            usage.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.invalid')
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.geturl.return_value = 'https://other.invalid'
        opener = mock.Mock()
        opener.open.return_value = response
        with self.assertRaisesRegex(usage.UsageError, 'REDIRECT_REFUSED'):
            self.query(opener)
        response.read.assert_not_called()

    def test_only_the_fixed_usage_endpoint_is_requested(self):
        opener = mock.Mock()
        opener.open.side_effect = urllib.error.URLError('private details')
        with self.assertRaisesRegex(usage.UsageError, '^NETWORK_ERROR$'):
            self.query(opener)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url,
           'https://claude.ai/api/organizations/11111111-1111-4111-8111-111111111111/usage?skip_spend=1')
        self.assertEqual(request.get_method(), 'GET')
        self.assertEqual(request.get_header('User-agent'), 'CASE/4.2.0')


if __name__ == '__main__':
    unittest.main()
