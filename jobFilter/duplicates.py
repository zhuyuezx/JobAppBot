"""Cross-source duplicates: one posting listed by two aggregators.

Ingestion already merges listings with the same apply URL, or the same company,
title and location text. What slips through are listings whose URL and
location wording differ. ApplyGuy links SeatGeek's own Greenhouse page as
"New York City, NY", while startup.jobs links its redirect page as "New York,
U.S.". Those stay separate rows and are tagged instead: same company, same
title, another source, and a compatible location. A tag only informs, so the
location test leans toward matching: listings match unless both name cities
and none of them agree.
"""
from __future__ import annotations

import re
from typing import Any

_COMPANY_SUFFIX = re.compile(r"\b(inc|llc|ltd|corp|corporation|co|company|limited|plc|group|holdings|technologies|technology)\b\.?")
_TITLE_WORDS = {"graduate": "grad", "graduates": "grad", "grads": "grad", "sr": "senior", "jr": "junior", "and": "",
                "engineering": "engineer", "i": "1", "ii": "2", "iii": "3"}
_NOT_A_CITY = {"", "united states", "united states of america", "us", "usa", "u s", "u s a", "anywhere",
               "multiple locations", "various locations", "north america", "americas", "hybrid", "onsite", "on site"}
_CITY_ALIASES = {"nyc": "new york", "new york city": "new york", "manhattan": "new york", "sf": "san francisco",
                 "la": "los angeles", "dc": "washington", "washington dc": "washington", "washington d c": "washington"}


def _words(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def company_key(company: str) -> str:
    return _words(_COMPANY_SUFFIX.sub(" ", (company or "").lower()))


def title_key(title: str) -> str:
    """Title words, lowercased, with spelling variants folded ("Graduate" = "Grad", "II" = "2")."""
    return " ".join(w for w in (_TITLE_WORDS.get(w, w) for w in _words(title).split()) if w)


def cities(location: str) -> set[str]:
    """City names in a location string; "remote" counts as one. Countries and filler are dropped."""
    found = set()
    for part in re.split(r"[;|/]|\bor\b|\band\b", (location or "").lower()):
        city = _words(part.split(",")[0])
        if "remote" in city.split():
            found.add("remote")
            continue
        city = _CITY_ALIASES.get(city, city)
        city = re.sub(r" (city|metro|area|bay area|metropolitan area)$", "", city)
        if city not in _NOT_A_CITY:
            found.add(_CITY_ALIASES.get(city, city))
    return found


def _same_place(a: set[str], b: set[str]) -> bool:
    if not a or not b or "remote" in a or "remote" in b:
        return True
    return any(x == y or x.startswith(y + " ") or y.startswith(x + " ") for x in a for y in b)


def find(rows: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """For each row, the candidates from other sources that are the same posting, oldest first.

    rows and candidates carry id, via, company, title, location, first_seen; candidates may add app_status.
    """
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for c in candidates:
        key = (company_key(c.get("company")), title_key(c.get("title")))
        if all(key):
            by_key.setdefault(key, []).append(c)
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        key = (company_key(r.get("company")), title_key(r.get("title")))
        here = cities(r.get("location"))
        same = [c for c in by_key.get(key, []) if c["id"] != r["id"] and (c.get("via") or "hiringcafe") != (r.get("via") or "hiringcafe")
                and _same_place(here, cities(c.get("location")))]
        if same:
            out[r["id"]] = sorted(same, key=lambda c: c.get("first_seen") or "")
    return out
