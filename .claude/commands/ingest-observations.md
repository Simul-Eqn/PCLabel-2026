---
description: Merge observations exported from the PCLabel app into docs/OBSERVATIONS.md
argument-hint: <path to the app's observations export (.jsonl or .md)>
---
Merge the observations exported by the PCLabel app at: $ARGUMENTS

1. Read the export and docs/OBSERVATIONS.md. Skip entries already ingested; match on the app's observation uuid,
   kept in each entry's `app id` field.
2. For each new observation:
   - give it the next `O-NNNN` id and convert it to the template;
   - copy coordinates, measurements and counts exactly;
   - link screenshots by their absolute path in the project directory (do not copy images into the repo unless I
     ask).
3. Group related observations (same tags, or within a few metres of each other) and note the links in both
   entries.
4. Report:
   - the new ids;
   - entries tagged decision-needed, each with proposed rule text;
   - candidate lint checks;
   - tool or view ideas the observations suggest. Add these to the docs/SPEC.md ideas backlog only if I agree.
