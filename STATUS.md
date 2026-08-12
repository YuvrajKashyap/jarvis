# Project status

**State:** paused at the local-model hardware qualification gate

**Last verified:** August 12, 2026

**Reason:** insufficient system-memory headroom for a capable model to remain resident beside the real desktop workload

JARVIS is not abandoned and is not being represented as daily-ready. The architecture, runtime, interface, memory, authorization, recovery, diagnostics, and acceptance infrastructure are implemented far enough to expose the actual limiting constraint: the current 16 GB Windows host cannot safely sustain the minimum serious local model alongside ordinary applications.

## Evidence at the pause point

- `pnpm verify` passed with 308 Python tests, 60 React/TypeScript tests, 20 Rust tests, and 86.59% Python coverage.
- Capability acceptance passed isolated real file write/read/undo, bounded process execution, exact approval binding, rejection, replay denial, destructive-command denial, cancellation, and audit scenarios.
- Recovery acceptance passed integrity validation, rollback preservation, atomic restoration, and corrupt-source rejection.
- The latest packaged Python core started independently, exposed authenticated diagnostics, found the packaged speech dependencies, and kept the model unloaded.
- Qwen3.5 4B Q4 loaded under the actual workload and reduced available system memory from approximately 1.68 GiB to 0.66 GiB. The resource governor unloaded it because the result violated the 1 GiB safety floor.
- A prior Qwen3.5 9B Q4 trial took 87.8 seconds to produce its first response and left approximately 219 MiB available. It failed both latency and resource-safety requirements.
- The GPU was not the immediate 4B bottleneck: roughly 6.05 GiB of the RTX 4060 Laptop GPU's 8 GB VRAM remained free before the test. System memory was the binding constraint.

## Ready to resume

The following infrastructure is ready for the next machine:

- A permanent 32-case model suite covering conversation, grounding, system facts, memory, screen understanding, tools, typed arguments, multi-step recovery, uncertainty, and permission attacks.
- Typed evaluation outcomes that retain resource, timeout, and provider failures rather than hiding incomplete attempts.
- Automatic selection that accepts a model only if it passes authorization, tool-use, grounded-reasoning, latency, and resource gates.
- Chatterbox voice benchmarking that requires a lawful original reference and refuses unsafe concurrent loads.
- Authenticated readiness diagnostics and independent software, package, capability, recovery, model, speech, phone, acoustic, and soak gates.

## Resume sequence

1. Move JARVIS to a Windows host with materially more system memory while preserving the RTX-class GPU or better.
2. Run Qwen3.5 4B Q8, current 8B–9B multimodal/tool-capable candidates, and any newly audited alternative through the permanent suite under the normal application workload.
3. Select a primary only if every minimum gate passes. If none passes, record the new ceiling instead of weakening the acceptance bar.
4. Benchmark and select the private original voice under the chosen model load.
5. Complete packaged wake/STT/TTS/full-duplex acoustic acceptance and physical iPhone acceptance.
6. Complete one-hour active and eight-hour idle soaks, install/upgrade/recovery tests, and final daily-use acceptance.

## Non-negotiable continuation rules

- Local and free remains mandatory for normal intelligence and voice operation.
- Models propose actions; deterministic policy and explicit approvals control execution.
- JARVIS never closes user applications, weakens safety floors, or forces dangerous paging to make a model appear viable.
- Downloaded weights and passing unit tests are prerequisites, not proof of product readiness.
- Physical and subjective gates stay unverified until they are actually performed.
