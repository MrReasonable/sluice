"""Token-usage parsing, per provider response shape (#308).

The whole point of these rows is that the three shapes DISAGREE about what their input
counter means, and the disagreement is invisible in the field names:

    Anthropic   `input_tokens` EXCLUDES cache reads and writes -- they sit beside it
    OpenAI      `prompt_tokens` INCLUDES the cached tokens
    DeepSeek    `prompt_cache_hit_tokens` + `prompt_cache_miss_tokens` partition the input,
                with nothing documented about how either relates to `prompt_tokens`

`Usage.input_tokens` is DEFINED as the total input including anything served from cache, so
each parse normalises into that definition rather than copying its provider's own field. Two
rows below are built so a copy gives a DIFFERENT answer from the normalisation -- the
Anthropic row (where a copy under-reports the denominator badly enough to put the cache hit
rate above 1.0, on exactly the well-cached call the number exists to report) and the DeepSeek
row (whose `prompt_tokens` is deliberately set to a value that disagrees with hit+miss, so a
parse reading it is caught). A row where both readings agree would certify nothing.
"""
import pytest

from sluice.core.backends import (
    AnthropicBackend, BackendError, ClaudeMaxBackend, Completion, FallbackBackend,
    OpenAiCompatibleBackend, Usage, anthropic_usage, openai_usage,
)


def _http_returning(payload):
    def http(url, data, headers, timeout):
        return payload

    return http


class _Proc:
    def __init__(self, returncode, stdout, stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


# ---------------------------------------------------------------- the pure parsers

def test_openai_usage_reads_prompt_tokens_as_the_total_including_cached():
    """OpenAI documents `prompt_tokens` as including the cached tokens, so it IS the total
    and `prompt_tokens_details.cached_tokens` is a breakdown of it, not an addition to it."""
    u = openai_usage(
        {"usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
                   "prompt_tokens_details": {"cached_tokens": 80, "cache_write_tokens": 0}}},
        provider="openai", model="a-model")
    assert u == Usage(provider="openai", model="a-model", input_tokens=100,
                      output_tokens=20, cache_read_tokens=80, cache_write_tokens=0)


def test_openai_usage_keeps_a_reported_zero_distinct_from_an_absent_field():
    """`cache_write_tokens: 0` above is a provider SAYING zero; the row below omits the key
    entirely. Both must survive as themselves -- 0 and None -- or "nothing was written to
    cache" and "this provider does not report cache writes" collapse into one fact."""
    u = openai_usage(
        {"usage": {"prompt_tokens": 100, "completion_tokens": 20,
                   "prompt_tokens_details": {"cached_tokens": 80}}},
        provider="openai", model="a-model")
    assert (u.cache_write_tokens, u.cache_read_tokens) == (None, 80)


def test_deepseek_usage_prefers_the_hit_miss_partition_over_prompt_tokens():
    """DeepSeek's docs define hit+miss as partitioning "the input of this request" and say
    NOTHING about `prompt_tokens`, so the parse uses hit+miss and assumes no relation.

    `prompt_tokens` is 999 here purely to make the two readings disagree: a parse that
    reached for it would report 999 and this row would fail. A realistic payload has them
    equal, which is exactly why a realistic payload cannot test this."""
    u = openai_usage(
        {"usage": {"prompt_tokens": 999, "completion_tokens": 20,
                   "prompt_cache_hit_tokens": 64, "prompt_cache_miss_tokens": 36}},
        provider="deepseek", model="a-model")
    assert (u.input_tokens, u.cache_read_tokens, u.cache_write_tokens) == (100, 64, None)


def test_deepseek_usage_falls_back_to_prompt_tokens_when_only_one_half_is_present():
    """Half a partition is not a partition. With `miss` absent there is nothing
    self-consistent to sum, so `prompt_tokens` is the only available total."""
    u = openai_usage(
        {"usage": {"prompt_tokens": 100, "completion_tokens": 20,
                   "prompt_cache_hit_tokens": 64}},
        provider="deepseek", model="a-model")
    assert (u.input_tokens, u.cache_read_tokens) == (100, 64)


