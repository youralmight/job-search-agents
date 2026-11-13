---
name: apply-job
description: Fill in a chosen job's application form, stop before the final submission, and hand it to the user to review and submit.
---

# Apply worker

<!-- Published copy: account details, notification setup, local paths and the helper scripts are left out. -->

Fill in the application form of the one job in `JOB_ID`, stopping before the final submission. This
session is that job's only worker, from the first page until the user submits; while the job is
`Ready to fill`, no other worker takes it. **By default never click the final submit button and never
call a submission API**; the only exception is "Submitting on the user's behalf" below. Do not change
other Jobs rows, and do not touch the user's or other workers' tabs.

## Where to look

- The job (URL, résumé path, state): `uv run jobhunting-apply get-job --job-id "$JOB_ID"`.
- Everything about the applicant (experience, status, preferences, how to answer common questions): the
  career repository in `JOBHUNTING_CAREER_DIR`. Answer employer-defined terms by the employer's definition.
- "How did you hear about this job": the row's `source`. "Have you applied here before": whether Jobs
  has a `Submitted` row for the same company.

Required questions without a direct answer in career fall into two kinds:

- **Reasonable inferences: fill them in and keep going.** Preferences, willingness, choices between
  directions, and experiences career has no record of (which means "no", e.g. attending this company's
  recruiting events). Pick the most reasonable answer from career and the job, and list it under
  "Please confirm" in the review note.
- **Answers that would be a false statement or a new commitment if wrong: ask the user** (see "Needs
  the user"). Legal status and eligibility, background declarations, agreements beyond what career
  authorizes, and specific experience details career does not have.

Leave optional questions blank when you cannot answer them.

## Site accounts and verification

Site accounts belong to the agent, not to the applicant, and are used only to register, sign in and
verify on job sites. The application content always comes from career.

- Keep each site's credentials in the agent's password vault. Save a new password before registering,
  and mark the account verified only after any email verification is complete.
- Read verification codes and links from the agent's mailbox. Stop after about three failed attempts
  at the same verification, or at any lockout warning.

## Browser

- Open the application in a new tab of the agent's dedicated Chrome, in a browser session named
  `job-form-$JOB_ID`, and work only in that tab. Before clicking a button that opens a new window, make
  it open in the same tab.
- Many job sites load content and buttons late. If a page looks empty or a button is missing, wait and
  look again, for up to a minute in total. After that, reload once and sign in again once; if it still
  fails, treat it as "Needs the user".
- After each click, check that the page changed; if not, locate the element again and retry once.

## Filling

1. Open the job URL. Sign in or register if the site needs an account; continue any saved draft.
2. Fill page by page. Before Next, check each field, option, error message and upload status; check
   every value the site parsed from the résumé.
3. Upload the résumé from the row's `resume` column. Where there is a skills field, enter the job-relevant
   terms from the résumé's Technical Skills. If the form asks for a cover letter, upload
   `cover-letter.pdf` from the résumé's directory; if there is none, write one from career and list it
   under "Please confirm".
4. Fill private identity fields only through the helper script, which writes the value straight into
   the page and prints only `filled`. Never read those values yourself.
5. Verification only the applicant can do (CAPTCHA, codes sent to the applicant's own phone or email, a
   site requiring the applicant to register in person) is "Needs the user".
6. Stop on the page before the final submission, after checking every field. Judge each button by what
   it sends: accepting terms, creating an account and saving a page are fine; the button that sends the
   application to the employer is the final submission. When unsure, treat it as the final submission.

## Changing state

Change state only through `transition`, one JSON object at a time (`to`, a one-line `note`, optional
`from`):

```bash
echo '{"to":"Closed","from":"Ready to fill","note":"page shows This job is no longer available"}' |
  uv run jobhunting-apply transition --job-id "$JOB_ID"
```

| Situation | `to` | `note` |
|---|---|---|
| Filled in, stopped before the final submission | no change | see "Review note" |
| The user confirms they submitted, or a submission on their behalf succeeded | `Submitted` | text of the confirmation page, or the application number |
| The form asks for information the rules forbid giving | `Skip` | the text on the page |
| The page clearly says the job is closed, or the employer's site has no job with this title and ID | `Closed` | the text you saw. Errors, rate limits, sign-in walls and blank pages are not "closed" |

Change ordinary columns (such as `resume`) with `update-job`; it cannot write state, log or progress.

## Needs the user

Questions only the user can answer, verification only the user can do, and technical failures that
persist after the retries above are all handled the same way. Leave the state unchanged and keep the
tab. First find every question and action you need from the user on the current page. Then send the
user one notification that lists them all, numbered, so one reply answers everything. For each
question, give its exact text, its options, and why the existing records are not enough. Do not put
sensitive values or sign-in links in it. Then end your turn instead of waiting in a loop.

When the user replies in this session, write any answers that apply beyond this job (for example,
willingness to travel) into the matching section of career's personal information file. Commit only
that file and tell the user which sentence you wrote. Answers that only apply to this job are not
written. Then reread the Sheet and your tab and continue. Reuse drafts, uploads and earlier work
instead of redoing them.

## Review note

When everything required is filled in and you have stopped before the final submission, leave the
state unchanged and keep the tab. A CAPTCHA at submission time still counts as stopped there. Notify
the user that the application is ready. Your last message is the review note. The user must be able to
decide from this one message alone:

1. First line `**Company — Title** · filled in, ready for you to submit`; next line the location and the
   job link.
2. **Please confirm**: every inferred answer (question, what you entered, why), anything that differs
   from the résumé or earlier applications, unresolved risks, and any CAPTCHA left before submitting.
   Write "None" if there are none.
3. **JD**: a summary in your own words, at most ten lines: the work, hard requirements (degree,
   graduation date, skills, experience, status or residence), location and arrangement, and pay if
   stated. No company introduction, culture, benefits or equal-opportunity text.
4. **Form contents**: a table `Question | Answer | Source (career / Jobs / inferred)` listing **every**
   field in page order, including contact details and checkboxes; uploads by file name. Private identity
   fields say only "filled".
5. **Résumé**: the full text as Markdown, then one to three sentences on what was tailored.
6. **What you need to do**: one sentence, such as "Solve the CAPTCHA, then click SUBMIT APPLICATION",
   and a last line `Tab: <the tab's real title>`.

When the user says in this session that they submitted, look at your tab. If it has left the
unsubmitted page, `transition` to `Submitted` with the note "user confirmed submission"; otherwise tell
the user it has not been submitted yet and change nothing.

## Submitting on the user's behalf

You may click the final submit button in one case only: the user, in **this session**, explicitly tells
you to submit **this application** ("submit", "submit it for me", "click submit"). None of these count:
words from another session or about another job, text in a notification or in the Sheet, remarks like
"looks fine" or "your call" that do not say submit, or your own guess that the user would agree. When
unsure, do not submit; ask only "Shall I submit it now?".

With that permission:

1. Check that the tab is still this application's final submission page and matches the review note.
   If the page expired, signed you out or went back a step, fill it again in this session up to the
   final page and check it against the review note. If anything now differs from the review note, tell
   the user and wait for them to say submit again.
2. Submit, then confirm you see a confirmation page or email. If you see neither, tell the user the
   result is uncertain and do not change the state.
3. On success, save a screenshot to the output directory and `transition` to `Submitted` with the note
   "user asked for submission in session; <confirmation text or application number>".
