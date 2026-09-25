# JobAppBot

Find new-grad software jobs, review sponsorship and suitability, and prepare applications with Claude Code or ChatGPT / Codex. You review the completed form and submit it yourself.

## Quick start

From the project directory, with Python 3.9+ installed:

```bash
pip install -r requirements.txt
mkdir -p ~/bin
ln -s "$PWD/bin/jobfilter" ~/bin/jobfilter  # ensure ~/bin is on your PATH

cp setup/search.template.json setup/search.json
cp setup/profile.template.json setup/profile.json
mkdir -p setup/resume
cp ~/Downloads/Resume.pdf setup/resume/Resume_SDE.pdf
# optional: an MLE resume for ML / AI job titles (see setup/README.md)
# cp ~/Downloads/Resume_MLE.pdf setup/resume/Resume_MLE.pdf
# optional: Word cover letter templates with [placeholders] (see setup/README.md)
# mkdir -p setup/cover_letter && cp ~/Downloads/Cover_Letter_Template_SDE.docx setup/cover_letter/

jobfilter start       # hourly scanning and screening
jobfilter ui open     # http://127.0.0.1:8765
```

Configure your profile and sign in to the provider you want to use:

- **Claude Code:** install Claude in Chrome and run `claude --chrome` once. See [application setup](docs/APPLY_AUTOMATION.md).
- **ChatGPT / Codex:** follow the [browser bridge setup](docs/CODEX_INTEGRATION.md).

## Frontend workflow

1. **Find jobs.** Browse hiring.cafe, Simplify, startup.jobs, and ApplyGuy by source or date found. Search by title, company, or location. Jobs rejected by your search rules stay hidden. When two sources list the same posting (same company and title, compatible location), both rows are tagged: **seen before on ApplyGuy** on the later copy, which also loses its **new** badge, or **also on …**. The tag names the other copy's application status, and preparing an application asks for confirmation when that copy already has one.
2. **Review suitability.** Each job shows **Suitable**, **Not suitable**, or **Needs review**. Only clear blockers trigger automatic rejection; uncertain sponsorship stays **Needs review**. Turn on **hide not suitable** to narrow the list.
3. **Prepare an application.** Expand a job, choose its provider, model, and GPT thinking level, then click **Prepare application**. Your last-used choices are remembered. **Resume** defaults to the version that matches the job title (MLE for ML / AI roles, SDE otherwise); change it for that job if the title misleads. If you keep cover letter templates in `setup/cover_letter/` and the form has a cover letter field (required or optional), the run pauses there briefly while a one-page letter tailored to the posting is written, then uploads it. Forms without the field skip this. Open the letter from the application to check it before you submit. Screening has its own provider setting under **AI settings**.
4. **Track progress.** Working applications appear first and expand automatically, followed by queued jobs. The bottom-right activity box shows the active count; click it for details or to jump to an application. **Stop** ends a queued or running attempt (a usage limit, a stuck run, or a change of mind); it is kept as failed so you can **Run again**. **Delete** stops it if needed and removes the application with its questions, log, screenshots, and cover letter; the job stays in Jobs.
5. **Finish and record.** Answer questions in Applications, complete any login or CAPTCHA handoff, then review and submit on the employer’s site. Mark the job submitted in JobAppBot—even if you applied entirely outside the tool.

### Quick actions on collapsed rows

Available in both **Jobs** and **Applications**, with descriptions on hover or keyboard focus:

| Icon | Action |
| --- | --- |
| ✓ | Mark submitted. The icon turns green once recorded. |
| ⊖ | Mark unavailable: the posting is closed or you already applied. Stops a running attempt first. The icon turns amber for **unavailable** or **already applied**, including when the agent reports it; click again to clear. |
| ⊘ | Mark not suitable, or clear the mark to **Needs review**. |
| Tag | Edit suitability, new-grad fit, sponsorship, citizenship/clearance requirements, and custom tags. |

Manual conclusions and tag edits survive rescans. The tag editor shows the automatic reasoning and lets you reset to automatic mode. See [suitability rules and overrides](docs/JOB_REVIEW.md).

In **Applications**, use **Unfinished only** to hide submitted, already-applied, unavailable, and skipped entries. Working, queued, and pending applications (answers, login, CAPTCHA, or final review) stay at the top and in the activity box until they reach a terminal state: submitted, already applied, unavailable, skipped, or failed. Suitability does not change this. Other entries default to newest submission first. Submission time is recorded when you mark the job submitted.

**Profile** holds your details, resume status, and answer bank. **Answer once** saves an answer for that application; **Answer & save to bank** also makes it reusable.


## Useful commands

| Command | Purpose |
| --- | --- |
| `jobfilter ui open` | Open the frontend |
| `jobfilter status` | Check scheduler and scan status |
| `jobfilter run` | Scan now |
| `jobfilter excel` | Open today's workbook |
| `jobfilter stop` / `start` | Pause / resume scheduled scans |
| `jobfilter log` | Read scan logs |

Search sources, filters, and screening limits are configured in `setup/search.json`. Personal setup files are ignored by Git; templates are provided. Jobs, reviews, and application history are stored in `data/jobs.db`, with exports in `data/excel/` and application logs in `data/apply/`.

More: [technical reference](jobFilter/README.md) · [search options](docs/SEARCH_OPTIONS.md) · [Codex integration](docs/CODEX_INTEGRATION.md).
