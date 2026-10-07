# Eightfold (`*.eightfold.ai`)
- Single-page form after clicking Apply. Resume upload triggers a parse; wait for it.
- Dropdowns are custom comboboxes: `input[role=combobox]` with `aria-controls` pointing at a listbox. Click the input, wait ~600 ms, then click the `[role=option]` whose text matches. Typed free text is not accepted; the value must be one of the options.
- Text inputs are React-controlled: set the value with the native setter (`Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set`) and dispatch `input` and `change` events, or use `form_input`.
- Voluntary disability radios have ids like `...DISABILITY_STATUS_EN-NO`; click the associated `label[for=...]`.
- "How did you hear about us" = Job Board can open a required "Which job board?" list. Apply the shared automatic source fallback rule, including this follow-up; do not stop just because hiring.cafe is absent.
- An invisible reCAPTCHA can run when the user submits; the "Save my answers for future applications" box is pre-checked.
- Fields like "U.S. Person" and clearance appear on defense companies; answer truthfully from the profile and warn in the summary if the posting requires citizenship.
