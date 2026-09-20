# ChatGPT subscription integration

Use **AI settings → Application filling → ChatGPT / Codex → Save application provider**.
Then open a job and click **Prepare with GPT**. The background worker fills the form,
asks missing questions in jobFilter, resumes after you answer, and stops for your review.
There is no desktop-chat copy/paste step for this option.

| Option | Browser | How it starts |
| --- | --- | --- |
| Claude Code (`claude-chrome`) | Existing Claude in Chrome integration | Prepare with Claude |
| ChatGPT / Codex (`codex-playwright`) | Managed Playwright MCP bridge; dedicated Chrome profile | Prepare with GPT |

Screening is selected independently under **AI settings → Job screening**.
Changing defaults affects new applications only. Existing applications retain their
provider and session; use **Run again** to resume them. Claude remains the default
on a fresh install. Its CLI command, Chrome integration and resume flow are unchanged.

## Setup for automatic GPT

Install Python dependencies and the browser bridge once, from the project root:

```sh
python3 -m pip install -r requirements.txt
npm install --prefix data/browser-tools --save-exact @playwright/mcp@0.0.81 --ignore-scripts --no-audit --no-fund
codex login
codex login status
```

Install Node.js and Google Chrome if missing. Sign in to Codex using **ChatGPT**.
The adapter also finds Codex bundled in the ChatGPT macOS app or VS Code extension.
If it is not on your shell PATH, use that executable's full path for the login commands,
or set `JOBFILTER_CODEX`. No API key is needed or used; runs consume subscription quota.
No extra browser extension is needed for the automatic bridge.

Restart the jobFilter server after updating and reload the frontend. Save the automatic
GPT provider in AI settings. **Open automation browser** lets you open its Chrome window
before queuing; otherwise the first application starts it automatically.

The bridge uses a **separate persistent Chrome profile**, stored in ignored
`data/codex-browser/profile`. Your normal Chrome sign-ins are not copied. If a job needs
login, sign in in the automation window, then choose **Run again** in Applications.
Missing factual answers can be entered in jobFilter; the worker resumes automatically.
**Answer once** stays with that application, while **Answer & save to bank** also saves
it for future jobs. CAPTCHA and account steps require your involvement.

Review the form in the automation Chrome window and submit it yourself, then mark the
application submitted in jobFilter. The runner is instructed never to submit; this is
an agent instruction, not a technical block on every website's submit controls.

The local bridge listens only on `127.0.0.1:8931`. It stays alive after individual Codex
runs and UI restarts so prepared tabs remain available. If Chrome or the bridge is
closed, sign-ins persist on disk but unsaved form state may be lost; retry the job.
Logs are in `data/codex-browser/bridge.log` and the per-application **View log** panel.
Port/profile overrides for isolated tests are `JOBFILTER_BRIDGE_PORT` and
`JOBFILTER_BRIDGE_DIR`; `JOBFILTER_BRIDGE_HEADLESS=1` is for tests only.

For CLI use:

```sh
python3 -m jobFilter apply queue JOB_ID_PREFIX --engine codex-playwright
python3 -m jobFilter apply worker --once
```

Codex receives the applicant context and the local MCP tools for each run. Its personal
configuration is not changed. Shell tools and web search are disabled during application
filling; the browser provides the live page. Each run has a timeout, cancellation handling,
a fresh claim token and screenshot path. Cancellation stops Codex and leaves the tab open;
already completed page actions cannot be undone by cancelling.

