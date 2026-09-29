"""SpeedyApply's US new-grad Markdown feed, with commit-anchored age estimates."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

from jobFilter.applyguy import countries_for

REPOSITORY = 'speedyapply/2027-SWE-College-Jobs'
FILE = 'NEW_GRAD_USA.md'
LISTING_URL = f'https://github.com/{REPOSITORY}/blob/main/{FILE}'
COMMITS_URL = f'https://api.github.com/repos/{REPOSITORY}/commits?path={FILE}&per_page=1'


class _Cell(HTMLParser):
    def __init__(self, markup):
        super().__init__(convert_charrefs=True)
        self.text, self.links = [], []
        self.feed(markup)

    def handle_data(self, data):
        self.text.append(data)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a' and attrs.get('href'):
            self.links.append(attrs['href'])
        if tag == 'br':
            self.text.append('; ')

    def value(self):
        return ''.join(self.text).strip().replace(r'\|', '|')


def parse_feed(markdown: str, updated_at: datetime, max_age_days: float = 3,
               now: datetime | None = None) -> list:
    from jobFilter.sources import _job, _ats_from_url
    from urllib.parse import urlsplit
    if max_age_days <= 0:
        raise ValueError('SpeedyApply max_age_days must be positive')
    if '<!-- TABLE_' not in markdown:
        raise ValueError('SpeedyApply response is not the expected job table')
    now = now or datetime.now(timezone.utc)
    updated_at = updated_at.astimezone(timezone.utc)
    jobs, seen = [], set()
    for line in markdown.splitlines():
        if not line.startswith('|'):
            continue
        cells = [_Cell(c.strip()) for c in re.split(r'(?<!\\)\|', line.strip().strip('|'))]
        if len(cells) not in (5, 6):  # FAANG/quant have an extra salary column.
            continue
        company, title, location = [c.value() for c in cells[:3]]
        age = re.fullmatch(r'(\d+)d', cells[-1].value())
        if not company or not title or not age or not cells[-2].links:
            continue
        url = cells[-2].links[0].strip()
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname:
            continue
        # Age is relative to the table's generation, never to each scan time.
        posted = updated_at - timedelta(days=int(age[1]))
        if not 0 <= (now - posted).total_seconds() < max_age_days * 86400:
            continue
        jid = 'speedyapply___' + hashlib.sha256(url.encode()).hexdigest()[:24]
        if jid in seen:
            continue
        seen.add(jid)
        primary_location = re.sub(r'\s+\+\d+$', '', location)
        jobs.append(_job('speedyapply', id=jid, dedup_key=jid, company=company, title=title,
                         company_website=cells[0].links[0] if cells[0].links else None,
                         location=location, countries=countries_for(primary_location, url),
                         category='Software Development', min_yoe=None, visa_sponsorship=None,
                         workplace_type='Remote' if 'remote' in location.lower() else None,
                         requirements_summary='Listed in SpeedyApply US new-grad feed; eligibility unverified. Posting date estimated from feed age.',
                         published_at=posted.isoformat(timespec='seconds'), published_millis=int(posted.timestamp() * 1000),
                         apply_url=url, listing_url=LISTING_URL, source=_ats_from_url(url),
                         raw={'feed_age_days': int(age[1]), 'feed_updated_at': updated_at.isoformat(),
                              'published_at_estimated': True}))
    return jobs


def fetch_speedyapply(cfg: dict) -> list:
    from jobFilter.sources import _cached_get_json, CACHE_DIR, requests
    commits = _cached_get_json(COMMITS_URL, 'speedyapply-commits')
    commit = commits[0]
    sha = commit['sha']
    if not re.fullmatch(r'[0-9a-f]{40}', sha):
        raise ValueError('Invalid SpeedyApply commit')
    updated_at = datetime.fromisoformat(commit['commit']['committer']['date'].replace('Z', '+00:00'))
    cache = CACHE_DIR / 'speedyapply-feed.json'
    try:
        saved = json.loads(cache.read_text())
    except (OSError, ValueError):
        saved = {}
    if saved.get('sha') != sha:
        response = requests.get(f'https://raw.githubusercontent.com/{REPOSITORY}/{sha}/{FILE}', impersonate='chrome', timeout=60)
        response.raise_for_status()
        saved = {'sha': sha, 'markdown': response.text}
        # Validate before replacing the last good cached body.
        parse_feed(saved['markdown'], updated_at, float(cfg.get('max_age_days', 3)))
        cache.write_text(json.dumps(saved))
    return parse_feed(saved['markdown'], updated_at, float(cfg.get('max_age_days', 3)))