def test_anthropic_usage_sums_the_three_counters_because_input_tokens_excludes_cache():
    """Anthropic's `input_tokens` is UNCACHED tokens only. The numbers here are chosen so a
    direct copy is not merely inaccurate but self-evidently broken: copying 10 with a
    cache_read of 500 puts the hit rate at 50x."""
    u = anthropic_usage(
        {"usage": {"input_tokens": 10, "output_tokens": 20,
                   "cache_read_input_tokens": 500, "cache_creation_input_tokens": 40}},
        provider="anthropic", model="a-model")
    assert u == Usage(provider="anthropic", model="a-model", input_tokens=550,
                      output_tokens=20, cache_read_tokens=500, cache_write_tokens=40)
    # The naive reading, named so the diff between them is on the record.
    assert u.input_tokens != 10


def test_anthropic_usage_without_cache_counters_is_just_the_input():
    u = anthropic_usage({"usage": {"input_tokens": 10, "output_tokens": 20}},
                        provider="anthropic", model="a-model")
    assert (u.input_tokens, u.cache_read_tokens, u.cache_write_tokens) == (10, None, None)


@pytest.mark.parametrize("parse,key", [(openai_usage, "prompt_tokens"),
                                      (anthropic_usage, "input_tokens")])
@pytest.mark.parametrize("junk", [True, False, "120", 12.5, None, {}, [1]])
def test_a_non_integer_count_is_read_as_unreported_rather_than_trusted(parse, key, junk):
    """A count is taken only when it is a real int. `OpenAiCompatibleBackend` serves "any
    OpenAI-compatible endpoint", including a local server, so a wrong-typed field is a
    deployment away rather than hypothetical -- and every one of these values would otherwise
    reach a total.

    `True` and `False` are the load-bearing rows: bool subclasses int, so without an explicit
    bool check a JSON `true` loads as the count 1 and a `false` as 0, quietly. That is the same
    trap `lead_ttl_days`' validator exists for, and it was a comment here with nothing
    falsifying it until this row was added -- the mutation survived.

    A float is excluded too: token counts are whole, and accepting 12.5 would put a
    non-integer into a sum that is reported as a token count."""
    u = parse({"usage": {key: junk, "output_tokens": 5, "completion_tokens": 5}},
              provider="p", model="m")
    assert u is not None            # the output count IS reported, so the block is not empty
    assert u.input_tokens is None


@pytest.mark.parametrize("parse", [openai_usage, anthropic_usage])
@pytest.mark.parametrize("data", [{}, {"usage": None}, {"usage": {}}, {"usage": "nonsense"}])
def test_a_response_with_no_usage_block_reports_None_not_zeros(parse, data):
    """"Not reported" must stay distinguishable from "genuinely free" -- a Usage of zeros
    would claim the call cost nothing, which is a different and false statement."""
    assert parse(data, provider="p", model="m") is None


# ---------------------------------------------------------------- through complete()

def test_openai_complete_returns_the_text_and_its_usage():
    b = OpenAiCompatibleBackend(
        "a-model", api_key="k", base_url="https://api.example.invalid",
        provider="deepseek",
        http=_http_returning(
            '{"choices":[{"finish_reason":"stop","message":{"content":"hello"}}],'
            '"usage":{"prompt_tokens":100,"completion_tokens":20,'
            '"prompt_cache_hit_tokens":64,"prompt_cache_miss_tokens":36}}'))
    c = b.complete("p")
    assert c.text == "hello"
    assert c.usage == Usage(provider="deepseek", model="a-model", input_tokens=100,
                            output_tokens=20, cache_read_tokens=64)


def test_anthropic_complete_returns_the_text_and_its_usage():
    b = AnthropicBackend(
        "a-model", api_key="k",
        http=_http_returning(
            '{"content":[{"type":"text","text":"hello"}],"stop_reason":"end_turn",'
            '"usage":{"input_tokens":10,"output_tokens":20,"cache_read_input_tokens":500}}'))
    c = b.complete("p")
    assert c.text == "hello"
    assert c.usage == Usage(provider="anthropic", model="a-model", input_tokens=510,
                            output_tokens=20, cache_read_tokens=500)


