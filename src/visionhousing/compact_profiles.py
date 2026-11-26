"""Compact tract profiles for the static site: one shared template, one small file per county.

Plain-language summary
----------------------
A tract profile repeats the same words in every tract: section titles, fact labels, units,
source names and limits, chart titles and notes. Only a small part changes from tract to
tract: the numbers and the text that shows them. Saving each of Pennsylvania's 3,445 tract
profiles whole would take far more room than the static site has.

So the exporter splits them:

* ``profiles/tract-template.json`` (one per state) holds the shape of a profile and every
  value that is the same in every tract, once.
* ``profiles/tracts-{county}.json`` (one per county) holds, for each of the county's tracts,
  only the values that change, column by column ("columnar": one list per value, one entry
  per tract). A value that is the same for every tract of the county is saved once.

The browser (``web/js/lib/api.js``) puts a profile back together by walking the template and
filling each slot from the county file. It copies values only: every number and every shown
text is the server's own, exactly as the live app sends it. Nothing is formatted, rounded or
worked out in the browser.

The export checks this on every tract: it rebuilds each profile from the saved files and
compares it with the live reply. A tract that does not come back exactly (for example a list
in an unusual order) is saved whole inside its county file instead (``raw``).

Template format
---------------
The template is a tree of small lists (``node``)::

    ["k", value]                 the same value in every tract
    ["c", n]                     the value is in column n
    ["e", n, [v0, v1, ...]]      one of a few values: column n says which (0, 1, ...)
    ["p", n, m, [wording, ...]]  a text with numbers in it: column n says which wording
                                 (the parts between the numbers), column m holds the
                                 numbers as shown, joined by "|"; the text is the wording
                                 parts with the numbers put back between them
    ["d", [[key, node, p], ...]] an object; p = -1 if the key is always there, else the
                                 column n that says (1 or 0) whether this tract has it
    ["d", [...], n]              an object whose keys come in a different order in some
                                 tracts: column n is "" for the template's order, else the
                                 entries this tract has, in its order, as "2,0,5" (the
                                 screen numbers source notes in key order, so order counts)
    ["l", [[node, p], ...]]      a list, items in order; p as above
    ["o", n, [node, ...]]        a list whose items come in a different order in different
                                 tracts (a ranked chart): column n lists, as "2,0,5", which
                                 items this tract has, in its order
    ["x", n, {"d"|"l"|"s": node}] the kind differs between tracts (an object in one tract,
                                 null in another): column n holds the kind

A county file is ``{"$tracts": 1, "geoids": [...], "cols": [...], "raw": {...}}``. ``cols``
has one entry per column of the template, each one of::

    value                        the same value for every tract of this county
    [v1, v2, ...]                one value per tract, in the order of ``geoids``
    {"v": [values], "i": [n1, n2, ...]}   a short list of values and, per tract, which one
                                 ("i" is a text of one character per tract, 0-9a-zA-Z,
                                 when there are at most 62 values)
    {"=": n}                     the same values as column n of this file

Columns hold only plain values (text, numbers, true/false, null). A tract that does not use
a column has a filler value there, never read.
"""

from __future__ import annotations

import json
import re
import string
from typing import Any

__all__ = ["TemplateBuilder", "assemble", "encode_county"]

_SCALAR = (str, int, float, bool, type(None))
ENUM_MAX = 256
"""A place with at most this many different values (in the whole state) keeps them in the
template; the county files then hold only a small number per tract."""
ENUM_SHARE = 8
"""...and only when a value repeats, on average, at least this many times."""
PATTERN_MAX = 256
"""A text place with at most this many different wordings keeps them in the template."""
_NUMBER = re.compile(r"(\d+(?:[.,]\d+)*)")
"""The pieces of a text that change from tract to tract: runs of digits, with their commas
and decimal points ("4,286", "51.8"). Everything around them is the wording."""
_SEP = "|"
"""Joins the pieces of one text in a county file. A piece is digits, commas and points only."""


def _split(text: str) -> tuple[tuple[str, ...], list[str]]:
    """A text as its wording (the parts between numbers) and its numbers, both as shown."""
    parts = _NUMBER.split(text)
    return tuple(parts[0::2]), parts[1::2]


def _kind(value: Any) -> str:
    if isinstance(value, dict):
        return "d"
    if isinstance(value, list):
        return "l"
    return "s"


