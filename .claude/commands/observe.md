---
description: Record a labelling observation in docs/OBSERVATIONS.md
argument-hint: <what you noticed, where, in which scan>
---
The labeller noticed this while working with the scans: $ARGUMENTS

1. Read docs/OBSERVATIONS.md (template, last O- id) and the rules and decision queue in docs/LABELLING_STANDARD.md.
2. Append one entry with the next `O-NNNN` id, following the template exactly. Fill in what the labeller gave.
   Write "(not recorded)" for anything missing. Never invent locations, counts or measurements.
3. Set the status to `open`. If the statement is really a labelling decision, still log it, and suggest `/decide`.
4. Under "implications", propose briefly:
   - affected rules (existing R- ids, or a new rule, always as `proposed`);
   - a lint `check:` query if the observation states a regularity (query fields are in docs/SPEC.md §9);
   - the follow-up measurement or check in another scan that would confirm it;
   - a view or tool change that would make this easier to notice next time.
5. If it bears on a question in docs/OPEN_QUESTIONS.md, add the question id (e.g. `Q-01`) to the entry's tags and
   append the new O- id to that question's **Evidence** line. If the question now looks ready to decide, say so.
   Do not decide it.
6. If it contradicts a `decided` rule, say so first in your reply. Do not edit that rule.
7. Reply with the new id, a summary of one to three lines, and at most two follow-up questions that would make the
   observation more useful (e.g. which scan, how many cases, measured heights).