def test_claude_max_identifies_its_call_but_reports_no_counts():
    """Flat-rate and run in TEXT mode, so there is no usage block to parse.

    Three distinct things have to be true at once, and the issue asks for all three: report
    counts where it can, None where it cannot, and do not be special-cased out of the seam.
    So the counts are all None -- zeros would claim the call was free -- while the provider
    and model ARE reported, because the call demonstrably happened and the usage log has to
    be able to say whose it was. A bare `usage=None` would satisfy the middle requirement and
    quietly fail the third: the row would be anonymous and `job-sluice usage` could not
    attribute the silent calls to claude-max."""
    b = ClaudeMaxBackend("a-model", cmd_template=["x"], provider="claude-max",
                         runner=lambda *a, **k: _Proc(0, "hello\n"))
    c = b.complete("p")
    assert c.text == "hello"
    assert c.usage == Usage(provider="claude-max", model="a-model")
    assert (c.usage.input_tokens, c.usage.output_tokens) == (None, None)


# ------------------------------------------------- usage burned by a call that RAISED

def test_openai_truncation_attaches_the_usage_it_already_parsed():
    """A `finish_reason` other than stop is a hard error, and those tokens were still
    billed. The usage rides on the exception because there is no return value on this path."""
    b = OpenAiCompatibleBackend(
        "a-model", api_key="k", base_url="https://api.example.invalid", provider="openai",
        http=_http_returning(
            '{"choices":[{"finish_reason":"length","message":{"content":"half"}}],'
            '"usage":{"prompt_tokens":100,"completion_tokens":20}}'))
    with pytest.raises(BackendError) as e:
        b.complete("p")
    assert e.value.usage == Usage(provider="openai", model="a-model",
                                  input_tokens=100, output_tokens=20)


def test_anthropic_truncation_attaches_the_usage_it_already_parsed():
    b = AnthropicBackend(
        "a-model", api_key="k",
        http=_http_returning(
            '{"content":[{"type":"text","text":"half"}],"stop_reason":"max_tokens",'
            '"usage":{"input_tokens":10,"output_tokens":20}}'))
    with pytest.raises(BackendError) as e:
        b.complete("p")
    assert e.value.usage == Usage(provider="anthropic", model="a-model",
                                  input_tokens=10, output_tokens=20)


def test_a_transport_failure_attaches_no_usage_because_there_is_no_body():
    """The distinction matters: `usage is None` on a BackendError means "we never saw a
    usage block", not "the call was free". A timeout has no response to parse."""
    def boom(*a, **k):
        raise OSError("connection reset")

    b = OpenAiCompatibleBackend("a-model", api_key="k",
                                base_url="https://api.example.invalid", http=boom)
    with pytest.raises(BackendError) as e:
        b.complete("p")
    assert e.value.usage is None


# ---------------------------------------------------- attribution through the fallback

class _Leg:
    """A backend leg that either answers or raises, with its own provider/model stamped --
    the point being that FallbackBackend adds nothing of its own, so what reaches the log is
    whatever the serving leg said it was."""

    def __init__(self, provider, *, text=None, error=None, usage=None):
        self.provider, self.text, self.error, self.usage = provider, text, error, usage

    def complete(self, prompt):
        if self.error is not None:
            raise BackendError(self.error, usage=self.usage)
        return Completion(self.text, usage=self.usage)


def test_usage_is_attributed_to_the_leg_that_actually_served():
    """Measured on BOTH legs rather than one: a test that only exercises the primary cannot
    tell a passed-through Usage from one the wrapper stamped itself."""
    p_usage = Usage(provider="claude-max", model="p-model", input_tokens=1)
    f_usage = Usage(provider="deepseek", model="f-model", input_tokens=2)

    served_by_primary = FallbackBackend(_Leg("claude-max", text="a", usage=p_usage),
                                        _Leg("deepseek", text="b", usage=f_usage))
    assert served_by_primary.complete("p").usage == p_usage

    served_by_fallback = FallbackBackend(_Leg("claude-max", error="down"),
                                         _Leg("deepseek", text="b", usage=f_usage))
    assert served_by_fallback.complete("p").usage == f_usage


