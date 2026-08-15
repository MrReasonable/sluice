"""Camofox browser client - the impure I/O boundary for browser-driven sources.

A thin HTTP wrapper over the Camofox server: a persistent, authenticated headless browser
reachable at CAMOFOX_URL. Every source's fetch() step drives a tab through this client;
parse() never touches it.

WHICH COOKIES A RUN GETS IS DECIDED BY CAMOFOX_USER, AND ONLY BY IT. The server's persistence
plugin stores one profile per user at sha256(userId), and does not consult the session key at
all. CAMOFOX_SESSION names a session WITHIN that profile: it groups tabs, and it does not
choose whose logins you inherit.

That distinction is not pedantry. Until 2026-08-15 this docstring told the reader that the
SESSION key chose the browser profile, and that setting it was how an operator reached their
own logged-in browser. A production runner set CAMOFOX_SESSION=example-session to reach a profile holding
335 cookies; the setting was inert, the run used the cookie-less `default` profile, LinkedIn
returned zero rows for eight-plus runs, and the auto-retire rule then removed linkedin,
jobserve and indeed. One false sentence, three dead sources, a week undiagnosed. Hence the
warning in __init__: session-without-user is always a mistake, and it must be audible.
"""
import json
import os
import urllib.error
import urllib.request

from sluice.core.log import get_logger

_log = get_logger("core.camofox")

_DEFAULT_URL = "http://127.0.0.1:9377"
_TIMEOUT = 45  # seconds; Camofox navigations can be slow to settle

# The profile driven when CAMOFOX_USER is unset. Named rather than inlined so `doctor` can
# report the resolved profile WITHOUT constructing a client (construction warns, and doctor
# would then say the same thing twice) and cannot drift from the value actually used.
DEFAULT_USER = "default"


class Camofox:
    def __init__(
        self,
        base_url: str | None = None,
        user: str = DEFAULT_USER,
        session: str = "sluice",
        timeout: int = _TIMEOUT,
    ):
        # Env overrides every knob so offline tests / alt sessions need no code change.
        self.base_url = base_url or os.environ.get("CAMOFOX_URL", _DEFAULT_URL)
        self.user = os.environ.get("CAMOFOX_USER", user)
        self.session = os.environ.get("CAMOFOX_SESSION", session)
        self.timeout = timeout
        # The one configuration shape that is always a mistake: an operator who set only
        # CAMOFOX_SESSION has chosen a session and believes they chose a PROFILE. Warn with
        # both names and the inert value, so the message says what to do rather than that
        # something is odd. Setting both is coherent and stays silent.
        if os.environ.get("CAMOFOX_SESSION") and not os.environ.get("CAMOFOX_USER"):
            _log.warning(
                "CAMOFOX_SESSION=%r is set but CAMOFOX_USER is not. The session key does NOT "
                "select the cookie profile -- profiles are keyed on CAMOFOX_USER (currently "
                "%r), so this run inherits %r's logins, not %r's. Set CAMOFOX_USER to the "
                "profile you actually logged in as.",
                self.session, self.user, self.user, self.session)

    def _api(self, method: str, path: str, data: dict | None = None) -> dict:
        """One HTTP call to the Camofox server. Network/JSON errors are captured
        as {"error": ...} rather than raised - the resilience layer (Task 8)
        owns the retry/timeout policy, not this client."""
        body = json.dumps(data).encode() if data else None
        req = urllib.request.Request(f"{self.base_url}{path}", method=method)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, data=body, timeout=self.timeout) as r:
                return json.loads(r.read())
        except Exception as e:
            return {"error": str(e)}

    def create_tab(self, url: str = "") -> str | None:
        """Open a tab in the configured authenticated session and return its id.
        Camofox 400s if a url is passed in the create body, so navigate
        separately once the tab exists."""
        tid = self._api(
            "POST", "/tabs", {"userId": self.user, "sessionKey": self.session}
        ).get("tabId")
        if tid and url:
            self.navigate(tid, url)
        return tid

    def close_tab(self, tid: str) -> dict:
        return self._api("DELETE", f"/tabs/{tid}?userId={self.user}")

    def navigate(self, tid: str, url: str) -> dict:
        return self._api(
            "POST", f"/tabs/{tid}/navigate", {"userId": self.user, "url": url}
        )

    def snapshot(self, tid: str) -> dict:
        return self._api("GET", f"/tabs/{tid}/snapshot?userId={self.user}&format=text")

    def evaluate(self, tid: str, expr: str) -> dict:
        return self._api(
            "POST", f"/tabs/{tid}/evaluate", {"userId": self.user, "expression": expr}
        )

    def scroll(self, tid: str, amount: int = 800) -> dict:
        return self._api(
            "POST",
            f"/tabs/{tid}/scroll",
            {"userId": self.user, "direction": "down", "amount": amount},
        )
