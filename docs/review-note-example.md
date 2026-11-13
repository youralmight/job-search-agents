# Example review note

What an Apply worker sends when a form is filled in and it has stopped before the final submission
(format: "Review note" in `apply-job/.agents/skills/apply-job/SKILL.md`). The job, company and
applicant below are invented.

---

**Northwind Analytics — Machine Learning Engineer, New Grad** · filled in, ready for you to submit
Toronto, ON (hybrid) · https://jobs.example.com/northwind/ml-engineer-new-grad

**Please confirm**

1. "Are you willing to work in the office three days a week?" → **Yes**. Inferred: career prefers
   hybrid and has no restriction on Toronto.
2. "Earliest start date" → **2027-07-05**. Inferred from the graduation date in career; the JD asks
   for a start in summer 2027.
3. The form's "Years of professional experience" dropdown has no "2"; chose **1–3 years**.

**JD**

- Build and ship ranking and recommendation models for the company's analytics product.
- Required: degree in CS or a related field, graduating 2026–2027; Python; one ML framework.
- Nice to have: experience serving models in production, SQL.
- Hybrid, three days a week in Toronto.
- Pay: CAD 95,000–115,000.

**Form contents**

| Question | Answer | Source |
|---|---|---|
| First name / Last name | Jane / Doe | career |
| Email / Phone | jane.doe@example.com / (on file) | career |
| Résumé | `resume.pdf` | Jobs |
| Cover letter | not requested | — |
| Are you legally eligible to work in Canada? | Yes | career |
| Will you now or in the future require sponsorship? | No | career |
| Willing to work in the office three days a week? | Yes | inferred |
| Earliest start date | 2027-07-05 | inferred |
| Years of professional experience | 1–3 years | inferred |
| How did you hear about this job? | Company website | Jobs |
| Government ID number | filled | private |
| I agree to the privacy policy | checked | — |

**Résumé**

(full text of the tailored résumé as Markdown)

Tailoring moved the model-serving bullet to the top of the first job and added "ranking" to the
headline; details in `changes.md`.

**What you need to do**: solve the CAPTCHA, then click SUBMIT APPLICATION.
Tab: Machine Learning Engineer, New Grad - Northwind Analytics Careers
