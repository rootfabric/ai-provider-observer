# HYBRID HARNESS R11 — H10 EFFICIENCY / SEMANTIC INTEGRITY HARDENING

Source regression: h-10 showed that R10 correctly rejected rewritten evidence/history, but another agent still spent many cycles on reset/reconstruction and could emit `assert_oracle(..., True)` as observed semantic proof. The same session also controlled reviewer/integrator keys and confused runtime TIMEOUT with Harness ROLE_BOUNDARY.

R11 changes:

1. generic boolean oracle is forbidden; runtime observations are structured; trivial constant/same-expression assertions fail verifier runner;
2. contract oracle declares observation kind and optional expected exception; runtime observation must match;
3. `attempt-retry` creates durable SUPERSEDED record + new control-only dispatch/branch without reset;
4. status exposes declared vs effective state; unproven completion becomes `INVALID_COMPLETION`;
5. `resume` rebuilds continuation from durable state and records runtime stop reason only diagnostically;
6. review/integration trust keys declare external custody domains and must be distinct; active-mission key generation is refused.

Historical h-10 verifier suite is rejected by R11 runner because unstructured oracle calls remain in multiple tests even though unittest itself reports PASS.
