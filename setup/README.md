# setup/

Your personal configuration. Everything in this folder is **gitignored**
except this README and the two templates, so your address, phone, answers and
resume never leave your machine.

| File | What | Start from |
|---|---|---|
| `search.json` | the hiring.cafe search + local rules | `search.template.json` |
| `profile.json` | facts Claude uses to fill forms | `profile.template.json` |
| `answers.json` | answer bank; grows as you answer questions in the UI | created automatically |
| `resume/*_SDE_*.pdf` | the default (SDE) resume to upload (newest file wins) | |
| `resume/*_MLE_*.pdf` | optional MLE resume, used for ML / AI job titles | |
| `cover_letter/*_SDE_*.docx`, `cover_letter/*_MLE_*.docx` | optional Word cover letter templates, one per version | |

The version is the word `SDE` or `MLE` in the file name, set off by `_`, `-`
or a space (`Resume_Jason_Zhu_MLE_2026-09-23.pdf`). A file without either word
counts as the default (SDE) version. Cover letter templates are named the same
way (`Cover_Letter_Template_MLE_2026-09-24.docx`).

Each application picks a resume version from the job title: titles that
mention machine learning, ML, AI, LLM, data scientist, research engineer and
similar get the MLE resume; everything else gets the default. You can override
it per job with **Resume** when you click Prepare application. Without an MLE
PDF, every job uses the default resume. The rules are `RESUME_RULES` in
`jobFilter/profile.py`.

**Cover letters.** Before each application starts, JobAppBot writes a cover
letter from the template of the same version (falling back to the SDE one) and
the job's description. The provider and model you chose for the application
fill every `[bracketed]` placeholder and tailor the wording, using only facts
from the template and the resume. The letter is saved as a one-page PDF in the
template's font and layout (`data/apply/<job>/Cover_Letter_<Name>_<Company>.pdf`).
It is uploaded whenever the form has a cover letter field, required or optional,
and you can open it from the application in the UI. A letter is sent back once
if it leaves a bracket, uses a number not found in the template, resume or
posting, or runs past one page; if it still fails, the application continues
without one. Without a template, no letter is written.

```bash
cp setup/search.template.json setup/search.json     # then edit
cp setup/profile.template.json setup/profile.json   # or fill the Profile tab in the web UI
mkdir -p setup/resume && cp ~/Downloads/Resume.pdf setup/resume/Resume_SDE.pdf
cp ~/Downloads/Resume_MLE.pdf setup/resume/Resume_MLE.pdf   # optional
mkdir -p setup/cover_letter && cp ~/Downloads/Cover_Letter_Template_SDE.docx setup/cover_letter/   # optional
jobfilter py validate
```

Full list of search options: `docs/SEARCH_OPTIONS.md`.
