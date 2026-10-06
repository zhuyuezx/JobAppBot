# Claude application context review — 2026-10-05

The main waste is loading all historical form lessons for every application. Browser observation frequency then multiplies that large context across requests. Recent runs already use Sonnet, so changing the default model alone would not address the main problem.

## Measured evidence

The measurements below are a snapshot; the lesson file continues to grow during live applications.

| Component | Characters |
| --- | ---: |
| Complete generated application prompt | 351,855 |
| Injected application skill | 336,321 |
| Core instructions before learned notes | 15,071 |
| Historical learned notes | 321,230 |

The skill accounts for about 96% of the generated prompt. It contains 860 learned-note lines. Of these, 310 mention Workday and occupy about 125,542 characters; a platform keyword filter alone would therefore still leave a large prompt.

Before optimization, `apply_engine.load_skill()` read the entire file and `build_prompt()` embedded it in every new application. `append_lessons()` continually appended notes and only checked a literal 60-character prefix for duplicates. There was no size limit, platform-specific retrieval, consolidation, or retirement of obsolete advice.

I inspected token metadata from four recent application sessions. Their first requests carried roughly 156,000–157,000 input-context tokens, combining new input, cache creation, and cache reads. This includes Claude's own instructions and tools, not just the application's prompt.

One JP Morgan Chase session had:

- 109 assistant request records with usage metadata, deduplicated by message ID.
- 77 computer screenshot calls and 104 computer left clicks.
- 26,702,288 cumulative cache-read input tokens and 486,343 cache-creation input tokens.
- 41,566 output tokens.

Cumulative cache reads count context read again on successive requests; they are not the size of one context window. Screenshot calls may be necessary for some controls; the opportunity is to avoid repeated full-page observation when a targeted field read establishes the result.

The last 30 logged start/resume segments include 986 computer calls, 248 JavaScript calls, and only 48 `form_input` calls. These are tool calls, not necessarily separate model requests. Calls grouped in one model response can share one request.

Saved fallback settings use Opus and a 120-turn cap, but the recent inspected applications selected Sonnet. The current app logs `total_cost_usd` and turn count while discarding the useful input/cache/output breakdown. Do not sum start/resume cost lines as independent charges without determining their scope.

Claude's dollar figures are token-cost estimates, not proof of an additional subscription bill. Actual plan usage and usage-credit charges should be checked in Claude's usage view. See [Anthropic's cost documentation](https://code.claude.com/docs/en/costs).

## Recommended changes, in order

1. **Separate the stable workflow from the lesson archive.** Keep complete applicant facts and completion checks in the main prompt. Store historical notes separately; retrieve a small, deduplicated selection for the employer and detected ATS. Use a fixed character/token budget, not just “all Workday notes.” If the apply URL is an aggregator, detect the final ATS after navigation and read the relevant short guide then. Keep the archive for recovery, and consolidate useful corrections rather than deleting it.
2. **Reduce redundant browser observations.** Read the current page's labelled fields once; fill independent fields together where the browser tools permit; then verify the changed values. Re-read after controls that change the form. Use screenshots for visual ambiguity, errors, and final review rather than after every ordinary text field. Do not weaken browser security restrictions or final verification.
3. **Stop unsuccessful loops with useful checkpoints.** Detect repeated attempts at the same field without a changed value, use a supported alternative, and report the remaining blocker when neither works. Persist the page URL, completed sections, unanswered facts, and outstanding history entries. Resume from that checkpoint. A lower blanket turn limit alone risks leaving forms incomplete.
4. **Measure before changing models.** Log unique request usage, cache creation/reads, output, browser calls, screenshot counts, prompt size, and separate attempt/session cost totals. Use a small set of representative forms to compare completion quality and usage. Existing logs are insufficient for reliable per-application cost accounting, particularly across resumes.
5. **Keep Sonnet as the normal filling choice where already selected; evaluate escalation separately.** Consider a stronger model only for a specific unresolved step, using a compact checkpoint. Do not switch existing sessions or saved user choices as part of context cleanup. Keep small-model cover-letter experiments separate from browser filling; letter writing is already deferred until a field is found and its result is reused.

Removing all historical notes would reduce this generated prompt from about 352k to 31k characters. Keeping a small relevant-note budget makes an **85–90% reduction in application-supplied prompt characters** a reasonable initial target. That is an engineering target, not a promised reduction in billed cost or subscription consumption; tools, screenshots, cache behavior, and output still contribute.

## Completion safeguards

Context reduction must preserve every work-history and education record, complete role descriptions, current availability, per-job answers, and submission boundaries. Reconcile the form's saved experience/education entries against the applicant record before reporting `review_ready`; resume parsing or one completed entry is not evidence that the section is complete. Measure missing entries and human corrections alongside usage so a cheaper incomplete form cannot count as a successful optimization.

## Implemented: 2026-10-06

The first recommendation is implemented. A generated prompt using the current real profile and the same sample software-engineer job fell from **354,030 to 23,106 characters (93.5%)**. The core is 6,367 characters. These measurements differ slightly from the original snapshot because lessons continued accumulating before migration. They measure application-supplied characters, not subscription quota or Claude's complete context.

- The version-controlled [core workflow](../jobFilter/guidance/SKILL.md) keeps facts, full history, handoffs, cover letters, and final verification. Full résumé and role descriptions are now included without the old 6,000/8,000-character truncation.
- Five short guides in `jobFilter/guidance/` retain platform mechanics. The agent reads the relevant guide after identifying the actual form, including redirects. Historical troubleshooting uses targeted `Grep` (six matches) and bounded `Read` ranges, rather than preloading all notes. This is an instruction to the agent, not a hard tool-output token cap.
- The original private skill, including all 865 historical notes present at migration, is preserved in `.claude/skills/apply-job/references/history.md`; a separate pre-migration backup is under `data/backups/`. The discoverable private `SKILL.md` is also shortened so skill loading cannot reintroduce the original archive. These private files remain ignored by Git.
- Future lessons append to the archive under a write lock, without enlarging the core or initial prompt. Edit shared workflow rules in `jobFilter/guidance/`; the private entrypoint is generated from it. Any replaced private entrypoint is archived before synchronization.
- Existing model choices, turn limits, browser integration, and resume session IDs are retained. **New applications get the smaller prompt; existing resumed Claude sessions retain their previous context.**

Validation: 100 Python tests pass, including lossless/idempotent migration, concurrent lesson writes, prompt size independent of archive growth, preservation of long applicant records, lazy guide availability, and Claude command/resume behavior. The skill validator passes. No live employer application was run for this change; actual quota savings and form completion quality still need observation on subsequent applications.
