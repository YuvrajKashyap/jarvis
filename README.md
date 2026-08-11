# JARVIS

JARVIS is a local-first personal intelligence for Windows and iPhone: an ambient desktop assistant, private phone companion, persistent memory, and approval-gated computer operator. The laptop remains authoritative, and normal operation requires no paid model API or cloud inference.

This repository contains the product architecture and implementation. It deliberately excludes personal memory, conversations, credentials, pairing material, private voice references, screenshots, recordings, databases, logs, model weights, and machine-specific runtime state.

## What exists today

- A transparent, movable, dynamically resizing Tauri desktop overlay with a supervised Python core.
- A shared React interface for desktop and an installable private iPhone PWA.
- Authenticated REST and WebSocket transport with typed, generated protocol contracts.
- Streamed local-model conversation through Ollama with cancellation and resource governance.
- Deterministic permissions, approvals, audit history, undo-aware capabilities, and durable schedules.
- Source-grounded SQLite and Markdown memory with FTS5 and rebuildable local embeddings.
- Explicit private, meeting, lecture, and ambient-memory modes with a RAM-only idle audio buffer.
- Screen context, a managed browser, Windows UI Automation, native reminders, backups, and redacted logs.
- Reproducible verification, benchmarking, SBOM generation, and unsigned Windows packaging.

Real-device wake-word, acoustic, thermal, selected-voice, and iPhone/Tailscale acceptance remain physical hardware gates. They are not represented as passing until measured.

## Architecture

JARVIS is a compact modular monolith: one authoritative FastAPI/Python process, one shared React client, and a thin Rust/Tauri Windows host. Pydantic models define the protocol; TypeScript contracts are generated and checked for drift. Every capability invocation crosses the deterministic policy engine immediately before execution.

The intended experience is defined in [`NORTHSTAR.md`](NORTHSTAR.md), the approved behavior in [`project_spec.md`](project_spec.md), and the implementation boundaries in [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Development

Prerequisites include Windows 11, Python 3.11, uv, Node.js, pnpm, stable Rust/MSVC, WebView2, and Ollama. Tailscale is required only for private phone access.

### Commands

```powershell
pnpm bootstrap   # install locked Python, JavaScript, and Rust dependencies
pnpm dev         # run the Tauri host, supervised Python core, and shared UI
pnpm verify      # format, lint, type-check, test, and build every layer
pnpm benchmark   # run local model, speech, memory, and resource evaluations
pnpm preflight   # report automatic readiness and the remaining physical/manual gates
pnpm security-audit # audit Python, Node, and Rust dependencies
pnpm supply-chain  # generate Python, Node, and Rust CycloneDX SBOMs
pnpm package     # build the Python sidecar and unsigned Windows installer
```

## Repository map

```text
src/jarvis/   Python intelligence and control plane
ui/           Shared React desktop overlay and iPhone PWA
src-tauri/    Thin Windows host and Python supervisor
tests/        Contract, module, integration, and end-to-end tests
scripts/      Reproducible bootstrap, verification, benchmark, and packaging
```

## Privacy and publication boundary

Runtime data belongs outside the repository. Secrets are stored in Windows Credential Manager; canonical local memory, voice references, model weights, databases, logs, generated pairing credentials, transcripts, and screenshots must never be committed. Idle ambient audio is held only in a bounded RAM buffer and continuously overwritten.

The repository is public for engineering transparency and portfolio review. The software remains proprietary unless and until a separate license explicitly grants additional rights.
