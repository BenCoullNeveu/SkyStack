"""Triage on this machine: serve the triage page locally, open it in an app
window, and wait for the reviewer to click "Finish review".

The page is the same self-contained HTML that goes to remote reviewers; the
only difference is DATA.local, which makes it POST its decisions back here
instead of downloading a file. Only 127.0.0.1 is bound, and every URL carries
a random token, so other local processes can't submit decisions by accident.
"""
from __future__ import annotations

import json
import logging
import secrets
import shutil
import subprocess
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

_BROWSER_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


class ReviewServer:
    """Serves one HTML page and accepts one decisions POST.

    html_for(base_url) builds the page once the URL (port + token) is known.
    validate(decisions) raises ValueError for a decisions file that doesn't
    belong to this package; the page shows the message.
    """

    def __init__(self, html_for: Callable[[str], bytes], validate: Callable[[Dict[str, Any]], None]):
        self.token = secrets.token_urlsafe(16)
        self._validate = validate
        self._done = threading.Event()
        self.decisions: Optional[Dict[str, Any]] = None
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):  # keep the job log readable
                logger.debug("review server: " + fmt, *args)

            def _send(self, code: int, body: bytes, ctype: str):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path.rstrip("/") == f"/{server.token}":
                    self._send(200, server.html, "text/html; charset=utf-8")
                else:
                    self._send(404, b"not found", "text/plain")

            def do_POST(self):
                if self.path != f"/{server.token}/submit":
                    return self._send(404, b'{"ok": false, "error": "not found"}', "application/json")
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    data = json.loads(self.rfile.read(length).decode("utf-8"))
                    server._validate(data)
                except (ValueError, json.JSONDecodeError) as e:
                    return self._send(400, json.dumps({"ok": False, "error": str(e)}).encode(), "application/json")
                server.decisions = data
                self._send(200, b'{"ok": true}', "application/json")
                server._done.set()

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self._httpd.server_address[1]}/{self.token}"
        self.html = html_for(self.base_url)

    def serve_until_submitted(self, timeout_s: Optional[float] = None) -> Optional[Dict[str, Any]]:
        thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        thread.start()
        try:
            self._done.wait(timeout_s)
        finally:
            self._httpd.shutdown()
            self._httpd.server_close()
        return self.decisions


def open_app_window(url: str, browser: str = "auto") -> None:
    """Open url as a standalone app window (Edge/Chrome --app) so it pops up
    like a tool and can close itself; falls back to the default browser."""
    exe = None
    if browser not in ("auto", "default"):
        exe = browser
    elif browser == "auto":
        exe = next((p for p in _BROWSER_CANDIDATES if Path(p).exists()), None) \
            or shutil.which("msedge") or shutil.which("chrome")
    if exe:
        try:
            subprocess.Popen([exe, f"--app={url}", "--new-window"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except OSError as e:
            logger.warning("Couldn't start %s (%s); using the default browser", exe, e)
    webbrowser.open(url)