Official references: [Codex authentication](https://learn.chatgpt.com/docs/auth),
[scripted Codex runs](https://learn.chatgpt.com/docs/non-interactive-mode),
[Codex MCP configuration](https://learn.chatgpt.com/docs/extend/mcp), and
[Microsoft Playwright MCP](https://github.com/microsoft/playwright-mcp).

## Screening

Try an individual job first:

```sh
python3 -m jobFilter screen --provider codex --job JOB_ID_PREFIX
```

You can switch scheduled screening from **AI settings → Job screening**: choose a provider and click **Save screening provider**. This is independent of the application provider and preserves your filters and scan limits.

Alternatively, for scheduled scans, merge these keys into the existing `screening` object in
`setup/search.json` (keep your other search and screening settings):

```json
{
  "provider": "codex",
  "codex_model": "",
  "codex_timeout": 300
}
```

An empty `codex_model` uses the CLI's default model. `--model` overrides the selected
provider for one command. Claude's `model` (such as `sonnet`) remains separate.
Switch `provider` back to `claude` to restore Claude screening.

Codex gets native web search and structured output, with shell tools disabled.
Your personal Codex config is not loaded, but your saved ChatGPT login is used.
Authentication/quota failures pause a batch; untouched jobs remain unscreened for
a later scan. Other failures are recorded and can be retried using `--job`.
Token usage is not a dollar cost, so Codex results do not invent a USD charge.

## GPT thinking level

In **AI settings**, choose **GPT thinking level** separately for application filling
and screening: **Model default**, **Low**, **Medium**, **High**, or **Extra high**.
Save that section to apply the choice to the next run. Existing configurations use
Model default. The optional model name is a separate advanced override; it is not
a thinking-level field. Supported effort levels depend on the selected model.

The saved `codex_reasoning_effort` is passed as Codex's `model_reasoning_effort`
for that run; personal Codex configuration and Claude settings are unchanged.
See the [official configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).

## reCAPTCHA and browser blocks

Both providers can encounter reCAPTCHA. It assesses browser interactions and risk
signals, including cookies; it is not specific to GPT. Claude uses your existing
Chrome session, while the GPT bridge uses a separate persistent automation profile.
That difference may affect challenges, but does not establish why a particular site
blocked a request. Increasing thinking level does not remove site-side restrictions.

When a CAPTCHA is reported, take over in that provider's browser window. After you
complete it, click **Run again**. A site may still reject the session; neither pathway
can guarantee access. Both runners are instructed to stop at CAPTCHA rather than
repeatedly attempt it. See Google's [reCAPTCHA keys overview](https://docs.cloud.google.com/recaptcha/docs/keys)
and [FAQ](https://docs.cloud.google.com/recaptcha/docs/faq).

## Upgrading from the retired manual pathway

The manual desktop-chat option and its CLI commands have been removed. Saved manual
provider settings resolve to the automatic Codex provider. Existing manual application
records are migrated to that provider while preserving results, screenshots and answers.
Queued or running manual jobs are marked failed with a **Run again** instruction;
they do not start automatically during migration. Finished and blocked statuses remain
intact. The old claim token is invalidated, and retry uses the dedicated automation
browser. Claude records and sessions are unaffected.

## Code layout

- `providers.py`: supported application engines and validation; legacy identifier mapping.
- `application_state.py`: shared result schema and artifact locations; atomic Codex claims and completion.
- `apply_engine.py`: worker, dispatch, and existing Claude command/session handling.
- `bridge_engine.py`: Codex application prompt and browser-run lifecycle.
- `bridge.py`: persistent local browser/MCP process.
- `codex.py`: subscription-backed CLI execution for screening and applications.
- `static/index.html`, `static/app.js`, `static/styles.css`: frontend structure, behavior and styling.

## Verification

```sh
python3 -B -m unittest discover -s tests -v
python3 -B tests/live_screen.py  # opt-in; consumes subscription quota
python3 -B tests/live_bridge.py --dir data/codex-verification/NEW_RUN
```

The offline suite covers defaults, queue isolation, Claude command/session
regressions, question continuation, stale/cancelled claims, structured output and
quota handling, automatic bridge routing, required review screenshots, and subprocess
cancellation/timeouts, legacy migration and removal of manual CLI commands. Claude regression calls are mocked, not live Claude runs.

The automatic bridge passed a live test on 2026-09-19 using the saved ChatGPT login:
HTTP queue → background Codex run → resume upload → missing start-date question →
Answer once → automatic continuation in the same tab → final review screenshot.
All five expected field values matched, with two attempts, one page load and zero
submissions. The review tab remained open after Codex exited. The report is in
`data/codex-verification/bridge-cleanup-20260919/report.json` (ignored test output).
The refactored integration is verified with `tests/live_bridge.py`; each run writes a
`report.json` and review screenshots beneath its isolated `--dir`. The test consumes
subscription quota and never uses the production queue or applicant profile.
Real employer login/CAPTCHA flows are not part of this test.
