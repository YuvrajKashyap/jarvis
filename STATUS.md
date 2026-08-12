# Project status

**State:** paused at the local-model hardware qualification gate

**Last verified:** August 12, 2026

**Reason:** insufficient system-memory headroom for a capable model to remain resident beside the real desktop workload

JARVIS is not abandoned and is not being represented as daily-ready. The architecture, runtime, interface, memory, authorization, recovery, diagnostics, and acceptance infrastructure are implemented far enough to expose the actual limiting constraint: the current 16 GB Windows host cannot safely sustain the minimum serious local model alongside ordinary applications.

I understand the limits of this machine and am looking at better hardware that can host a substantially more capable JARVIS. The project is paused so that the acceptance bar stays intact, not because the intended product has been reduced.

## Evidence at the pause point

- `pnpm verify` passed with 308 Python tests, 64 React/TypeScript tests, 20 Rust tests, and 86.59% Python coverage.
- Capability acceptance passed isolated real file write/read/undo, bounded process execution, exact approval binding, rejection, replay denial, destructive-command denial, cancellation, and audit scenarios.
- Recovery acceptance passed integrity validation, rollback preservation, atomic restoration, and corrupt-source rejection.
- The latest packaged Python core started independently, exposed authenticated diagnostics, found the packaged speech dependencies, and kept the model unloaded.
- Qwen3.5 4B Q4 loaded under the actual workload and reduced available system memory from approximately 1.68 GiB to 0.66 GiB. The resource governor unloaded it because the result violated the 1 GiB safety floor.
- A prior Qwen3.5 9B Q4 trial took 87.8 seconds to produce its first response and left approximately 219 MiB available. It failed both latency and resource-safety requirements.
- The GPU was not the immediate 4B bottleneck: roughly 6.05 GiB of the RTX 4060 Laptop GPU's 8 GB VRAM remained free before the test. System memory was the binding constraint.

## Hardware conclusion

The complete workload must keep more than a language model alive. It also includes wake detection, transcription, voice synthesis, screen perception, retrieval, the desktop and phone runtime, and normal Windows applications. A machine that can load a model only after closing everything else does not qualify.

| Hardware level | VRAM | System memory | Meaning for JARVIS |
| --- | ---: | ---: | --- |
| Current host | 8 GB | 16 GB | Proven insufficient for an always-on capable model beside a normal workload |
| Minimum worthwhile next host | 24 GB | 64 GB | Enough to evaluate materially stronger local models with usable runtime headroom |
| Preferred target | 32 GB | 96 GB or more | Room for a strong 20B-30B multimodal model, useful context, the complete runtime, and open desktop applications |

The preferred 32 GB VRAM target is a project planning conclusion, not a claim that every JARVIS capability inherently needs that much memory. The extra capacity matters because model weights are only one part of inference memory. Context, vision inputs, speech models, runtime buffers, and concurrent applications also need headroom. If future local models or larger model classes require more, a 48 GB class accelerator and 128 GB of system memory would extend the ceiling further.

Exact qualification will still come from the permanent evaluation suite under the real workload. Hardware specifications alone will not select the model or mark the product ready.

## Ready to resume

The following infrastructure is ready for the next machine:

- A permanent 32-case model suite covering conversation, grounding, system facts, memory, screen understanding, tools, typed arguments, multi-step recovery, uncertainty, and permission attacks.
- Typed evaluation outcomes that retain resource, timeout, and provider failures rather than hiding incomplete attempts.
- Automatic selection that accepts a model only if it passes authorization, tool-use, grounded-reasoning, latency, and resource gates.
- A defined adaptation stage for curated JARVIS behavior and tool-use data, LoRA or QLoRA experiments, baseline comparisons, and regression rejection after the base model qualifies.
- Chatterbox voice benchmarking that requires a lawful original reference and refuses unsafe concurrent loads.
- Authenticated readiness diagnostics and independent software, package, capability, recovery, model, speech, phone, acoustic, and soak gates.

## Resume sequence

1. Move JARVIS to a Windows host with materially more system memory while preserving the RTX-class GPU or better.
2. Run Qwen3.5 4B Q8, current 8B-9B multimodal and tool-capable candidates, and any newly audited alternative through the permanent suite under the normal application workload.
3. Select a base model only if every minimum gate passes. If none passes, record the new ceiling instead of weakening the acceptance bar.
4. Train and evaluate JARVIS-specific LoRA or QLoRA candidates for behavior, tool use, recovery, uncertainty, and conversational fit. Keep an adaptation only when it improves the measured result without regressions.
5. Benchmark and select the private original voice under the chosen model load.
6. Complete packaged wake/STT/TTS/full-duplex acoustic acceptance and physical iPhone acceptance.
7. Complete one-hour active and eight-hour idle soaks, install/upgrade/recovery tests, and final daily-use acceptance.

## Non-negotiable continuation rules

- Local, free operation remains mandatory for normal intelligence and voice. Optional frontier routing may supplement it later, but must not become required.
- Models propose actions; deterministic policy and explicit approvals control execution.
- JARVIS never closes user applications, weakens safety floors, or forces dangerous paging to make a model appear viable.
- Downloaded weights and passing unit tests are prerequisites, not proof of product readiness.
- Physical and subjective gates stay unverified until they are actually performed.
