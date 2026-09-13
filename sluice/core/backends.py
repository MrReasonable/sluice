"""LLM judge backends, flat-rate first and self-healing.

ClaudeMaxBackend shells `claude --print` on a configured host (flat-rate, the
primary): set `host` to ssh there, or leave it empty to run `claude_path`
locally. AnthropicBackend calls the Anthropic Messages API directly, and
OpenAiCompatibleBackend calls an OpenAI-compatible chat/completions endpoint
(per-token, the fallback). FallbackBackend tries the primary and, if it errors
(primary host down, timeout, nonzero exit, empty response), falls back
automatically so a run is never blocked. `make_backend` builds any backend by name -- delegating the
per-provider construction to the `backend` seam registry (`sluice/backends/`) so
selection is config-driven and a new provider is a drop-in module. The subprocess
runner and HTTP poster are injected, so everything is tested offline.
"""
import json
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, replace

from sluice.core.log import get_logger

_log = get_logger("core.backends")

DEFAULT_MODELS = {
    "claude-max": "claude-sonnet-4-5",
    "anthropic": "claude-sonnet-4-5",
    "deepseek": "deepseek-v4-flash",
    "openai": "gpt-4o",
}

# Each per-token provider's default API root, overridable per-deployment via the
# provider's *_BASE_URL env var. Named here rather than inlined at each call site
# so the endpoint has one definition to audit and change -- and so tests can pin
# the default without restating a live URL. claude-max is absent: it shells the
# flat-rate CLI and has no endpoint.
DEFAULT_BASE_URLS = {
    "anthropic": "https://api.anthropic.com",
    "deepseek": "https://api.deepseek.com",
    "openai": "https://api.openai.com/v1",
}

# Seconds any one backend invocation may take. ONE spelling, deliberately: this value had
# grown three independent copies (the seam, a factory-local constant, and cv's config
# default), and a factory-local one was measurably INERT -- rebinding it changed nothing,
# because the seam coalesced None before the factory ever saw it, so a maintainer raising
# it for slow composes would have got a silent no-op. Every provider class default, the
# seam, and `CvConfig.compose_timeout` now read this name.
DEFAULT_TIMEOUT = 300


@dataclass(frozen=True)
class Usage:
    """What one backend call cost, as the provider reported it (#308).

    Every count is `int | None`, and `None` is load-bearing: it means THIS PROVIDER DID NOT
    REPORT THIS NUMBER, which is a different fact from a reported zero. A zero-filled Usage
    would claim a call was free, so `ClaudeMaxBackend` -- flat-rate, and run in text mode
    where there is no usage block at all -- answers with its provider and model and every
    COUNT None, rather than being left out of the seam OR handing back a bare `usage=None`:
    the counts it cannot report stay None, while the call stays attributable, so
    `core/usage.py::summarize` can say how many calls a given provider answered that way.
    (This paragraph said `usage=None` until the identity change landed and contradicted it.)

    `input_tokens` is DEFINED as the total input for the call INCLUDING anything served from
    cache, and each parser below normalises into that definition rather than copying its own
    provider's similarly-named field. That is not tidiness: Anthropic's `input_tokens`
    counts UNCACHED tokens only (the cache counters sit beside it, not inside it), so a
    straight copy yields a cache hit rate of `cache_read / (input - cache_read)` -- above
    1.0 on exactly the well-cached call the number exists to report. OpenAI's
    `prompt_tokens` is the other convention and already includes them.

    No `total_tokens`: it is `input + output`, and a stored total is a second value free to
    disagree with its own parts. `completion_tokens_details.reasoning_tokens` is likewise
    not captured -- reasoning tokens are already inside `completion_tokens`, so recording
    both invites double counting for no new fact.

    `provider` is passed in by the registry factory rather than inferred, because
    `OpenAiCompatibleBackend` serves deepseek, openai and any other compatible endpoint: the
    class genuinely does not know which vendor it is talking to, and a base_url guess would
    be a classifier where the caller already has the answer.
    """
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None


