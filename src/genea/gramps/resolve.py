"""Map proposals to Gramps Web objects and refuse any whose target the user has edited since (spec §4, §5)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from genea.analysis.models import Proposal
from genea.gramps.client import GrampsClient
from genea.gramps.dates import same_date, to_gramps_date
from genea.ingest.gedcom.dates import MONTHS

# GEDCOM individual event tags -> Gramps EventType strings; EVEN has no fixed Gramps type, so it is not matched
TAG_TO_GRAMPS = {
    "BIRT": "Birth", "CHR": "Christening", "BAPM": "Baptism", "DEAT": "Death", "BURI": "Burial",
    "CREM": "Cremation", "ADOP": "Adopted", "RESI": "Residence", "OCCU": "Occupation", "EDUC": "Education",
    "EMIG": "Emigration", "IMMI": "Immigration", "NATU": "Naturalization", "CENS": "Census",
    "PROB": "Probate", "WILL": "Will", "GRAD": "Graduation", "RETI": "Retirement", "CONF": "Confirmation",
    "FCOM": "First Communion", "ORDN": "Ordination", "RELI": "Religion",
}
_PLACE_ID = re.compile(r"^P\d+$")
_SOURCE_ID = re.compile(r"^S\d+$")
_ISO = re.compile(r"^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$")
_MONTH_NAMES = {v: k for k, v in MONTHS.items()}
_MODIFIER_WORDS = {1: "BEF", 2: "AFT", 3: "ABT"}
_QUALITY_WORDS = {1: "EST", 2: "CAL"}


class Stale(Exception):
    """Gramps no longer looks the way the proposal expects; the message names both values."""


@dataclass
class Target:
    proposal: Proposal
    reads: dict[str, tuple[str, dict, str]] = field(default_factory=dict)  # key -> (kind, obj, etag) to PUT
    place_handle: str | None = None
    source_handle: str | None = None
    gramps_date: dict | None = None
    child_handle: str | None = None  # link_child: the child has no ETag in reads, the writer reads it fresh


def gramps_id(tree_id: str) -> str:
    """'tree.ged#@I0012@' -> 'I0012'."""
    return tree_id.rsplit("#", 1)[-1].strip("@")


def _type(event: dict) -> str:
    value = event.get("type")
    return value.get("string", "") if isinstance(value, dict) else str(value or "")


def _gramps_type(tag: str) -> str:
    name = TAG_TO_GRAMPS.get(tag)
    if name is None:
        raise ValueError(f"event type {tag} cannot be matched to a Gramps event type")
    return name


def _show_date(d: dict | None) -> str:
    """A Gramps date written GEDCOM-style, for Stale messages."""
    if not d or not any(v for v in (d.get("dateval") or []) if v is not False):
        return "no date"

    def part(day, month, year) -> str:
        return " ".join(str(x) for x in (day or None, _MONTH_NAMES.get(month), year) if x)

    dv = d.get("dateval") or []
    first = part(*dv[:3])
    modifier = d.get("modifier", 0)
    if modifier in (4, 5) and len(dv) >= 7:
        words = ("BET", "AND") if modifier == 4 else ("FROM", "TO")
        return f"{words[0]} {first} {words[1]} {part(*dv[4:7])}"
    word = _MODIFIER_WORDS.get(modifier) or _QUALITY_WORDS.get(d.get("quality", 0))
    return f"{word} {first}" if word else first


def _profile_date_matches(value, raw: str | None) -> bool:
    """Whether a person profile's birth date may equal raw; unknown formats count as equal (fail closed)."""
    if isinstance(value, dict) or value is None:
        return same_date(value, raw)
    text = str(value).strip()
    if not text or not raw:
        return not text and not raw
    iso = _ISO.match(text)
    if not iso:
        return True
    try:
        want = to_gramps_date(raw)
    except ValueError:
        return True
    if want["modifier"] or want["quality"] or want["calendar"]:
        return False  # an exact ISO date never shows a modified, estimated or Julian date
    return [int(g or 0) for g in (iso.group(3), iso.group(2), iso.group(1))] == want["dateval"][:3]


