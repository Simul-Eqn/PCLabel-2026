---
description: Record a labelling decision as a rule in docs/LABELLING_STANDARD.md
argument-hint: <the decision, e.g. "dirt paths are not pavement">
---
The labeller has decided: $ARGUMENTS

1. Read docs/LABELLING_STANDARD.md, config/taxonomy.yaml, docs/OBSERVATIONS.md and the user's notes
   (ignore/labelling_standards.md, ignore/labelling_procedure.md). The notes are the standard; if the decision
   contradicts them, say so and ask before going on.
2. Write the decision into the class's section of the standard, in one or two sentences that can be applied to
   individual points, with the date. If it answers an item under "undecided", remove that item. If it changes a
   decided rule, replace the old text; git history keeps it.
3. If the decision came with an example, add an observation for it (OBSERVATIONS.md template) and link it.
4. If the decision changes the taxonomy (a new, enabled, disabled or moved class), edit config/taxonomy.yaml. Never
   reuse an id, never remove a class an existing project's labels use (disable it instead), and bump the version in
   both files with a changelog line in the standard.
5. Reply with the new text and its consequences:
   - which existing labels may now be wrong, and how to find them in the app;
   - affected tools or exports.

   Ask before changing anything else, and ask whether the user's notes in ignore/ should change to match.
