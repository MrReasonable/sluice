"""Per-run diagnostic artefacts for `cv run`, kept beside the composed CV.

WHY. The engine used to keep the rendered PDF and nothing else. The prompt the composer was
sent (which already carries the source bundle, the job description and the rules), each
attempt's composed text and the gate and audit findings were all discarded once `cv run`
printed its result line. A CV that came out badly tailored could not be explained afterwards,
and a run that rendered NOTHING -- a gate failure, the run most in need of a diagnosis -- left
nothing on disk at all.

WHAT. Into the per-lead working directory the renderer already writes its PDF into,
`<cv.output_dir>/<slug(company, role)>/`:

  prompt.attempt-N.txt  the exact prompt handed to the backend for attempt N. Written BEFORE
                        the backend call, so a compose that hangs or raises still leaves it.
  cv.attempt-N.md       what attempt N's compose returned.
  cv.rendered.md        the text handed to the renderer; absent when nothing was rendered.
                        Written before the render call, so a renderer that raises still
                        leaves what it was given.
  run.json              the run record, written last. Its `files` list is the manifest of
                        every OTHER file this run wrote, so anything else in the directory
                        (the PDF of an earlier run, a file that failed to write part-way) is
                        identifiably not this run's.

Never into `cv.served_dir`. That holds what `apply` stages and what a user may publish, and
the prompt carries the whole bundle, the contact block and the lead's triage notes (#329).

LIFECYCLE. Nothing is written until the engine reaches composition (`RunArtefacts.begin`), so
a lead refused earlier -- not shortlisted, held for sign-off, stale, identity unset -- leaves an
earlier run's artefacts exactly as they were. For a held lead that is the point: they explain
the hold for as long as a human has it to review, since `cv signoff` never touches this
directory and the #60 latch refuses the lead before composition. `begin` clears the previous
run's set first, by NAME, so a `cv.attempt-2.md` from a longer earlier run cannot sit beside
this run's `run.json` looking current. Files this module did not write (the renderer's PDF,
anything a user put there) are left alone. No history is kept: a later run for the same slug
replaces the set, and that includes a `--dry-run`, which writes artefacts too (see the engine
module docstring for why).

FAILURE. A diagnostic must never cost a CV, so nothing here raises. It must not fail quietly
either: every write or clear that fails logs a WARNING naming the path and the error, is
listed in `run.json`'s `artefact_errors` whenever run.json itself can still be written, and
sets `failed`, which the engine copies onto `CvResult.artefacts_failed` for `cv run`'s result
line."""
import json
import os
import re
import uuid
from datetime import datetime, timezone

from sluice.core.log import get_logger

_log = get_logger("cv.artefacts")

RUN_RECORD = "run.json"
RENDERED_TEXT = "cv.rendered.md"


def prompt_name(attempt: int) -> str:
    return f"prompt.attempt-{attempt}.txt"


def draft_name(attempt: int) -> str:
    return f"cv.attempt-{attempt}.md"


# Every name this module writes, and nothing else, because `begin` DELETES whatever matches.
# It has to stay in step with the four names above in both directions: a name written but not
# matched survives into the next run as a stale file, and a pattern wider than the names
# deletes something this module never wrote. Attempt numbers are `\d+` rather than the
# engine's current two, so a larger retry budget cannot leave a stale attempt 3 behind.
_OWNED = re.compile(r"run\.json|cv\.rendered\.md|prompt\.attempt-\d+\.txt|cv\.attempt-\d+\.md")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


