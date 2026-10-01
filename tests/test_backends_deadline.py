"""An HTTP backend call ends within its timeout IN TOTAL (#337).

`urllib`'s `timeout` bounds each blocking socket read, and every byte that arrives resets it. A
provider that holds a queued request open by sending keep-alive bytes -- DeepSeek documents
"continuously return empty lines" for up to ten minutes -- therefore held a `complete()` far past
`DEFAULT_TIMEOUT`, and a call that never raises reaches none of the failure handling downstream
of it (the stage's own, the digest's, triage's tier-3 breaker). Observed: `doctor` blocked for
about thirteen minutes in an SSL read.

`_urlopen` now reads in chunks against a wall-clock deadline. The rows below drive it with a fake
response and an injected clock, and once against a real local socket, because the property that
matters -- control returns between trickled bytes -- is a property of the read call, which a fake
can only imitate.
"""
import http.server
import json
import socket
import threading
import time
import urllib.request

import pytest

from sluice.core import backends
from sluice.core.backends import BackendError, OpenAiCompatibleBackend

_URL = "https://api.example.invalid/v1/chat/completions"


class _Trickle:
    """A response whose `read1` hands back `chunks` one per call, then b"" (end of body)."""

    def __init__(self, chunks, clock=None, step=0.0):
        self.chunks = list(chunks)
        self.clock = clock
        self.step = step
        self.reads = 0

    def read1(self, n=-1):
        self.reads += 1
        if self.clock is not None:
            self.clock.now += self.step
        return self.chunks.pop(0) if self.chunks else b""

    def read(self, n=-1):
        """The whole-body read, costing what a real one does: it waits out every chunk, so a
        regression to it shows in `reads` and on the clock rather than reading as instant."""
        out = b""
        while self.chunks:
            out += self.read1()
        return out

    def close(self):  # urllib's HTTPError wrapper closes its body on cleanup
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _serve(monkeypatch, response):
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: response)


def test_a_trickle_of_blank_lines_past_the_deadline_raises(monkeypatch):
    clock = _Clock()
    endless = _Trickle([b"\n"] * 10_000, clock=clock, step=1.0)
    _serve(monkeypatch, endless)

    with pytest.raises(BackendError, match=r"within 30s"):
        backends._urlopen(_URL, b"{}", {}, 30, clock=clock)

    assert endless.reads < 100, "the deadline was not checked between reads"


def test_a_response_that_completes_inside_the_deadline_is_returned_whole(monkeypatch):
    body = {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
    _serve(monkeypatch, _Trickle([b"\n", b"\n", json.dumps(body).encode()[:10],
                                  json.dumps(body).encode()[10:]]))

    out = backends._urlopen(_URL, b"{}", {}, 30, clock=_Clock())

    assert out == "\n\n" + json.dumps(body)


def test_leading_keep_alive_lines_do_not_disturb_the_parse():
    """DeepSeek's documented keep-alive for a queued non-streaming request is empty lines ahead of
    the body; `json.loads` already skips leading whitespace, so the completion is unchanged."""
    body = {"choices": [{"message": {"content": "  answer  "}, "finish_reason": "stop"}]}
    be = OpenAiCompatibleBackend("m", api_key="not-a-real-key", base_url="https://x.invalid/v1",
                                 http=lambda url, data, headers, timeout: "\n\n\n" + json.dumps(body))

    assert be.complete("p").text == "answer"


def test_no_timeout_means_no_deadline(monkeypatch):
    clock = _Clock()
    _serve(monkeypatch, _Trickle([b"\n"] * 50 + [b"{}"], clock=clock, step=100.0))

    assert backends._urlopen(_URL, b"{}", {}, None, clock=clock).endswith("{}")


class _TrickleHandler(http.server.BaseHTTPRequestHandler):
    """Answers a POST with headers and then one blank line every `PACE` seconds for `FOR` seconds,
    the shape of a queued DeepSeek request, then closes."""
    PACE = 0.05
    FOR = 6.0

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        end = time.monotonic() + self.FOR
        try:
            while time.monotonic() < end:
                self.wfile.write(b"\n")
                self.wfile.flush()
                time.sleep(self.PACE)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):
        pass


def _loopback_only(monkeypatch):
    """The suite forbids DNS (`tests/conftest.py::_forbid_dns`); a numeric loopback address needs
    none, so it is answered here without a lookup and every other host still reaches the guard.

    No proxy either: urllib's default opener honours `HTTP_PROXY` and friends, so a developer
    machine with one set would send this request off the box instead of to the loopback server."""
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(urllib.request, "_opener",
                        urllib.request.build_opener(urllib.request.ProxyHandler({})))
    guard = socket.getaddrinfo

    def resolve(host, port, *args, **kwargs):
        if host == "127.0.0.1":
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (host, port))]
        return guard(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)


