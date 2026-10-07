"""Line-level edits to a sluice config file: text in, text out, no I/O (in-session setup).

Only the one line a change names is touched, so a user's comments, order and formatting
survive. What this module cannot place, it REFUSES with a reason rather than guessing: the real
config loaders then check every edit it does make (`Sluice.apply_setup`), so a wrong placement
is caught before anything is written. Clearing writes back exactly the line `init` renders for
an unset key (`onboard/plan.py::_render_key`), so set-then-clear on an `init` file is
byte-identical and the gate abstains again.
"""
import re

from sluice.onboard.emit import scalar

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML is a hard dependency of the config loaders
    yaml = None

UNSET_MARKER = "# <- uncomment and set YOUR OWN"
_HEADER = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):[ \t]*(?:#.*)?$")


class EditRefused(ValueError):
    """A config edit this module cannot place safely; the message says why."""


def unset_line(leaf: str, indent: str) -> str:
    """The line `init` writes for an unset key -- `plan.py::_render_key`'s shape."""
    return f"{indent}# {leaf}:   {UNSET_MARKER}"


def _split(dotted):
    parts = dotted.split(".")
    return (parts[0], parts[1]) if len(parts) == 2 else ("", parts[0])


def _nl(lines):
    return "\r\n" if any(ln.endswith("\r\n") for ln in lines) else "\n"


def _bare(line):
    return line.rstrip("\r\n")


def _eol(line, nl):
    return line[len(_bare(line)):] or nl


def _has_indented_body(lines, i):
    """A bare `name:` opens a block only if the next non-blank line that is not a column-0
    comment is indented. Otherwise it is a ROOT key holding YAML null (`vault_dir:`): reading it
    as a block header hid the key from `_find`, so `set_key` inserted a second line and YAML's
    last-wins kept the null. An indented comment counts as body, because `init` writes a block
    whose keys are all commented out."""
    for nxt in lines[i + 1:]:
        s = _bare(nxt)
        if not s.strip() or s.startswith("#"):
            continue
        return s[0].isspace()
    return False


def _block_ranges(lines):
    """{block: (header index, end index)}: a block runs until the next line at column 0 that
    is neither blank nor a comment.

    A block header that appears twice keeps its LAST range, so every edit lands in the last
    copy. That relies on PyYAML's loader being last-wins for a repeated key: the last block is
    the one the loaders read. Do not "fix" this into first-wins -- an edit to the first copy
    would be written and then overridden. An empty trailing `triage:` is not a range at all (it
    has no indented body, so it is a root key holding null that replaces the whole block when
    loaded); an edit then lands in the earlier block, reads back wrong, and the config check
    in `Sluice.apply_setup` sets it aside rather than writing it."""
    out, i = {}, 0
    while i < len(lines):
        m = _HEADER.match(_bare(lines[i]))
        if m and _has_indented_body(lines, i):
            j = i + 1
            while j < len(lines):
                s = _bare(lines[j])
                if s and not s[0].isspace() and not s.startswith("#"):
                    break
                j += 1
            out[m.group(1)] = (i, j)
            i = j
        else:
            i += 1
    return out


def _region(lines, block):
    """Indices to search for `block`'s keys: inside its range, or (root) outside every range."""
    ranges = _block_ranges(lines)
    if block:
        if block not in ranges:
            return []
        start, end = ranges[block]
        return list(range(start + 1, end))
    inside = {i for s, e in ranges.values() for i in range(s, e)}
    return [i for i in range(len(lines)) if i not in inside]


def _find(lines, block, leaf):
    indent = "  " if block else ""
    active = re.compile(rf"^{indent}{re.escape(leaf)}:(?:[ \t]|$)")
    commented = re.compile(rf"^{indent}# {re.escape(leaf)}:(?:[ \t]|$)")
    region = _region(lines, block)
    return ([i for i in region if active.match(_bare(lines[i]))],
            [i for i in region if commented.match(_bare(lines[i]))])


def _opens_multiline(lines, i):
    value = _bare(lines[i]).split(":", 1)[1].strip()
    if value.startswith(("|", ">")):
        return True
    if value and not value.startswith("#"):
        return False
    indent = len(lines[i]) - len(lines[i].lstrip(" "))
    for nxt in lines[i + 1:]:
        s = _bare(nxt)
        if not s.strip() or s.lstrip().startswith("#"):
            continue
        return len(s) - len(s.lstrip(" ")) > indent or s.lstrip().startswith("- ")
    return False


def is_active(text: str, dotted: str) -> bool:
    block, leaf = _split(dotted)
    return bool(_find(text.splitlines(keepends=True), block, leaf)[0])


def set_key(text: str, dotted: str, rendered: str) -> str:
    lines = text.splitlines(keepends=True)
    nl = _nl(lines)
    block, leaf = _split(dotted)
    indent = "  " if block else ""
    active, commented = _find(lines, block, leaf)
    if len(active) > 1 or (not active and len(commented) > 1):
        raise EditRefused(f"`{dotted}` appears more than once in the config")
    target = active[0] if active else (commented[0] if commented else None)
    if target is not None:
        if active and _opens_multiline(lines, target):
            raise EditRefused(f"`{dotted}` holds a value spread over several lines")
        lines[target] = f"{indent}{leaf}: {rendered}{_eol(lines[target], nl)}"
        return "".join(lines)
    return _insert(lines, block, f"{indent}{leaf}: {rendered}", nl)


def clear_key(text: str, dotted: str) -> str:
    lines = text.splitlines(keepends=True)
    nl = _nl(lines)
    block, leaf = _split(dotted)
    active, _ = _find(lines, block, leaf)
    if not active:
        return text
    if len(active) > 1:
        raise EditRefused(f"`{dotted}` appears more than once in the config")
    if _opens_multiline(lines, active[0]):
        raise EditRefused(f"`{dotted}` holds a value spread over several lines")
    lines[active[0]] = unset_line(leaf, "  " if block else "") + _eol(lines[active[0]], nl)
    return "".join(lines)


def _terminated(lines, nl):
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] += nl
    return lines