def _same(value: Any) -> str:
    """A key that tells values apart exactly (``1``, ``1.0`` and ``True`` differ)."""
    return json.dumps([type(value).__name__, value], ensure_ascii=False)


def _idents(items: list[Any]) -> list[tuple[Any, ...]]:
    """Name each item of a list so the same item lines up across tracts.

    An object with an ``id`` (or else a ``key``) text is named by it; anything else by its
    position. A repeated name gets a running number.
    """
    seen: dict[tuple[Any, ...], int] = {}
    out = []
    for i, item in enumerate(items):
        name: tuple[Any, ...] = ("#", i)
        if isinstance(item, dict):
            for field in ("id", "key"):
                if isinstance(item.get(field), str):
                    name = (field, item[field])
                    break
        n = seen.get(name, 0)
        seen[name] = n + 1
        out.append((*name, n))
    return out


class _Node:
    """One place in the merged profile shape, with what every tract had there."""

    __slots__ = (
        "chars",
        "constant",
        "count",
        "first",
        "items",
        "keys",
        "kinds",
        "korder",
        "kreordered",
        "order",
        "piece_chars",
        "reordered",
        "values",
        "wordings",
    )  # fmt: skip

    def __init__(self) -> None:
        self.count = 0  # tracts that reached this place
        self.kinds: dict[str, int] = {}
        self.first: str | None = None  # scalar: the first value seen (as _same key)
        self.constant = True
        self.keys: dict[str, _Node] = {}  # object children
        self.korder: list[str] = []  # object keys, merged order (as for list items)
        self.kreordered = False  # object: some tract has its keys in another order
        self.items: dict[tuple[Any, ...], _Node] = {}  # list children by name
        self.order: list[tuple[Any, ...]] = []  # list children, merged order
        self.reordered = False  # list: some tract has its items in another order
        self.values: dict[str, Any] | None = {}  # scalar: distinct values, until ENUM_MAX
        self.wordings: dict[tuple[str, ...], int] | None = {}  # text: wordings, until PATTERN_MAX
        self.chars = 0  # text: characters of all values seen
        self.piece_chars = 0  # text: characters of their numbers (plus one per number)

    def add(self, value: Any) -> None:
        self.count += 1
        kind = _kind(value)
        self.kinds[kind] = self.kinds.get(kind, 0) + 1
        if kind == "d":
            at = 0
            for key, child in value.items():
                if key not in self.keys:
                    self.keys[key] = _Node()
                    self.korder.insert(at, key)
                place = self.korder.index(key)
                if place < at:
                    self.kreordered = True
                at = place + 1
                self.keys[key].add(child)
        elif kind == "l":
            names = _idents(value)
            at = 0
            for name, child in zip(names, value, strict=True):
                if name not in self.items:
                    self.items[name] = _Node()
                    self.order.insert(at, name)
                place = self.order.index(name)
                if place < at:
                    self.reordered = True
                at = place + 1
                self.items[name].add(child)
        else:
            key = _same(value)
            if self.first is None:
                self.first = key
            elif key != self.first:
                self.constant = False
            if self.values is not None and key not in self.values:
                self.values[key] = value
                if len(self.values) > ENUM_MAX:
                    self.values = None
            if not isinstance(value, str):
                self.wordings = None
            elif self.wordings is not None:
                wording, numbers = _split(value)
                self.chars += len(value)
                self.piece_chars += sum(len(x) + 1 for x in numbers)
                if wording not in self.wordings:
                    self.wordings[wording] = len(self.wordings)
                    if len(self.wordings) > PATTERN_MAX:
                        self.wordings = None


