# Contributing to Skill Radar

Help make recommendations more useful and easier to verify.

## Good first contributions

- A synthetic or anonymized project where a recommendation fits or fails.
- A supported dependency manifest that the scanner currently misses.
- A client compatibility report with the client version and exact steps.
- A regression test for a concrete evidence-validation or privacy-boundary bug.
- Improvements to documentation or scoring explanations.

## Before submitting

Run `python -m unittest discover -s tests -v` from the repository root. Explain the problem, the behavior after your change, and how you checked it. Keep changes focused.

Never include credentials, private repository contents, customer data, absolute local paths, or raw scans of a real project. Prefer a small synthetic fixture. Do not bundle third-party skill text unless its license permits redistribution and attribution requirements are met.

For recommendation feedback, include the generic task, relevant stack, candidate source URL and commit, the expected decision, and the specific reason for disagreement. Separate content review from actual execution results.

## Design principles

Project fit comes first. Source evidence must be inspectable. Unknowns remain explicit. Zero new recommendations is a valid result. Candidate code is never run merely to assess fit. Roadmap features must not be described as shipped.
