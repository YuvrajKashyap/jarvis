# JARVIS Project Specification

## Document Authority

This document defines the approved behavior and requirements of the complete JARVIS product currently being designed.

Nothing is a product decision merely because it was proposed, recommended, researched, or discussed. A requirement enters this specification only after Yuvraj explicitly approves it. Unresolved choices remain visibly marked as open.

The product's intended outcomes and lived experience are defined separately in `NORTHSTAR.md`. This specification defines what the product must do and how it must behave to realize that North Star.

## Product Identity and Role

JARVIS is one persistent personal intelligence. It combines the roles of intelligent interface, second brain, chief of staff, digital counterpart, collaborator, companion, and friend.

All underlying models, applications, tools, services, and devices must present as one coherent JARVIS identity with continuous memory, context, personality, and relationship.

The target experience is the closest practically achievable modern equivalent to JARVIS from the *Iron Man* films, built as ambitiously as Yuvraj's current hardware and available software permit.

## Product Surfaces

### Windows Laptop

The complete product is centered on Yuvraj's Windows laptop. JARVIS must start automatically with the computer, remain available without requiring Yuvraj to open or close an AI application, respond to the "Hey JARVIS" wake phrase, and present interactions through a minimal overlay that appears when useful and dismisses itself when the interaction is finished.

### Phone Companion

The complete product includes a phone companion connected to the same JARVIS identity, relationship, memory, and conversation history. It must feel like another doorway into the same JARVIS rather than a separate mobile assistant.

The target phone is Yuvraj's iPhone 17 Pro.

When the laptop is awake and reachable, the phone companion should use the laptop-hosted JARVIS core and local inference rather than duplicating the full laptop model on the phone.

The phone companion must be delivered as a private installable web application. It must provide a polished, phone-native-feeling voice and text interface without requiring a publicly accessible JARVIS website or a native App Store application.

The phone and laptop must communicate through Yuvraj's private Tailscale network. The phone interface must connect to the JARVIS core running on the laptop rather than exposing that core directly to the public internet.

When the laptop or JARVIS core is asleep, off, disconnected, or otherwise unreachable, the phone companion must clearly show that JARVIS is unavailable. It must not substitute Apple Intelligence or another separate assistant as a reduced offline JARVIS.

The exact iPhone invocation mechanism and application-level streaming and authentication protocols remain open product decisions.

## Ambient Listening and Awareness

### Normal Mode

While JARVIS is idle on the laptop:

- A lightweight wake-word detector runs locally.
- Recent raw microphone audio is held in a RAM-only rolling buffer.
- The buffer is continuously overwritten.
- Idle buffered audio is not normally transcribed, indexed, written to disk, or added to memory.
- After Yuvraj invokes JARVIS, the buffer may be used to answer a request that explicitly depends on immediately preceding audio, such as "What did he just say?"
- Everything intentionally said to JARVIS after activation is transcribed and added to the direct JARVIS conversation history.
- Ambient speech cannot issue commands, authorize actions, or become permanent memory.

The default rolling-buffer duration is 120 seconds. The duration must be configurable.

### Explicit Awareness Modes

JARVIS must support intentionally activated awareness modes, including:

- Meeting mode
- Lecture or learning mode
- Explicit ambient-memory mode
- Private mode

Meeting, lecture, or ambient-memory modes may persist authorized audio-derived information such as transcripts, speakers, decisions, tasks, and summaries.

Private mode must disable the rolling audio buffer while retaining only the minimum processing necessary to detect the wake phrase, unless Yuvraj explicitly disables the microphone entirely.

### Proactivity While Idle

In normal mode, proactive behavior may originate from authorized digital events such as calendars, files, downloads, processes, temperatures, builds, messages, reminders, and project state.

JARVIS must not eavesdrop on unrelated ambient conversations and spontaneously interject based on their content unless Yuvraj has explicitly enabled an awareness mode that permits this behavior.

## Intelligence and Cost Policy

### Local Intelligence

The complete product must function without an OpenAI API key, Anthropic API key, or another mandatory paid model API.

JARVIS's intelligence is local by default. Its normal conversations, memory operations, voice interaction, contextual reasoning, and agent loops must not incur per-token frontier-model charges.

Yuvraj's existing ChatGPT, Claude, Codex, and Claude Code access remains separate from JARVIS. JARVIS must not automatically call those products, consume their subscriptions, or send information to them.

JARVIS may prepare a context package or prompt for Yuvraj to use manually in a separate frontier product, but it must not transmit that package without explicit authorization and a future approved integration.

The architecture should preserve the ability to add optional frontier providers later without moving JARVIS's identity, memory, voice, permissions, or continuity into those providers. No frontier integration is part of the currently approved product.

