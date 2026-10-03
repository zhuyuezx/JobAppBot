"""Employer-scoped posting identity, shared by ingestion and duplicate warnings."""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit


def posting_identity(url: str | None) -> tuple[str, str, str] | None:
    if not url:
        return None
    u = urlsplit(url.strip())
    host = (u.hostname or '').lower()
    path = unquote(u.path).rstrip('/')
    if host.endswith('.myworkdayjobs.com'):
        match = re.search(r'/job/[^/]+/[^/]+_([^/]+?)(?:/apply)?$', path, re.I)
        if match:
            req = match[1].lower()
            # Workday publication copies use R72211-1 / 01866752-1.
            req = re.sub(r'^((?:r)?\d+)-\d+$', r'\1', req)
            return ('workday', host, req)
    if host in ('boards.greenhouse.io', 'job-boards.greenhouse.io'):
        match = re.fullmatch(r'/([^/]+)/jobs/(\d+)(?:/application)?', path, re.I)
        if match:
            return ('greenhouse', match[1].lower(), match[2])
    if host in ('jobs.lever.co', 'jobs.ashbyhq.com'):
        match = re.fullmatch(r'/([^/]+)/([^/]+)(?:/(?:apply|application))?', path, re.I)
        if match:
            return (host, match[1].lower(), match[2].lower())
    if host.endswith('.icims.com'):
        match = re.match(r'/jobs/(\d+)(?:/|$)', path, re.I)
        if match:
            tenant = host.removesuffix('.icims.com')
            tenant = re.sub(r'^(?:careers|talent)-', '', tenant)
            return ('icims', tenant, match[1])
    if host in ('amazon.jobs', 'www.amazon.jobs'):
        match = re.search(r'/jobs/(\d+)(?:/|$)', path, re.I)
        if match:
            return ('amazon.jobs', 'amazon', match[1])
    if host.endswith('.oraclecloud.com') or host == 'careers.oracle.com':
        match = re.search(r'/job/(\d+)(?:/|$)', path + '/' + unquote(u.fragment), re.I)
        if match:
            # Oracle's own branded site and tenant; other Oracle customers stay scoped.
            tenant = 'oracle' if host in ('careers.oracle.com', 'eeho.fa.us2.oraclecloud.com') else host
            return ('oraclecloud', tenant, match[1])
    return None


def normalize_apply_url(url: str | None) -> str | None:
    if not url:
        return None
    identity = posting_identity(url)
    if identity:
        return '/'.join(identity)
    u = urlsplit(url.strip())
    # Unknown providers retain functional query parameters and fragments.
    tracking = ('utm_', 'gh_src', 'lever-source', 'source', 'ref', 'src')
    qs = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True)
          if not k.lower().startswith(tracking)]
    return (u.netloc.lower() + u.path.rstrip('/')
            + ('?' + urlencode(sorted(set(qs))) if qs else '')
            + ('#' + u.fragment if u.fragment else ''))