@dataclass(frozen=True)
class Completion:
    """What `Backend.complete` returns: the text, and optionally what it cost.

    The seam used to return a bare `str`, so every provider's `usage` block was parsed past
    and dropped and a run's spend was unobservable (#308). Carrying it on the RESULT rather
    than on a mutable attribute is what makes it attributable to a specific call --
    `FallbackBackend.last_backend` is the counter-example, overwritten on every call and so
    unable to say which leg served which completion.

    `unserved_usage` is spend that happened but did NOT produce this text: a
    `FallbackBackend` primary that parsed a usage block and then raised, whose tokens were
    still billed. Without it that spend vanishes inside the fallback's `except`, and a leg
    that bills on every call while never serving one reads as free.
    """
    text: str
    usage: Usage | None = None
    unserved_usage: tuple[Usage, ...] = ()


class BackendError(Exception):
    """A backend call that cannot be used, whatever it may already have cost.

    `usage` is set when the provider reported a usage block and the response was THEN judged
    unusable -- a truncation, a non-stop finish_reason. It is the only route by which that
    spend can be recorded at all, since on this path there is no return value to carry it.
    A transport failure (timeout, HTTP error, missing binary) has no body to parse and leaves
    it None, so `usage is None` here means "no usage was ever seen", never "it was free".

    `unserved_usage` carries the OTHER legs that also billed, mirroring the field of the same
    name on `Completion`. One call can spend on more than one backend: `FallbackBackend` with
    both legs reporting usage and then failing is paid for nothing TWICE, which is the worst
    case for cost and so exactly the one a report must not under-state. A single `usage` field
    could only carry the first of them, and did -- the first cut of this coalesced the two
    with `e.usage or fe.usage` and silently dropped the fallback's.

    Every count on this path is unserved by definition (the call raised), so both fields are
    recorded with `served: false`; which leg each belonged to is in its own `provider`.
    """

    def __init__(self, *args, usage: Usage | None = None,
                 unserved_usage: tuple = ()):
        super().__init__(*args)
        self.usage = usage
        self.unserved_usage = unserved_usage


