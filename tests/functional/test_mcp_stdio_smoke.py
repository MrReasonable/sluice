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
import subprocess
import sys

# NOT `pytest.importorskip("mcp")`: `mcp` is in the `test` extra precisely so this runs for
# real in CI, and a skip guard here would turn an absent dependency into a false green rather
# than protecting anything. `tests/test_renderer_template.py::test_no_test_module_uses_
# importorskip` sweeps the whole tree for exactly this shape (see `test_ai_setup_contract.py`
# for the same reasoning applied to another mcp-dependent test). This test also never imports
# `mcp` itself -- it drives the real package only indirectly, via the child `mcp serve` process.


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
        line = proc.stdout.readline()
    finally:
        proc.kill()
        proc.wait(timeout=10)

    assert line, (
        "mcp serve produced no frame. If the wrapper stopped delegating `.buffer`, "
        "`_claim_fd`'s fallback raises AttributeError and the server dies at startup: "
        f"stderr={proc.stderr.read()[:400]!r}")
    assert json.loads(line)["id"] == 1