def _insert(lines, block, new_line, nl):
    lines = _terminated(lines, nl)
    ranges = _block_ranges(lines)
    if not block:
        first = min((s for s, _ in ranges.values()), default=len(lines))
        return "".join(lines[:first] + [new_line + nl] + lines[first:])
    if block not in ranges:
        sep = [nl] if lines and _bare(lines[-1]).strip() else []
        return "".join(lines + sep + [f"{block}:{nl}", new_line + nl])
    start, end = ranges[block]
    last = end
    while last > start + 1 and (not _bare(lines[last - 1]).strip()
                                or not lines[last - 1][0].isspace()):
        last -= 1
    return "".join(lines[:last] + [new_line + nl] + lines[last:])


def _entry(label, url):
    return f"      - [{scalar(label)}, {scalar(url)}]"


def _parse_entry(line):
    s = _bare(line).strip()
    if not s.startswith("- "):
        return None
    body = s[2:]
    if not (body.startswith("[") and yaml is not None):
        raise EditRefused("a searches entry is not in flow form (`- [label, url]`)")
    try:
        value = yaml.safe_load(body)
    except yaml.YAMLError:
        # One physical line of an entry YAML spreads over several (`- [a,` then the url on the
        # next line) does not parse alone, and YAMLError is not a ValueError: uncaught, it
        # escaped every setup caller as an unstructured error after the user had ticked.
        raise EditRefused("a searches entry is not in flow form (`- [label, url]`)") from None
    if not (isinstance(value, list) and len(value) >= 2):
        raise EditRefused("a searches entry is not in flow form (`- [label, url]`)")
    return value[0], value[1]


def _searches(lines, source_id):
    """(index of `    searches:` or None, [(index, (label, url))], index to insert a source
    block at, index of the source header or None)."""
    ranges = _block_ranges(lines)
    if "sources" not in ranges:
        if any(re.match(r"^sources\s*:", _bare(ln)) for ln in lines):
            raise EditRefused("the `sources:` block is written in a form this editor cannot "
                              "place an edit in")
        return None, [], None, None
    start, end = ranges["sources"]
    hdr = re.compile(rf'^  "?{re.escape(source_id)}"?:[ \t]*(?:#.*)?$')
    src = next((i for i in range(start + 1, end) if hdr.match(_bare(lines[i]))), None)
    if src is None:
        mention = re.compile(rf"""^\s*["']?{re.escape(source_id)}["']?\s*:""")
        if any(mention.match(_bare(lines[i])) for i in range(start + 1, end)):
            raise EditRefused(f"`{source_id}` is written in a form this editor cannot place "
                              f"an edit in")
        return None, [], end, None
    src_end = next((i for i in range(src + 1, end)
                    if _bare(lines[i]).strip() and not _bare(lines[i]).startswith("    ")
                    and not _bare(lines[i]).lstrip().startswith("#")), end)
    s_idx = next((i for i in range(src + 1, src_end)
                  if _bare(lines[i]).startswith("    searches:")), None)
    entries = []
    if s_idx is not None:
        for i in range(s_idx + 1, src_end):
            s = _bare(lines[i])
            if not s.strip() or s.lstrip().startswith("#"):
                continue
            if not s.startswith("      "):
                break
            parsed = _parse_entry(lines[i])
            if parsed is None:
                raise EditRefused("a searches entry is not in flow form (`- [label, url]`)")
            entries.append((i, parsed))
    return s_idx, entries, src_end, src


def add_search(text: str, source_id: str, label: str, url: str) -> str:
    lines = _terminated(text.splitlines(keepends=True), _nl(text.splitlines(keepends=True)))
    nl = _nl(lines)
    s_idx, entries, src_end, src = _searches(lines, source_id)
    if any(e == (label, url) for _, e in entries):
        raise EditRefused("that search is already configured")
    entry = _entry(label, url) + nl
    if src_end is None:                               # no sources block
        sep = [nl] if lines and _bare(lines[-1]).strip() else []
        return "".join(lines + sep + [f"sources:{nl}", f"  {scalar(source_id)}:{nl}",
                                      f"    searches:{nl}", entry])
    if src is None:                                   # sources block without this source
        return "".join(lines[:src_end] + [f"  {scalar(source_id)}:{nl}", f"    searches:{nl}",
                                          entry] + lines[src_end:])
    if s_idx is None:
        return "".join(lines[:src_end] + [f"    searches:{nl}", entry] + lines[src_end:])
    at = (entries[-1][0] + 1) if entries else s_idx + 1
    return "".join(lines[:at] + [entry] + lines[at:])


def remove_search(text: str, source_id: str, label: str, url: str) -> str:
    lines = text.splitlines(keepends=True)
    _s, entries, _e, _src = _searches(lines, source_id)
    match = [i for i, e in entries if e == (label, url)]
    if not match:
        raise EditRefused("that search is not configured")
    if len(entries) == 1:
        raise EditRefused(
            f"it is the last search for {source_id}, and an empty list makes the source run its "
            f"built-in example search; run `job-sluice ingest disable {source_id}` to stop it")
    del lines[match[0]]
    return "".join(lines)
