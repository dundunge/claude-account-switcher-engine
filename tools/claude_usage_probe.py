#!/usr/bin/env python3
"""Read the unmodified official CLI's /usage. Never read credential stores.

Embedded in CASE at build time. Only CASE-owned CLAUDE_CONFIG_DIR metadata is
read; Claude itself handles login, tokens and network access. Raw terminal
captures and auth output never leave this process.
"""
import codecs
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import select
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ProbeError(Exception):
    pass


def environment(config, workspace):
    # An allowlist prevents ambient API keys, gateways, injected account UUIDs,
    # NODE_OPTIONS, shell hooks and alternate credential helpers crossing scopes.
    allowed = {'HOME', 'USER', 'LOGNAME', 'PATH', 'TMPDIR', 'TZ', 'HTTP_PROXY',
               'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY', 'http_proxy',
               'https_proxy', 'all_proxy', 'no_proxy'}
    env = {k: v for k, v in os.environ.items() if k in allowed}
    # The broad nonessential-traffic switch also blocks /api/oauth/usage in
    # current official CLI versions. Disable telemetry/reporting individually.
    env.update(CLAUDE_CONFIG_DIR=str(config), PWD=str(workspace),
               TERM='xterm-256color', LANG='en_US.UTF-8', LC_ALL='en_US.UTF-8',
               DISABLE_AUTOUPDATER='1', DISABLE_TELEMETRY='1',
               DISABLE_ERROR_REPORTING='1', DO_NOT_TRACK='1',
               CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN='1')
    return env


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def metadata_identity(config):
    # Both official config locations are supported. If both exist, the CLI's
    # legacy .config.json takes precedence. Neither is a credential file.
    file = config / '.config.json'
    if not file.exists():
        file = config / '.claude.json'
    if file.is_symlink() or not file.is_file() or file.stat().st_size > 1024 * 1024:
        raise ProbeError('CLI_AUTH_REQUIRED')
    try:
        account = json.loads(file.read_text()).get('oauthAccount', {})
        values = [account.get('accountUuid'), account.get('organizationUuid')]
        if not all(isinstance(v, str) and v for v in values):
            raise ValueError()
        return tuple(digest(v) for v in values)
    except (ValueError, AttributeError, TypeError):
        raise ProbeError('CLI_IDENTITY_UNAVAILABLE') from None


def check_auth(output, config, expected):
    try:
        status = json.loads(output)
    except (ValueError, TypeError):
        raise ProbeError('CLI_AUTH_STATUS_INVALID') from None
    if not isinstance(status, dict):
        raise ProbeError('CLI_AUTH_STATUS_INVALID')
    if status.get('loggedIn') is not True or status.get('authMethod') != 'claude.ai':
        raise ProbeError('CLI_AUTH_REQUIRED')
    if status.get('apiProvider') != 'firstParty':
        raise ProbeError('CLI_AUTH_REQUIRED')
    directory = status.get('configDirectory')
    if directory is not None and Path(directory).resolve() != config.resolve():
        raise ProbeError('CLI_IDENTITY_MISMATCH')
    organization = status.get('orgId')
    if not isinstance(organization, str) or digest(organization) != expected[1]:
        raise ProbeError('CLI_IDENTITY_MISMATCH')
    if metadata_identity(config) != expected:
        raise ProbeError('CLI_IDENTITY_MISMATCH')


