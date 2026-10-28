# Test cases — V1
Automated: `python -m unittest discover -s tests -t . -v` (12 tests, must all pass). Owner: Ananyaa.

## Manual smoke test (3 terminals) — tick when done
| # | Steps | Expected | Done |
|---|---|---|---|
| S1 | Start server; connect alice | alice sees "document at version 0" | [ ] |
| S2 | alice `a hello` | `ok (v1)` | [ ] |
| S3 | connect bob | bob sees `1 \| hello` at version 1 | [ ] |
| S4 | bob `r 1 HELLO` | bob `ok (v2)`; alice sees "bob changed lines 1-1" | [ ] |
| S5 | connect carol; all three type `a x` as fast as possible | all converge after `sync`; some may print REJECTED + re-fetch | [ ] |
| S6 | close bob's terminal, alice `a more`, reopen bob with same name | bob sees the latest document | [ ] |
| S7 | `r 99 x`, `d 0`, `foo` | friendly error, nothing crashes | [ ] |
| S8 | stop server (Ctrl+C) while clients are open | clients print "server closed the connection" | [ ] |
