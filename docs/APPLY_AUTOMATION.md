# Can the application step be automated?

Scope: search -> filter -> **open the job, create/log in, fill every field,
upload the resume, answer the questions, stop on the review page**. The final
Submit click stays with you.

Short answer: yes for most postings, with two different engines depending on
which application system the company uses, and a human review queue at the
end. Expect roughly 70-80% of applications to reach "ready to submit" with no
help, and the rest to stop at a specific field that needs you (CAPTCHA, email
code, an odd question). That is still a large win over doing all of it by
hand, because the boring 90% of every form is identical.

## 1. What we are dealing with

From the jobs matched so far (22 postings, one day):

| Application system | Share | Login needed | Form shape | Automation difficulty |
|---|---|---|---|---|
| Workday (`*.myworkdayjobs.com`) | 23% | account per company + email verify | 4-6 step wizard, "My Information / Experience / Questions / Voluntary / Review" | medium-hard, but very uniform |
| Ashby (`jobs.ashbyhq.com`) | 18% | none | one page | easy |
| Eightfold | 14% | optional | one page + resume parse | easy-medium |
| Oracle Cloud HCM | 9% | account | wizard | medium |
| UltiPro / iCIMS / SuccessFactors / SmartRecruiters / JazzHR / Breezy | ~15% | varies | wizard | medium each, long tail |
| Greenhouse / Lever (very common for startups) | 9% today, usually more | none | one page | easy |
| Company-custom (ServiceNow, C3, Johnson Controls...) | ~15% | varies | anything | needs the LLM engine |

Two facts follow:
- **Six systems cover ~85% of postings.** Each has a stable DOM (Workday even
  exposes `data-automation-id` attributes), so a deterministic script can fill
  them reliably.
