"""Camofox browser client - the impure I/O boundary for browser-driven sources.

A thin HTTP wrapper over the Camofox server: a persistent, authenticated
headless browser reachable at CAMOFOX_URL. Every source's fetch() step drives a
tab through this client; parse() never touches it. The session key (which named
browser profile to drive) defaults to "sluice" and is overridable via
CAMOFOX_SESSION so an operator can point at their own authenticated session.
"""
import json
import os
import urllib.error
import urllib.request

_DEFAULT_URL = "http://127.0.0.1:9377"
_TIMEOUT = 45  # seconds; Camofox navigations can be slow to settle


class Camofox:
    def __init__(
        self,
        base_url: str | None = None,
        user: str = "default",
        session: str = "sluice",
        timeout: int = _TIMEOUT,
    ):
        # Env overrides every knob so offline tests / alt sessions need no code change.
        self.base_url = base_url or os.environ.get("CAMOFOX_URL", _DEFAULT_URL)
        self.user = os.environ.get("CAMOFOX_USER", user)
        self.session = os.environ.get("CAMOFOX_SESSION", session)
        self.timeout = timeout

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
