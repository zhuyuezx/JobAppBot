---
name: apply-job
description: Fill a job application form in the user's Chrome (Claude in Chrome) from their profile, resume and answer bank, and stop on the review page without submitting. Use for any "prepare/fill this application" task.
---

# Filling a job application

You are operating the user's real Chrome through the Claude in Chrome tools.
The goal is a completely filled application that stops on the final review
page. The user presses Submit themselves.

## Non-negotiables

1. Never click the final **Submit / Send application / Apply now** button on the last page. Stop there with `status: review_ready`.
2. Never invent facts. Everything comes from the PROFILE, the RESUME, or the ANSWER BANK in the task. A required field none of them cover is an unanswered question (`status: needs_answer`).
3. Never guess on questions about legal status, citizenship, clearance, criminal history, or compensation unless the profile/bank answers them.
4. Stop and report on CAPTCHAs, phone/SMS verification, email codes and login walls you can't pass (`captcha` / `needs_login`). Leave the tab open and give its URL.
5. Leave the tab open at the end. Do not close tabs the user may want to look at.

## Workflow

1. `tabs_context_mcp` then `tabs_create_mcp`; navigate to the apply URL. If it is a listing page, find and click **Apply** (Eightfold: "Apply" button near the title; Workday: "Apply" then "Apply manually" or "Autofill with resume"; Greenhouse/Lever/Ashby: form is on the page).
2. Take one screenshot (scale 0.5) to see the layout, then `get_page_text` and `read_page filter=interactive` to enumerate fields. On long React forms, one `javascript_tool` call that lists every `input/select/textarea/[role=combobox]/[role=radio]/[role=checkbox]` with its label and current value is faster than scrolling.
3. Upload the resume first (`find` "resume file upload input" then `file_upload` with the path from the task). Wait 3-5 s; many sites parse it and prefill name/email/education. Re-read the form afterwards and fix what the parser got wrong rather than typing everything again.
4. Fill in this order: contact and address, work authorization, education, experience, custom questions, voluntary self-identification, consents. Use `form_input` for plain inputs, checkboxes and native selects.
   - Fill **every** field the profile has a value for, including optional ones: preferred name, address line 2, LinkedIn, GitHub, website. Optional does not mean skip.
   - **Experience / role descriptions**: copy the bullets for that job from RESUME TEXT, which already has one `• ` line per bullet. Paste them as one bullet per line, every line starting with `• `, a single newline between bullets and no newline inside a bullet. Never take the description from the site's resume parser: it keeps the PDF's line wrapping and drops the first bullet marker. After filling, read the field back and check that every line starts with `• `.
   - Education: degree, field, school, GPA and dates from `profile.education` (fall back to RESUME TEXT).
5. Standard yes/no background questions are answered from `profile.background` and `profile.work_authorization` (over 18, worked here before, relatives, military, US government employment, clearance, non-compete). "How did you hear about us" from `profile.preferences.how_did_you_hear`; if the options don't include it, pick "Job board" / "Other". Consent and terms checkboxes: check them. EEO fields marked `decline` in the profile: choose the "I do not wish to answer / self-identify" option.
6. Salary questions: use `profile.preferences.salary_expectation_text` or the bank; if a number is required and the bank has none, ask.
7. When a multiple-choice question has fixed options and the bank/profile answer is not one of them, choose the closest option yourself and mention it in the summary. Only ask the user when no option is a reasonable match.
8. Before finishing, run a check for empty required fields (look for `required` / `aria-required` / red validation text) and fix them.
9. Screenshot the final page with `computer` `action: screenshot, save_to_disk: true` and report the returned path in `screenshot_path`. Do not copy or convert the file yourself; the caller stores it.
10. Return the structured result: status, summary (what was filled, what is left, account credentials if you created one), page_url, unanswered_questions with options, filled_fields, lessons (see below).

## Site-specific notes