class TemplateBuilder:
    """Merge many tract profiles into one template; then encode each county's tracts.

    Use: ``add`` every profile, then ``template()``; then ``encode_county`` per county.
    """

    def __init__(self) -> None:
        self.root = _Node()
        self.columns: list[tuple[str, _Node, Any]] = []  # (what, node, extra)
        self.names: dict[int, list[Any]] = {}  # list node -> its item names, for encoding
        self.wordings: dict[int, dict[tuple[str, ...], int]] = {}  # "p" node -> wording numbers
        self.enums: dict[int, dict[str, int]] = {}  # "e" node -> value numbers
        self._template: Any = None

    def add(self, profile: dict[str, Any]) -> None:
        """Merge one tract profile (the live reply, parsed)."""
        self.root.add(profile)

    def _column(self, what: str, node: _Node, extra: Any = None) -> int:
        self.columns.append((what, node, extra))
        return len(self.columns) - 1

    def template(self) -> Any:
        """The template tree (see the module text). Columns are numbered as they are met."""
        if self._template is None:
            self._template = self._build(self.root)
        return self._template

    def _build(self, node: _Node) -> Any:
        parts = {kind: self._part(node, kind) for kind in sorted(node.kinds)}
        if len(parts) == 1:
            return next(iter(parts.values()))
        return ["x", self._column("kind", node), parts]

    def _part(self, node: _Node, kind: str) -> Any:
        total = node.kinds[kind]
        if kind == "d":
            entries = []
            for key in node.korder:
                child = node.keys[key]
                pres = -1 if child.count == total else self._column("has", node, ("d", key))
                entries.append([key, self._build(child), pres])
            if node.kreordered:
                return ["d", entries, self._column("keyorder", node)]
            return ["d", entries]
        if kind == "l" and node.reordered:
            part = [
                "o",
                self._column("order", node),
                [self._build(node.items[n]) for n in node.order],
            ]
            self.names[id(part)] = list(node.order)
            return part
        if kind == "l":
            entries = []
            for name in node.order:
                child = node.items[name]
                pres = -1 if child.count == total else self._column("has", node, ("l", name))
                entries.append([self._build(child), pres])
            part = ["l", entries]
            self.names[id(part)] = list(node.order)
            return part
        if node.constant and node.first is not None:
            return ["k", json.loads(node.first)[1]]
        if node.values is not None and len(node.values) * ENUM_SHARE <= node.kinds["s"]:
            part = ["e", self._column("value", node), list(node.values.values())]
            self.enums[id(part)] = {key: i for i, key in enumerate(node.values)}
            return part
        if node.wordings and _worth_wording(node):
            which = self._column("wording", node)
            pieces = self._column("numbers", node)
            part = ["p", which, pieces, [list(w) for w in node.wordings]]
            self.wordings[id(part)] = node.wordings
            return part
        return ["c", self._column("value", node)]


def _worth_wording(node: _Node) -> bool:
    """Keep a text's wordings in the template only when that saves at least a tenth.

    Per tract, a text costs its characters; split, it costs its numbers plus about one
    character to say which wording (the template holds each wording once).
    """
    wording_chars = sum(sum(len(p) + 3 for p in w) for w in (node.wordings or {}))
    per_tract = node.piece_chars + node.kinds.get("s", 0)
    return wording_chars + per_tract < node.chars * 0.9


DIGITS = string.digits + string.ascii_lowercase + string.ascii_uppercase
"""A short list of values is picked, tract by tract, with one of these characters each."""


def _encode(values: list[Any]) -> Any:
    """The smallest column form for one county's values (see the module text)."""
    keys = [_same(v) for v in values]
    if len(set(keys)) == 1:
        return values[0]
    plain = values
    distinct: dict[str, int] = {}
    order: list[Any] = []
    index = []
    for v, k in zip(values, keys, strict=True):
        if k not in distinct:
            distinct[k] = len(order)
            order.append(v)
        index.append(distinct[k])
    coded: dict[str, Any] = {"v": order, "i": index}
    if len(order) <= len(DIGITS):
        coded["i"] = "".join(DIGITS[i] for i in index)
    size = len(json.dumps(plain, separators=(",", ":"), ensure_ascii=False))
    if len(json.dumps(coded, separators=(",", ":"), ensure_ascii=False)) < size:
        return coded
    return plain


def encode_county(
    builder: TemplateBuilder, geoids: list[str], profiles: list[dict[str, Any]]
) -> dict[str, Any]:
    """The county file for these tracts (profiles in the order of ``geoids``).

    Every tract is rebuilt from the result and compared with its profile; a tract that does
    not come back exactly is saved whole in ``raw``.
    """
    template = builder.template()
    n = len(profiles)
    cols: list[list[Any]] = [[_MISSING] * n for _ in builder.columns]
    for t, profile in enumerate(profiles):
        _fill(builder, template, profile, t, cols)
    entries = []
    for values in cols:
        present = [v for v in values if v is not _MISSING]
        filler = present[0] if present else None
        entries.append(_encode([filler if v is _MISSING else v for v in values]))
    # A column that holds the same values as an earlier one (a headline and its fact)
    # points at it.
    first: dict[str, int] = {}
    for k, entry in enumerate(entries):
        if isinstance(entry, (list, dict)):
            key = _dumps_plain(entry)
            if key in first:
                entries[k] = {"=": first[key]}
            else:
                first[key] = k
    data: dict[str, Any] = {"$tracts": 1, "geoids": list(geoids), "cols": entries, "raw": {}}
    for geoid, profile in zip(geoids, profiles, strict=True):
        if _canon(assemble(template, data, geoid)) != _canon(profile):
            data["raw"][geoid] = profile
    return data


