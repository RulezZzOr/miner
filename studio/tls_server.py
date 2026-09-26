"""Threaded HTTP(S) listener whose TLS handshakes never run on the accept thread."""

from __future__ import annotations

import ssl
from http.server import ThreadingHTTPServer

HANDSHAKE_SECONDS = 15
CONNECTION_SECONDS = 30
WRITE_SLICE = 256 * 1024  # At CONNECTION_SECONDS per slice, a reader needs only about 9 KB/s.


def write_body(stream, data):
    """Write a response body in bounded slices.

    A socket timeout limits one sendall (plain or TLS) as a whole, so a single large write would
    cut off a slow client that is still reading steadily. Each slice gets its own deadline, while
    a client that stops reading still releases the thread after CONNECTION_SECONDS.
    """
    view = memoryview(data)
    for start in range(0, len(view), WRITE_SLICE):
        stream.write(view[start:start + WRITE_SLICE])


class ThreadingTLSServer(ThreadingHTTPServer):
    """Accept plain sockets and wrap each one in its own connection thread.

    Wrapping the listening socket would run every handshake inside serve_forever,
    so one idle TCP client could freeze the whole listener and its shutdown.
    """

    daemon_threads = True
    ssl_context: ssl.SSLContext | None = None

    def process_request_thread(self, request, client_address):
        if self.ssl_context is not None:
            try:
                request.settimeout(HANDSHAKE_SECONDS)
                request = self.ssl_context.wrap_socket(request, server_side=True)
            except OSError:  # ssl.SSLError and timeouts included: drop only this client.
                self.shutdown_request(request)
                return
        super().process_request_thread(request, client_address)