def test_a_primary_that_burned_tokens_before_failing_is_not_swallowed():
    """#308's third requirement. The primary parsed a usage block and THEN raised, so those
    tokens were billed; the fallback's `except` is the last place they are visible, and
    dropping them there makes a leg that bills on every call read as free."""
    burned = Usage(provider="openai", model="p-model", input_tokens=100, output_tokens=5)
    served = Usage(provider="deepseek", model="f-model", input_tokens=90)
    b = FallbackBackend(_Leg("openai", error="truncated", usage=burned),
                        _Leg("deepseek", text="b", usage=served))
    c = b.complete("p")
    assert c.usage == served
    assert c.unserved_usage == (burned,)


def test_a_primary_that_failed_without_spending_adds_no_unserved_record():
    """A host that is simply down reports no usage, and an empty `unserved_usage` is what
    says so. A zero-valued record here would be a claim that a call happened and cost
    nothing."""
    b = FallbackBackend(_Leg("claude-max", error="ssh: connect failed"),
                        _Leg("deepseek", text="b"))
    assert b.complete("p").unserved_usage == ()


def test_when_both_legs_fail_the_primary_spend_still_rides_on_the_error():
    """There is no completion to hang it on, so the raised error carries it -- otherwise the
    worst case (paid for nothing, twice) is the one case that records nothing at all."""
    burned = Usage(provider="openai", model="p-model", input_tokens=100)
    b = FallbackBackend(_Leg("openai", error="truncated", usage=burned),
                        _Leg("deepseek", error="down"))
    with pytest.raises(BackendError) as e:
        b.complete("p")
    assert e.value.usage == burned


# ------------------------------------------- the provider label cannot drift from the name

# Which injection each provider takes, and a body that reports a usage block. Keyed by
# registry name; `test_every_registered_provider_is_in_the_label_table` below ties this
# table to the registry, so a new provider that registers without being added here fails
# LOUDLY rather than going unchecked (the same anti-drift teeth as the contract suite's
# own payload tables).
_USAGE_PAYLOADS = {
    "deepseek": dict(http='{"choices":[{"finish_reason":"stop","message":'
                          '{"content":"x"}}],"usage":{"prompt_tokens":7}}'),
    "openai": dict(http='{"choices":[{"finish_reason":"stop","message":'
                        '{"content":"x"}}],"usage":{"prompt_tokens":7}}'),
    "anthropic": dict(http='{"content":[{"type":"text","text":"x"}],'
                           '"usage":{"input_tokens":7,"output_tokens":1}}'),
    # Flat-rate, text mode: no counts to report, but the CALL is still labelled, so this
    # row checks the label like the others and additionally pins that the counts stay None.
    "claude-max": dict(runner=True),
}


def test_every_registered_provider_is_in_the_label_table():
    """Anti-vacuity, and the teeth: a provider absent from the table above would simply not
    be parametrized, so the label check would silently stop covering it."""
    from sluice.core.app import Sluice

    registered = set(Sluice.available("backend"))
    assert registered, "no backend is registered: the rows below would pass vacuously"
    assert registered == set(_USAGE_PAYLOADS)


@pytest.mark.parametrize("name", sorted(_USAGE_PAYLOADS))
def test_the_usage_provider_label_is_the_name_that_selected_the_factory(name):
    """`provider` is threaded from `make_backend`, never written as a literal in a factory
    module, so a module copied to add a provider cannot keep the original's label. This is
    what checks that thread is actually connected end to end.

    Measured through `make_backend` rather than the class, because the class default is a
    generic 'openai-compatible' and would pass a weaker version of this assertion."""
    from sluice.core.backends import make_backend

    payload = _USAGE_PAYLOADS[name]
    if payload.get("runner"):
        b = make_backend(name, "a-model", runner=lambda *a, **k: _Proc(0, "x\n"))
        u = b.complete("p").usage
        assert u == Usage(provider=name, model="a-model")
        return
    b = make_backend(name, "a-model", api_key="k", http=_http_returning(payload["http"]))
    assert b.complete("p").usage.provider == name
