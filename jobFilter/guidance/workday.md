# Workday: fill → compare → save → verify

Apply this procedure to new applications, saved-profile autofill, and resumed drafts. Use the shared FIELD REVIEW STANDARD. A populated field, successful upload, or working Next button does not establish correctness. Keep a compact checklist of expected records and pending discrepancies; batch independent field reads instead of repeatedly taking full-page screenshots.

## Start or resume

- Use the existing application tab on resume; reopening the posting can reset progress. For a new application, choose Autofill with Resume when offered, upload the selected file, and wait for parsing to settle. Treat every parsed value as a draft.
- Accounts are per company. Fill known email; leave passwords, account creation and binding terms to the user with `needs_login`. Resume from the live step after sign-in. Cookie banners can move controls; inspect the current layout after dismissing one.

## Check every page before advancing

For each field compare **expected fact → displayed value → corrected value → saved value**. Mark verified only after read-back. Include optional and prefilled fields; inspect fields that appear after a selection. Review the following wherever the tenant exposes them:

| Page | Required comparison |
| --- | --- |
| My Information | Legal and preferred names, address lines, city/state/postal code/country, email, phone number/device/country code, previous-employment answer and referral source. Use a truthful Job board/Other fallback if the supplied source is absent; otherwise ask. |
| My Experience — work | One saved entry for every applicable job, including separate roles at the same employer. Compare employer, title, location, each start/end month and year, current-job checkbox and complete description. Reconcile duplicates and remove only demonstrably erroneous parser-created entries (e.g. projects converted to employment). Preserve user-entered entries unless facts establish a correction. |
| My Experience — education | Every applicable degree: exact school/campus, degree, field, GPA, start date, end/expected graduation date and current-study status where offered. Compare each month/year segment with `profile.education`; do not accept a plausible but wrong school or parser date. An expected degree must not be marked already earned. Never replace graduation with earliest availability. |
| My Experience — other | Correct selected résumé filename, LinkedIn/GitHub/other supplied websites and applicable skills. Some tenants omit history sections; report them as not offered, not verified. |
| Application Questions | Read each full question and visible options. Check every selected answer against profile/bank/per-job answers; answer useful optional questions or ask for material missing facts. Check any conditional follow-ups. |
| Voluntary Disclosures / Self Identify | Follow supplied EEO/decline preferences; read back actual selected states. Use known name and current form date where requested, not a graduation date. Leave binding agreements to the user. |

## Repair role descriptions and date controls

Replace parser descriptions with the matching complete ROLE DESCRIPTIONS text, not a summary. Verify the raw textarea read-back: first and subsequent bullets start with `• `, each complete bullet occupies one newline-delimited line, and wording/bullet count match the source. Screen-width soft wrapping is harmless; actual newlines inside a bullet are not. No blank first bullet, merged bullets, missing final bullet, or truncated text. If a field has an explicit length limit, preserve supported facts and report the limit and omissions.

Use supported input controls, not just assignments to DOM values. Workday date inputs may have separate month/year segments; focus and replace each segment, leave the field to trigger validation, then read both back. Check current-employment/study selections that disable end dates. Never fabricate unknown day precision.

Custom dropdowns often use `button[aria-haspopup]` and `[role=option]`. Inspect actual options, select a truthful match, close the menu before the next control, and read the displayed label back. Textareas, checkboxes and Save and Continue may need real clicks for React state to persist. `data-automation-id` attributes can help identify controls; observe them on the live page rather than assuming a selector.

## Save and final audit

Use clearly non-submitting Save and Continue. Resolve validation errors, then verify saved cards/review details against the checklist, especially descriptions and education dates. If details are hidden, reopen only those entries, read them, and return to review without restarting the application. Re-check dependent fields after corrections. Do not blindly repeat a failed input method; use a supported alternative or report the precise blocker.

Before `review_ready`, every offered section must be accounted for. Report work/education record counts, verified education end dates, description-format checks, and meaningful optional omissions with reasons. A final screenshot or absence of red errors alone is insufficient. Use `needs_answer` for missing facts or `failed` for unresolved save/control failures. Stop before Submit and leave the tab open.