- **Every posting can still add custom questions** ("Why us?", "Are you
  authorized to work in the US?", "Do you now or in the future require
  sponsorship?", EEO, "How did you hear about us?"). Those need a model that
  reads the question and answers from your profile.

So the design is hybrid: **scripted adapters for the known systems, an
LLM-driven browser agent for custom forms and custom questions, and a review
queue in the existing web UI**.

## 2. The three core points

### 2.1 Info access and webpage access

**Your information** needs to live in one structured place, not only in the
PDF. The resume covers name, email, education, experience, links. Forms also
ask for things it does not contain: phone (already on the resume), street
address, city/state/zip, work authorization, sponsorship answer, gender /
ethnicity / veteran / disability (you may decline), earliest start date,
salary expectation, "how did you hear", LinkedIn URL, GitHub URL, GPA,
graduation date, and free-text answers.

Proposed layout (all under `data/`, gitignored):

```
data/resume/Resume_....pdf          the file to upload
data/profile/profile.yaml           structured facts: identity, address, education, work auth,
                                    EEO choices (or "decline"), links, preferences
data/profile/resume.txt             extracted text (PyMuPDF is installed), fed to the model
data/profile/answers.yaml           question bank: canonical question -> your answer
                                    (grows as the agent asks you about new questions)
data/profile/accounts.yaml          per-ATS/per-company logins (or a keychain reference)
```

`answers.yaml` is the important one. The model matches a new question against
it semantically ("Will you require sponsorship?" == "visa sponsorship
needed?"). If nothing matches, the application is parked with status
`needs_answer`, you answer once in the UI, and it is reused forever.

**Webpage access** must be a real browser, not HTTP requests: these forms are
JavaScript apps, some detect bots, and Workday/Oracle need sessions and
cookies. Options:

- **Your own Chrome, driven by Claude in Chrome** (subscription; see 2.3).
  Uses your real profile, so logins persist and nothing looks like a bot.
- **Playwright with a persistent profile** (`launch_persistent_context`,
  headed, Chrome channel) for the scripted adapters. Same idea: one profile
  directory keeps every ATS login.

Both can coexist: the script does the mechanical parts, the model does the
judgement parts, in the same browser.

### 2.2 How to operate (clicking and filling)

**Deterministic adapters** for the top systems. Each adapter is a Python
class with `matches(url)`, `open(job)`, `fill(profile)`, `upload(resume)`,
`answer_questions(answerer)`, `stop_at_review()`. Selectors live in the
adapter; unknown fields are handed to the answerer callback. Rough order of
value: Workday, Ashby, Greenhouse, Lever, Eightfold, Oracle HCM, iCIMS.

**LLM browser agent** for everything else and for the callbacks. It reads the
page (accessibility tree / screenshot), decides what each field means, fills
it from the profile, and asks you when unsure. This is what Claude in Chrome
or Playwright-MCP + Claude Code provide out of the box; the work on our side
is the prompt (profile, rules, "never click Submit", "stop and report when a
CAPTCHA or verification code appears") and the result contract (JSON: status,
unanswered questions, screenshot path).

**The two engines share one state machine per application**:

```
queued -> opening -> account (create / login / verify email) -> filling
       -> questions -> uploaded -> review_ready   (you submit)
                          \-> needs_answer / needs_login / captcha / failed (you unblock)
```

Every state change is written to the database with a screenshot, so the web
UI can show a queue: "5 ready to submit, 2 need an answer, 1 CAPTCHA".

### 2.3 Subscription instead of API key

Two first-party routes use your Claude subscription and need no API key:

1. **Claude Code headless + Claude in Chrome.** The `claude` binary is on
   this machine (VS Code extension, v2.1.272) and supports
   `claude -p "<task>" --chrome --output-format json`. With the Claude in
   Chrome extension installed, the model can open tabs, read pages, click and
   type in *your* Chrome. The orchestrator (Python) spawns one such call per
   application with the profile and the job URL in the prompt, parses the JSON
   result, and updates the database. Extension install: chrome web store,
   "Claude in Chrome"; not detected on this machine yet.
2. **Claude Code headless + Playwright MCP.** Same `claude -p`, but the browser
   is a Playwright-controlled Chrome via `--mcp-config` pointing at
   `@playwright/mcp`. Works without the extension and gives a persistent
   profile directory; slightly more setup.

Either way the model calls are billed to the subscription's usage window (Max
plans: 5-hour rolling limits). A typical form costs tens of thousands of
tokens because of page snapshots, so budget roughly 10-30 LLM-driven
applications per window; scripted adapters cost nothing, which is another
reason to keep them for the common systems.

Caveat to check yourself: Anthropic's consumer terms govern automated use of
a subscription. Claude Code and Claude in Chrome are first-party tools built
for exactly this kind of agentic use, but read the current terms before
running dozens of applications unattended.

## 3. Other capabilities this needs (and what makes it better)

Essential:
- **Account + email handling.** Workday/Oracle require an account per company
  and a verification email. Automate with a generated password stored in
  `accounts.yaml` (or macOS Keychain) and IMAP access to your inbox to read
  the code. Without inbox access these stop at `needs_login`.
- **Human gates.** CAPTCHA, phone verification, and 2FA are not automatable and
  should not be; the agent screenshots, parks the application, and the UI shows
  a "continue" button that focuses the tab.
- **Idempotence.** Never apply twice: `applications` table keyed by job id,
  plus a check of the ATS "already applied" banner.
- **Audit trail.** Screenshot of the review page and the exact answers given,
  stored per application, so you know what was sent.
- **Politeness.** One application at a time, human-like delays, headed
  browser; this is your identity, not a scraper.

Makes it better:
- **Answer learning loop.** Every question the agent could not answer becomes
  a card in the UI; your answer goes to `answers.yaml`. After ~20 applications
  the bank covers almost everything.
- **Sponsorship pre-check.** Before filling, the agent reads the posting for
  "will not sponsor" / "US citizenship required" and skips with a reason (the
  `--llm` screening step from earlier fits here).
- **Resume variants.** Keep 2-3 PDFs (backend, ML, general) and let the
  profile map job categories to a variant; optional tailored cover letter.
- **Application tracker.** Statuses beyond submit (applied, OA, interview,
  rejected) in the same database and UI, with the Excel export extended.
- **Dry-run mode.** Fill everything, take the review screenshot, then clear
  the form. Good for testing adapters without leaving half-filled sessions.

## 4. Suggested build order

1. `profile.yaml` + `answers.yaml` schema, resume text extraction, and an
   `applications` table + queue view in the UI. (No browser yet.)
2. LLM engine first, not adapters: `claude -p --chrome` (or Playwright MCP)
   with a prompt that fills any form from the profile and stops at review.
   This alone covers every system, just slowly. Run it on 5 real postings,
   collect the questions it could not answer, fill the answer bank.
3. Scripted adapters for Ashby and Greenhouse (an afternoon each), then
   Workday (the big one: account creation, email code, wizard steps).
4. Inbox integration for verification codes; account store.
5. Tracker statuses and screenshots in the UI.

Milestone 2 is the point where you can already run "prepare these 10 jobs"
and come back to 10 review pages.
