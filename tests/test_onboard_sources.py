"""The board walk. Folded in after round 1 flagged `build_plan(sources=)` as a parameter with no
caller -- the premature abstraction the seams doctrine warns against."""
import pathlib
import subprocess
import sys

import yaml

from sluice.core.config import load_config
from sluice.onboard.plan import build_plan

SRC = {"reed": {"enabled": True,
                "searches": [["Example search", "https://example.invalid/jobs"]]},
       "remoteok": {"enabled": False, "searches": []}}


def _text(sources=None):
    return build_plan({}, config_dest="/example/c.yaml", profile_dest="/example/p.md",
                      default_vault="/example/vault", sources=sources).config_text


def test_no_sources_emits_only_the_commented_example(tmp_path):
    """The abstain default: every source runs its own neutral example search."""
    text = _text()
    assert "# sources:" in text
    # An unanswered run emits a document that is ALL comments, so it loads as None rather than as a
    # mapping with a null `sources`. That is the abstain property at full strength -- there is no
    # active key to override anything -- and every loader tolerates it (pinned by the enumerated
    # differential in test_onboard_plan.py). Asserting `.get("sources")` on the loaded document
    # AttributeErrors here, which is how this was found.
    assert yaml.safe_load(text) is None


def test_a_walked_source_round_trips_through_the_real_loader(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(_text(SRC), encoding="utf-8")
    cfg = load_config(str(path))
    assert cfg.sources["reed"].enabled is True
    assert cfg.sources["remoteok"].enabled is False
    assert cfg.sources["reed"].searches == [["Example search", "https://example.invalid/jobs"]]


def test_a_search_label_with_yaml_metacharacters_survives(tmp_path):
    nasty = {"reed": {"enabled": True,
                      "searches": [["O'Example: #1, \"remote\"", "https://example.invalid/j?a=b"]]}}
    path = tmp_path / "c.yaml"
    path.write_text(_text(nasty), encoding="utf-8")
    assert load_config(str(path)).sources["reed"].searches[0][0] == "O'Example: #1, \"remote\""


def test_the_walk_is_offline_and_sees_every_registered_board():
    """The walk enumerates every board; it must not drive a browser to do it.

    In a FRESH interpreter, because `sys.modules` is process-global. The same assertion inline
    passes when this file runs alone and FAILS in the full suite -- eight other test files import
    camofox before it -- so it was order-dependent in the direction that reads as a real regression.
    Spawning is well-precedented here (test_paths, test_backends, the guard tests all do it).

    Measured: all 22 sources load with no camofox import.
    """
    probe = (
        "import sys\n"
        "from sluice.ingest import sources as r\n"
        "print(len(r.all_sources()))\n"
        'print([m for m in sys.modules if "camofox" in m])\n'
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                         cwd=str(pathlib.Path(__file__).resolve().parent.parent))
    assert out.returncode == 0, out.stderr
    count, imported = out.stdout.strip().splitlines()
    assert int(count) >= 20, f"the registry enumerated only {count} sources"
    assert imported == "[]", f"enumerating boards imported camofox: {imported}"
