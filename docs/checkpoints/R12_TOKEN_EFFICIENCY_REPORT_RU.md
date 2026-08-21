# R12 Token Efficiency Hardening — empirical trigger

Observed R11 dogfood session ended on provider quota before mission closure.

Measured from the durable session log:

- 586 model/tool steps in the long turn;
- 386 bash calls + 116 file reads;
- ~5.32M non-cached input tokens and ~209k output tokens;
- ~130.94M cache-read tokens accumulated across calls;
- effective context carried by the last call: ~412.5k tokens;
- product was substantially working near step 100; most later calls were verifier/evidence/review/integration choreography and recovery;
- verifier runner was repeatedly echoing roughly 38–40 KB machine JSON per run;
- 59 git commits, 7 resets and 6 branch deletions appeared in the session, showing that a manual evidence protocol was too error-prone.

R12 response: bounded `brief`, grouped diagnostics, raw-output suppression with durable logs, compressed verifier transport, and atomic candidate/verifier/event transitions. Proof validators remain unchanged/fail-closed.