class Screen:
    """Bounded VT screen: redraws replace cells, never append old percentages."""
    def __init__(self, rows=200, columns=160):
        self.rows, self.columns = rows, columns
        self.lines = [[' '] * columns for _ in range(rows)]
        self.row = self.col = 0
        self.saved = (0, 0)
        self.state, self.sequence = 'text', ''
        self.decoder = codecs.getincrementaldecoder('utf-8')('replace')

    def newline(self):
        self.col = 0
        self.row += 1
        if self.row >= self.rows:
            self.lines.pop(0)
            self.lines.append([' '] * self.columns)
            self.row = self.rows - 1

    def csi(self, sequence, command):
        # Private terminal protocols do not use ANSI cursor semantics. In
        # particular, Kitty's CSI ? u query must not restore the saved cursor;
        # doing so corrupts the official CLI's subsequent relative redraws.
        if sequence.startswith(('?', '>', '<', '=')):
            return
        try:
            parts = [int(p or 0) for p in sequence.split(';')]
        except ValueError:
            return
        n = (parts or [0])[0]
        amount = n or 1
        if command in 'Hf':
            self.row = min(self.rows - 1, max(0, amount - 1))
            self.col = min(self.columns - 1, max(0, (parts[1] if len(parts) > 1 else 1) - 1))
        elif command == 'A': self.row = max(0, self.row - amount)
        elif command == 'B': self.row = min(self.rows - 1, self.row + amount)
        elif command == 'C': self.col = min(self.columns - 1, self.col + amount)
        elif command == 'D': self.col = max(0, self.col - amount)
        elif command == 'G': self.col = min(self.columns - 1, amount - 1)
        elif command == 'd': self.row = min(self.rows - 1, amount - 1)
        elif command == 'E': self.row, self.col = min(self.rows - 1, self.row + amount), 0
        elif command == 'F': self.row, self.col = max(0, self.row - amount), 0
        elif command == 's': self.saved = (self.row, self.col)
        elif command == 'u': self.row, self.col = self.saved
        elif command == 'J':
            if n in (2, 3):
                self.lines = [[' '] * self.columns for _ in range(self.rows)]
            elif n == 0:
                self.lines[self.row][self.col:] = [' '] * (self.columns - self.col)
                for r in range(self.row + 1, self.rows): self.lines[r] = [' '] * self.columns
            elif n == 1:
                for r in range(self.row): self.lines[r] = [' '] * self.columns
                self.lines[self.row][:self.col + 1] = [' '] * (self.col + 1)
        elif command == 'K':
            start, end = (0, self.columns) if n == 2 else ((0, self.col + 1) if n == 1 else (self.col, self.columns))
            self.lines[self.row][start:end] = [' '] * (end - start)
        elif command == 'X':
            end = min(self.columns, self.col + amount)
            self.lines[self.row][self.col:end] = [' '] * (end - self.col)
        elif command == 'P':
            line = self.lines[self.row]
            del line[self.col:min(self.columns, self.col + amount)]
            line.extend([' '] * (self.columns - len(line)))
        elif command in 'ST':
            for _ in range(min(amount, self.rows)):
                if command == 'S': self.lines.pop(0); self.lines.append([' '] * self.columns)
                else: self.lines.pop(); self.lines.insert(0, [' '] * self.columns)

    def feed(self, data):
        for char in self.decoder.decode(data):
            if self.state == 'osc':
                if char == '\a': self.state = 'text'
                elif char == '\x1b': self.state = 'osc_end'
            elif self.state == 'osc_end':
                self.state = 'text' if char == '\\' else 'osc'
            elif self.state == 'esc':
                if char == '[': self.state, self.sequence = 'csi', ''
                elif char == ']': self.state = 'osc'
                else:
                    if char == '7': self.saved = (self.row, self.col)
                    if char == '8': self.row, self.col = self.saved
                    self.state = 'text'
            elif self.state == 'csi':
                if '@' <= char <= '~':
                    self.csi(self.sequence, char)
                    self.state = 'text'
                elif len(self.sequence) < 100: self.sequence += char
                else: self.state = 'text'
            elif char == '\x1b': self.state = 'esc'
            elif char == '\r': self.col = 0
            elif char == '\n': self.newline()
            elif char == '\b': self.col = max(0, self.col - 1)
            elif char == '\t': self.col = min(self.columns - 1, (self.col // 8 + 1) * 8)
            elif char >= ' ' and char != '\x7f' and not unicodedata.combining(char):
                width = 2 if unicodedata.east_asian_width(char) in ('W', 'F') else 1
                if self.col + width > self.columns: self.newline()
                # An empty continuation cell preserves terminal width without
                # inserting a visible space into Chinese labels when rendered.
                if self.lines[self.row][self.col] == '' and self.col > 0:
                    self.lines[self.row][self.col - 1] = ' '
                if self.col + 1 < self.columns and self.lines[self.row][self.col + 1] == '':
                    self.lines[self.row][self.col + 1] = ' '
                self.lines[self.row][self.col] = char
                if width == 2: self.lines[self.row][self.col + 1] = ''
                self.col += width

    def text(self):
        return '\n'.join(''.join(line).rstrip() for line in self.lines).strip()


def local_timezone():
    name = os.environ.get('TZ', '').lstrip(':')
    if name:
        try:
            return ZoneInfo(name)
        except (ValueError, ZoneInfoNotFoundError):
            pass
    # A fixed "current UTC offset" would be wrong for a weekly reset that
    # crosses daylight saving time. Read the system's transition table.
    with open('/etc/localtime', 'rb') as file:
        return ZoneInfo.from_file(file)


def reset_notice(text, now, window, local=None):
    """Return (epoch milliseconds, estimated); unknown/ambiguous stays empty."""
    if not text:
        return None, False
    limit = (5 * 3600 if window == 'fh' else 7 * 86400) * 1000
    body = re.sub(r'^(?:Resets?\b|重置(?:时间|于)?)\s*[:：]?\s*', '', text.strip(), flags=re.I)
    relative = re.fullmatch(r'in\s+(.+)', body, re.I)
    if relative:
        units = {'d': 86400, 'h': 3600, 'm': 60, 's': 1}
        token = re.compile(r'(\d+)\s*(days?|d|hours?|hrs?|hr|h|minutes?|mins?|min|m|seconds?|secs?|sec|s)\b', re.I)
        duration, found = 0, False
        for match in token.finditer(relative[1]):
            duration += int(match[1]) * units[match[2][0].lower()]
            found = True
        residue = token.sub('', relative[1])
        if found and re.fullmatch(r'[\s,]*(?:and[\s,]*)?', residue, re.I) and 0 <= duration * 1000 <= limit:
            return now + duration * 1000, True
        return None, False
    try:
        zone_match = re.search(r'\s*\(([^()]+)\)$', body)
        zone = ZoneInfo(zone_match[1]) if zone_match else (local or local_timezone())
        if zone_match:
            body = body[:zone_match.start()].strip()
        observed = datetime.fromtimestamp(now / 1000, zone)
        dates = []
        # Explicit dates (including ISO offsets) do not need day/year inference.
        if re.fullmatch(r'\d{4}[-/]\d{1,2}[-/]\d{1,2}[ T]\d{1,2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?', body):
            normalized = body.replace('/', '-').replace('Z', '+00:00')
            # fromisoformat requires zero-padded date fields on Python 3.9.
            match = re.match(r'(\d{4})-(\d{1,2})-(\d{1,2})(.*)', normalized)
            dates = [datetime.fromisoformat(f'{int(match[1]):04}-{int(match[2]):02}-{int(match[3]):02}{match[4]}')]
        else:
            month = re.fullmatch(r'([A-Za-z]+)\s+(\d{1,2})(?:,?\s+(\d{4}))?\s+at\s+(.+)', body, re.I)
            weekday = re.fullmatch(r'(Monday|Mon|Tuesday|Tue|Wednesday|Wed|Thursday|Thu|Friday|Fri|Saturday|Sat|Sunday|Sun)\s+(?:at\s+)?(.+)', body, re.I)
            clock = month[4] if month else weekday[2] if weekday else re.sub(r'^at\s+', '', body, flags=re.I)
            time_match = re.fullmatch(r'(\d{1,2})(?::(\d{2}))?\s*([ap]m)?', clock, re.I)
            if not time_match or (not time_match[2] and not time_match[3]):
                return None, False
            hour, minute = int(time_match[1]), int(time_match[2] or 0)
            if time_match[3]:
                if not 1 <= hour <= 12:
                    return None, False
                hour = hour % 12 + (12 if time_match[3].lower() == 'pm' else 0)
            if month:
                months = ['january', 'february', 'march', 'april', 'may', 'june',
                          'july', 'august', 'september', 'october', 'november', 'december']
                month_number = next((i + 1 for i, name in enumerate(months) if month[1].lower() in (name, name[:3])), None)
                if not month_number:
                    return None, False
                years = [int(month[3])] if month[3] else [observed.year, observed.year + 1]
                for year in years:
                    try:
                        dates.append(datetime(year, month_number, int(month[2]), hour, minute))
                    except ValueError:
                        continue
            elif weekday and window == 'sd':
                day_index = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'].index(weekday[1][:3].lower())
                offset = (day_index - observed.weekday()) % 7
                for days in (offset, offset + 7):
                    day = observed.date() + timedelta(days=days)
                    dates.append(datetime(day.year, day.month, day.day, hour, minute))
            elif not weekday and window == 'fh':
                for days in (0, 1):
                    day = observed.date() + timedelta(days=days)
                    dates.append(datetime(day.year, day.month, day.day, hour, minute))
            else:
                return None, False
        candidates = set()
        for value in dates:
            if value.tzinfo:
                candidates.add(int(value.timestamp() * 1000))
                continue
            for fold in (0, 1):
                aware = value.replace(tzinfo=zone, fold=fold)
                # Reject nonexistent local times and ambiguous fall-back hours.
                if aware.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == value:
                    candidates.add(int(aware.timestamp() * 1000))
        candidates = {value for value in candidates if -60000 <= value - now <= limit + 60000}
        return (candidates.pop(), False) if len(candidates) == 1 else (None, False)
    except (ValueError, OverflowError, OSError, ZoneInfoNotFoundError):
        return None, False


def usage(text, now):
    lower = text.lower()
    if any(s in lower for s in ('not logged in', 'please run /login', 'authentication_error', 'token expired')):
        raise ProbeError('CLI_AUTH_REQUIRED')
    if any(s in lower for s in ('rate_limit_error', 'rate limited', 'too many requests')):
        raise ProbeError('CLI_RATE_LIMITED')
    if any(s in lower for s in ('failed to load usage', 'error fetching usage')):
        raise ProbeError('CLI_USAGE_UNAVAILABLE')
    if any(s in lower for s in ('do you trust', 'quick safety check', 'ready to code here?')):
        raise ProbeError('CLI_WORKSPACE_TRUST_REQUIRED')
    if any(s in lower for s in ('choose the text style', 'select the text style', 'browser didn’t open')):
        raise ProbeError('CLI_SETUP_REQUIRED')
    # A quota-specific loading notice invalidates old numbers on the screen.
    # Background downloads/uploads/plugin reloads are unrelated to this panel.
    if re.search(r'^\s*(?:loading\s+(?:usage(?:\s+data)?|quota)|(?:正在)?加载(?:额度|用量)(?:数据)?(?:中)?)[\s.…]*$', text, re.I | re.M):
        return None
    # Model-specific limits and context/insights percentages never substitute
    # for either subscription window. Read only the labelled block.
    label = re.compile(r'^\s*(Current session|Current week\s*\(all models\)|Current week\s*\([^)]*\)|当前会话|本周\s*[（(]所有模型[）)])\s*$', re.I)
    result = {'at': now, 'age': 0, 'source': 'claude-cli', 'fh': None, 'sd': None,
              'fhResetAt': None, 'sdResetAt': None, 'fhResetText': None, 'sdResetText': None,
              'fhResetEstimated': False, 'sdResetEstimated': False}
    active, values = None, {'fh': [], 'sd': []}
    for line in text.splitlines():
        m = label.match(line)
        if m:
            name = m[1].lower()
            active = 'fh' if name in ('current session', '当前会话') else ('sd' if 'all models' in name or '所有模型' in name else None)
            continue
        if any(x in line.lower() for x in ('extra usage', "what's contributing", 'usage insights')):
            active = None
        if active:
            if re.fullmatch(r'\s*(?:loading|(?:正在)?加载中)[\s.…]*', line, re.I):
                return None
            for number in re.findall(r'(?<![\d.])(\d+(?:\.\d+)?)\s*%\s*(?:used|已用)', line, re.I):
                value = float(number)
                if not math.isfinite(value) or not 0 <= value <= 100:
                    raise ProbeError('CLI_USAGE_UNRECOGNIZED')
                values[active].append(int(math.floor(value + .5)))
            reset = line.strip()
            if re.match(r'^(Resets?\b|重置)', reset, re.I) and len(reset) <= 180 and '@' not in reset and 'http' not in reset:
                result[active + 'ResetText'] = reset
    for key, found in values.items():
        if len(set(found)) > 1: raise ProbeError('CLI_USAGE_UNRECOGNIZED')
        if found:
            result[key] = found[0]
            result[key + 'ResetAt'], result[key + 'ResetEstimated'] = reset_notice(result[key + 'ResetText'], now, key)
    return result if result['fh'] is not None else None


SETTINGS = json.dumps({'remoteControlAtStartup': False, 'disableAllHooks': True,
                       'disableDeepLinkRegistration': 'disable'})


def cli_arguments(binary):
    return [binary, '--allowed-tools', '', '--strict-mcp-config', '--settings', SETTINGS]


class UsageEvidence:
    """Keep only fixed official diagnostic markers; raw debug stays in memory."""
    def __init__(self):
        self.tail = b''
        self.request = self.fresh = self.snapshot = self.failed = False

    def feed(self, data):
        lines = (self.tail + data).split(b'\n')
        self.tail = lines.pop()[-8192:]
        for line in lines:
            match = re.match(rb'^\d{4}-\d{2}-\d{2}T\S+ \[(?:DEBUG|ERROR)\] (.*)$', line)
            if not match:
                continue
            message = match[1].rstrip(b'\r')
            if re.fullmatch(rb'fetchUtilization: GET /api/oauth/usage(?:\?[^ ]*)? \(attempt \d+\)', message):
                self.request = True
            if re.fullmatch(rb'fetchUtilization: 200 after \d+ attempt\(s\).*', message):
                self.fresh = True
            if re.fullmatch(rb'Usage read answered from a snapshot \d+s old; endpoint not asked', message):
                self.snapshot = True
            if message.startswith((b'Usage fetch returned a fieldless or non-object body',
                                   b'Failed to load usage data:')):
                self.failed = True


def cached_observation_at(config, now):
    # This is non-credential CLI bookkeeping, in the same identity metadata
    # file already inspected above. Never turn its timestamp into "just now".
    file = config / '.config.json'
    if not file.exists():
        file = config / '.claude.json'
    if file.is_symlink() or not file.is_file() or file.stat().st_size > 1024 * 1024:
        return None
    try:
        data = json.loads(file.read_text())
        cached, account = data.get('cachedUsageUtilization', {}), data.get('oauthAccount', {})
        at = cached.get('fetchedAtMs')
        if (cached.get('accountUuid') == account.get('accountUuid')
                and isinstance(account.get('accountUuid'), str) and account['accountUuid']
                and type(at) is int and 0 <= now - at < 60000):
            return at
    except (ValueError, AttributeError, TypeError):
        pass
    return None


def capture(binary, config, workspace, deadline):
    import fcntl
    import pty
    import termios
    master, slave = pty.openpty()
    proc = None
    debug_fd = None
    diagnostic = None
    try:
        # A FIFO prevents official --debug-file output being stored on disk.
        # No raw terminal/debug text is returned, persisted or sent to CASE.
        diagnostic = tempfile.TemporaryDirectory(prefix='case-usage-')
        debug_path = Path(diagnostic.name) / 'debug.pipe'
        os.mkfifo(debug_path, 0o600)
        debug_fd = os.open(debug_path, os.O_RDWR | os.O_NONBLOCK)
        evidence = UsageEvidence()
        cache_at = cached_observation_at(config, int(time.time() * 1000))
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 200, 160, 0, 0))
        # The official CLI accepts /usage as its startup command. Waiting for
        # a shortcuts/help footer breaks when the renderer or footer changes.
        # No keyboard command, Enter or permission confirmation is sent.
        proc = subprocess.Popen(cli_arguments(binary) + ['--debug-file', str(debug_path), '/usage'], stdin=slave, stdout=slave, stderr=slave,
                                cwd=workspace, env=environment(config, workspace))
        os.close(slave); slave = -1
        os.set_blocking(master, False)
        screen, total = Screen(), 0
        candidate_at, candidate_key = None, None
        query_tail = b''
        while time.monotonic() < deadline:
            readable, _, _ = select.select([master, debug_fd], [], [], .08)
            for fd in readable:
                try: data = os.read(fd, 65536)
                except OSError: data = b''
                if data:
                    total += len(data)
                    if total > 2 * 1024 * 1024: raise ProbeError('CLI_OUTPUT_LIMIT')
                    if fd == debug_fd:
                        evidence.feed(data)
                        continue
                    scan = query_tail + data
                    if b'\x1b[6n' in scan: os.write(master, b'\x1b[1;1R')
                    query_tail = scan[-3:]
                    screen.feed(data)
            text = screen.text()
            now = int(time.time() * 1000)
            parsed = usage(text, now)
            if parsed:
                key = (parsed['fh'], parsed['sd'], parsed['fhResetText'], parsed['sdResetText'])
                if key != candidate_key: candidate_key, candidate_at = key, time.monotonic()
                if time.monotonic() - candidate_at >= 1.5:
                    if evidence.failed:
                        raise ProbeError('CLI_USAGE_UNAVAILABLE')
                    if evidence.fresh:
                        return parsed
                    if evidence.snapshot and not evidence.request and cache_at is not None:
                        if 0 <= now - cache_at < 60000:
                            parsed = usage(text, cache_at)
                            parsed['age'] = now - cache_at
                            return parsed
                    # A failed official request can render an old persisted
                    # quota as a fallback. Percentages alone are not success.
                    raise ProbeError('CLI_USAGE_UNAVAILABLE')
            else:
                candidate_at, candidate_key = None, None
            if proc.poll() is not None:
                raise ProbeError('CLI_USAGE_UNRECOGNIZED')
        raise ProbeError('REFRESH_TIMEOUT')
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try: proc.wait(timeout=.5)
            except subprocess.TimeoutExpired: proc.kill(); proc.wait()
        os.close(master)
        if slave >= 0: os.close(slave)
        if debug_fd is not None: os.close(debug_fd)
        if diagnostic is not None: diagnostic.cleanup()


