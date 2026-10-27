#!/usr/bin/env python3
"""Run offline gates; annotate failures so public CI diagnostics are accessible."""
import glob
import os
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
os.environ['CARGO_TERM_COLOR'] = 'never'

checks = [
    ['cargo', 'fmt', '--check'],
    ['cargo', 'clippy', '--locked', '--all-targets', '--', '-D', 'warnings'],
    ['cargo', 'test', '--locked'],
    ['node', '--test', *sorted(glob.glob('tests/*.test.cjs'))],
    [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests/python', '-q'],
]
for command in checks:
    print(' '.join(command), flush=True)
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    text = re.sub(r'\x1b\[[0-9;]*m', '', result.stdout.decode('utf-8', errors='replace'))
    print(text, flush=True)
    if result.returncode:
        if os.environ.get('GITHUB_ACTIONS'):
            # An annotation has a small size limit. Keep both the first failure
            # and final summary, with enough separate annotations for tracebacks.
            detail = text if len(text) <= 24000 else text[:12000] + '\n[output truncated]\n' + text[-12000:]
            for index, start in enumerate(range(0, len(detail), 3000), 1):
                chunk = detail[start:start + 3000].replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
                print(f'::error title=Offline verification failed {index}::' + chunk, flush=True)
        sys.exit(result.returncode)
