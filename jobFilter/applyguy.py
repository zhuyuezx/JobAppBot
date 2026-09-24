"""ApplyGuy's public new-grad feed; employer links, not its one-click apply service."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit

FEED_URL = 'https://raw.githubusercontent.com/ApplyGuy/2027-New-Grad-Jobs/main/data/new-grad-jobs.json'
REPOSITORY_URL = 'https://github.com/ApplyGuy/2027-New-Grad-Jobs'


def countries_for(location: str, url: str) -> list[str]:
    from jobFilter.sources import _US_STATES
    # Do not trust the feed's "US roles" description or interpret India as Indiana.
    if re.search(r'\b(india|hyderabad|bengaluru|bangalore)\b', location, re.I):
        return ['IN']
    if re.search(r'\b(germany|neu-ulm)\b', location, re.I):
        return ['DE']
    if re.search(r'\b(australia|VIC)\b', location, re.I):
        return ['AU']
    # Only use the location segment of a Workday URL, never company/HQ/title.
    parsed = urlsplit(url)
    match = re.search(r'/job/([^/]+)/', unquote(parsed.path)) if parsed.hostname and parsed.hostname.endswith('.myworkdayjobs.com') else None
    evidence = location + (' ' + match[1] if match else '')
    if re.search(r'(?<![A-Za-z])(?:united[ -]states|USA|U\.?S\.?)(?![A-Za-z])', evidence, re.I):
        return ['US']
    state = re.search(r'(?:,\s*|\s+)([A-Z]{2})(?:\s+metro area)?$', location)
    if state and state[1] in _US_STATES - {'IN', 'DE'}:
        return ['US']
    # Full state names are unambiguous; bare IN/DE remain unknown without US evidence.
    if location.lower() in ('new york', 'minnesota', 'tennessee') or re.search(r'\b(pennsylvania|indiana|delaware)\b', location, re.I):
        return ['US']
    return []


def parse_feed(data: dict, max_age_days: float = 3, now: datetime | None = None) -> list:
    from jobFilter.sources import _job, _ats_from_url
    if not isinstance(data, dict) or not isinstance(data.get('jobs'), list):
        raise ValueError('ApplyGuy feed must contain a jobs list')
    if max_age_days <= 0:
        raise ValueError('ApplyGuy max_age_days must be positive')
    today = (now or datetime.now(timezone.utc)).date()
    jobs = []
    for item in data['jobs']:
        if not isinstance(item, dict):
            continue
        if not all(isinstance(item.get(k), str) and item[k].strip() for k in ('id', 'company', 'title', 'location', 'listingUrl', 'posted')):
            continue
        if item.get('active') is False or item.get('is_visible') is False:
            continue
        url = item['listingUrl'].strip()
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.hostname in ('applyguy.ai', 'www.applyguy.ai'):
            continue
        try:
            posted = datetime.strptime(item['posted'], '%Y-%m-%d').replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if not 0 <= (today - posted.date()).days < max_age_days:
            continue
        jid = 'applyguy___' + item['id']
        location = item['location'].strip()
        eligibility = item.get('eligibility') if isinstance(item.get('eligibility'), str) else None
        jobs.append(_job('applyguy', id=jid, dedup_key=jid, company=item['company'].strip(), title=item['title'].strip(),
            location=location, countries=countries_for(location, url),
            workplace_type='Remote' if 'remote' in location.lower() else None,
            seniority=eligibility, min_yoe=None, category='Software Development',
            requirements_summary='Source eligibility: ' + (eligibility or 'unspecified'),
            published_at=posted.isoformat(timespec='seconds'), published_millis=int(posted.timestamp() * 1000),
            apply_url=url, listing_url=REPOSITORY_URL, source=_ats_from_url(url), raw=item))
    return jobs


def fetch_applyguy(cfg: dict) -> list:
    from jobFilter.sources import _cached_get_json
    return parse_feed(_cached_get_json(FEED_URL, 'applyguy'), float(cfg.get('max_age_days', 3)))
