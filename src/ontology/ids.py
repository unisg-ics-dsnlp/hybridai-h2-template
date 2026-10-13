"""National stop-point identity (BPUIC) and its many spellings.

Every dataset on data.sbb.ch that refers to a stop point does so with the same
national identifier, but never with the same type or column name. Observed on
the live Explore API:

    dienststellen.number   int64    8509000
    wifistation.bpuic      float64  8503424.0     -> "8503424.0" when stringified
    passagierfrequenz.uic  str      "8502113.0"
    linie-*.didok          str      "2113"        -> the 5-digit short number
    billetautomat.bps      str      "CH"          -> an abbreviation, not a number

Normalising this in one place is what lets a dataset be attached with a short
declarative spec instead of bespoke cleanup code.
"""

from __future__ import annotations

from collections import Counter
import math
import re

#: Swiss UIC country code. A full BPUIC is <country><5-digit short number>.
SWISS_UIC_COUNTRY = 85

_DIGITS = re.compile(r"^\d+$")


def normalise_bpuic(value) -> int | None:
    """Return a BPUIC as a plain int, or None when the value carries no id.

    Accepts int, float, numpy scalars and strings, including the float-shaped
    strings the API returns ("8503424.0"). Returns None for None, NaN, empty
    strings and anything that is not a whole number, so callers can count
    unresolved rows instead of crashing on one dirty cell.
    """
    if value is None:
        return None
    # numpy scalars expose .item(); pandas NA/NaT raise or are caught below.
    item = getattr(value, "item", None)
    if callable(item):
        try:
            value = item()
        except (ValueError, TypeError):
            return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer() or value <= 0:
            return None
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if _DIGITS.match(text):
            number = int(text)
            return number if number > 0 else None
        # "8503424.0" and "8503424.00" are the same stop point.
        try:
            number = float(text)
        except ValueError:
            return None
        if not math.isfinite(number) or not number.is_integer() or number <= 0:
            return None
        return int(number)
    return None


def bpuic_from_didok(value, country: int = SWISS_UIC_COUNTRY) -> int | None:
    """Expand a DIDOK short number (2113) into a full BPUIC (8502113).

    Verified against linie-mit-betriebspunkten: 1890 of 1891 rows resolve this
    way, matching the dataset's own bpuic column.
    """
    short = normalise_bpuic(value)
    if short is None or short >= 100_000:
        # Already a full BPUIC (or out of range for a 5-digit short number).
        return short
    return country * 100_000 + short


def stop_point_id(bpuic: int) -> str:
    """The FrameX object identifier for a stop point.

    Identity is the number, never the name: two stations can share a display
    name, so `sp_8509000` is the object and "Chur" is a designation slot.
    """
    return f"sp_{int(bpuic)}"


#: Columns that may carry a stop point's name, in the order they are tried.
#: Used only as a fallback when the numeric id is missing.
NAME_COLUMNS = (
    "standort",
    "bezeichnung_offiziell",
    "designationofficial",
    "bps_name",
    "haltestellen_name",
    "bahnhof_gare_stazione",
    "name",
)

_NAME_NOISE = re.compile(r"[^a-z0-9]+")
_NAME_FOLD = str.maketrans(
    {
        "ä": "ae", "ö": "oe", "ü": "ue", "Ä": "ae", "Ö": "oe", "Ü": "ue",
        "à": "a", "â": "a", "á": "a", "è": "e", "é": "e", "ê": "e", "ë": "e",
        "ì": "i", "î": "i", "í": "i", "ï": "i", "ò": "o", "ô": "o", "ó": "o",
        "ù": "u", "û": "u", "ú": "u", "ç": "c", "ñ": "n", "ß": "ss",
    }
)


def fold_name(value) -> str:
    """Normalise a station name for exact comparison only."""
    return _NAME_NOISE.sub("", str(value).translate(_NAME_FOLD).lower())