Using a local model does not prohibit JARVIS from using authorized internet-connected tools. Web browsing, search, communications, connected services, and other online capabilities are separate from paid frontier-model inference.

### Future Hybrid Intelligence

The approved long-term direction is a model-independent agentic harness that can route work across local, specialized, and optional frontier models without fragmenting JARVIS's identity or conversation.

Local intelligence remains the default for private memory, sensitive context, ambient interaction, routine operation, and offline availability. Optional frontier intelligence may be added when a task materially benefits from it, but only through an approved adapter with explicit privacy, authorization, availability, and cost controls.

The router must consider required capability, modality, sensitivity, latency, resource pressure, availability, and cost. Yuvraj may override routing with instructions such as "keep this local" or "use the strongest model available." The selected model and any transmitted context must remain inspectable.

Automatic routing is not blanket authorization to disclose data. Sensitive information may reach a frontier provider only under a specific approved policy or immediate approval for that context. Provider output remains untrusted input to the same capability, policy, provenance, and audit system used for local models.

JARVIS owns the durable identity, system behavior, memory, permissions, voice, tools, and conversation state. No model provider may become the canonical store for those concerns. A model can be replaced or switched during an objective without creating a different assistant.

## Voice Identity

JARVIS must have an original, recognizable voice identity created specifically for this JARVIS rather than using a widely shared stock assistant voice.

The voice should evoke the desired qualities of cinematic JARVIS without being an unauthorized clone of an actor. It should be sophisticated, calm, intelligent, precise, quietly warm, subtly humorous, and capable of adjusting delivery to the situation without losing its identity.

The canonical voice assets must remain private and reusable across the laptop and phone. Normal speech generation must run locally without a mandatory per-use speech API fee.

The voice must support controlled variation in pace, pauses, urgency, warmth, formality, volume, pronunciation, and operational versus conversational delivery.

The specific speech model and voice-design workflow remain open until candidates are evaluated on quality, latency, resource use, licensing, consistency, and customizability.

## Technical Architecture

### Core Runtime

The JARVIS core must be a local Python 3.11 application with a typed FastAPI and Pydantic boundary for the laptop interface, phone companion, internal services, and integrations.

Model providers, speech systems, memory retrieval, perception, tools, and external services must connect through replaceable adapters. JARVIS's identity, memory, permissions, and behavior must not be implemented inside or depend on one model runtime.

Ollama is the approved initial local model runtime on Windows. It must remain replaceable rather than becoming the JARVIS application itself.

### Local Model Policy

JARVIS must prioritize the most capable local model that still satisfies the approved conversational latency, stability, and resource-safety requirements on Yuvraj's current laptop.

No primary language model has been selected. Candidate models and quantizations must be benchmarked on Yuvraj's laptop under the complete expected JARVIS workload, including concurrent speech, perception, memory retrieval, tool operation, the desktop interface, and Yuvraj's normal applications. A terminal-only generation test is insufficient.

The candidate set must include Qwen3.5 9B at multiple practical quantizations, Qwen3.5 4B, and any current roughly 7B-to-12B alternatives that may provide a better overall capability, latency, multimodal, tool-use, and memory fit. The active primary model must be selected from measured results rather than parameter count or vendor benchmarks alone.

More than one model may be stored locally. Only one heavyweight language model may be active at a time. A smaller automatic fallback is optional and must be adopted only if it materially improves availability without fragmenting JARVIS's identity or requiring Yuvraj to select models manually.

Only one heavyweight language model may be loaded at a time. JARVIS must control model residency, context allocation, parallelism, GPU use, system-memory use, temperature, and model switching through a resource governor. A model that cannot meet safe resource limits must not be loaded merely because it can technically execute through heavy paging or CPU offload.

Larger local models that materially damage conversational responsiveness, system stability, or concurrent speech and tool operation do not qualify as more capable JARVIS models for the current hardware.

### Model Adaptation and Personalization

The selected open-weight model is a base model, not the finished JARVIS intelligence. After a base model passes the hardware and quality gates, the project must evaluate JARVIS-specific parameter-efficient adaptation, including LoRA or QLoRA where supported and lawful.

Training data must be curated for stable JARVIS behavior such as tool selection, typed argument construction, multi-step recovery, uncertainty, truthful progress language, interruption, permission discipline, and the approved conversational character. Training assets and outputs must have clear provenance and remain private when they contain personal information.

Every adapted candidate must run against the same permanent evaluation suite as its untouched base model. An adapter must be rejected if it weakens authorization behavior, general reasoning, tool reliability, latency, resource safety, or another minimum gate. Improvement claims require saved comparative evidence.