def test_a_real_socket_trickling_blank_lines_is_cut_off_at_the_deadline(monkeypatch):
    """Every byte resets urllib's own per-read timeout, so only a deadline checked BETWEEN reads
    can end this call -- and only if each read returns as soon as some bytes arrive. A read that
    waits to fill a fixed-size buffer would sit through the whole trickle and then return a body
    of blank lines, which is the hang this test is shaped to catch without hanging itself."""
    _loopback_only(monkeypatch)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _TrickleHandler)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/v1/chat/completions"
        start = time.monotonic()
        with pytest.raises(BackendError, match=r"within 0\.5s"):
            backends._urlopen(url, b"{}", {"Content-Type": "application/json"}, 0.5)
        assert time.monotonic() - start < 3.0
    finally:
        server.shutdown()
        server.server_close()


def test_the_deadline_error_names_no_more_than_the_url_it_was_given(monkeypatch):
    """The message joins the HTTP-error path's shape: the url and the reason, nothing else."""
    clock = _Clock()
    _serve(monkeypatch, _Trickle([b"\n"] * 100, clock=clock, step=10.0))

    with pytest.raises(BackendError) as ei:
        backends._urlopen(_URL, b"{}", {}, 30, clock=clock)

    assert str(ei.value) == f"no complete response from {_URL} within 30s"



def test_doctor_probes_every_backend_with_its_own_short_deadline(monkeypatch, tmp_path):
    """`doctor` is the command run BECAUSE something is wrong, so a queueing provider must show as a
    failed check rather than a hung command. Every probed backend is built with PROBE_TIMEOUT."""
    from sluice.core.app import Sluice
    from sluice.core.backends import DEFAULT_TIMEOUT, PROBE_TIMEOUT
    monkeypatch.setenv("DEEPSEEK_API_KEY", "not-a-real-key")
    monkeypatch.setattr("shutil.which", lambda name: str(tmp_path / "claude"))
    # Two providers, so the sweep below covers both construction shapes (CLI and HTTP): the
    # shipped defaults put every stage on claude-max alone since #333 removed the fallback.
    cfgp = tmp_path / "cfg.yaml"
    cfgp.write_text("track:\n  backend: deepseek\n")
    monkeypatch.setenv("SLUICE_CONFIG", str(cfgp))
    seen = []

    Sluice().doctor(probe=lambda b: seen.append((b.provider, b.timeout)))

    assert {p for p, _t in seen} >= {"claude-max", "deepseek"}, seen
    assert all(t == PROBE_TIMEOUT for _p, t in seen), seen
    assert PROBE_TIMEOUT < DEFAULT_TIMEOUT


class _Sized(_Trickle):
    """A `_Trickle` that, like http.client, counts down the bytes its Content-Length still owes."""

    def __init__(self, chunks, declared, **kw):
        super().__init__(chunks, **kw)
        self.length = declared

    def read1(self, n=-1):
        chunk = super().read1(n)
        self.length -= len(chunk)
        return chunk


def test_a_body_complete_by_its_declared_length_is_returned_past_the_deadline(monkeypatch):
    """A finished body is the answer the caller paid for, and its usage is in it: the deadline stops
    a call that has not finished, never one whose declared length has all arrived."""
    clock = _Clock()
    _serve(monkeypatch, _Sized([b'{"a":', b"1}"], declared=7, clock=clock, step=20.0))

    assert backends._urlopen(_URL, b"{}", {}, 30, clock=clock) == '{"a":1}'


def test_a_body_that_ends_with_bytes_still_owed_raises(monkeypatch):
    """`read1` answers b"" when the peer closes early, which a loop would read as the end of the
    body; `urllib`'s whole-body read raised IncompleteRead there, and a prefix must not be returned
    as if it were the response."""
    _serve(monkeypatch, _Sized([b'{"choices": [{"mess'], declared=65))

    with pytest.raises(BackendError, match=r"ended 46 bytes short"):
        backends._urlopen(_URL, b"{}", {}, 30, clock=_Clock())


_SECRET_URL = "https://user:not-a-real-pass@api.example.invalid:8443/v1/chat/completions?key=not-a-real-key"


def test_the_deadline_error_carries_no_credentials_from_the_url(monkeypatch):
    clock = _Clock()
    _serve(monkeypatch, _Trickle([b"\n"] * 100, clock=clock, step=10.0))

    with pytest.raises(BackendError) as ei:
        backends._urlopen(_SECRET_URL, b"{}", {}, 30, clock=clock)

    assert str(ei.value) == (
        "no complete response from https://api.example.invalid:8443/v1/chat/completions within 30s")


def test_the_http_error_message_carries_no_credentials_from_the_url(monkeypatch):
    import io
    import urllib.error

    def boom(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b"bad key"))

    monkeypatch.setattr(urllib.request, "urlopen", boom)

    with pytest.raises(BackendError) as ei:
        backends._urlopen(_SECRET_URL, b"{}", {}, 30)

    assert "not-a-real" not in str(ei.value) and "user" not in str(ei.value)
    assert "HTTP 401 from https://api.example.invalid:8443/v1/chat/completions: bad key" == str(ei.value)


