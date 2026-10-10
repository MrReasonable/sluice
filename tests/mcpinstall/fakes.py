"""Rigs and fake client CLIs for the install tests. A fake edits its config file the way the
measured client does (the spec's measurement table) and records every call; the runner refuses
to run anything outside the test's own folder."""
import json
import os
import pathlib
import subprocess
from dataclasses import dataclass, field

from sluice.core import backup
from sluice.mcpinstall import clients, jsonc, routes


class FakeClient:
    def __init__(self, name, config_path, *, noop=False, drop=None, drop_top=None,
                 write_to=None, fail=None, fail_verb="add"):
        self.name, self.path = name, config_path
        self.noop, self.drop, self.drop_top, self.write_to = noop, drop, drop_top, write_to
        self.fail, self.fail_verb = fail, fail_verb
        self.calls, self.copy_present = [], []

    def _doc(self, path):
        try:
            return jsonc.load(pathlib.Path(path).read_bytes(), jsonc=True)
        except FileNotFoundError:
            return {}

    def _table(self, doc):
        node = doc
        for key in clients.by_name(self.name).table:
            node = node.setdefault(key, {})
        return node

    def run(self, args):
        self.calls.append(list(args))
        verb = args[1]
        if self.fail is not None and verb == self.fail_verb:
            return self.fail
        if self.noop:
            return 0
        target = self.write_to or self.path
        doc = self._doc(target)
        table = self._table(doc)
        if verb == "remove":
            if "job-sluice" not in table:
                return 1
            del table["job-sluice"]
        else:
            env, argv = self._parse_add(args)
            if self.name == "claude-code" and "job-sluice" in table:
                return 1          # measured: refuses an existing name
            value = (
                {"type": "local", "command": argv} if self.name == "opencode" else
                {"type": "stdio", "command": argv[0], "args": argv[1:]}
                if self.name == "claude-code" else {"command": argv[0], "args": argv[1:]})
            if env:
                value["environment" if self.name == "opencode" else "env"] = env
            table["job-sluice"] = value
        if self.drop:
            table.pop(self.drop, None)
        if self.drop_top:
            doc.pop(self.drop_top, None)
        pathlib.Path(target).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(target).write_text(json.dumps(doc, indent=2))
        return 0

    def _parse_add(self, args):
        rest, env = list(args[2:]), {}
        if self.name == "claude-code":
            assert rest[:3] == ["--scope", "user", "job-sluice"], rest
            rest = rest[3:]
            while rest[0] == "-e":
                k, v = rest[1].split("=", 1)
                env[k], rest = v, rest[2:]
            assert rest[0] == "--", rest
            return env, rest[1:]
        if self.name == "opencode":
            assert rest[0] == "--global", rest
            rest = rest[1:]
            while rest[0] == "--env":
                k, v = rest[1].split("=", 1)
                env[k], rest = v, rest[2:]
            assert rest[:2] == ["job-sluice", "--"], rest
            return env, rest[2:]
        while rest[0] == "-e":                        # gemini
            k, v = rest[1].split("=", 1)
            env[k], rest = v, rest[2:]
        assert rest[:3] == ["-s", "user", "job-sluice"], rest
        return env, rest[3:]


class FakeRunner:
    def __init__(self, root, backup_dir):
        self.root, self.backup_dir, self.fakes, self.hang = str(root), backup_dir, {}, set()

    def __call__(self, argv, timeout):
        if not argv[0].startswith(self.root + os.sep):
            raise AssertionError(
                f"install tried to run {os.path.basename(argv[0])!r} outside the test folder")
        fake = self.fakes[argv[0]]
        fake.copy_present.append(os.path.isdir(self.backup_dir)
                                 and bool(os.listdir(self.backup_dir)))
        if argv[0] in self.hang:
            raise subprocess.TimeoutExpired(argv, timeout)
        return fake.run(argv[1:])


@dataclass
class Rig:
    tmp: pathlib.Path
    platform: str = "darwin"
    env: dict = field(default_factory=dict)

    def __post_init__(self):
        self.home = str(self.tmp / "home")
        self.bin = self.tmp / "bin"
        self.bin.mkdir(parents=True, exist_ok=True)
        self.backup_dir = str(self.tmp / "state" / routes.BACKUP_FOLDER)
        self.runner = FakeRunner(self.tmp, self.backup_dir)
        self.host = clients.Host(
            home=self.home, env=self.env, platform=self.platform,
            which=lambda n: str(self.bin / n) if (self.bin / n).exists() else None,
            isdir=os.path.isdir, isfile=os.path.isfile)
        self.copies_written = []
        self.deps = routes.Deps(run=self.runner, write_copy=self._write_copy,
                                backup_dir=self.backup_dir,
                                display=lambda p: clients.display_path(p, self.host),
                                timeout=5.0)
        self.after_copy = None
        launcher = self.tmp / "launcher" / "job-sluice"
        launcher.parent.mkdir(parents=True, exist_ok=True)
        launcher.write_text("#!/bin/sh\n")
        launcher.chmod(0o755)
        self.launcher = str(launcher)

    def _write_copy(self, *args, **kwargs):
        name = backup.write_copy(*args, **kwargs)
        self.copies_written.append(name)
        if self.after_copy:
            self.after_copy()
        return name

    def path(self, name) -> str:
        return clients.config_path(clients.by_name(name), self.host)

    def write(self, name, doc_or_text) -> pathlib.Path:
        p = pathlib.Path(self.path(name))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(doc_or_text if isinstance(doc_or_text, str)
                     else json.dumps(doc_or_text, indent=2))
        return p

    def read(self, name) -> dict:
        return jsonc.load(pathlib.Path(self.path(name)).read_bytes(), jsonc=True)

    def cli(self, name, **knobs) -> FakeClient:
        c = clients.by_name(name)
        exe = self.bin / c.executable
        exe.write_text("#!/bin/sh\nexit 0\n")
        exe.chmod(0o755)
        fake = FakeClient(name, self.path(name), **knobs)
        self.runner.fakes[str(exe)] = fake
        return fake

    def copies(self) -> list:
        return sorted(os.listdir(self.backup_dir)) if os.path.isdir(self.backup_dir) else []


OTHER = {"command": "/opt/other/bin/srv", "args": ["--flag"],
         "env": {"OTHER_TOKEN": "SENTINEL-NOT-A-SECRET-OTHER"}}
