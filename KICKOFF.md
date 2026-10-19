# kickoff

Prompts to paste into Claude Code, opened in this `PCLabel/` folder, plus a plan for the first labelling sessions.

## first session (M0)

```
Read CLAUDE.md, then docs/CONTEXT.md, docs/SPEC.md, docs/ARCHITECTURE.md and docs/ROADMAP.md
(skim docs/LABELLING_STANDARD.md and docs/OBSERVATIONS.md).

We are starting milestone M0 (stack spike). Before writing code:
1. Summarise in at most 12 bullets what PCLabel is for and what M0 must prove.
2. List anything in the docs that is contradictory or under-specified for M0, and the assumption
   you will make for each. Only stop to ask if it blocks M0.
3. Propose the M0 file list.

Then: git init (using the existing .gitignore), create the conda env `pclabel` (Python 3.12) with a
pinned requirements.txt, implement M0 as specified in docs/ROADMAP.md, run the benchmarks and fill in
the M0 results block with measured numbers against the targets in docs/ARCHITECTURE.md. End with your
recommendation (keep or switch the stack and tiling, with reasons) and stop. Do not start M1.
```

## each following milestone

```
Read CLAUDE.md and docs/ROADMAP.md. Implement milestone M<n>.
Start by listing the SPEC sections it covers, the acceptance tests you will write, and any open
questions. Finish with: test results, measured performance on the named scans (written into the
milestone's results block), and a "try it" guide telling me what to open, which keys to press and
what to look for.
```

## after a labelling session

```
/ingest-observations <path to the app's observations export>
```
then
```
Review the observations added today. For each one, propose (a) a rule change or new rule, (b) a lint
check, and (c) a tool or view change that would have made it faster to notice. Put agreed tool ideas
in the SPEC ideas backlog. Do not change decided rules.
```

## reviewing the standard

```
Read docs/LABELLING_STANDARD.md and docs/OBSERVATIONS.md. Find contradictions, rules without
examples, observations without follow-up, and decision-queue items that now have enough examples
to decide. Report only; do not edit.
```

## asking for a feature while labelling

```
While labelling I noticed <X> and wished the tool could <Y>. Add it to docs/SPEC.md with a priority
and milestone, update docs/ROADMAP.md, then implement it if it is small (under about an hour);
otherwise tell me the plan first.
```

## plan for the first labelling sessions

The aim is many observations on varied features, not large labelled areas.

1. **newroad 2** (small, 8.4M points).
   - Learn the tools on it.
   - Label one 10–20 m stretch of road + curb + pavement end to end.
   - Try flatten + wrap at 3 cm, 1 cm and multi-period.
   - Pin anything surprising.
2. **streets 1** at the sites earlier work found interesting. Coordinates are from the 5 cm downsample; confirm
   they match.
   - south kerb, x 15..35, y −30..−18: steps, and the bright line pair (O-0003, O-0006);
   - side-road bend, x 12..22, y −10..0;
   - island nose by the west crossing, x 2..12, y 15..25 (O-0007);
   - curved island kerb, x −12..−2, y 10..20;
   - the east zebra crossing with lowered kerbs (O-0004).

   At each site:
   - patrol along the kerb with cross-sections;
   - label and measure the steps;
   - check the drain regularity (O-0002).
3. **ExchangeGreen.** Label the same small area in the RTC360 scan and in BLK ARC Fast. Compare noise (the flatten
   RMS), density and intensity. Does every rule hold for both scanners?
4. **A week later**, blind re-label one area and review where you disagree with yourself.

### a two-minute observation routine at every site

1. Flatten the local road patch and read off its crossfall and grade.
2. Cycle RGB → intensity → wrap 3 cm → diverging ±5 cm → density.
3. Take one cross-section through each edge you see.
4. Ask the field-guide question ([docs/CONTEXT.md](docs/CONTEXT.md#singapore-street-field-guide)) for each feature
   type present.
5. Press `N` for anything you cannot explain, and set `ambiguous` on anything you cannot decide.
