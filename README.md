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
cp ~/Downloads/Resume.pdf setup/resume/

jobfilter start       # hourly scanning and screening
jobfilter ui open     # http://127.0.0.1:8765
```

Configure your profile and sign in to the provider you want to use:

- **Claude Code:** install Claude in Chrome and run `claude --chrome` once. See [application setup](docs/APPLY_AUTOMATION.md).
- **ChatGPT / Codex:** follow the [browser bridge setup](docs/CODEX_INTEGRATION.md).

## Frontend workflow

1. **Find jobs.** Browse hiring.cafe, Simplify, startup.jobs, and ApplyGuy by source or date found. Search by title, company, or location. Jobs rejected by your search rules stay hidden.
2. **Review suitability.** Each job shows **Suitable**, **Not suitable**, or **Needs review**. Only clear blockers trigger automatic rejection; uncertain sponsorship stays **Needs review**. Turn on **hide not suitable** to narrow the list.
3. **Prepare an application.** Expand a job, choose its provider, model, and GPT thinking level, then click **Prepare application**. Your last-used choices are remembered. Screening has its own provider setting under **AI settings**.
4. **Track progress.** Working applications appear first and expand automatically, followed by queued jobs. The bottom-right activity box shows the active count; click it for details or to jump to an application.
5. **Finish and record.** Answer questions in Applications, complete any login or CAPTCHA handoff, then review and submit on the employer’s site. Mark the job submitted in JobAppBot—even if you applied entirely outside the tool.

### Quick actions on collapsed rows

Available in both **Jobs** and **Applications**, with descriptions on hover or keyboard focus:

| Icon | Action |
| --- | --- |
| ✓ | Mark submitted. The icon turns green once recorded. |
| ⊘ | Mark not suitable, or clear the mark to **Needs review**. |
| Tag | Edit suitability, new-grad fit, sponsorship, citizenship/clearance requirements, and custom tags. |

Manual conclusions and tag edits survive rescans. The tag editor shows the automatic reasoning and lets you reset to automatic mode. See [suitability rules and overrides](docs/JOB_REVIEW.md).

In **Applications**, use **Unfinished only** to hide submitted, already-applied, and skipped entries. Working, queued, and pending applications (answers, login, CAPTCHA, or final review) stay at the top and in the activity box until submitted, already applied, skipped, or failed. Other entries default to newest submission first. Submission time is recorded when you mark the job submitted.

**Profile** holds your details, resume status, and answer bank. **Answer once** saves an answer for that application; **Answer & save to bank** also makes it reusable.

Pending jobs marked **Not suitable** leave the pinned group, activity box, and attention count; their application history remains available. Queued or running attempts stay visible until they stop.

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