def authenticated_scope(payload):
    config, workspace = Path(payload['config']), Path(payload['workspace'])
    expected = (payload['accountHash'], payload['organizationHash'])
    if config.is_symlink() or config.resolve() != config or workspace.is_symlink():
        raise ProbeError('CLI_SCOPE_INVALID')
    if metadata_identity(config) != expected: raise ProbeError('CLI_IDENTITY_MISMATCH')
    try:
        result = subprocess.run([payload['binary'], 'auth', 'status', '--json'],
                                env=environment(config, workspace), cwd=workspace,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=8)
    except subprocess.TimeoutExpired:
        raise ProbeError('REFRESH_TIMEOUT') from None
    if len(result.stdout) > 64 * 1024: raise ProbeError('CLI_AUTH_STATUS_INVALID')
    check_auth(result.stdout, config, expected)
    if result.returncode != 0: raise ProbeError('CLI_AUTH_REQUIRED')
    return config, workspace, expected


def probe(payload):
    deadline = time.monotonic() + 45
    config, workspace, expected = authenticated_scope(payload)
    value = capture(payload['binary'], config, workspace, deadline)
    if metadata_identity(config) != expected: raise ProbeError('CLI_IDENTITY_MISMATCH')
    return value


def setup(payload):
    # Reuse the existing official login. Only the user can accept first-run
    # prompts in this visible terminal; never rewrite trust settings ourselves.
    config, workspace, _ = authenticated_scope(payload)
    label = ''.join(c for c in str(payload.get('label', ''))[:80] if c.isprintable())
    print('CASE 独立 CLI' + (' · ' + label if label else '') + '：正在复用此账号的现有授权。\n'
          '如出现首次设置或目录信任提示，请核对目录后自行确认。'
          '可查看 /usage；完成后输入 /exit，再回到 CASE 手动查询额度。\n'
          '/usage 获取失败不等于未登录；此入口不会重新发起授权。', flush=True)
    return subprocess.call(cli_arguments(payload['binary']) + ['/usage'],
                           env=environment(config, workspace), cwd=workspace)


