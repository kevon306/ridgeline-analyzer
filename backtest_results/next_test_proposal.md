# Proposal: a second back-test before changing how comps are chosen (not started)

Status: **draft for the project owner's decision.** Nothing in the model has changed.

## Why
In the graded back-test, 22% of held-out houses (1,068 of 4,817) got no estimate because they had fewer than 3 comparable
sales in their neighborhood within 24 months. A wider comp pool could raise coverage, but it can also add error.

## Why it cannot be tuned on the existing results
The held-out half has been used for the graded 69.2% figure. Choosing a new rule by looking at those results, and then
reporting the new rule's accuracy on the same pairs, would overstate how well it works.

## What a fair test would look like
1. Write the candidate rule down first, for example: "if fewer than 3 comps after 24 months, add sales from the
   neighborhoods whose codes share the first N digits", with N fixed in advance.
2. Develop it only on the **exploration half** (4,816 pairs never valued so far).
3. Grade it once on **new data**: repeat sales after 2026-05-04, available when the county republishes and
   `build_county_db.py` is rerun. Same pair rules, same 15% threshold, seed fixed in a new protocol file.
4. Adopt it only if coverage rises and the within-15% rate among valued houses does not fall by more than a margin
   set in advance.