class RunArtefacts:
    """One `run_one` call's artefacts, written as the run learns them.

    Created before anything about the run is known, and INERT until `begin`: every method is a
    no-op on an instance that never began. That is what lets the engine call `finish` on every
    way out of `run_one` without first asking whether that way out reached composition.
    """

    def __init__(self, *, dry_run: bool):
        self.dry_run = dry_run
        self.run_id = uuid.uuid4().hex
        self.out_dir = None
        # False until `begin` has created the directory and read it back. A directory that
        # cannot be created is reported ONCE, by `begin`; every later write is then skipped
        # rather than reporting the same cause again for each file.
        self._writable = False
        self._started_at = None
        self._facts = {}
        self._attempts = []
        self._retained = None
        self._rendered_pdf = None
        self.files = []
        self.errors = []

    @property
    def failed(self) -> bool:
        return bool(self.errors)

    def begin(self, out_dir, *, lead, entry_ids, dossier_failed, skills_unreadable):
        """Start the record at the point composition starts, and clear the previous run's.

        `lead` is the store-issued slug, never the note's `ref`: a ref is an opaque store
        handle (a filesystem path for the vault store), the same reason `core/usage.py`'s
        rows name the slug."""
        self.out_dir = out_dir
        self._started_at = _now()
        self._facts = {"lead": lead, "dossier_failed": dossier_failed,
                       "skills_unreadable": skills_unreadable,
                       "bundle_entry_ids": list(entry_ids)}
        try:
            os.makedirs(out_dir, exist_ok=True)
            names = os.listdir(out_dir)
        except OSError as e:
            self._report(out_dir, "created", e)
            return
        self._writable = True
        for name in sorted(names):
            if not _OWNED.fullmatch(name):
                continue
            path = os.path.join(out_dir, name)
            try:
                os.remove(path)
            except OSError as e:
                # Left in place and REPORTED, never ignored: a stale file that survives the
                # clear is exactly the one that could be mistaken for this run's. It is not in
                # this run's `files` manifest, which is how run.json tells the two apart.
                self._report(path, "cleared", e)

    def prompt(self, attempt, text):
        self._attempt(attempt)
        self._write(prompt_name(attempt), text)

    def composed(self, attempt, text):
        self._attempt(attempt)
        self._write(draft_name(attempt), text)

    def compose_failed(self, attempt, exc):
        self._attempt(attempt)["compose_error"] = _describe(exc)

    def retained(self, attempt):
        """The attempt whose draft the engine kept -- the one it renders, or would have on a
        dry run. Not necessarily the last: a hard-clean attempt 1 outlives a worse retry."""
        self._retained = attempt

    def rendering(self, text):
        self._write(RENDERED_TEXT, text)

    def rendered(self, pdf):
        self._rendered_pdf = pdf

    def finish(self, result):
        """Write run.json for a run that returned a `CvResult`, in the engine's own status
        vocabulary."""
        self._write_record(status=result.status, backend=result.backend,
                           violations=list(result.violations),
                           audit_flags=list(result.audit_flags), slop=list(result.slop),
                           voice_flags=list(result.voice_flags), served=result.served,
                           error=None)

    def finish_error(self, exc):
        """Write run.json for a run that RAISED after composition started. `error` is
        run_batch's word for that outcome. The finding lists are null rather than empty:
        the run never got as far as settling them, and an empty list would read as clean."""
        self._write_record(status="error", backend=None, violations=None, audit_flags=None,
                           slop=None, voice_flags=None, served=None, error=_describe(exc))

    def _write_record(self, *, status, backend, violations, audit_flags, slop, voice_flags,
                      served, error):
        if self.out_dir is None:
            return
        record = {
            "run_id": self.run_id,
            "lead": self._facts["lead"],
            "status": status,
            "dry_run": self.dry_run,
            "started_at": self._started_at,
            "finished_at": _now(),
            "backend": backend,
            "dossier_failed": self._facts["dossier_failed"],
            "skills_unreadable": self._facts["skills_unreadable"],
            "bundle_entry_ids": self._facts["bundle_entry_ids"],
            "attempt_count": len(self._attempts),
            "attempts": self._attempts,
            "retained_attempt": self._retained,
            "violations": violations,
            "audit_flags": audit_flags,
            "slop": slop,
            "voice_flags": voice_flags,
            "rendered_pdf": self._rendered_pdf,
            "served": served,
            "error": error,
            "files": list(self.files),
            "artefact_errors": list(self.errors),
        }
        # `files` never names run.json itself, by ORDER rather than by a flag: the list is
        # copied into `record` above, before the write below appends to it.
        #
        # `ensure_ascii` stays at its True default: the record quotes composed text and
        # backend errors, and escaping keeps a character UTF-8 cannot encode (a lone
        # surrogate) from costing the one file that says what else went wrong.
        #
        # Serialised INSIDE the artefact failure path, not ahead of it. `rendered_pdf` is
        # whatever the injected renderer returned, and nothing checks that at runtime: the
        # Renderer protocol says `str`, but a renderer returning anything JSON cannot encode
        # made `json.dumps` raise TypeError out of `run_one`, reporting `error` for a CV that
        # had already rendered. ValueError is json's circular-reference refusal.
        try:
            text = json.dumps(record, indent=2) + "\n"
        except (TypeError, ValueError) as e:
            self._report(os.path.join(self.out_dir, RUN_RECORD), "written", e)
            return
        self._write(RUN_RECORD, text)

    def _attempt(self, attempt):
        for entry in self._attempts:
            if entry["attempt"] == attempt:
                return entry
        entry = {"attempt": attempt, "compose_error": None}
        self._attempts.append(entry)
        return entry

    def _write(self, name, text):
        if not self._writable:
            return
        path = os.path.join(self.out_dir, name)
        try:
            # Encoded BEFORE the file is opened, so a string UTF-8 refuses leaves no truncated
            # file behind to be mistaken for a complete one.
            data = text.encode("utf-8")
            with open(path, "wb") as f:
                f.write(data)
        # OSError for the filesystem. ValueError for the encode above: a lone surrogate in
        # composed text raises UnicodeEncodeError, which is a ValueError and NOT an OSError, so
        # an OSError-only handler would let it escape and cost the CV.
        # tests/test_cv_run_artefacts.py has a row for each arm.
        except (OSError, ValueError) as e:
            self._report(path, "written", e)
            return
        self.files.append(name)

    def _report(self, path, what, exc):
        self.errors.append(f"{path}: {_describe(exc)}")
        _log.warning("cv run artefact %s could not be %s: %s", path, what, _describe(exc))
