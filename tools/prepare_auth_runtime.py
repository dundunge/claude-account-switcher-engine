#!/usr/bin/env python3
"""Explicit local install of a SHA-256 pinned public Electron archive.

No network, account data, browser profiles or credential files are read here.
Download the matching official release archive separately, then pass its path.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import posixpath
import shutil
import stat
import subprocess
import tempfile
import zipfile

VERSION = '42.4.0'
ASSETS = {
    ('Darwin', 'arm64'): ('darwin-arm64', '3ce55988c9998bcd1e9c69478dd26887b90e8f8010441172e520e94ba575e520'),
    ('Darwin', 'x64'): ('darwin-x64', '0f141809eebe3f3f8c8f8377c10c93f21a39433f71526598de5e989f452cae29'),
    ('Windows', 'x64'): ('win32-x64', 'ffc056685b4a769d7977ef3d58bdc332446d081f025ee074d77b498d2962e2cd'),
    ('Linux', 'x64'): ('linux-x64', '9a8194635548490a56099cc4c2b116738ae56834dee4472506d5a8b262bcbda4'),
    ('Linux', 'arm64'): ('linux-arm64', 'd3bf612de0b651302fb46e50ed3282b609ea9d4d99bb296f7c9bb8ffd92fd69b'),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    args = parser.parse_args()
    machine = {'AMD64': 'x64', 'x86_64': 'x64', 'aarch64': 'arm64', 'arm64': 'arm64'}.get(platform.machine())
    if (platform.system(), machine) not in ASSETS:
        raise SystemExit('Unsupported operating system or architecture.')
    target, expected = ASSETS[(platform.system(), machine)]
    checksum = hashlib.sha256(args.archive.read_bytes()).hexdigest()
    if checksum != expected:
        raise SystemExit('Official Electron archive SHA-256 mismatch; nothing installed.')
    with zipfile.ZipFile(args.archive) as archive:
        for item in archive.infolist():
            name = PurePosixPath(item.filename)
            if name.is_absolute() or '..' in name.parts or '\\' in item.filename:
                raise SystemExit('Unsafe archive path.')
            if stat.S_ISLNK(item.external_attr >> 16):
                link = archive.read(item).decode()
                resolved = posixpath.normpath(posixpath.join(str(name.parent), link))
                if link.startswith('/') or resolved == '..' or resolved.startswith('../'):
                    raise SystemExit('Unsafe archive link.')
    if platform.system() == 'Darwin': support = Path.home() / 'Library/Application Support'
    elif platform.system() == 'Windows': support = Path(os.environ['APPDATA'])
    else: support = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
    root = support / 'CASE/auth-runtime'
    if root.resolve() != root or (root / VERSION).exists():
        raise SystemExit('Runtime path is redirected or this version is already installed.')
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.prepare-', dir=root))
    try:
        if platform.system() == 'Darwin':
            subprocess.run(['/usr/bin/ditto', '-x', '-k', str(args.archive.resolve()), str(staging)], check=True)
            app = staging / 'Electron.app'
            subprocess.run(['/usr/bin/codesign', '--force', '--deep', '--sign', '-', str(app)], check=True)
            subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)], check=True)
            binary = app / 'Contents/MacOS/Electron'
        else:
            with zipfile.ZipFile(args.archive) as archive:
                archive.extractall(staging)
                if platform.system() == 'Linux':
                    for item in archive.infolist():
                        path = staging / item.filename
                        if not path.is_dir(): path.chmod((item.external_attr >> 16) & 0o755 or 0o600)
            binary = staging / ('electron.exe' if platform.system() == 'Windows' else 'electron')
            if not binary.is_file(): raise SystemExit('Electron executable missing.')
        manifest = {'version': VERSION, 'asset': f'electron-v{VERSION}-{target}.zip',
                    'archiveSha256': checksum, 'binarySha256': hashlib.sha256(binary.read_bytes()).hexdigest()}
        (staging / 'runtime.json').write_text(json.dumps(manifest, indent=2) + '\n')
        os.rename(staging, root / VERSION)
        print(root / VERSION)
    finally:
        if staging.exists(): shutil.rmtree(staging)


if __name__ == '__main__':
    main()
