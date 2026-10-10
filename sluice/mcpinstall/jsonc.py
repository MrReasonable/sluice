"""The readers for the client config files `mcp install` reads.

Strict JSON is the only thing install WRITES, so the JSON route reads strictly and refuses
what it could not re-save without loss: a byte-order mark, non-UTF-8 bytes, comments, a
duplicate key (`json.loads` keeps the last, so a re-save would silently drop the other).

JSONC is read-only: opencode and Gemini keep comments in their files, and install still has to
read their server table for the collateral check. Comments and trailing commas are dropped
outside strings and the rest goes to `json.loads`.

A `ReadError`'s text is printed, so it names the PROBLEM and never quotes the file, whose
values can be another tool's credentials."""
import json


class ReadError(ValueError):
    """A file install will not read as a server table; `str()` is the printable reason."""


def strip_jsonc(text: str) -> str:
    """`text` with `//` and `/* */` comments and trailing commas removed, outside strings."""
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
        elif text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end < 0 else end
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end < 0:
                raise ReadError("it has an unterminated /* comment")
            out.append(" ")
            i = end + 2
            continue
        out.append(c)
        i += 1
    return _drop_trailing_commas("".join(out))


def _drop_trailing_commas(text: str) -> str:
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
        elif c == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "}]":
                i += 1
                continue
        out.append(c)
        i += 1
    return "".join(out)


def _no_duplicates(pairs):
    doc = {}
    for key, value in pairs:
        if key in doc:
            raise ReadError("a key appears twice in one object, and a re-save would drop one")
        doc[key] = value
    return doc


def load(data: bytes, *, jsonc: bool) -> dict:
    """Parse a config file's bytes. An empty (or whitespace-only) file is an empty document,
    the state a create starts from. Raises `ReadError` with a printable reason."""
    if data.startswith(b"\xef\xbb\xbf"):
        raise ReadError("it starts with a byte-order mark, which install does not write")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ReadError("it is not UTF-8") from None
    if not text.strip():
        return {}
    source = strip_jsonc(text) if jsonc else text
    try:
        doc = json.loads(source, object_pairs_hook=_no_duplicates)
    except json.JSONDecodeError as exc:
        if not jsonc:
            try:
                json.loads(strip_jsonc(text))
            except (json.JSONDecodeError, ReadError):
                pass
            else:
                raise ReadError("it has comments or trailing commas, and install writes plain "
                                "JSON only") from None
        raise ReadError(f"it is not valid JSON (line {exc.lineno})") from None
    if not isinstance(doc, dict):
        raise ReadError("its top level is not an object")
    return doc
