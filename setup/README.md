# setup/

Your personal configuration. Everything in this folder is **gitignored**
except this README and the two templates, so your address, phone, answers and
resume never leave your machine.

| File | What | Start from |
|---|---|---|
| `search.json` | the hiring.cafe search + local rules | `search.template.json` |
| `profile.json` | facts Claude uses to fill forms | `profile.template.json` |
| `answers.json` | answer bank; grows as you answer questions in the UI | created automatically |
| `resume/*.pdf` | the resume to upload (newest file wins) | |

```bash
cp setup/search.template.json setup/search.json     # then edit
cp setup/profile.template.json setup/profile.json   # or fill the Profile tab in the web UI
mkdir -p setup/resume && cp ~/Downloads/Resume.pdf setup/resume/
jobfilter py validate
```

Full list of search options: `docs/SEARCH_OPTIONS.md`.