def authorization_url(value):
    # Only the official subscription flow may open an isolated auth window.
    # OAuth URLs remain in memory; they are never written to logs or profiles.
    if not isinstance(value, str) or len(value) > 8192 or any(ord(c) < 32 for c in value):
        raise ProbeError('AUTH_BROWSER_URL_INVALID')
    try:
        url = urlsplit(value)
        if (url.scheme != 'https' or url.username or url.password or url.port not in (None, 443)
                or (url.hostname, url.path) not in {
                    ('claude.com', '/cai/oauth/authorize'), ('claude.ai', '/oauth/authorize')}):
            raise ValueError()
        query = parse_qs(url.query)
        redirect, = query['redirect_uri']
        callback = urlsplit(redirect)
        if (callback.scheme != 'http' or callback.hostname not in ('localhost', '127.0.0.1', '::1')
                or not callback.port or callback.path != '/callback' or callback.username
                or callback.password or callback.query or callback.fragment
                or len(query.get('state', [])) != 1 or not query['state'][0]):
            raise ValueError()
    except (ValueError, KeyError):
        raise ProbeError('AUTH_BROWSER_URL_INVALID') from None
    return value


def browser_open(value):
    """Official BROWSER entry point. Return promptly, with no system fallback."""
    try:
        authorization_url(value)
        address = os.environ['CASE_AUTH_SOCKET']
        info, parent = os.stat(address), os.stat(Path(address).parent)
        if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid()
                or parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) != 0o700):
            return 1
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(16)
            client.connect(address)
            client.sendall(value.encode() + b'\n')
            client.shutdown(socket.SHUT_WR)
            return 0 if client.recv(16) == b'OK\n' else 1
    except (OSError, KeyError, ProbeError):
        return 1


