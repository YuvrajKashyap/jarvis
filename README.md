# JARVIS

JARVIS is Yuvraj's private, local-first AI control plane: an ambient desktop assistant, a secure phone companion, a persistent memory, and an approval-gated operator. The laptop is authoritative. No paid model API or cloud inference is required.

The product definition lives in `project_spec.md`; the intended lived outcome lives in `NORTHSTAR.md`. Implementation details must not quietly redefine either document.

## Commands

```powershell
pnpm bootstrap   # install locked Python, JavaScript, and Rust dependencies
pnpm dev         # run the Tauri host, supervised Python core, and shared UI
pnpm verify      # format, lint, type-check, test, and build every layer
pnpm benchmark   # run local model, speech, memory, and resource evaluations
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

See `ARCHITECTURE.md` for seams, ownership, and dependency rules. See `AGENTS.md` before changing code.
