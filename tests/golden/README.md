# Golden tests — calibrating the engine against the ITD utility

Each `cases/*.yaml` file is one complete assessment: the `inputs` block is
an `AssessmentInputs` dict (same JSON shape the portal stores), and
`expected` pins the line values the engine must reproduce **to the rupee**.

## SOP for verifying a case (Pulkit)

1. Open the official ITD offline utility (or the e-filing portal's online
   form) for AY 2026-27.
2. Enter exactly the figures from the case's `inputs` block.
3. Read off: total income, tax at normal rates, special-rate tax, 87A
   rebate, surcharge, cess, total liability, 234A/B/C interest, 234F fee,
   net payable/refund — for the regime(s) in `expected`.
4. If a value differs from `expected`, the ENGINE (or the AY config) is
   wrong until proven otherwise: fix, re-run, and only then update the
   YAML. Set `verified_against_utility: true` once the utility agrees.

Cases with `verified_against_utility: false` still run — they pin current
engine behaviour so regressions are caught — but treat them as unconfirmed
until checked against the utility.