class AuthBrowser:
    """Own only this login's Electron child; never inspect browser storage."""
    def __init__(self, payload):
        self.payload, self.browser = payload, payload.get('browser', {})
        self.child = self.server = self.thread = self.temp = self.lock = None
        self.stop = threading.Event()
        self.error = None

    def __enter__(self):
        import fcntl
        base = Path(self.payload['config']).parent
        expected = {'script': base / 'auth-browser.cjs', 'opener': base / 'browser.command',
                    'userData': base / 'browser'}
        for key, path in expected.items():
            if self.browser.get(key) != str(path) or path.resolve() != path:
                raise ProbeError('CLI_SCOPE_INVALID')
        for key in ('binary', 'script', 'opener'):
            path = Path(self.browser.get(key, ''))
            if not path.is_absolute() or path.resolve() != path or not path.is_file():
                raise ProbeError('AUTH_BROWSER_NOT_INSTALLED')
        if (Path(self.payload['workspace']) != base / 'workspace' or base.resolve() != base
                or Path(self.payload['config']).resolve() != Path(self.payload['config'])):
            raise ProbeError('CLI_SCOPE_INVALID')
        try:
            self.lock = os.fdopen(os.open(base / '.auth.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600), 'a')
            try:
                fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ProbeError('AUTH_IN_PROGRESS') from None
            self.temp = tempfile.TemporaryDirectory(prefix='case-auth-')
            self.address = str(Path(self.temp.name) / 's')
            self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.server.bind(self.address)
            self.server.listen(2)
            self.server.settimeout(.2)
            self.thread = threading.Thread(target=self.serve, daemon=True)
            self.thread.start()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def serve(self):
        while not self.stop.is_set():
            try:
                connection, _ = self.server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with connection:
                try:
                    connection.settimeout(2)
                    request = bytearray()
                    while len(request) <= 8192:
                        chunk = connection.recv(8193 - len(request))
                        if not chunk: break
                        request.extend(chunk)
                    if len(request) > 8192:
                        raise ProbeError('AUTH_BROWSER_URL_INVALID')
                    url = authorization_url(request.decode().removesuffix('\n'))
                    if self.child is not None:
                        raise ProbeError('AUTH_IN_PROGRESS')
                    env = environment(Path(self.payload['config']), Path(self.payload['workspace']))
                    # Browser configuration and URL travel only over stdin, not
                    # process arguments, shell expansion or a persisted file.
                    self.child = subprocess.Popen([self.browser['binary'], self.browser['script']],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                        env=env, cwd=self.payload['workspace'], start_new_session=True)
                    self.child.stdin.write((json.dumps(dict(self.browser, url=url)) + '\n').encode())
                    self.child.stdin.close()
                    deadline, received = time.monotonic() + 12, bytearray()
                    while time.monotonic() < deadline and not self.stop.is_set():
                        ready, _, _ = select.select([self.child.stdout], [], [], .1)
                        if ready:
                            chunk = os.read(self.child.stdout.fileno(), 1024)
                            if not chunk: break
                            received.extend(chunk)
                            if b'CASE_AUTH_READY\n' in received:
                                connection.sendall(b'OK\n')
                                break
                            if len(received) > 8192: break
                    else:
                        raise ProbeError('AUTH_BROWSER_FAILED')
                    if b'CASE_AUTH_READY\n' not in received:
                        raise ProbeError('AUTH_BROWSER_FAILED')
                except (OSError, ValueError, ProbeError) as error:
                    self.error = str(error) if isinstance(error, ProbeError) else 'AUTH_BROWSER_FAILED'
                    try: connection.sendall(b'FAILED\n')
                    except OSError: pass

    def __exit__(self, *_):
        self.stop.set()
        if self.thread is not None: self.thread.join(timeout=3)
        if self.server is not None: self.server.close()
        if self.child is not None:
            try:
                if self.child.poll() is None:
                    # Only this live child process group's descendants are used.
                    os.killpg(self.child.pid, signal.SIGTERM)
                    try: self.child.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        os.killpg(self.child.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            self.child.wait()
            for pipe in (self.child.stdin, self.child.stdout):
                if pipe: pipe.close()
        if self.temp is not None: self.temp.cleanup()
        if self.lock is not None: self.lock.close()


def login(payload):
    # Invoked only by a user's Connect button in a visible Terminal. No tokens
    # pass through CASE. The user completes official login and first-run setup.
    config, workspace = Path(payload['config']), Path(payload['workspace'])
    env = environment(config, workspace)
    print('CASE: 请在此账号的独立 Electron 窗口中登录并授权。'
          '完成 /usage 设置后输入 /exit，再回到 CASE 手动查询。', flush=True)
    with AuthBrowser(payload) as browser:
        env.update(BROWSER=payload['browser']['opener'], CASE_AUTH_SOCKET=browser.address)
        child = subprocess.Popen([payload['binary'], 'auth', 'login', '--claudeai'], env=env, cwd=workspace)
        try:
            while child.poll() is None:
                if browser.error: raise ProbeError(browser.error)
                time.sleep(.1)
            if browser.error: raise ProbeError(browser.error)
            result = child.returncode
        finally:
            if child.poll() is None:
                child.terminate()
                try: child.wait(timeout=2)
                except subprocess.TimeoutExpired: child.kill(); child.wait()
    if result == 0:
        return setup(payload)
    return result


def main():
    try:
        if sys.argv[1] == 'browser':
            return browser_open(sys.argv[2]) if len(sys.argv) == 3 else 1
        payload = json.loads(sys.argv[2])
        if sys.argv[1] == 'login': return login(payload)
        if sys.argv[1] == 'setup': return setup(payload)
        value = probe(payload)
        result = {'ok': True, 'status': 'success', 'usage': value}
    except ProbeError as error:
        code = str(error)
        status = ('auth_failed' if code in ('CLI_AUTH_REQUIRED', 'CLI_IDENTITY_MISMATCH', 'CLI_IDENTITY_UNAVAILABLE')
                  else 'timeout' if code == 'REFRESH_TIMEOUT' else 'unavailable')
        result = {'ok': False, 'status': status, 'code': code, 'usage': None}
    except (OSError, ValueError, KeyError, TypeError):
        result = {'ok': False, 'status': 'failed', 'code': 'CLI_PROBE_FAILED', 'usage': None}
    print(json.dumps(result), flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
