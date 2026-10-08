"""Exact posting matches and possible repeats, including repeats within one source.

Only a shared employer URL/requisition confirms a duplicate. Matching company,
title and location is a warning; distinct requisitions retain separate records.
"""
from __future__ import annotations

import re
from typing import Any

from jobFilter.job_identity import normalize_apply_url, posting_identity

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
    """Conservative wording aliases for warnings, never for merging records."""
    text = " ".join(w for w in (_TITLE_WORDS.get(w, w) for w in _words(title).split()) if w)
    # Aggregators can retain a previous title after the employer renames a role:
    # "Software Engineer I (New Grad 2027)" -> "Software Engineer, Early Career".
    # Keep specialties, internships, higher levels and title locations intact.
    if re.search(r"\b(?:new grad|early career)\b", text):
        text = re.sub(r"\b(?:new grad|early career)\b", "early career", text)
        text = re.sub(r"\bengineer 1\b", "engineer", text)
        text = re.sub(r"\b20\d{2}\b", "", text)
    return " ".join(text.split())


def _compatible_cohort(a: str, b: str) -> bool:
    """An omitted cohort is unknown; two explicitly different cohorts do not match."""
    years_a = set(re.findall(r"\b20\d{2}\b", a or ""))
    years_b = set(re.findall(r"\b20\d{2}\b", b or ""))
    return not years_a or not years_b or bool(years_a & years_b)


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
    countries_a, countries_b = _countries(a), _countries(b)
    if countries_a and countries_b and countries_a.isdisjoint(countries_b):
        return False
    states_a, states_b = _states(a), _states(b)
    if states_a and states_b and states_a.isdisjoint(states_b):
        return False
    return _same_place(cities(a), cities(b))


def _countries(location: str) -> set[str]:
    text = ' ' + _words(location) + ' '
    names = {'us': ('united states', 'united states of america', 'usa', 'u s', 'us'),
             'ca': ('canada',), 'gb': ('united kingdom', 'uk', 'england'),
             'in': ('india',), 'de': ('germany',), 'au': ('australia',),
             'ie': ('ireland',), 'sg': ('singapore',)}
    return {code for code, aliases in names.items() if any(' ' + name + ' ' in text for name in aliases)}


def classify(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any] | None:
    """Shared evidence rules. Grouping is reversible; only exact URLs justify storage dedup."""
    url_a, url_b = normalize_apply_url(a.get('apply_url')), normalize_apply_url(b.get('apply_url'))
    if url_a and url_a == url_b:
        return {'match_type': 'exact', 'groupable': True}
    key = (company_key(a.get('company')), title_key(a.get('title')))
    if not all(key) or key != (company_key(b.get('company')), title_key(b.get('title'))):
        return None
    if not (_compatible_location(a.get('location'), b.get('location'))
            and _compatible_cohort(a.get('title'), b.get('title'))):
        return None
    identity_a, identity_b = posting_identity(a.get('apply_url')), posting_identity(b.get('apply_url'))
    # Same title is not enough to collapse explicit, different requisitions.
    # Broad "US" / remote matches remain warnings rather than hidden rows.
    shared_cities = (cities(a.get('location')) & cities(b.get('location'))) - {'remote'}
    groupable = bool(shared_cities) and not (identity_a and identity_b and identity_a != identity_b)
    return {'match_type': 'possible', 'groupable': groupable}


def find(rows: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Return annotated matches, without merging jobs or their application history."""
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    by_url: dict[str, list[dict[str, Any]]] = {}
    for c in candidates:
        key = (company_key(c.get("company")), title_key(c.get("title")))
        if all(key):
            by_key.setdefault(key, []).append(c)
        url = normalize_apply_url(c.get("apply_url"))
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
            match = classify(r, c)
            if match:
                same.append({**c, **match})
        if same:
            out[r['id']] = sorted(same, key=lambda c: (c.get('first_seen') or '', c['id']))
    return out