### Eightfold (`*.eightfold.ai`)
- Single-page form after clicking Apply. Resume upload triggers a parse; wait for it.
- Dropdowns are custom comboboxes: `input[role=combobox]` with `aria-controls` pointing at a listbox. Click the input, wait ~600 ms, then click the `[role=option]` whose text matches. Typed free text is not accepted; the value must be one of the options.
- Text inputs are React-controlled: set the value with the native setter (`Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set`) and dispatch `input` and `change` events, or use `form_input`.
- Voluntary disability radios have ids like `...DISABILITY_STATUS_EN-NO`; click the associated `label[for=...]`.
- "How did you hear about us" = Job Board opens a required "Which job board?" list without a hiring.cafe entry; choose "Indeed" (or the closest) and note it.
- An invisible reCAPTCHA can run when the user submits; the "Save my answers for future applications" box is pre-checked.
- Fields like "U.S. Person" and clearance appear on defense companies; answer truthfully from the profile and warn in the summary if the posting requires citizenship.

### Workday (`*.myworkdayjobs.com`)
- Requires an account per company. Try "Sign in" with the profile email; if unknown, "Create account" with the profile email and a generated password; report the password in the summary. Email verification codes: stop with `needs_login`.
- Start choice: prefer **Autofill with Resume** to get the skeleton, but treat everything it produces as a draft to verify field by field.
- Wizard steps: **My Information** → **My Experience** → **Application Questions** → **Voluntary Disclosures** → **Self Identify** → **Review**. Click "Save and Continue" between steps; validation errors appear at the top of the step and as red text under fields.
- Fields carry `data-automation-id` attributes (e.g. `legalNameSection_firstName`, `addressSection_addressLine1`, `phone-number`); use them in `find`/JS. Country, phone device type, state and "How did you hear" are custom dropdowns: click the button, then click the option text.
- **My Information**: Legal Name first/last; tick the **"I have a preferred name"** checkbox and fill Preferred Name from `profile.identity.preferred_name` when it is set. Address (line 1, line 2 if set, city, state, postal code), email is fixed to the account, phone device type + number.
- **My Experience**: one Work Experience block per job (Job Title, Company, Location, "I currently work here", From/To month-year, Role Description). The Role Description textarea gets the reflowed bullets from RESUME TEXT as described in the workflow; the autofill version is wrong (wrapped lines, missing first bullet). Education blocks: school, degree, field of study, GPA, dates. Websites: add LinkedIn and GitHub. Confirm the resume file is attached in the Resume/CV section.
- **Application Questions** are company-specific; answer from profile/bank, ask only when nothing fits.
- **Voluntary Disclosures / Self Identify**: EEO from `profile.eeo`; "decline" → "I do not wish to answer". Tick the terms checkbox at the bottom of Voluntary Disclosures.
- On the Review step stop; the button there is Submit. Screenshot it.

### Greenhouse (`boards.greenhouse.io`, `job-boards.greenhouse.io`), Lever (`jobs.lever.co`), Ashby (`jobs.ashbyhq.com`)
- One page, no account. Resume upload is a plain file input. Custom questions are labelled plainly; EEO section at the bottom. The last button is Submit: stop before it.

### Oracle HCM (`*.oraclecloud.com`), iCIMS, UltiPro, SuccessFactors, SmartRecruiters
- Wizards with an account or an email-only "apply" flow. Follow the same rules as Workday; report the exact step you stopped at.

## Learned from runs

Entries are appended automatically by the apply engine (date, company, note).
Keep the ones that generalize; fold recurring ones into the sections above.

