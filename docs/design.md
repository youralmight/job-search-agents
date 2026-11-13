# Design

## Goal

Apply to every new-grad role that fits my written screening rules, with each application reviewed by
me, while my own time goes only to reviewing and submitting.

The bottleneck turned out to be form filling and my review, not finding jobs (see "Where the postings
went" below). So each change was judged by one question: does it cut wasted form work, make review faster, or reduce how often I have to step in?

## Where the postings went

The Jobs tab on 2026-11-13, with 2,301 postings collected since 2026-09-18:

| State | Rows |
|---|---|
| `Submitted` | 229 |
| `Ready to fill`: résumé tailored, waiting for a form worker and my review | 239 |
| `AI recommends` / `Apply`: waiting for tailoring | 320 |
| `Skip`: 180 excluded by rescreening after the rules changed; most of the rest carried over from an earlier version of the system or set by me | 935 |
| `Duplicate` | 358 |
| `Closed` | 220 |

The two halves mean different things:

- **Filtering.** The 1,513 `Duplicate`, `Skip` and `Closed` rows were copies of another posting,
  failed the rules when they changed, closed before filling, or were turned down by me.
- **The bottleneck.** The 559 rows waiting for tailoring or a form are queued behind form filling and
  my review. Each form means registering an account, verifying an email and filling several pages.
  The worker stops whenever a CAPTCHA or a question needs me, and every application waits for me to
  read its review note and submit. Filling runs only when I start it.

So the number of submitted applications measures how much review time I had, not how selective
screening was.

## Non-goals

- Submitting applications. Final submission and its declarations are mine.
- Inventing any fact about me. Every résumé claim must trace back to my career records.
- Follow-up after submission, networking and outreach.

## Flow

```
Discover: collect postings and JDs → Screen: judge by the rules → Jobs Sheet (recommended, JD attached)
Daily:    Screen dedupes the Sheet, then Apply tailors a résumé per chosen job → ready to fill
Rules changed: rescreen waiting rows; excluded ones become "not applying"
Fill (started by me): up to N jobs at once, one worker each, filled up to the review page → I review and submit
```

## One source for each kind of data

| Data | Only source |
|---|---|
| Each job, its state and history | The Sheet's Jobs tab (`state`, an append-only `log`, and my own `progress` column) |
| Facts about me: experience, how to answer common questions | A separate career repository |
| Which jobs to apply to | `docs/screening-rules.md` |
| Website accounts | The agent's own Bitwarden vault |
| Highly sensitive identity values | A private directory outside Git. Workers are told to fill them with a helper script (not included here) instead of reading them, and Sheet writes for that job are checked so the values never land there. |

## Key decisions

- **One implementation per concern.** Sheet access, the state machine and credentials live in `core/`.
  Each workflow answers one question: which jobs exist, which to apply to, with what materials, and how
  to apply. Workflows call each other only through their CLIs.
- **No copies of the same data.** An earlier answers table stored replies keyed by the question's exact
  wording. A reworded question became a new entry; it grew to 188 entries and started contradicting the
  career records. I deleted it and merged the answers into the career repository.
- **Ask me only what cannot be found or reasonably inferred.** Workers look facts up first. They fill in
  reasonable inferences and flag them in the review note. They stop only when a wrong answer would be a
  false statement or a new commitment, and then ask everything at once. I once tried collecting all
  questions before filling: it produced 281 questions from 50 companies.
- **Open each form once.** A separate "survey the form first" pass ran 415 times while filling ran 19
  times, and most failures came from the survey. I removed it.
- **Apply to each job once.** Rows are the same job when company and title match loosely and their JDs
  share at least half their word 5-grams, regardless of site, location or ID. Without a JD there is no
  guessing: the row waits as `Missing JD`. The one exception is a row whose company and title match a job I
  already submitted; it is marked as a duplicate of that job.
- **Screen and tailor with the stronger model.** A cheaper model misread start dates and optional
  language requirements during screening, so screening went back to the stronger model. Collection
  stays on the cheaper one. Each stage's model is one line in `.env`; the collector is not included here.
- **The agent has its own browser identity.** Form filling runs in a dedicated browser profile, and site
  logins use accounts kept in the agent's vault rather than my personal browser. The application itself
  always carries my real name and contact details.

## Lessons from running it

- `values().append()` in the Sheets API detected a hidden column A as the table's left edge and shifted
  every written row one column right. Rows are now written to an explicit range.
- Reading a row back to confirm a write never matched, because the API drops trailing empty cells. Its
  retries turned a 20-row import into 200 rows. The write is no longer read back.
- Idle agent sessions kept their process and memory: one overnight Discover run left 90 of them. Agents
  are now archived as soon as they finish.

## Open questions

- How the system can learn that I submitted, without me returning to the worker's session to say so.