Fine-tuning must not become the canonical store for changing personal knowledge. Facts, preferences, people, projects, decisions, and unfinished work remain in sourced memory with correction, deletion, export, and conflict handling. Model adaptation targets stable behavior and task skill, while retrieval supplies current personal context.

JARVIS's system identity, model instructions, context assembly, retrieval policy, capability schemas, routing, and voice behavior are also part of the personalization layer. They must remain portable across base models and providers.

### Speech Pipeline

The approved local speech foundation consists of:

- openWakeWord for the laptop wake phrase
- Silero VAD for speech activity detection
- faster-whisper for local speech recognition
- Chatterbox as the foundation for the original local JARVIS voice

The exact models, quantizations, operating modes, and voice assets within this foundation must be selected through hardware and quality validation.

### Interfaces

The Windows application must use Tauri 2 as its native shell and React with TypeScript for the interface. The desktop overlay and private phone web application should share interface code and design primitives where practical.

The packaged Windows application must launch the local core without requiring Yuvraj to manually run Python, a terminal, Docker, or another developer tool.

Docker must not be required for normal JARVIS operation.

### Computer Operation

JARVIS must prefer structured control mechanisms before visual input simulation:

- Playwright for supported browser operation
- Windows UI Automation for supported desktop controls
- PowerShell and typed operating-system integrations for system operations
- Controlled mouse, keyboard, and visual interaction as a fallback when structured mechanisms are unavailable

### Permission Boundary

Action authorization must be enforced by a deterministic permission layer outside the language model. A model request or generated tool call cannot grant itself authority.

### Memory Storage

The authoritative JARVIS data store must be local SQLite managed through the JARVIS core as the single write authority. Phone and interface clients must never connect to the database directly.

The memory system must separate:

- Authoritative structured data for conversations, facts, sources, people, projects, events, actions, permissions, and durable jobs
- Human-readable Markdown for durable canonical knowledge that Yuvraj can inspect, edit, export, and preserve independently
- Derived full-text, embedding, and vector-search indexes that can be discarded and rebuilt without losing canonical memory

The storage layer must remain behind a portable repository boundary so a future change in deployment topology can replace SQLite without changing JARVIS's identity, memory semantics, or higher-level behavior.

### Local Scheduling

Scheduling and durable background work must run locally as part of the JARVIS core without requiring Redis, Celery, Temporal, a cloud scheduler, or another always-running infrastructure service.

The scheduler must use a stable local scheduling library with durable SQLite persistence and support one-time, interval, calendar, and cron-style schedules; time zones and daylight-saving changes; retries and timeouts; cancellation; concurrency limits; missed-run policy; permission enforcement; and auditable execution history.

Windows startup facilities may launch or recover JARVIS, but individual JARVIS schedules must remain owned and interpreted by JARVIS. Work cannot execute while every authorized JARVIS host is off; after recovery, each missed job must follow its recorded catch-up, skip, combine, or ask policy.

### Real-Time Conversation

The speech pipeline must stream recognition, model output, and speech generation rather than waiting for each complete stage before beginning the next one.

JARVIS must acknowledge activation immediately through a minimal audio or visual signal. When work genuinely takes time, JARVIS may speak a brief, natural acknowledgment that confirms what Yuvraj asked, reflects relevant context, and, when useful, names the real next action. These acknowledgments should feel attentive and personable rather than canned or repetitive. Generic filler must not be used merely to disguise latency, and a nonverbal listening or thinking state must remain available when speech would add nothing.

For ordinary conversation on an otherwise available laptop, JARVIS should begin speaking as soon as the first stable response phrase is available. Longer reasoning or tool work may take longer, but progress communication must be concise, truthful, and useful.

Conversation must support full-duplex turn-taking. When Yuvraj begins speaking during JARVIS's response, output speech must stop promptly, input speech must take priority, and any superseded generation or safely cancellable work must be cancelled. JARVIS may proactively interject only when permitted by the approved proactivity and urgency policy.

### Engineering Verification

The implementation must support automated verification with pytest for the Python core, Vitest for TypeScript behavior, and Playwright for interface and browser workflows. High-risk permissions and actions require dedicated enforcement tests.

## Open Product Decisions

- How JARVIS is invoked from the iPhone within current iOS platform constraints.
- The exact application-level streaming and authentication protocols used between the private phone web application and the laptop-hosted JARVIS core.
- The exact original JARVIS voice design and the local speech system that realizes it.
- The acceptance-tested primary local language model, quantization, context limits, residency behavior, fallback policy, and measured performance on Yuvraj's laptop under the complete concurrent JARVIS workload.