def test_a_multibyte_character_split_across_reads_is_decoded_whole(monkeypatch):
    """The chunks are joined as bytes before decoding: a read boundary can fall inside a UTF-8
    sequence, and decoding each chunk on its own raises on half a character."""
    _serve(monkeypatch, _Trickle(['{"a": "caf'.encode(), b"\xc3", b"\xa9" + b'"}']))

    assert json.loads(backends._urlopen(_URL, b"{}", {}, 30, clock=_Clock())) == {"a": "caf\u00e9"}


class _ShortHandler(http.server.BaseHTTPRequestHandler):
    """Declares a Content-Length and closes after sending part of it."""

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", "65")
        self.end_headers()
        self.wfile.write(b'{"choices": [{"message": {"cont')
        self.wfile.flush()
        self.close_connection = True

    def log_message(self, *args):
        pass


def _loopback_server(handler):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def test_a_real_socket_closing_with_bytes_still_owed_raises(monkeypatch):
    _loopback_only(monkeypatch)
    server, base = _loopback_server(_ShortHandler)
    try:
        with pytest.raises(BackendError, match=r"ended 34 bytes short"):
            backends._urlopen(base + "/v1/chat/completions", b"{}", {}, 5)
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("provider", ["anthropic", "openai", "deepseek"])
def test_every_http_backend_ends_a_trickled_call_at_its_deadline(monkeypatch, provider):
    """Through the factory and `complete()`, not `_urlopen` alone: both HTTP backend classes reach
    the provider through this transport, and a stall must surface as their BackendError."""
    from sluice.core.backends import make_backend
    _loopback_only(monkeypatch)
    server, base = _loopback_server(_TrickleHandler)
    try:
        backend = make_backend(provider, "m", api_key="not-a-real-key", base_url=base, timeout=0.5)
        start = time.monotonic()
        with pytest.raises(BackendError, match=r"within 0\.5s"):
            backend.complete("p")
        assert time.monotonic() - start < 3.0
    finally:
        server.shutdown()
        server.server_close()


def test_a_finished_body_with_no_declared_length_is_returned_past_the_deadline(monkeypatch):
    """Chunked and close-delimited bodies declare no length -- DeepSeek's keep-alive mode cannot
    know one when it starts -- so only one further, empty read shows the body finished."""
    clock = _Clock()
    _serve(monkeypatch, _Trickle([b'{"a":', b"1}"], clock=clock, step=20.0))

    assert backends._urlopen(_URL, b"{}", {}, 30, clock=clock) == '{"a":1}'


class _ChunkedHandler(http.server.BaseHTTPRequestHandler):
    """A whole JSON body sent chunked over HTTP/1.1, terminating chunk included."""

    protocol_version = "HTTP/1.1"

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Connection", "close")
        self.end_headers()
        body = b'{"ok": true}'
        self.wfile.write(b"%x\r\n%s\r\n0\r\n\r\n" % (len(body), body))
        self.wfile.flush()

    def log_message(self, *args):
        pass


def test_a_real_chunked_body_finished_as_the_deadline_passes_is_returned(monkeypatch):
    """The clock reads past the deadline from the first body read on, so only the terminating
    chunk -- one further, empty read -- can return the body rather than raise."""
    _loopback_only(monkeypatch)
    server, base = _loopback_server(_ChunkedHandler)
    ticks = iter([0.0])
    try:
        got = backends._urlopen(base + "/v1/chat/completions", b"{}", {}, 30,
                                clock=lambda: next(ticks, 100.0))
    finally:
        server.shutdown()
        server.server_close()

    assert json.loads(got) == {"ok": True}


def test_an_error_body_that_trickles_is_cut_off_and_the_status_still_reported(monkeypatch):
    import urllib.error
    clock = _Clock()
    trickle = _Trickle([b"queued"] + [b"\n"] * 10_000, clock=clock, step=10.0)

    def boom(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 503, "Service Unavailable", {}, trickle)

    monkeypatch.setattr(urllib.request, "urlopen", boom)

    with pytest.raises(BackendError, match=r"^HTTP 503 from https://api\.example\.invalid/v1/chat/completions: queued$"):
        backends._urlopen(_URL, b"{}", {}, 30, clock=clock)
    assert trickle.reads < 10, trickle.reads


def test_an_ipv6_url_keeps_its_brackets_in_the_message(monkeypatch):
    clock = _Clock()
    _serve(monkeypatch, _Trickle([b"\n"] * 100, clock=clock, step=10.0))

    with pytest.raises(BackendError) as ei:
        backends._urlopen("http://user:pw@[::1]:8080/v1?key=k", b"{}", {}, 30, clock=clock)

    assert str(ei.value) == "no complete response from http://[::1]:8080/v1 within 30s"
