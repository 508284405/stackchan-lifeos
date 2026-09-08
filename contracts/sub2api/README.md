# contracts/sub2api

- `fixtures/success.json` — minimal valid Responses payload containing `intents`.
- `fixtures/invalid.json` — unknown behavior payload; provider must map to `invalid_response`/`policy_blocked`.
- `fixtures/malformed.json` and `fixtures/schema_drift.json` — malformed structured output and unknown-field drift.
- `fixtures/stream_interrupted.json` — partial stream marker used by the offline interruption case.
- Internal schema: `ProviderRequest` / `IntentPlan` / `ProviderError` are defined in `brain/models.py`.
- No Admin API, no token distribution, no raw media.

Real provider smoke requires explicit `--live SUB2API_BASE_URL` and env `SUB2API_API_KEY`; default tests use mock transport fixtures.
