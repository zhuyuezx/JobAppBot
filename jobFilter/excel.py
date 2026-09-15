"""Excel output for a list of stored job rows."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

COLUMNS: list[tuple[str, str, int]] = [
    # header, key into flattened row, column width
    ("First seen", "first_seen", 20),
    ("Posted", "published_at", 20),
    ("Title", "title", 45),
    ("Company", "company", 28),
    ("Location", "location", 32),
    ("Workplace", "workplace_type", 11),
    ("Min YoE", "min_yoe", 9),
    ("Visa flag", "visa", 10),
    ("Clearance", "security_clearance", 14),
    ("Salary min", "yearly_min_comp", 12),
    ("Salary max", "yearly_max_comp", 12),
    ("Category", "category", 22),
    ("Tools", "tools", 40),
    ("Requirements", "requirements_summary", 80),
    ("Apply URL", "apply_url", 60),
    ("hiring.cafe URL", "hc_url", 60),
    ("Source", "source", 12),
]


def flatten(row: dict[str, Any]) -> dict[str, Any]:
    job = row["job"]
    return {
        "first_seen": (row.get("first_seen") or "")[:16].replace("T", " "),
        "published_at": (job.get("published_at") or "")[:16].replace("T", " "),
        "title": job.get("title"),
        "company": job.get("company"),
        "location": job.get("location"),
        "workplace_type": job.get("workplace_type"),
        "min_yoe": job.get("min_yoe"),
        "visa": "yes" if job.get("visa_sponsorship") else "",
        "security_clearance": job.get("security_clearance"),
        "yearly_min_comp": job.get("yearly_min_comp"),
        "yearly_max_comp": job.get("yearly_max_comp"),
        "category": job.get("category"),
        "tools": ", ".join(job.get("technical_tools") or []),
        "requirements_summary": job.get("requirements_summary"),
        "apply_url": job.get("apply_url"),
        "hc_url": row.get("hc_url") or job.get("hc_url"),
        "source": job.get("source"),
    }


def write_excel(rows: list[dict[str, Any]], path: str | Path, sheet_title: str = "jobs") -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title[:31]
    ws.append([h for h, _, _ in COLUMNS])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for r in rows:
        flat = flatten(r)
        ws.append([flat.get(k) for _, k, _ in COLUMNS])
    for i, (_, key, width) in enumerate(COLUMNS, 1):
        ws.column_dimensions[get_column_letter(i)].width = width
        if key in ("apply_url", "hc_url"):
            for cell in ws[get_column_letter(i)][1:]:
                if cell.value:
                    cell.hyperlink = cell.value
                    cell.font = Font(color="0563C1", underline="single")
    for row_cells in ws.iter_rows(min_row=2):
        for cell in row_cells:
            cell.alignment = Alignment(vertical="top", wrap_text=False)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    wb.save(path)
    return path
