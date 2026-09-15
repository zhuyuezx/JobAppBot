"""Thin client for hiring.cafe's server-rendered search.

hiring.cafe sits behind a Cloudflare managed challenge, so plain `requests`
gets a 403. `curl_cffi` with a Chrome TLS fingerprint passes it. Search
results are embedded in the Next.js page props, which we read either from the
lightweight `/_next/data/<buildId>/index.json` route or, as a fallback, from
the `__NEXT_DATA__` blob in the HTML.
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
from html.parser import HTMLParser
from typing import Any, Iterator, Optional

from curl_cffi import requests

BASE_URL = "https://hiringcafe.com"
_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)


class HiringCafeError(RuntimeError):
    pass


class HiringCafeClient:
    def __init__(self, impersonate: str = "chrome", timeout: float = 60, delay: float = 1.0):
        self.session = requests.Session(impersonate=impersonate)
        self.timeout = timeout
        self.delay = delay
        self._build_id: Optional[str] = None

    # ----- low level -----------------------------------------------------
    def _get(self, path: str, params: Optional[dict[str, str]] = None,
             headers: Optional[dict[str, str]] = None, attempts: int = 3):
        url = BASE_URL + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        last = None
        for i in range(attempts):
            resp = self.session.get(url, headers=headers or {}, timeout=self.timeout)
            if resp.status_code == 200:
                return resp
            last = resp
            if resp.status_code in (403, 429, 502, 503):
                time.sleep(2 * (i + 1))
                continue
            break
        raise HiringCafeError(f"GET {path} failed: HTTP {last.status_code if last else '?'}")

    @staticmethod
    def _parse_next_data(html: str) -> dict[str, Any]:
        m = _NEXT_DATA_RE.search(html)
        if not m:
            raise HiringCafeError("__NEXT_DATA__ not found (Cloudflare challenge page?)")
        return json.loads(m.group(1))

    def build_id(self, refresh: bool = False) -> str:
        if self._build_id and not refresh:
            return self._build_id
        html = self._get("/").text
        self._build_id = self._parse_next_data(html)["buildId"]
        return self._build_id

    # ----- search --------------------------------------------------------
    @staticmethod
    def search_url(search_state: dict[str, Any], page: int = 0) -> str:
        params = {"searchState": json.dumps(search_state, separators=(",", ":"))}
        if page:
            params["page"] = str(page)
        return BASE_URL + "/?" + urllib.parse.urlencode(params)

    def search_page(self, search_state: dict[str, Any], page: int = 0) -> dict[str, Any]:
        """Return the `pageProps` dict for one results page."""
        params = {"searchState": json.dumps(search_state, separators=(",", ":"))}
        if page:
            params["page"] = str(page)
        # Fast path: Next.js data route.
        for attempt in range(2):
            try:
                path = f"/_next/data/{self.build_id(refresh=attempt > 0)}/index.json"
                resp = self._get(path, params, headers={"x-nextjs-data": "1", "referer": BASE_URL + "/"}, attempts=1)
                if resp.headers.get("content-type", "").startswith("application/json"):
                    return resp.json()["pageProps"]
            except (HiringCafeError, KeyError, ValueError):
                pass
        # Fallback: full HTML page.
        html = self._get("/", params).text
        return self._parse_next_data(html)["props"]["pageProps"]

    def search(self, search_state: dict[str, Any], max_pages: int = 25) -> Iterator[dict[str, Any]]:
        """Yield raw hits across all result pages."""
        for page in range(max_pages):
            props = self.search_page(search_state, page)
            if props.get("ssrError"):
                raise HiringCafeError(f"search error: {props['ssrError']}")
            hits = props.get("ssrHits") or []
            yield from hits
            if props.get("ssrIsLastPage", True) or not hits:
                return
            time.sleep(self.delay)

    # ----- job description ----------------------------------------------
    def job_description_html(self, job_id: str) -> str:
        resp = self._get("/api/job-description", {"id": job_id}, headers={"referer": BASE_URL + "/"})
        data = resp.json()
        job = data.get("job") or {}
        return (job.get("job_information") or {}).get("description") or ""

    def job_description_text(self, job_id: str) -> str:
        return html_to_text(self.job_description_html(job_id))


class _TextExtractor(HTMLParser):
    _BLOCK = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._BLOCK and (not self.parts or self.parts[-1] != "- "):
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")

    def handle_endtag(self, tag):
        if tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def html_to_text(html: str) -> str:
    p = _TextExtractor()
    p.feed(html)
    text = "".join(p.parts).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()
