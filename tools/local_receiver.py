"""Local webhook receiver for demos and tests. Binds to loopback only.

Paths decide the response:
  /ok (or anything else) -> 200      /fail -> 500       /status/<n> -> n (200-599)
  /redirect -> 302 to /ok            /slow -> sleeps, then 200

Run:  python -m tools.local_receiver --port 9000 --secret <endpoint secret>
Then register http://127.0.0.1:9000/ok with HOOKRELAY_TEST_ALLOWED_HOSTS=127.0.0.1.
"""

import argparse
import ipaddress
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.core.security import SIGNATURE_HEADER, verify_signature

MAX_BODY = 1_048_576


@dataclass(frozen=True)
class Received:
    path: str
    headers: dict[str, str]
    body: bytes
    signature_valid: bool | None  # None when no secret was configured
    status_sent: int


class ReceiverServer:
    def __init__(
        self,
        secret: str | None = None,
        slow_seconds: float = 1.5,
        host: str = "127.0.0.1",
        port: int = 0,
        on_request: Callable[[Received], None] | None = None,
    ) -> None:
        if not ipaddress.ip_address(host).is_loopback:
            raise ValueError("receiver only binds to loopback addresses")
        self.secret = secret
        self.slow_seconds = slow_seconds
        self.received: list[Received] = []
        self._lock = threading.Lock()
        self._on_request = on_request
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(min(length, MAX_BODY))
                path = self.path.split("?", 1)[0]
                if path == "/slow":
                    time.sleep(outer.slow_seconds)
                status = outer._status_for(path)
                valid: bool | None = None
                if outer.secret is not None:
                    valid = verify_signature(
                        outer.secret, body, self.headers.get(SIGNATURE_HEADER), time.time()
                    )
                record = Received(
                    path, {k.lower(): v for k, v in self.headers.items()}, body, valid, status
                )
                with outer._lock:
                    outer.received.append(record)
                if outer._on_request:
                    outer._on_request(record)
                self.send_response(status)
                if status == 302:
                    self.send_header("Location", "/ok")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:
                return

        self._server = ThreadingHTTPServer((host, port), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @staticmethod
    def _status_for(path: str) -> int:
        if path == "/fail":
            return 500
        if path == "/redirect":
            return 302
        if path.startswith("/status/"):
            tail = path.removeprefix("/status/")
            if tail.isdigit() and 200 <= int(tail) <= 599:
                return int(tail)
        return 200

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    def url(self, path: str = "/ok") -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def start(self) -> "ReceiverServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def paths(self) -> list[str]:
        with self._lock:
            return [r.path for r in self.received]


def main() -> None:
    parser = argparse.ArgumentParser(description="HookRelay local test receiver")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--secret", default=None, help="endpoint secret used to verify signatures")
    args = parser.parse_args()

    def show(r: Received) -> None:
        sig = {None: "unchecked", True: "valid", False: "INVALID"}[r.signature_valid]
        event = r.headers.get("hookrelay-event-id", "-")
        print(f"POST {r.path} -> {r.status_sent} signature={sig} event={event} bytes={len(r.body)}")

    server = ReceiverServer(secret=args.secret, port=args.port, on_request=show)
    server.start()
    print(f"listening on {server.url('/')}  (paths: /ok /fail /status/<n> /redirect /slow)")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()
