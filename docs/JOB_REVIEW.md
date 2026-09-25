# Job suitability and editable tags

The collapsed row in **Jobs** and **Applications** has four quick actions:

- **✓ Mark submitted:** record a submission without launching automation.
- **⊖ Mark unavailable / clear:** the posting is closed or you already applied.
  A running attempt is stopped first. The agent sets the same state when it finds
  a closed posting (`unavailable`) or an "already applied" message (`already_applied`).
- **⊘ Mark not suitable / clear:** toggle a manual rejection. Clearing it sets
  **Needs review**, so an automatic rejection does not immediately restore it.
- **Tag icon:** edit the conclusion, new-grad fit, sponsorship, citizenship or
  clearance restriction, and your own comma-separated tags.

The automatic state is recomputed from the latest evidence and edited tags:

| Evidence | Automatic state |
| --- | --- |
| Explicit no-sponsorship statement, not new-grad, stated minimum above 1 year, citizenship/clearance requirement, or a definite search-rule mismatch | Not suitable |
| New-grad fit, sponsorship supported or likely, and no indicated citizenship/clearance restriction | Suitable |
| Unknown information or merely unlikely sponsorship | Needs review |

Manual conclusions take precedence over the automatic state. Manual tag values
take precedence over the corresponding source tags. Rescanning and screening again
update the automatic evidence without overwriting either kind of manual choice.
The tag editor shows the current automatic conclusion and its reasons.

Choose **Use automatic conclusion & tags**, then **Save changes**, to remove those
overrides. Custom tags remain until you remove them from the text field and save.
Original screening evidence stays available in the expanded job details.

**Hide not suitable** uses the effective conclusion, including your override.
Existing search-rule exclusions remain hidden as before. Suitability is separate
from application progress: editing tags never starts, cancels, submits, or deletes
an application, and a pending application stays pinned in Applications even when
its job is marked not suitable. Active attempts must finish before they can be marked submitted.

Implementation: `jobFilter/suitability.py` defines the three states and precedence.
`POST /api/jobs/review` accepts `job_id` and any of `conclusion` (a state or `null`
for automatic), `tags` (the complete map of manual tag overrides; `{}` resets it),
and `custom_tags`. Omitted fields are preserved. Edits are persisted separately
from job and screening data in `jobs.review_json`.