class Scope:
    """The set of stop points that exist, and how a row is matched onto them.

    Identity is the BPUIC. A name is only a fallback, and only an *exact*
    fold-equal match counts: the FrameX documentation is explicit that an alias
    match establishes nothing, and Swiss station names bear that out --
    wifistation's "Wil" could be Wil SG or any other Wil, so it is left
    unresolved rather than guessed. Names that are ambiguous within the spine
    are recorded as ambiguous and never resolve.
    """

    def __init__(self, ids: set[int], names: dict[str, int | None] | None = None):
        self.ids = ids
        self.names = names or {}

    @classmethod
    def build(cls, pairs: list[tuple[int, str]]) -> "Scope":
        ids: set[int] = set()
        names: dict[str, int | None] = {}
        for bpuic, designation in pairs:
            ids.add(bpuic)
            key = fold_name(designation)
            if not key:
                continue
            if key in names and names[key] != bpuic:
                names[key] = None  # ambiguous: two stations share this name
            else:
                names.setdefault(key, bpuic)
        return cls(ids, names)

    def __contains__(self, bpuic: int) -> bool:
        return bpuic in self.ids

    def __len__(self) -> int:
        return len(self.ids)

    def by_name(self, row: dict) -> int | None:
        for column in NAME_COLUMNS:
            if column not in row:
                continue
            key = fold_name(row.get(column) or "")
            if not key:
                continue
            found = self.names.get(key)
            if found is not None:
                return found
        return None

    def label(self, row: dict) -> str:
        for column in NAME_COLUMNS:
            value = row.get(column)
            if value is not None and str(value).strip():
                return str(value).strip()
        return ""


class Resolution:
    """Outcome of resolving one dataset's rows onto the stop-point spine.

    Rows are never dropped silently. ist-daten-sbb, for example, carries 292
    distinct foreign stop points (Koeln Hbf, Hamburg Hbf, Annemasse) that are
    correctly out of a Swiss-rail scope -- but that is a fact about the data
    worth reporting, not an error to hide.
    """

    def __init__(self, dataset: str):
        self.dataset = dataset
        self.resolved = 0
        self.out_of_scope_rows = 0
        self.excluded = Counter()
        #: Resolved by exact name because the id column was empty. Counted
        #: separately because a name match is a weaker claim than an id match.
        self.by_name = 0
        self.out_of_scope: dict[int, str] = {}
        self.unidentified = 0
        #: Names of rows that could not be placed at all, for the report.
        self.unresolved_labels: list[str] = []
        #: Set when a declared completeness contract had to be withheld.
        self.withheld: tuple[str, ...] = ()
        #: "fact" or "layer"; see spec.Attribute.provenance.
        self.provenance = "fact"

    def hit(self, how: str = "id") -> None:
        self.resolved += 1
        if how == "name":
            self.by_name += 1

    @property
    def complete(self) -> bool:
        """True when every row found a home.

        A `closed slot` declaration turns absence into `false`, so it is only
        sound when nothing was dropped. wifistation is the cautionary case: six
        of its rows carry no id, and closing the slot regardless would answer
        "St. Gallen has no WiFi" -- which is false.
        """
        return not self.out_of_scope and not self.unidentified

    def miss(self, bpuic: int, label: str = "") -> None:
        self.out_of_scope_rows += 1
        self.out_of_scope.setdefault(bpuic, label)

    def no_id(self, label: str = "") -> None:
        self.unidentified += 1
        if label and label not in self.unresolved_labels:
            self.unresolved_labels.append(label)

    @property
    def total(self) -> int:
        return self.resolved + self.out_of_scope_rows + self.unidentified

    def summary(self) -> str:
        parts = [f"{self.resolved} resolved"]
        if self.by_name:
            parts.append(f"{self.by_name} by exact name")
        if self.out_of_scope:
            sample = ", ".join(
                label or str(number)
                for number, label in list(self.out_of_scope.items())[:3]
            )
            parts.append(f"{len(self.out_of_scope)} outside scope ({sample}...)")
        if self.unidentified:
            detail = ""
            if self.unresolved_labels:
                detail = f" ({', '.join(self.unresolved_labels[:4])})"
            parts.append(f"{self.unidentified} unresolvable{detail}")
        if self.provenance == "layer":
            parts.append("provenance at layer level")
        if self.withheld:
            parts.append(
                f"completeness withheld for {', '.join(self.withheld)} "
                "-- absence stays unknown"
            )
        return "; ".join(parts)