class Resolver:
    def __init__(self, client: GrampsClient):
        self.client = client
        self._places: list[dict] | None = None
        self._sources: list[dict] | None = None

    @property
    def places(self) -> list[dict]:
        if self._places is None:
            self._places = self.client.list_all("places", "handle,gramps_id,profile")
        return self._places

    @property
    def sources(self) -> list[dict]:
        if self._sources is None:
            self._sources = self.client.list_all("sources", "handle,gramps_id,title")
        return self._sources

    def _place_title(self, handle: str | None) -> str | None:
        if not handle:
            return None
        for place in self.places:
            if place.get("handle") == handle:
                return (place.get("profile") or {}).get("name") or None
        return None

    def place_handle(self, value: str) -> str:
        if _PLACE_ID.match(value):
            hits = [p for p in self.places if p.get("gramps_id") == value]
        else:
            hits = [p for p in self.places if (p.get("profile") or {}).get("name") == value]
        if len(hits) != 1:
            raise ValueError(f"place {value!r} not in Gramps; add it there or give its Gramps id")
        return hits[0]["handle"]

    def source_handle(self, value: str) -> str:
        if _SOURCE_ID.match(value):
            hits = [s for s in self.sources if s.get("gramps_id") == value]
        else:
            hits = [s for s in self.sources if s.get("title") == value]
        if len(hits) != 1:
            raise ValueError(f"source {value!r} not in Gramps; add it there or give its Gramps id")
        return hits[0]["handle"]

    def _read(self, kind: str, tree_id: str) -> tuple[dict, str]:
        found = self.client.find_by_gramps_id(kind, gramps_id(tree_id))
        if found is None:
            raise Stale(f"{tree_id} is no longer in Gramps")
        return self.client.get(kind, found["handle"])

    def _events(self, person: dict, gramps_type: str) -> list[tuple[dict, str]]:
        out = []
        for ref in person.get("event_ref_list") or []:
            event, etag = self.client.get("events", ref["ref"])
            if _type(event) == gramps_type:
                out.append((event, etag))
        return out

    def _show_event(self, event: dict) -> str:
        text = f"{_type(event)} {_show_date(event.get('date'))}"
        place = self._place_title(event.get("place"))
        if place:
            text += f" in {place}"
        if event.get("description"):
            text += f" ({event['description']})"
        return text

    def resolve(self, p: Proposal, new_people: dict[str, str]) -> Target:
        """Raise Stale if Gramps changed since the proposal, ValueError if it cannot be written at all."""
        pl, target = p.payload, Target(proposal=p)
        date = pl.get("birth_date") if p.operation == "add_person" else pl.get("date")
        if date and p.operation in ("add_event", "update_event", "add_person"):
            target.gramps_date = to_gramps_date(date)
        place = pl.get("birth_place") if p.operation == "add_person" else pl.get("place")
        if place and p.operation in ("add_event", "update_event", "add_person"):
            target.place_handle = self.place_handle(place)
        getattr(self, f"_{p.operation}")(p, target, new_people)
        return target

    def _update_event(self, p: Proposal, target: Target, new_people: dict[str, str]) -> None:
        person, _ = self._read("people", p.payload["person_id"])
        anchor = p.anchor or {}
        tag = anchor.get("type") or ""
        events = self._events(person, _gramps_type(tag))
        hits = [(e, etag) for e, etag in events
                if same_date(e.get("date"), anchor.get("date"))
                and self._place_title(e.get("place")) == (anchor.get("place") or None)
                and (not anchor.get("description") or e.get("description") == anchor["description"])]
        was = f"{tag} {anchor.get('date') or 'no date'}" + (f" in {anchor['place']}" if anchor.get("place") else "")
        if not hits:
            now = "; ".join(self._show_event(e) for e, _ in events) or f"no {_type_name(tag)} event"
            raise Stale(f"Gramps has {now}, the proposal was made against {was}")
        if len(hits) > 1:
            raise Stale(f"{was} is in Gramps more than once")
        event, etag = hits[0]
        target.reads["event"] = ("events", event, etag)

    def _add_event(self, p: Proposal, target: Target, new_people: dict[str, str]) -> None:
        person, etag = self._read("people", p.payload["person_id"])
        tag = p.payload["type"]
        for event, _ in self._events(person, _gramps_type(tag)):
            if same_date(event.get("date"), p.payload.get("date")):
                raise Stale(f"Gramps already has {self._show_event(event)}, the event this proposal adds")
        target.reads["person"] = ("people", person, etag)

    def _add_note(self, p: Proposal, target: Target, new_people: dict[str, str]) -> None:
        person, etag = self._read("people", p.payload["person_id"])
        target.reads["person"] = ("people", person, etag)

    def _add_source_citation(self, p: Proposal, target: Target, new_people: dict[str, str]) -> None:
        target.source_handle = self.source_handle(p.payload["source"])
        person, etag = self._read("people", p.payload["person_id"])
        tag = p.payload.get("event_type")
        if not tag:
            target.reads["owner"] = ("people", person, etag)
            return
        events = self._events(person, _gramps_type(tag))
        if len(events) != 1:
            raise Stale(f"Gramps has {len(events)} {_type_name(tag)} events for {p.payload['person_id']}, "
                        f"the proposal expects exactly one")
        event, event_etag = events[0]
        target.reads["owner"] = ("events", event, event_etag)

    def _link_child(self, p: Proposal, target: Target, new_people: dict[str, str]) -> None:
        family, etag = self._read("families", p.payload["family_id"])
        child_id = p.payload["child_id"]
        if child_id.startswith("proposal:"):
            ref = child_id.split(":", 1)[1]
            if ref not in new_people:
                raise Stale(f"{ref} was not written")
            child, _ = self.client.get("people", new_people[ref])
        else:
            child, _ = self._read("people", child_id)
        if any(c.get("ref") == child["handle"] for c in family.get("child_ref_list") or []):
            raise Stale(f"{child_id} is already a child of {p.payload['family_id']} in Gramps")
        others = [h for h in child.get("parent_family_list") or [] if h != family["handle"]]
        if others:
            raise Stale(f"Gramps has {child_id} in another parent family, the proposal expected none")
        target.reads["family"] = ("families", family, etag)
        target.child_handle = child["handle"]

    def _add_person(self, p: Proposal, target: Target, new_people: dict[str, str]) -> None:
        pl = p.payload
        for row in self.client.list_all("people", "handle,gramps_id,profile"):
            profile = row.get("profile") or {}
            if (profile.get("name_given") == pl["given"] and profile.get("name_surname") == pl["surname"]
                    and _profile_date_matches((profile.get("birth") or {}).get("date"), pl.get("birth_date"))):
                raise Stale(f"Gramps already has {pl['given']} {pl['surname']} "
                            f"born {pl.get('birth_date') or 'on no date'} ({row.get('gramps_id')})")


def _type_name(tag: str) -> str:
    return TAG_TO_GRAMPS.get(tag, tag)