- 2026-09-15 CACI (Eightfold): first successful run. Required fields not in the profile were desired salary, US military service, US government employment, and the terms consent; these now live in the answer bank and in `profile.background`.
- 2026-09-15 CACI (Eightfold): copying the screenshot with `sips` needed a Bash permission that headless runs don't have; the engine now stores the reported `screenshot_path` itself.
- 2026-09-15 U.S. Bank: U.S. Bank Workday (usbank.wd1.myworkdayjobs.com): Apply opens a 'Start Your Application' dialog with Autofill with Resume / Apply Manually / Use My Last Application / Apply With LinkedIn; both paths start with a 7-step wizard whose step 1 is Create Account/Sign In.
- 2026-09-15 U.S. Bank: Claude in Chrome safety rules forbid the agent from creating accounts or entering passwords, so on Workday the SKILL's 'create account with generated password' step can't be done; the user must sign in first (have them log in to the company's Workday tenant before the run), otherwise the run ends with needs_login.
- 2026-09-15 U.S. Bank: U.S. Bank Workday: 'How Did You Hear About Us?' is a nested multiselect (Job Boards submenu lists absolvent.pl, Career Builder, Direct Employers, Glassdoor, Indeed, infopraca.pl, irishjobs.ie, LinkedIn, Naukri, Other); no hiring.cafe entry.
- 2026-09-15 U.S. Bank: U.S. Bank Workday resume parser picks the wrong school (UC San Diego -> 'University of California-Davis'), leaves ByteDance company names blank, and turns resume projects into Work Experience entries without a company; Degree is always left 'Select One' (options include "Master's Degree", "Bachelor's degree (4-year)").
- 2026-09-15 U.S. Bank: U.S. Bank Application Questions step has 16 required-ish dropdowns plus a text box: banking prohibition order, public-official relationships, secondary employment in financial services, discharge/termination description (N/A), desired base compensation as fixed salary ranges, withdrawn conditional offer, plus background check and bonding acknowledgements; add these to the answer bank.
- 2026-09-15 U.S. Bank: Workday dropdowns (button[aria-haspopup] inside main) can be driven by JS: button.click(), wait ~1 s, then click the [role=listbox] [role=option] whose innerText matches; this is faster than coordinate clicks. javascript_tool output containing 'key=value; ' pairs gets blocked as cookie data, so return JSON.stringify arrays instead.
- 2026-09-15 U.S. Bank: Workday textareas and checkboxes need real clicks: setting focus by JS and then typing into a textarea showed the value but failed 'required' validation, and x.click() on the self-identify disability checkbox didn't register. A real left_click on the element ref fixed both. JS option clicks work for dropdown lists, but a JS click on 'Save and Continue' sometimes does nothing, so use a real click.
- 2026-09-15 U.S. Bank: U.S. Bank Workday Voluntary Disclosures step: Gender (Female/Male/Other/Prefer Not to Disclose), Hispanic or Latino (Yes/No), Race (includes 'Asian (Not Hispanic or Latino) (United States of America)' and 'I do not wish to answer'), and Veterans Status with options 'I AM NOT A CATEGORY VETERAN' / 'I IDENTIFY AS ONE OR MORE OF THE CLASSIFICATIONS OF VETERANS' / 'I PREFER NOT TO ANSWER'. It ends with a required privacy-notice checkbox (id termsAndConditions--acceptTermsAndAgreements).
- 2026-09-15 U.S. Bank: U.S. Bank Workday Self Identify step (CC-305 form) requires Name (text), Date (MM/DD/YYYY; click the month field and type the 8 digits), and one of three disability checkboxes.
- 2026-09-15 U.S. Bank: U.S. Bank answer-bank values: banking prohibition order No; public official relationship No; secondary financial-services employment No; discharge description 'N/A'; desired base compensation range '$110,000-$129,999'; withdrawn conditional offer No. The optional 'desired total annual compensation' box can be left blank.
- 2026-09-15 U.S. Bank: When one Workday dropdown is opened right after picking from another, the pick can silently fail; press Escape first and read the button's innerText afterwards to confirm.
- 2026-09-15 Workday (first run): Role Description was pasted with the PDF's hard line breaks and no marker on the first bullet, and Preferred Name was left empty. Fixed by reflowing RESUME TEXT into one `• ` line per bullet at extraction time and by the "fill every field the profile has a value for" rule.
