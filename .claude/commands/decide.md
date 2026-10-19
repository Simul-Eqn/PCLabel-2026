---
description: Record a labelling decision as a rule in docs/LABELLING_STANDARD.md
argument-hint: <the decision, e.g. "park connectors count as pavement">
---
The labeller has decided: $ARGUMENTS

1. Read docs/LABELLING_STANDARD.md, config/taxonomy.yaml and docs/OBSERVATIONS.md.
2. If an existing rule covers this, set it to `decided` and record the choice. If the meaning changes, mark the old
   rule `superseded` and add a new one. Otherwise add a new rule with the next `R-NNN` id. Never renumber or delete.
3. Write the rule:
   - the rule itself, in one or two precise sentences that can be applied to individual points;
   - why;
   - examples (link O- ids; if the decision came with an example, add an observation for it using the
     OBSERVATIONS.md template);
   - open edge cases, phrased as questions;
   - a `check:` query if the rule can be checked (docs/SPEC.md §9).
4. If the decision changes the taxonomy (new, enabled, disabled or renamed class), edit config/taxonomy.yaml.
   Never reuse an id. Bump the version in both files and add a changelog line.
5. If the decision answers a question in docs/OPEN_QUESTIONS.md (the argument may name it, e.g. `Q-01: B`), move
   that question to the **decided** section there, with the date, the choice and the rule id. Note whether the
   choice differs from the question's *meanwhile* line. If it does, labels made meanwhile need revisiting: give a
   query that finds them.
6. Reply with the rule text and its consequences:
   - a query the app can run to find existing labels that may now be wrong;
   - affected tools or exports;
   - any rule it conflicts with.

   Ask before changing anything else.
