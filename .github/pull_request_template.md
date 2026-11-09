## What changed

<!-- One paragraph. Link the card/issue and the behavior a user can observe. -->

## Verification

- [ ] `make check` passes locally (lint, harness, full offline pytest).
- [ ] `make check-all` passes locally when this touches the review interface,
  launcher, packet exporter, campaign tracker or browser scripts (includes both
  browser suites and axe-core; install axe once with `make axe`).
- [ ] New/changed behavior has a test in the same commit.
- [ ] No paid OpenRouter call, real review submission, HF upload or credential
  is included in this PR.
- [ ] If a live-smoke fixture is stale, it was re-recorded in this PR (or the
  reason it is intentionally unchanged is written below).

Evidence (screenshot path, `.cache/browser-evidence/.../results.json`, command
output, or "not applicable"):

```text

```

## Safety and limits

- [ ] The public dataset is not edited by this change.
- [ ] Review decisions are still explicit human/agent actions with an identity;
  nothing publishes or approves a campaign automatically.
- [ ] Model identities use the short display name (`Haiku-5.5`), never a
  provider-qualified route (`Anthropic/Haiku-5.5`).
