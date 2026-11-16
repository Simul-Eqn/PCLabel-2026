---
description: Record a labelling observation in docs/OBSERVATIONS.md
argument-hint: <what you noticed, where, in which scan>
---
The labeller noticed this while working with the scans: $ARGUMENTS

1. Read docs/OBSERVATIONS.md (template, last O- id) and docs/LABELLING_STANDARD.md, including its "undecided" section.
2. Append one entry with the next `O-NNNN` id, following the template exactly. Fill in what the labeller gave.
   Write "(not recorded)" for anything missing. Never invent locations, counts or measurements.
3. Set the status to `open`. If the statement is really a labelling decision, still log it, and suggest `/decide`.
4. Under "implications", propose briefly:
   - affected sections of the standard, or a new rule, always as `proposed`;
   - a regularity worth measuring, stated so a script can check it;
   - the follow-up measurement or check in another scan that would confirm it;
   - a view or tool change that would make this easier to notice next time.
5. If it bears on an item under "undecided" in the standard, add a tag naming it and say whether it now looks
   ready to decide. Do not decide it.
6. If it contradicts a `decided` rule, say so first in your reply. Do not edit that rule.
7. If a number in the entry comes from a script, the script goes in `bench/experiments/` and the entry names the
   command that reproduces it. Never cite a script in the scratchpad.
8. Reply with the new id, a summary of one to three lines, and at most two follow-up questions that would make the
   observation more useful (e.g. which scan, how many cases, measured heights).
