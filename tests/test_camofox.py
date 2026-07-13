from unittest.mock import MagicMock, patch

from sluice.core.camofox import Camofox


def _resp(payload: bytes):
    """A urlopen stand-in usable as a `with` context manager returning `payload`."""
    r = MagicMock()
    r.read.return_value = payload
    r.__enter__.return_value = r
    return r


def test_create_tab_returns_tabid():
    with patch("urllib.request.urlopen", return_value=_resp(b'{"tabId":"abc"}')):
        assert Camofox().create_tab() == "abc"


def test_create_tab_navigates_when_url_given():
    with patch("urllib.request.urlopen", return_value=_resp(b'{"tabId":"abc"}')) as up:
        Camofox().create_tab("https://example.com/jobs")
    # One POST creates the tab, a second POST navigates it.
    assert up.call_count == 2
    navigate_req = up.call_args_list[1].args[0]
    assert navigate_req.full_url.endswith("/tabs/abc/navigate")


def test_create_tab_skips_navigate_when_no_url():
    with patch("urllib.request.urlopen", return_value=_resp(b'{"tabId":"abc"}')) as up:
        Camofox().create_tab()
    assert up.call_count == 1


def test_evaluate_posts_expression_to_the_tab():
    with patch("urllib.request.urlopen", return_value=_resp(b'{"result":[]}')) as up:
        assert Camofox().evaluate("t1", "doStuff()") == {"result": []}
    req = up.call_args_list[0].args[0]
    assert req.full_url.endswith("/tabs/t1/evaluate")
    assert req.method == "POST"


def test_api_captures_network_errors():
    with patch("urllib.request.urlopen", side_effect=OSError("boom")):
        assert Camofox().evaluate("t1", "1+1") == {"error": "boom"}
