"""H1: the Jev client's real transport bounds a whole request over a loopback server."""

from __future__ import annotations

import http.server
import threading
import time

import pytest

from raidar.judge.client import RequestsTransport
from tests.jev_support import TransportTimeoutError

pytestmark = pytest.mark.slow


class _TrickleHandler(http.server.BaseHTTPRequestHandler):
    """Answers 200, then sends one byte every 50 ms for about two seconds."""

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(200)
        self.send_header("Content-Length", "40")
        self.end_headers()
        for _ in range(40):
            self.wfile.write(b" ")
            self.wfile.flush()
            time.sleep(0.05)

    def log_message(self, *_args: object) -> None:
        return


def test_requests_transport_timeout_bounds_the_whole_request_not_each_read() -> None:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _TrickleHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/decisions"
        started = time.monotonic()
        with pytest.raises(TransportTimeoutError):
            RequestsTransport().post(url, headers={}, body=b"{}", timeout_s=0.4)
        assert time.monotonic() - started < 1.0
    finally:
        server.shutdown()
        server.server_close()
