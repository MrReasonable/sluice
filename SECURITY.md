# Security

## Reporting a vulnerability

Use GitHub's private vulnerability reporting: the **Security** tab on
[github.com/MrReasonable/sluice](https://github.com/MrReasonable/sluice) → "Report a
vulnerability". That opens a private advisory only the maintainer can see, rather than a
public issue. If that option isn't available, open a normal issue describing the class of
problem without exploit details, and say you have more to share privately — someone will
follow up.

There's no fixed response-time SLA; this is a personally-run project. A confirmed
vulnerability gets a fix released via the normal release-please flow (see
[CONTRIBUTING.md](CONTRIBUTING.md)), with the advisory published once a fix is out.

## What's actually sensitive here

Sluice runs a real job search: your config and your Obsidian vault hold employer names,
locations, contact details, sometimes private SSH hosts, and API keys. None of that lives in
this repository or its test fixtures — see [CONTRIBUTING.md](CONTRIBUTING.md)'s Neutrality
section — but it lives on your machine, in `sluice.local.yaml` and your vault, both of which
should stay out of any public repo you keep them alongside.

## The SSRF guard (`core/urlguard.py`)

The dossier fetcher follows URLs scraped off job boards — untrusted input by construction — to
pull job-ad content for the triage judge and CV composer. By default it refuses anything that
isn't `http(s)` and any address that isn't globally routable: loopback, private ranges,
link-local, and cloud-metadata addresses, including one embedded in an IPv4-mapped IPv6
address rather than only the plain form. `dossier_allow_hosts` is the one way to grant an
exception (for running a source against your own network) — see `docs/CONFIGURATION.md`. It's
a narrow allowlist, not a general bypass: granting a hostname covers every address that name
resolves to, today and at each future fetch, so a CIDR is the tighter grant where you can name
one instead.

## Minimal runtime surface

`sluice/` is standard-library only apart from `pyyaml`. `jinja2`/`weasyprint` (the `render`
extra), the Google client libraries (`google`), and `argcomplete` (`completion`) are the sole,
deliberate exceptions, each opt-in and lazily imported so a bare install never pulls them in.
Every dependency this project takes on is a larger third-party attack surface sitting between
your job search and the internet — see `.rulesync/rules/CLAUDE.md`'s dependency-decision rule
in [CONTRIBUTING.md](CONTRIBUTING.md) if you're proposing a new one.

## Credentials

- LLM backend API keys (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`) are read
  from the environment only, never written to config or logs. They are not embedded in the
  HTTP backends' own error text either, since they travel as a request header rather than
  being interpolated into a message -- but that is incidental to how the error is built, not
  an active redaction step. The `claude-max` backend's errors go through a real, deliberate
  scrub (`core/backends.py`'s `_redact`): its configured `host` and `claude_path` -- which
  otherwise leak into `proc.stderr` on an SSH/exec failure -- are replaced with `<host>`/
  `<path>` labels, token-aware so a short host like `db` isn't mangled inside an unrelated
  word.
- The Google OAuth token (`track`) is written to disk at `0600` via a temp-file-plus-rename, so
  it's never briefly world-readable mid-write.
- The `claude-max` backend, run over SSH (`claude_max_host`), refuses a host or CLI path
  beginning with `-` — an argument-injection guard, since either value can originate from a
  config file.

## Supply chain

Every GitHub Actions step in `.github/workflows/` is pinned to a commit SHA (with a version
comment for readability) rather than a floating tag, and `zizmor --offline --strict-collection`
audits the workflows in CI on every push. `.github/zizmor-requirements.txt` is
`--require-hashes`, so zizmor's own dependency chain is pinned too.
