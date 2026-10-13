"""Emitting FrameX F-Script. The only module that writes engine syntax.

Keeping syntax in one place means a mapping spec never contains a quote, a
bracket or an escape -- it declares meaning, and this module renders it.

String escaping is JSON-compatible (verified against the engine: `\\"` and
`\\\\` round-trip, and UTF-8 station names like "Genève-Aéroport" or
"Zürich HB" pass through unchanged), so json.dumps is the correct escaper.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal, InvalidOperation
import json
import math
import re

#: Characters FrameX accepts in a bare identifier. Anything else must be
#: rewritten, because a malformed identifier is a syntax error at load time.
_BARE_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_UNSAFE = re.compile(r"[^A-Za-z0-9_]+")

#: Transliterations applied before stripping, so "Zürich" and "Zurich" do not
#: collapse onto different identifiers than a reader would expect.
_FOLD = str.maketrans(
    {
        "ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue",
        "à": "a", "â": "a", "á": "a", "è": "e", "é": "e", "ê": "e", "ë": "e",
        "ì": "i", "î": "i", "í": "i", "ï": "i", "ò": "o", "ô": "o", "ó": "o",
        "ù": "u", "û": "u", "ú": "u", "ç": "c", "ñ": "n", "ß": "ss",
    }
)


def identifier(*parts: object, prefix: str = "x") -> str:
    """A safe, stable, lower-case FrameX object identifier built from parts.

    Deterministic: the same inputs always produce the same identifier, so
    re-running an ingest overwrites rather than duplicates.
    """
    joined = "_".join(str(p) for p in parts if p is not None and str(p) != "")
    folded = joined.translate(_FOLD)
    cleaned = _UNSAFE.sub("_", folded).strip("_").lower()
    if not cleaned:
        cleaned = "unknown"
    if not cleaned[0].isalpha():
        cleaned = f"{prefix}_{cleaned}"
    return cleaned


def class_name(name: str) -> str:
    """A FrameX class name. Classes are capitalised by convention."""
    folded = str(name).translate(_FOLD)
    cleaned = _UNSAFE.sub("", folded.title())
    if not cleaned or not cleaned[0].isalpha():
        raise ValueError(f"cannot form a class name from {name!r}")
    return cleaned


def literal(value) -> str | None:
    """Render a Python value as a FrameX literal, or None if it carries nothing.

    None is returned (rather than a "null" literal) for missing data: under an
    open world, saying nothing leaves the statement `unknown`, which is the
    honest encoding of an empty cell. Writing a placeholder would assert a
    value that was never observed.
    """
    if value is None:
        return None
    item = getattr(value, "item", None)
    if callable(item):
        try:
            value = item()
        except (ValueError, TypeError):
            return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return str(int(value)) if value.is_integer() else repr(value)
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "nat", "<na>"}:
        return None
    # The API delivers booleans as the strings 'true'/'false'. Keeping them as
    # strings would make `?S[stoppoint -> true]` silently fail to match.
    if text.lower() == "true":
        return "true"
    if text.lower() == "false":
        return "false"
    return json.dumps(text, ensure_ascii=False)


def stable_identifier(prefix: str, *parts: object) -> str:
    """Collision-resistant identity; preserves typed component boundaries."""
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str,
                         allow_nan=False, separators=(",", ":"))
    return identifier(prefix) + "_" + hashlib.sha256(payload.encode()).hexdigest()[:32]


def typed_literal(value, kind: str | None = None) -> str | None:
    """Render a declared field type, rejecting malformed nonempty values."""
    rendered = literal(value)
    if rendered is None or kind is None:
        return rendered
    if kind == "String":
        return json.dumps(str(value).strip(), ensure_ascii=False)
    if kind == "Float":
        if isinstance(value, bool):
            raise ValueError("boolean is not a decimal measurement")
        try:
            number = Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError(f"invalid decimal: {value!r}") from exc
        if not number.is_finite():
            raise ValueError("decimal must be finite")
        text = format(number, "f")
        return text if "." in text else text + ".0"
    if kind == "Integer":
        if isinstance(value, bool):
            raise ValueError("boolean is not an integer measurement")
        try:
            number = Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError(f"invalid integer: {value!r}") from exc
        if not number.is_finite() or number != number.to_integral_value():
            raise ValueError(f"invalid integer: {value!r}")
        return str(int(number))
    if kind == "Boolean":
        if rendered not in {"true", "false"}:
            raise ValueError(f"invalid boolean: {value!r}")
        return rendered
    raise ValueError(f"unknown literal type: {kind}")


def membership(subject: str, klass: str) -> str:
    return f"{subject}:{klass}."


def fact(subject: str, slot: str, value: str) -> str:
    """One property fact. `value` must already be a rendered literal or id."""
    return f"{subject}[{slot} -> {value}]."


def annotation(subject: str, slot: str, value: str, source: str, time: str) -> str:
    """Provenance for one fact, so `framex explain` can name where it came from.

    Verified: after annotating, `explain` reports
    `Source: wifistation | time: 2026-09-18` instead of `<json-source>`.
    """
    return (
        f"annotate {subject}[{slot} -> {value}] "
        f"source {json.dumps(source)} time {json.dumps(time)}."
    )


def closed_slot(slot: str) -> str:
    """Declare a slot's evidence complete, so absence means `false`.

    This is a modelling commitment, not a convenience: it is only valid when
    the source dataset really is the full inventory for that slot.
    """
    return f"closed slot {slot}."


def comment(text: str) -> str:
    return "\n".join("// " + line for line in str(text).splitlines())


def header(title: str, lines: list[str]) -> list[str]:
    out = [comment("=" * 68), comment(title)]
    out.extend(comment(line) for line in lines)
    out.append(comment("=" * 68))
    return out