def _int_or_none(value):
    """A count, or None when the provider did not report one.

    `bool` is rejected before `int` because `bool` subclasses `int`, so a JSON `true`
    would otherwise load as the count 1 -- the same trap `lead_ttl_days`' validator
    exists for. Anything non-numeric is treated as absent rather than raised on: a
    malformed usage block must not fail a call whose TEXT is perfectly good.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def openai_usage(data, *, provider: str, model: str) -> Usage | None:
    """Usage from an OpenAI-compatible chat/completions body, or None if it reported none.

    ONE parser for the whole family this class serves -- openai, deepseek, a local server --
    because which keys a compatible endpoint fills is a property of the deployment, not of
    a name in our registry.

    The input total is read in two ways, and which one applies is decided by what is
    PRESENT rather than by the provider name:

      * DeepSeek reports `prompt_cache_hit_tokens` + `prompt_cache_miss_tokens`, documented
        as partitioning "the input of this request". Their docs say nothing about how either
        relates to `prompt_tokens`, so when BOTH are present their sum is used as the total
        -- self-consistent by construction, with no relation assumed. Half a partition is
        not a partition, so one without the other falls through.
      * Otherwise `prompt_tokens`, which OpenAI documents as including the cached tokens,
        with `prompt_tokens_details.cached_tokens` as a breakdown of it.
    """
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return None
    details = usage.get("prompt_tokens_details")
    details = details if isinstance(details, dict) else {}
    hit = _int_or_none(usage.get("prompt_cache_hit_tokens"))
    miss = _int_or_none(usage.get("prompt_cache_miss_tokens"))
    if hit is not None and miss is not None:
        total_in = hit + miss
        cache_read = hit
    else:
        total_in = _int_or_none(usage.get("prompt_tokens"))
        cache_read = _int_or_none(details.get("cached_tokens"))
        if cache_read is None:
            cache_read = hit
    out = Usage(provider=provider, model=model, input_tokens=total_in,
                output_tokens=_int_or_none(usage.get("completion_tokens")),
                cache_read_tokens=cache_read,
                cache_write_tokens=_int_or_none(details.get("cache_write_tokens")))
    # An empty `usage: {}` parses to a Usage of all-None counts, which says the same thing
    # as no usage block at all while looking like a report. Collapse it to None so the two
    # cannot be told apart downstream by accident.
    return out if _reported_anything(out) else None


def anthropic_usage(data, *, provider: str, model: str) -> Usage | None:
    """Usage from an Anthropic Messages body, or None if it reported none.

    `input_tokens` here is UNCACHED tokens only -- the two cache counters are separate
    totals, not a breakdown of it -- so the normalised input is the sum of all three. See
    `Usage` for why copying the field straight across breaks the hit rate.
    """
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return None
    uncached = _int_or_none(usage.get("input_tokens"))
    cache_read = _int_or_none(usage.get("cache_read_input_tokens"))
    cache_write = _int_or_none(usage.get("cache_creation_input_tokens"))
    parts = [n for n in (uncached, cache_read, cache_write) if n is not None]
    out = Usage(provider=provider, model=model,
                input_tokens=sum(parts) if parts else None,
                output_tokens=_int_or_none(usage.get("output_tokens")),
                cache_read_tokens=cache_read, cache_write_tokens=cache_write)
    return out if _reported_anything(out) else None


def _reported_anything(u: Usage) -> bool:
    """Did the provider actually report any number at all?

    Compared against a Usage carrying the same identity and NOTHING else, rather than by
    enumerating the count fields: a count added to `Usage` later defaults to None on both
    sides of this comparison, so it is covered with no second edit here.

    Introspecting `dataclasses.fields` to skip the two `str` fields was the first shape and
    was measurably broken: `f.type` holds the EVALUATED annotation for this module (there is
    no `from __future__ import annotations`), so it is `<class 'str'>` and `int | None`, and
    `f.type != "str"` is therefore true of every field including the two identity strings --
    which are never None, so the function would have answered True unconditionally. It would
    also have meant something different again under postponed annotations, where `f.type` IS
    the source spelling. A derivation that depends on which of those is in force is not one.
    """
    return u != Usage(provider=u.provider, model=u.model)


def option_like(value) -> bool:
    """Would `value` be read as an OPTION rather than the positional it is meant to be?

    Pure, and SHARED with `core/doctor.py`'s rules table on purpose. `doctor --offline`
    never constructs a backend (it classifies from config alone), so the construction
    guard below is unreachable there -- and an offline run reporting `ok` for a config the
    constructor would refuse is exactly the quiet-wrong-answer this codebase engineers out.
    One predicate means the two cannot drift into disagreeing about what counts.

    A LEADING `-` only. Hyphens inside a value are ordinary in hostnames
    (`build-box-01.invalid` is fine); it is the leading one that flips the argument's
    meaning from a value into a flag.
    """
    return isinstance(value, str) and value.startswith("-")


def _urlopen(url, data, headers, timeout):
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode()
    except urllib.error.HTTPError as e:
        # The provider's actual complaint -- unknown model id, bad key, rate limit
        # -- is in the response *body*. urllib does not put it in str(e), so
        # without this every 4xx/5xx collapses to "HTTP Error 400: Bad Request"
        # and the real cause is lost. Read it once and attach it.
        try:
            detail = e.read().decode(errors="replace").strip()
        except Exception:  # body already consumed / not readable; fall back to the reason
            detail = ""
        raise BackendError(
            f"HTTP {e.code} from {url}: {detail[:500] or e.reason}") from e


def _redact(text: str, secrets: dict[str, str]) -> str:
    """Replace each sensitive value with a label, so a backend error keeps its
    diagnostic shape without disclosing the host or an absolute path -- both reach
    proc.stderr on an ssh/exec failure (and str(a runner exception)), or proc.stdout
    as the fallback diagnostic source on a non-zero exit (#115), and fan out to
    WARNING logs (FallbackBackend, judge) and the doctor health report.

    Matching is TOKEN-AWARE: a value is replaced only where it stands as a whole token
    (`(?<!\\w)value(?!\\w)`), never inside a longer word. That is what lets a genuinely
    short host -- `db`, `qa` -- be scrubbed without a length floor mangling every `db`
    inside `database`; a plain substring replace could not have both. The lookaround
    (not `\\b`) is deliberate: `\\b` cannot anchor a value that begins with a non-word
    char, so an absolute claude_path (`/home/.../claude`) would never match under `\\b`.

    A secret is SKIPPED only when it is empty. The generic-default exemption is NOT here:
    it is ROLE-BASED and lives in the caller (ClaudeMaxBackend._scrub), which omits
    claude_path from the map when it is the default 'claude' -- so this function redacts
    every non-empty token it is given, and a host that happens to be NAMED 'claude' is
    scrubbed like any other configured host.

    Secrets are replaced LONGEST-first so that when one is a substring of another (the
    host can appear inside the absolute claude_path) the longer is caught whole before the
    shorter can fragment it -- otherwise the shorter replace would alter the longer's text
    and its remaining, possibly username-bearing, fragment would survive.

    ONE EXCEPTION, named here so this docstring is not quietly false: the option-like
    argv refusal in ClaudeMaxBackend.__init__ prints its offending value VERBATIM. It
    fires before self.host is assigned, so _scrub cannot reach it, and the refusal would
    be unactionable without naming what was rejected -- the operator has to see which of
    their two fields carries the leading '-'. The value is by construction not a working
    host: it is a flag the config author typed by mistake.
    """
    for value, label in sorted(secrets.items(), key=lambda kv: len(kv[0]), reverse=True):
        if value:
            text = re.sub(rf"(?<!\w){re.escape(value)}(?!\w)", label, text)
    return text


class ClaudeMaxBackend:
    def __init__(self, model, *, host: str = "", claude_path: str = "claude",
                 cmd_template=None, runner=subprocess.run,
                 timeout=DEFAULT_TIMEOUT, effort="max", provider="claude-max"):
        # cmd_template is the argv up to (but not including) the prompt on stdin.
        # host/claude_path are ignored once cmd_template is supplied explicitly.
        #
        # ARGUMENT INJECTION (CWE-88). `host` lands where ssh expects a DESTINATION and
        # `claude_path` where it expects the remote command, but both are read as OPTIONS
        # the moment they begin with `-`. `host="-oProxyCommand=<cmd>"` runs <cmd> on THIS
        # machine, before any connection is attempted -- so it sits UNDER the
        # `--disallowedTools` deny-list below rather than behind it: ssh runs before claude
        # exists, and no claude-level control can constrain it. Refused at construction,
        # which is this codebase's rule for a value that cannot be made safe later.
        #
        # Checked UNCONDITIONALLY, not only when this constructor builds the argv. An
        # explicit cmd_template makes these two unused for the argv, but validating anyway
        # keeps the contract "these fields are never option-like" with no path around it,
        # and a caller supplying both a template and a nonsense host has a config bug worth
        # hearing about either way.
        #
        # BackendError, not ValueError: `Sluice.doctor` wraps construction in
        # `except BackendError` and renders it as a `dead` backend. A ValueError would
        # escape that and crash the command someone runs precisely to be told their host is
        # wrong.
        #
        # A leading `-` only. Hyphens INSIDE a value are ordinary in hostnames, and this
        # deliberately does NOT try to police shell metacharacters in `claude_path`: over
        # ssh the remote args are joined and run by the remote shell, but that field's whole
        # purpose is to name an executable, so a user putting a command there is choosing
        # what runs, not escaping a boundary they thought they had.
        for field, value in (("host", host), ("claude_path", claude_path)):
            if option_like(value):
                raise BackendError(
                    f"claude-max {field} must not begin with '-': {value!r}. ssh and the "
                    f"shelled binary both read a leading '-' as an OPTION rather than a "
                    f"{'destination' if field == 'host' else 'command'}, which turns this "
                    f"config value into argument injection (e.g. -oProxyCommand=...)."
                )
        self.model = model
        self.provider = provider
        self.host = host
        self.claude_path = claude_path
        if cmd_template is not None:
            self.cmd_template = cmd_template
        else:
            base = [
                claude_path, "--print",
                "--model", model, "--effort", effort,
                # CWE-250. `bypassPermissions` otherwise hands this agent unrestricted
                # Write/Edit/Bash on whatever host runs it -- a real privilege surface
                # whether or not it ever misbehaves. Deny rules are evaluated BEFORE the
                # permission mode, so they still bind under bypassPermissions (whereas
                # --allowedTools is ignored in that mode, which is why this is the
                # deny-list and not an allow-list).
                #
                # It NARROWS the escape routes rather than closing them: `Task` and MCP
                # write tools are not covered, and Bash is a write *vector* rather than a
                # write tool. These four are what an agent reaches for first.
                #
                # Placed BEFORE `--permission-mode` deliberately. The flag is variadic
                # (`<tools...>`), so the following flag is what terminates it; leaving it
                # last would make any argv appended later get swallowed as a tool name.
                # Measured: composition still passes the gate with violations=0 in this
                # position, and the prompt arrives on stdin so nothing positional follows.
                "--disallowedTools", "Write", "Edit", "NotebookEdit", "Bash",
                "--permission-mode", "bypassPermissions",
            ]
            # Empty host runs claude_path locally; a configured host (e.g.
            # "<your-claude-host>") shells out over ssh instead. claude is
            # commonly NOT on a remote host's non-interactive PATH, so
            # claude_path should be the absolute path in that case.
            self.cmd_template = ["ssh", host] + base if host else base
        self.runner = runner
        self.timeout = timeout

    def _scrub(self, text: str) -> str:
        """Strip this backend's own secrets (host, configured claude_path) from any
        text that becomes a BackendError message -- proc.stderr OR str(a runner
        exception), whose TimeoutExpired.cmd / FileNotFoundError forms carry the argv.

        The 'claude' exemption is ROLE-BASED, not value-based: claude_path is added to the
        secret map only when it has been configured away from its default 'claude' (the
        CLI's own binary name, non-sensitive and a token in ordinary diagnostics). host is
        always added when set -- so a host that happens to be NAMED 'claude' is a
        configured, sensitive value and IS redacted, rather than colliding with the default
        path's exemption. Scrubs by self.host / self.claude_path, which cover the argv only
        when they built it: the production path (make_backend passes host=/claude_path=,
        never an explicit cmd_template). A caller supplying a divergent cmd_template with
        default host/path is out of scope -- not reachable via make_backend."""
        secrets: dict[str, str] = {}
        if self.host:
            secrets[self.host] = "<host>"
        if self.claude_path and self.claude_path != "claude":
            secrets[self.claude_path] = "<path>"
        return _redact(text, secrets)

    def complete(self, prompt: str) -> "Completion":
        try:
            proc = self.runner(self.cmd_template, input=prompt,
                               capture_output=True, text=True, timeout=self.timeout)
        except Exception as e:  # timeout, ssh failure, missing binary
            # str(e) carries the argv -- TimeoutExpired.cmd is self.cmd_template
            # (["ssh", host, claude_path, ...]) and FileNotFoundError names the binary.
            # A hung host times out here, NOT at the exit-code branch, so this leak route
            # is real; scrub before the message reaches a WARNING log or the health report.
            #
            # `from None`, not `from e`: the scrubbed message already carries the diagnostic,
            # but the RAW cause `e` would remain chained -- and track/classify.py logs a failed
            # complete() with `_log.exception`, which renders the whole chain, re-leaking the
            # unscrubbed argv the message just scrubbed. `from None` clears `__cause__` and sets
            # `__suppress_context__`, so every traceback-RENDERING sink (`_log.exception`,
            # `traceback.format_exception`) omits `e` -- which is the entire realistic leak
            # surface. (`e` stays referenced via `__context__`; only a sink that walked that
            # attribute by hand, which nothing here does, could still reach it.) This is the
            # residual an earlier round wrongly called "safe on the premise no sink uses
            # exc_info" -- one does; the rendered chain is closed here at the source.
            raise BackendError(f"claude-max invocation failed: {self._scrub(str(e))}") from None
        if proc.returncode != 0:
            # A non-zero exit carrying NO stderr is a real, observed state (seen while
            # running three composes concurrently against one host, where a single call at
            # the same moment succeeded). The bare message was `claude-max exit 1:` and
            # stopped there, so the operator learned only that something failed -- no cause,
            # no next step. Say what is known and what to try; an error that names its own
            # emptiness beats one that trails off mid-sentence.
            #
            # `.strip()` before the test, not just truthiness: whitespace-only stderr renders
            # identically to empty and must take the same branch.
            #
            # stdout is the FALLBACK source, not the first choice: stderr is the
            # conventional diagnostic channel, and this CLI's own quirk is what makes
            # stdout worth checking at all. #115 (production): a non-zero exit from a
            # genuine, mundane cause ("You've hit your weekly limit") wrote NOTHING to
            # stderr and the whole message to stdout -- so the generic fallback below
            # fired instead, actively naming the wrong two causes (contention, an expired
            # session) while the real one sat unread in the stream this branch never read.
            detail = self._scrub(proc.stderr).strip()[:200]
            if not detail:
                detail = self._scrub(proc.stdout).strip()[:200]
            raise BackendError(
                f"claude-max exit {proc.returncode}: " + (detail or
                "the CLI exited non-zero and wrote nothing on either stream, so there is "
                "no diagnostic to report. Commonly contention (concurrent invocations "
                "against one host) or an expired CLI session; retry the lead on its own, "
                "and if it persists run the CLI by hand on that host to see it "
                "interactively."))
        text = proc.stdout.strip()
        # Exit 0 with no text is a FAILED call wearing a successful one's clothes. Both
        # siblings already refuse it (OpenAiCompatibleBackend, AnthropicBackend); claude-max
        # was the outlier, returning "" for the caller to notice by itself. Raising here means
        # the same underlying condition triggers the documented fallback whichever provider
        # hits it, instead of one raising and one handing back a useless string.
        #
        # The message also appends the SCRUBBED stderr (self._scrub) when present: an exit-0
        # empty response often has a warning on stderr (quota, deprecation) that is the only
        # clue why. Scrubbing at construction is what makes surfacing it safe (see _scrub);
        # the append is conditional so a truly empty stderr keeps the clean "...whitespace)".
        #
        # Only the EMPTY half of the siblings' pair is implemented here. Their other guard keys on
        # finish_reason/stop_reason to catch a TRUNCATION, and this backend has no equivalent
        # because it runs the CLI in TEXT mode -- not because a CLI cannot report one:
        # `claude --print --output-format json` returns exactly that, a `stop_reason` field (plus
        # `is_error`/`subtype`). Adopting it would replace this whole parse path, so it is deferred,
        # not impossible. The emptiness check is text-mode-coupled for the same reason: a JSON
        # envelope is never empty, so under --output-format json a null `result` would sail through
        # this guard untouched.
        if not text:
            detail = self._scrub(proc.stderr).strip()[:200]
            raise BackendError(
                f"claude-max returned no text (exit 0, {len(proc.stdout)} chars of whitespace"
                + (f"; stderr: {detail}" if detail else "") + ")"
            )
        # A Usage carrying the IDENTITY and no counts. Flat-rate, and run in TEXT mode,
        # so there are no token counts to report -- `--output-format json` would carry them
        # (along with a stop_reason), but adopting it replaces this whole parse path, see the
        # truncation note above. Every count therefore stays None, which is what "did not
        # report" looks like; inventing zeros would claim the call was free.
        #
        # Returning the identity anyway rather than a bare `usage=None` is what lets the
        # usage log record that this call HAPPENED, on this provider and model. Without it a
        # flat-rate call is anonymous in the log and `job-sluice usage` cannot say which
        # provider the silent calls went to -- the summary would simply be missing them.
        return Completion(text, usage=Usage(provider=self.provider, model=self.model))


class OpenAiCompatibleBackend:
    """Any OpenAI-compatible chat/completions endpoint (DeepSeek, OpenAI, Together,
    a local server, ...). Provider is just a base_url + key. `max_tokens` is sent
    only when set; an incomplete response (finish_reason other than stop, e.g.
    length or content_filter) or empty content is a hard error, not a partial.

    `provider` is the registry name of whoever is being called, supplied by that provider's
    factory, and it exists only to label the `Usage` this returns. It has a generic default
    because the class genuinely cannot know: one deployment of it is DeepSeek and the next
    is a local server, and guessing from base_url would be a classifier where the caller
    already has the answer."""

    def __init__(self, model, *, api_key, base_url, http=_urlopen, timeout=DEFAULT_TIMEOUT,
                 max_tokens=None, provider="openai-compatible"):
        self.model = model
        self.provider = provider
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key = api_key
        self.http = http
        self.timeout = timeout
        self.max_tokens = max_tokens

    def complete(self, prompt: str) -> "Completion":
        body = {"model": self.model,
                "messages": [{"role": "user", "content": prompt}]}
        if self.max_tokens is not None:
            body["max_tokens"] = self.max_tokens
        headers = {"Content-Type": "application/json",
                   "Authorization": f"Bearer {self.api_key}"}
        try:
            data = json.loads(self.http(self.url, json.dumps(body).encode(),
                                        headers, self.timeout))
            # Parsed BEFORE the refusals below, and attached to them. Those tokens were
            # billed whether or not the response is usable, and an exception is the only
            # thing left to carry them on a path that has no return value (#308).
            # `or Usage(identity)`: an endpoint that sent no usage block still made a
            # call, and the log needs to know whose. The parser answers None for "this body
            # reported no counts", which is the honest answer ABOUT THE BODY and is what its
            # own tests pin; the coalesce here is about the CALL, whose provider and model
            # are known regardless. Counts stay None either way -- nothing is invented.
            usage = (openai_usage(data, provider=self.provider, model=self.model)
                     or Usage(provider=self.provider, model=self.model))
            choice = data["choices"][0]
            reason = choice.get("finish_reason")
            # Only a natural stop (or an endpoint that omits the field) is a
            # complete answer. length/content_filter/etc. are partials and must
            # fail loudly, not slip through as a truncated CV -- mirror the
            # AnthropicBackend guards below.
            if reason not in (None, "stop"):
                raise BackendError(
                    f"openai-compatible response incomplete (finish_reason={reason})",
                    usage=usage)
            text = choice["message"]["content"].strip()
            if not text:
                raise BackendError(
                    f"openai-compatible returned no text (finish_reason={reason})",
                    usage=usage)
            return Completion(text, usage=usage)
        except BackendError:
            raise
        except Exception as e:
            raise BackendError(f"openai-compatible call failed: {e}") from e


class AnthropicBackend:
    """Direct Anthropic Messages API client (no `claude` CLI needed). The response
    content is a list of typed blocks (text, thinking, tool_use); we join every
    text block. Empty text content (as a refusal produces) and a truncation
    (stop_reason==max_tokens) are both hard errors, never a silent partial."""

    _VERSION = "2023-06-01"

    def __init__(self, model, *, api_key, base_url=DEFAULT_BASE_URLS["anthropic"],
                 http=_urlopen, timeout=DEFAULT_TIMEOUT, max_tokens=8192,
                 provider="anthropic"):
        self.model = model
        self.provider = provider
        self.url = base_url.rstrip("/") + "/v1/messages"
        self.api_key = api_key
        self.http = http
        self.timeout = timeout
        self.max_tokens = max_tokens

    def complete(self, prompt: str) -> "Completion":
        body = {"model": self.model, "max_tokens": self.max_tokens,
                "messages": [{"role": "user", "content": prompt}]}
        headers = {"content-type": "application/json",
                   "x-api-key": self.api_key,
                   "anthropic-version": self._VERSION}
        try:
            data = json.loads(self.http(self.url, json.dumps(body).encode(),
                                        headers, self.timeout))
            # Before the refusals, for the same reason as the sibling above: a truncated
            # response still billed, and the exception is the only carrier left.
            usage = (anthropic_usage(data, provider=self.provider, model=self.model)
                     or Usage(provider=self.provider, model=self.model))
            if data.get("stop_reason") == "max_tokens":
                raise BackendError("anthropic response truncated (stop_reason=max_tokens)",
                                   usage=usage)
            text = "\n".join(
                b.get("text", "") for b in data.get("content", [])
                if b.get("type") == "text" and b.get("text")).strip()
            if not text:
                raise BackendError(
                    f"anthropic returned no text (stop_reason={data.get('stop_reason')})",
                    usage=usage)
            return Completion(text, usage=usage)
        except BackendError:
            raise
        except Exception as e:
            raise BackendError(f"anthropic call failed: {e}") from e


class FallbackBackend:
    def __init__(self, primary, fallback):
        self.primary = primary
        self.fallback = fallback
        self.last_backend = None

    def complete(self, prompt: str) -> "Completion":
        try:
            out = self.primary.complete(prompt)
            self.last_backend = "primary"
            return out
        except BackendError as e:
            _log.warning("primary backend failed, falling back: %s", e)
            try:
                out = self.fallback.complete(prompt)
            except BackendError as fe:
                # Both legs are down. Report both causes: the fallback's error alone
                # is the less interesting half (the primary going down is what put us
                # here), and chaining from the primary keeps its traceback attached.
                #
                # BOTH legs' spend rides along on the raised error: with both down there is
                # no completion to hang it on, and dropping either would make a leg that bills
                # then fails look free. Kept as two fields rather than coalesced -- `e.usage
                # or fe.usage` was the first cut and silently reported only the primary, in
                # the one case where the user paid twice and got nothing.
                raise BackendError(
                    f"both backends failed: primary={e}; fallback={fe}",
                    usage=e.usage,
                    unserved_usage=() if fe.usage is None else (fe.usage,)) from e
            self.last_backend = "fallback"
            # No provider/model of its own to stamp: the LEG that served already did that,
            # which is what makes attribution structural here rather than reconstructed.
            # What this must NOT do is swallow a primary that spent tokens before raising --
            # those are billed, and this `except` is the only place they are still visible.
            if e.usage is not None:
                out = replace(out, unserved_usage=out.unserved_usage + (e.usage,))
            return out


def make_backend(name, model="", *, http=_urlopen, runner=subprocess.run,
                 timeout=DEFAULT_TIMEOUT,
                 api_key="", base_url="", max_tokens=None,
                 claude_host="", claude_path="claude", effort="max"):
    """Build one backend by name, delegating provider construction to the `backend`
    seam registry (`sluice/backends/`).

    A thin compatibility shim, deliberately: this is the tested, config-driven by-name
    factory every caller (and `Sluice.backend`'s role helpers) already uses. It keeps
    two responsibilities here, above the registry, so behaviour is unchanged:

    - the unknown-name guard raises `BackendError` listing the valid names (never a
      silent default, and never the bare `UnknownAdapter`/`KeyError` the registry would
      raise -- callers assert `BackendError`), and
    - `model` defaults to `DEFAULT_MODELS[name]` when omitted, so the default-model map
      stays the single place a provider's default model lives.

    Everything provider-specific -- which class, which default endpoint, whether a key is
    required -- now lives in the provider's module under `sluice/backends/`, reached via
    `plugins.get("backend", name)`. The caller (which knows `name`) still resolves and
    passes the right api_key/base_url; each factory reads only what it needs.
    """
    from sluice.core import plugins
    import sluice.backends  # noqa: F401  -- import triggers factory self-registration

    if name not in DEFAULT_MODELS:
        raise BackendError(
            f"unknown backend '{name}' (expected {', '.join(DEFAULT_MODELS)})")
    model = model or DEFAULT_MODELS[name]
    try:
        factory = plugins.get("backend", name)
    except plugins.UnknownAdapter as e:
        # name is in DEFAULT_MODELS but its plugin module failed to import (autoload
        # swallows a broken plugin's ImportError, leaving the name unregistered). Surface
        # loudly as BackendError -- the fail-at-construction contract callers rely on --
        # rather than the KeyError-flavoured UnknownAdapter. The registry-completeness
        # test is what stops this reaching a user in the first place.
        raise BackendError(str(e)) from e
    # Coalesce None HERE, at the one choke point every provider crosses, so a future
    # provider that forgets the factory-level idiom is still covered. An explicit
    # `timeout=None` reaching an HTTP provider ends at `urlopen(timeout=None)`, which
    # blocks on the socket default -- the same wait-forever as
    # `subprocess.run(timeout=None)`, by a different call.
    #
    # Each factory ALSO omits the argument when it is None, so the class default applies
    # on the DIRECT construction path this function never sees (the seam's guard suite
    # resolves factories through `plugins.get`). An earlier version claimed that second
    # line existed while only claude-max had it -- the three HTTP siblings returned
    # `timeout=None` from a direct `_make`, the exact hazard this comment names.
    timeout = DEFAULT_TIMEOUT if timeout is None else timeout
    # `provider=name` is passed from HERE rather than written as a literal inside each
    # factory, for the same reason the timeout is coalesced here: this is the one choke point
    # every provider crosses, and it already holds the name. A per-factory literal is a third
    # spelling of something already stated twice in that module (the `register(...)` call and
    # the missing-key message), so a module copied to add a provider would keep the original's
    # label and mislabel every usage row it wrote -- silently, since nothing downstream can
    # tell a wrong provider name from a right one. Threaded through the seam instead, the
    # label cannot disagree with the name that selected the factory.
    return factory(model, api_key=api_key, base_url=base_url, http=http, runner=runner,
                   timeout=timeout, max_tokens=max_tokens, claude_host=claude_host,
                   claude_path=claude_path, effort=effort, provider=name)
