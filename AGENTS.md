# Working agreement

Read `NORTHSTAR.md`, `project_spec.md`, and `ARCHITECTURE.md` before making product decisions.

## Hard boundaries

- Never read from or write to a personal profile repository or profile directory for this project.
- Never create a remote, publish, push, contact an external party, connect an account, or spend money without Yuvraj's explicit approval.
- Never commit secrets, model weights, private voice references, transcripts, screenshots, databases, logs, or generated credentials.
- Never let model output bypass the policy engine or become executable code implicitly.
- Never persist idle ambient audio. The rolling buffer is RAM-only and overwritten continuously.

## Code shape

- Keep a compact modular monolith. A module must hide meaningful behavior behind a small typed interface.
- Do not create `utils`, `helpers`, generic `services`, one-file wrappers, duplicate DTOs, or speculative abstractions.
- Pydantic protocol models are authoritative; TypeScript contracts are generated and drift-tested.
- `src/jarvis/bootstrap.py` is the only composition root. Accept dependencies; do not construct adapters inside product modules.
- Introduce a seam only when there are at least two justified adapters, normally production and an in-memory fake.
- Keep blocking work off the async event loop. Every queue, timeout, buffer, prompt, capture, and subprocess output must be bounded.
- Use explicit allowlists for origins, hosts, paths, subprocess arguments, capabilities, and external destinations.

## Test and verification discipline

- For every behavior change: write one focused failing test, run it and confirm the expected failure, implement the minimum, run it green, then refactor.
- Test through module interfaces and observable outcomes. Do not assert that mocks were called instead of asserting behavior.
- Fakes are permanent adapters and must mirror production contracts.
- Never claim success without a fresh `pnpm verify` and direct evidence for any relevant hardware gate.
- If a real-device, thermal, voice-quality, wake-word, or model benchmark has not run, report it as unverified rather than passing.

## Safety classes

- Observe: may run after activation when context is explicitly available.
- Local reversible: requires a direct request or standing rule and records undo information.
- External or irreversible: requires confirmation immediately before execution.
- Forbidden: cannot be approved, scheduled, or overridden.
