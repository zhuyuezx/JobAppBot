"""Exact posting matches and possible repeats, including repeats within one source.

Only a shared employer URL/requisition confirms a duplicate. Matching company,
title and location is a warning; distinct requisitions retain separate records.
"""
from __future__ import annotations

import re
from typing import Any

from jobFilter.job_identity import normalize_apply_url

_COMPANY_SUFFIX = re.compile(r"\b(inc|llc|ltd|corp|corporation|co|company|limited|plc|group|holdings|technologies|technology)\b\.?")
_TITLE_WORDS = {"graduate": "grad", "graduates": "grad", "grads": "grad", "sr": "senior", "jr": "junior", "and": "",
                "engineering": "engineer", "i": "1", "ii": "2", "iii": "3"}
_NOT_A_CITY = {"", "united states", "united states of america", "us", "usa", "u s", "u s a", "anywhere",
               "multiple locations", "various locations", "north america", "americas", "hybrid", "onsite", "on site"}
_CITY_ALIASES = {"nyc": "new york", "new york city": "new york", "manhattan": "new york", "sf": "san francisco",
                 "la": "los angeles", "dc": "washington", "washington dc": "washington", "washington d c": "washington"}
_STATE_NAMES = dict(pair.split(':') for pair in (
    'al:alabama|ak:alaska|az:arizona|ar:arkansas|ca:california|co:colorado|ct:connecticut|de:delaware|'
    'fl:florida|ga:georgia|hi:hawaii|id:idaho|il:illinois|in:indiana|ia:iowa|ks:kansas|ky:kentucky|'
    'la:louisiana|me:maine|md:maryland|ma:massachusetts|mi:michigan|mn:minnesota|ms:mississippi|'
    'mo:missouri|mt:montana|ne:nebraska|nv:nevada|nh:new hampshire|nj:new jersey|nm:new mexico|'
    'ny:new york|nc:north carolina|nd:north dakota|oh:ohio|ok:oklahoma|or:oregon|pa:pennsylvania|'
    'ri:rhode island|sc:south carolina|sd:south dakota|tn:tennessee|tx:texas|ut:utah|vt:vermont|'
    'va:virginia|wa:washington|wv:west virginia|wi:wisconsin|wy:wyoming|dc:district of columbia'
).split('|'))
_STATES = {**{name: code for code, name in _STATE_NAMES.items()}, **{code: code for code in _STATE_NAMES}}


def _words(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def company_key(company: str) -> str:
    company = re.sub(r'\s+(?:u\.?s\.?|usa|united states)\s*$', '', (company or '').strip(), flags=re.I)
    return _words(_COMPANY_SUFFIX.sub(" ", company.lower()))


def title_key(title: str) -> str:
    """Title words, lowercased, with spelling variants folded ("Graduate" = "Grad", "II" = "2")."""
    return " ".join(w for w in (_TITLE_WORDS.get(w, w) for w in _words(title).split()) if w)


def cities(location: str) -> set[str]:
    """City names in a location string; "remote" counts as one. Countries and filler are dropped."""
    found = set()
    for part in re.split(r"[;|/·]|\s+(?:or|and)\s+", re.sub(r"\s+\+\d+$", "", (location or "").lower())):
        city = _words(part.split(",")[0])
        if "remote" in city.split():
            found.add("remote")
            continue
        # Preserve ambiguous city names (New York / Washington); other lone
        # state names describe a region, not a city.
        if city in _STATES and city not in {'new york', 'washington', 'la', 'dc'}:
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


def _states(location: str) -> set[str]:
    found = set()
    for part in re.split(r'[;|/·]|\s+(?:or|and)\s+', re.sub(r'\s+\+\d+$', '', (location or '').lower())):
        pieces = [_words(p) for p in part.split(',')]
        # A trailing state is explicit; a leading state is regional only if
        # followed by country/filler, so Washington, DC is not Washington state.
        for piece in pieces[1:]:
            if piece in _STATES:
                found.add(_STATES[piece])
        if pieces[0] in _STATES and pieces[0] != 'la' and all(p in _NOT_A_CITY for p in pieces[1:]):
            found.add(_STATES[pieces[0]])
    return found


def _compatible_location(a: str, b: str) -> bool:
    states_a, states_b = _states(a), _states(b)
    if states_a and states_b and states_a.isdisjoint(states_b):
        return False
    return _same_place(cities(a), cities(b))


def find(rows: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Return annotated matches, without merging jobs or their application history."""
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    by_url: dict[str, list[dict[str, Any]]] = {}
    urls = {}
    for c in candidates:
        key = (company_key(c.get("company")), title_key(c.get("title")))
        if all(key):
            by_key.setdefault(key, []).append(c)
        url = normalize_apply_url(c.get("apply_url"))
        urls[c["id"]] = url
        if url:
            by_url.setdefault(url, []).append(c)
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        key = (company_key(r.get("company")), title_key(r.get("title")))
        url = normalize_apply_url(r.get("apply_url"))
        pool = {c['id']: c for c in by_key.get(key, []) + by_url.get(url, [])}
        same = []
        for c in pool.values():
            if c['id'] == r['id']:
                continue
            exact = bool(url and url == urls[c['id']])
            if exact or _compatible_location(r.get('location'), c.get('location')):
                same.append({**c, 'match_type': 'exact' if exact else 'possible'})
        if same:
            out[r['id']] = sorted(same, key=lambda c: (c.get('first_seen') or '', c['id']))
    return out
