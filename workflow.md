---
inclusion: always
---

# Working agreement for Kiro

`AGENTS.md` holds the hard rules and commands. `docs/` holds the design. This file holds how to work in Kiro.

- Read the section you need from `docs/scope.md`, `docs/prd.md` and `docs/spec.md` before you write code. Do not work from memory of them.
- Work on one build step at a time (`docs/spec.md` Section 12). Stop at the end of the step and report. Do not start the next step.
- Do not edit `docs/scope.md`, `docs/prd.md` or `docs/spec.md`. If you find a conflict or an error, stop and report it with the document and section. You may tick boxes in `docs/checklist.md`.
- Do not create Kiro specs under `.kiro/specs/` that restate the requirements. `docs/` is the single source of truth.
- Run `pytest` and make it pass before you report. Do not call the real Gnani or Gemini APIs except in Step 0, the Step 7 benchmark and Step 14, unless I ask.
- Do not put transcript text, names, API keys or real patient data in code, logs, tests, commit messages or chat output.
- If a requirement is unclear, ask one question. Do not guess.
