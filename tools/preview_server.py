#!/usr/bin/env python3
"""Loopback-only screenshot preview. Serves allowlisted UI files and mock data."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import mimetypes
from pathlib import Path
import re
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
UI = {p.name: p for p in (ROOT / 'ui').iterdir() if p.suffix in {'.html', '.js', '.css'}}
DEMO = {p.name: p for p in (ROOT / 'docs/demo').iterdir() if p.suffix in {'.html', '.js', '.css'}}


class Preview(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        content = None
        media = 'text/html; charset=utf-8'
        if path == '/':
            content = DEMO['index.html'].read_bytes()
        elif path in ('/app/index.html', '/app/settings.html'):
            source = UI[path.rsplit('/', 1)[1]].read_text()
            source = source.replace('<head>', '<head><base href="/ui/">')
            source = source.replace('<script src="api.js"></script>',
                                    '<script src="/demo/mock-tauri.js"></script><script src="api.js"></script>')
            source = source.replace('</head>', '<link rel="stylesheet" href="/demo/frame.css"></head>')
            content = source.encode()
        elif path == '/demo/theme.css':
            css = (ROOT / 'ui/styles.css').read_text()
            dark = re.search(r':root\s*\{([^}]+)\}', css)[1]
            light = re.search(r'@media\s*\(prefers-color-scheme:\s*light\)\s*\{\s*:root\s*\{([^}]+)\}', css)[1]
            content = (':root[data-demo-theme="dark"] {' + dark + '}\n' +
                       ':root[data-demo-theme="light"] {' + light + '}').encode()
            media = 'text/css; charset=utf-8'
        else:
            table = UI if path.startswith('/ui/') else DEMO if path.startswith('/demo/') else {}
            name = path.rsplit('/', 1)[-1]
            # Exact routes only: no directory listings, .git, out/ or runtime data.
            prefix = '/ui/' if table is UI else '/demo/'
            if name in table and path == prefix + name:
                content = table[name].read_bytes()
                media = mimetypes.guess_type(name)[0] or 'application/octet-stream'
        if content is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', media)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'none'; frame-src 'self'; object-src 'none'; base-uri 'self'; form-action 'none'; frame-ancestors 'self'")
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, *_args):
        pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=57678)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Preview)
    print(f'CASE simulated preview: http://127.0.0.1:{server.server_port}/', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
