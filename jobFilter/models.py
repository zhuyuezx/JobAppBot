"""Normalized job record built from a hiring.cafe search hit."""
from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

HC_JOB_URL = "https://hiringcafe.com/job/{id}"
_TRACKING_PARAMS = ("utm_", "gh_src", "lever-source", "source", "ref", "src")


@dataclass
class Job:
    id: str
    title: str
    company: str
    company_website: Optional[str]
    location: Optional[str]
    workplace_type: Optional[str]
    countries: list[str]
    states: list[str]
    seniority: Optional[str]
    min_yoe: Optional[float]
    visa_sponsorship: Optional[bool]
    security_clearance: Optional[str]
    commitment: list[str]
    category: Optional[str]
    requirements_summary: Optional[str]
    technical_tools: list[str]
    yearly_min_comp: Optional[float]
    yearly_max_comp: Optional[float]
    published_at: Optional[str]
    published_millis: Optional[int]
    apply_url: Optional[str]
    source: Optional[str]
    dedup_key: str
    via: str = "hiringcafe"                 # aggregator the job came from: hiringcafe | simplify | startupjobs
    listing_url: Optional[str] = None       # page on the aggregator; defaults to the hiring.cafe job page
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def hc_url(self) -> str:
        """Listing page on the source aggregator (kept under the historical name)."""
        return self.listing_url or HC_JOB_URL.format(id=urllib.parse.quote(self.id, safe=""))

    # ----- cross-source dedup keys ------------------------------------------
    def norm_url(self) -> Optional[str]:
        """Apply URL without tracking params, scheme or trailing slash."""
        if not self.apply_url:
            return None
        u = urllib.parse.urlsplit(self.apply_url.strip())
        qs = [(k, v) for k, v in urllib.parse.parse_qsl(u.query, keep_blank_values=True)
              if not k.lower().startswith(_TRACKING_PARAMS)]
        path = u.path.rstrip("/").lower()
        return f"{u.netloc.lower()}{path}" + (("?" + urllib.parse.urlencode(sorted(qs))) if qs else "")

    def norm_key(self) -> str:
        """Company + title + location, lowercased and stripped of punctuation.

        Location is part of the key on purpose: the same title in two cities
        is two positions (Visa posts one "Software Engineer New Grad" per office).
        """
        return re.sub(r"[^a-z0-9]+", " ", f"{self.company} {self.title} {self.location or ''}".lower()).strip()

    def to_dict(self, include_raw: bool = False) -> dict[str, Any]:
        d = asdict(self)
        if not include_raw:
            d.pop("raw", None)
        d["hc_url"] = self.hc_url
        return d

    @classmethod
    def from_hit(cls, hit: dict[str, Any]) -> "Job":
        info = hit.get("job_information") or {}
        v5 = hit.get("v5_processed_job_data") or {}
        org = hit.get("attributed_org") or {}
        job_id = hit.get("objectID") or hit["id"]
        return cls(
            id=job_id,
            title=info.get("title") or v5.get("core_job_title") or "",
            company=v5.get("company_name") or org.get("name") or "",
            company_website=v5.get("company_website") or org.get("website"),
            location=v5.get("formatted_workplace_location"),
            workplace_type=v5.get("workplace_type"),
            countries=list(v5.get("workplace_countries") or []),
            states=list(v5.get("workplace_states") or []),
            seniority=v5.get("seniority_level"),
            min_yoe=v5.get("min_industry_and_role_yoe"),
            visa_sponsorship=v5.get("visa_sponsorship"),
            security_clearance=v5.get("security_clearance"),
            commitment=list(v5.get("commitment") or []),
            category=v5.get("job_category"),
            requirements_summary=v5.get("requirements_summary"),
            technical_tools=list(v5.get("technical_tools") or []),
            yearly_min_comp=v5.get("yearly_min_compensation"),
            yearly_max_comp=v5.get("yearly_max_compensation"),
            published_at=v5.get("estimated_publish_date"),
            published_millis=v5.get("estimated_publish_date_millis"),
            apply_url=hit.get("apply_url"),
            source=hit.get("source"),
            dedup_key=hit.get("strict_dedup_cluster_id") or hit.get("collapse_key") or job_id,
            raw=hit,
        )