class _Missing:
    __slots__ = ()


_MISSING = _Missing()


def _fill(b: TemplateBuilder, node: Any, value: Any, t: int, cols: list[list[Any]]) -> None:
    """Write tract ``t``'s ``value`` at template ``node`` into the columns."""
    tag = node[0]
    if tag == "x":
        kind = _kind(value)
        cols[node[1]][t] = kind
        if kind in node[2]:
            _fill(b, node[2][kind], value, t, cols)
        return
    if tag == "k":
        return
    if tag == "c":
        cols[node[1]][t] = value if isinstance(value, _SCALAR) else None
        return
    if tag == "e":
        cols[node[1]][t] = b.enums[id(node)].get(_same(value), 0)
        return
    if tag == "p":
        wording, numbers = _split(value) if isinstance(value, str) else ((), [])
        cols[node[1]][t] = b.wordings[id(node)].get(wording, 0)
        cols[node[2]][t] = _SEP.join(numbers)
        return
    if tag == "d":
        if not isinstance(value, dict):
            return
        for key, child, pres in node[1]:
            if pres >= 0:
                cols[pres][t] = 1 if key in value else 0
            if key in value:
                _fill(b, child, value[key], t, cols)
        if len(node) > 2:
            place = {key: i for i, (key, _c, _p) in enumerate(node[1])}
            order = [place[key] for key in value if key in place]
            cols[node[2]][t] = "" if order == sorted(order) else ",".join(map(str, order))
        return
    if not isinstance(value, list):
        return
    if tag == "o":
        place = {name: i for i, name in enumerate(b.names[id(node)])}
        names = _idents(value)
        cols[node[1]][t] = ",".join(str(place[n]) for n in names)
        for name, item in zip(names, value, strict=True):
            _fill(b, node[2][place[name]], item, t, cols)
        return
    by_name = dict(zip(_idents(value), value, strict=True))
    names = b.names[id(node)]
    for (child, pres), name in zip(node[1], names, strict=True):
        if pres >= 0:
            cols[pres][t] = 1 if name in by_name else 0
        if name in by_name:
            _fill(b, child, by_name[name], t, cols)


def _dumps_plain(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _canon(value: Any) -> str:
    """Exact comparison text: key order counts (the screen numbers notes by key order)."""
    return json.dumps(value, ensure_ascii=False)


def assemble(template: Any, data: dict[str, Any], geoid: str) -> Any:
    """Rebuild one tract's profile from the template and its county file."""
    if geoid in data.get("raw", {}):
        return data["raw"][geoid]
    t = data["geoids"].index(geoid)
    cols = data["cols"]

    def col(k: int) -> Any:
        entry = cols[k]
        if isinstance(entry, list):
            return entry[t]
        if isinstance(entry, dict):
            if "=" in entry:
                return col(entry["="])
            i = entry["i"][t]
            return entry["v"][DIGITS.index(i) if isinstance(i, str) else i]
        return entry

    def build(node: Any) -> Any:
        tag = node[0]
        if tag == "k":
            return node[1]
        if tag == "c":
            return col(node[1])
        if tag == "e":
            return node[2][col(node[1])]
        if tag == "p":
            words = node[3][col(node[1])]
            numbers = col(node[2]).split(_SEP) if len(words) > 1 else []
            out = [words[0]]
            for number, word in zip(numbers, words[1:], strict=True):
                out += [number, word]
            return "".join(out)
        if tag == "x":
            return build(node[2][col(node[1])])
        if tag == "o":
            order = col(node[1])
            return [build(node[2][int(i)]) for i in order.split(",")] if order else []
        if tag == "d":
            entries = node[1]
            order = col(node[2]) if len(node) > 2 else ""
            if order:
                entries = [entries[int(i)] for i in order.split(",")]
            return {key: build(child) for key, child, pres in entries if pres < 0 or col(pres)}
        return [build(child) for child, pres in node[1] if pres < 0 or col(pres)]

    return build(template)
