#!/usr/bin/env python3
"""Package only known build outputs, never developer account data or out/."""
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = tomllib.loads((ROOT / 'Cargo.toml').read_text())['package']['version']
DIST = ROOT / 'dist'
PREFIX = 'CASE-' + VERSION


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def package(platform):
    DIST.mkdir(exist_ok=True)
    if platform == 'mac':
        app = ROOT / 'target/universal-apple-darwin/release/bundle/macos/CASE.app'
        subprocess.run(['ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', str(app), str(DIST / (PREFIX + '-mac-universal.zip'))], check=True)
        with tarfile.open(DIST / (PREFIX + '-local-sync-source.tar.gz'), 'w:gz') as archive:
            for name in ['engine', 'tools', 'docs', 'README.md', 'README.en.md', 'NOTICE.md', 'LICENSE']:
                archive.add(ROOT / name, arcname=name, filter=lambda item: None if '__pycache__' in item.name or item.name.endswith('.pyc') else item)
    elif platform == 'win':
        installers = list((ROOT / 'target/release/bundle/nsis').glob('*-setup.exe'))
        if len(installers) != 1: raise RuntimeError('Expected exactly one NSIS installer')
        shutil.copy2(installers[0], DIST / (PREFIX + '-win-x64-setup.exe'))
        with zipfile.ZipFile(DIST / (PREFIX + '-win-x64.zip'), 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.write(ROOT / 'target/release/CASE.exe', 'CASE/CASE.exe')
            for name in ['README.md', 'README.en.md', 'NOTICE.md', 'LICENSE']:
                archive.write(ROOT / name, 'CASE/' + name)
            archive.write(ROOT / 'docs/CROSS-PLATFORM.md', 'CASE/PLATFORM-NOTES.md')
    elif platform == 'linux':
        for kind, suffix in [('deb', '.deb'), ('appimage', '.AppImage')]:
            files = list((ROOT / 'target/release/bundle' / kind).glob('*' + suffix))
            if len(files) != 1: raise RuntimeError('Expected exactly one Linux ' + kind)
            shutil.copy2(files[0], DIST / (PREFIX + '-linux-x64' + suffix))
    else:
        raise ValueError(platform)
    for path in DIST.iterdir():
        if not path.name.endswith('.sha256'):
            path.with_name(path.name + '.sha256').write_text(checksum(path) + '  ' + path.name + '\n')


def verify():
    expected = [PREFIX + suffix for suffix in ['-mac-universal.zip', '-win-x64-setup.exe', '-win-x64.zip', '-linux-x64.deb', '-linux-x64.AppImage', '-local-sync-source.tar.gz']]
    if {p.name for p in DIST.iterdir()} != set(expected + [p + '.sha256' for p in expected]):
        raise RuntimeError('Missing or unexpected release assets')
    for name in expected:
        path = DIST / name
        if not path.is_file() or path.stat().st_size < 1000: raise RuntimeError('Invalid asset: ' + name)
        if (DIST / (name + '.sha256')).read_text().strip() != checksum(path) + '  ' + name:
            raise RuntimeError('Checksum mismatch: ' + name)
    print('Verified all six release assets and their SHA-256 checksums.')


if __name__ == '__main__':
    verify() if sys.argv[1] == 'verify' else package(sys.argv[1])
