"""`job-sluice mcp serve` still serves real frames with the output filter installed (#280).

Deliberately a SUBPROCESS over real stdio. The in-memory contract test never wraps `sys.stdout`,
so it is green whether or not the wrapper delegates `.buffer` -- and a wrapper that does not
delegate kills `mcp serve` at startup.

The subprocess INHERITS the ambient environment on purpose -- no `env=` dict. `tests/conftest.py`
sandboxes `SLUICE_CONFIG`, `XDG_*`, `HOME` and `VAULT_DIR` via `monkeypatch.setenv`, which mutates
`os.environ` and so reaches the child. Passing an explicit `env=` would escape that sandbox and let
the child read a developer's real config and vault.

This test is GREEN FROM BIRTH: it passes both before and after the wrapper existed, because
correct `.buffer` delegation is bypassed by mcp's own fd-dup fast path and no wrapper at all is
equally fine. Its entire value is as a regression guard on that delegation -- see
`sluice.core.safeout._Escaped.__getattr__` -- and it was witnessed to fail by breaking that
delegation and confirming this test goes red with the child's own `AttributeError` in the
assertion message, not merely read and trusted.
"""
import json
import queue
import subprocess
import sys
import threading

# NOT `pytest.importorskip("mcp")`: `mcp` is in the `test` extra precisely so this runs for
# real in CI, and a skip guard here would turn an absent dependency into a false green rather
# than protecting anything. `tests/test_renderer_template.py::test_no_test_module_uses_
# importorskip` sweeps the whole tree for exactly this shape (see `test_ai_setup_contract.py`
# for the same reasoning applied to another mcp-dependent test). This test also never imports
# `mcp` itself -- it drives the real package only indirectly, via the child `mcp serve` process.

# Generous for a healthy process (real runs land well under 5s), and bounds the ONE failure mode
# a bare `readline()` cannot see: a child that stays ALIVE but never writes a line -- a protocol
# stall or a deadlock inside `mcp serve`, as opposed to a crash, which closes the pipe and makes
# an unbounded `readline()` return "" immediately. That is why an unbounded read looked fine
# before this bound existed -- the only failure mode reachable in practice was the one that
# self-terminates. There is no `pytest-timeout` plugin and no `--timeout` in `pyproject.toml`'s
# `[tool.pytest.ini_options]`, so nothing above this test would catch a real hang either.
_READ_TIMEOUT_S = 15


def _readline_bounded(stream, timeout):
    """`stream.readline()`, bounded to `timeout` seconds.

    Runs the blocking read on a daemon thread and waits on a queue instead, so a child that
    never writes cannot block the calling thread past `timeout` -- `queue.Queue.get(timeout=...)`
    raises on expiry while a bare `stream.readline()` has no such option. The reader thread is
    abandoned rather than joined again on timeout: it is a daemon, so it cannot keep the process
    alive, and it unblocks on its own once the caller's `finally` kills the child and the pipe's
    write end closes.
    """
    result: queue.Queue = queue.Queue(maxsize=1)
    threading.Thread(target=lambda: result.put(stream.readline()), daemon=True).start()
    try:
        return result.get(timeout=timeout)
    except queue.Empty:
        return None


def test_mcp_serve_starts_and_answers_over_real_stdio(tmp_path):
    proc = subprocess.Popen(
        [sys.executable, "-m", "sluice.cli", "mcp", "serve"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,          # no env= : inherit the conftest-sandboxed environment
    )
    request = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
               "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                          "clientInfo": {"name": "smoke", "version": "0"}}}
    try:
        proc.stdin.write(json.dumps(request) + "\n")
        proc.stdin.flush()
        line = _readline_bounded(proc.stdout, _READ_TIMEOUT_S)
    finally:
        # Killed and reaped on EVERY path out of the try -- answered, timed out, or raised --
        # so a stalled child is never left running past this test.
        proc.kill()
        proc.wait(timeout=10)

    assert line, (
        f"mcp serve produced no frame within {_READ_TIMEOUT_S}s. If the wrapper stopped "
        "delegating `.buffer`, `_claim_fd`'s fallback raises AttributeError and the server dies "
        f"at startup: stderr={proc.stderr.read()[:400]!r}")
    # `id` alone is not enough: a JSON-RPC ERROR frame carries the request id too, so an
    # `id == 1` assertion passes when initialisation FAILED. Require the success shape and
    # reject the failure one, so this row cannot go green on a server that started and then
    # refused -- which is exactly the state a wrapper regression would produce.
    frame = json.loads(line)
    assert frame["id"] == 1, frame
    assert "error" not in frame, f"mcp serve returned a JSON-RPC error frame: {frame}"
    assert "result" in frame, f"mcp serve returned no result: {frame}"
