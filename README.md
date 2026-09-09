# Fusion Agent

**Provider-agnostic multi-model coding agent orchestrator.**

Fusion Agent enables multiple AI models, coding CLIs (such as Codex CLI, Gemini CLI), cloud APIs (OpenAI, Gemini), and local models (Ollama, LM Studio) to collaborate dynamically while presenting a single, cohesive engineering agent experience to the user.

---

## Key Features

- **One Persona, Multiple Contributors**: You interact with Fusion Agent. Multi-agent planning, critique, and reviews happen under the hood.
- **Dynamic Roles**: No fixed "architect" or "coder" silos. Agents are assigned roles based on task complexity, capabilities, and optimization goals.
- **Shared Persistent Brain**: Projects, tasks, architectural decisions, and agent run histories persist in a local SQLite database (`.fusion/fusion.db`).
- **Normalized Provider Abstraction**: Switch or combine providers (Codex CLI, Gemini API/CLI, OpenAI API, Ollama, OpenAI-compatible endpoints) without touching orchestrator logic.
- **Deliberation Engine**: Dynamic strategies (`DIRECT`, `EXECUTE_AND_REVIEW`, `PROPOSE_CRITIQUE_REFINE`, `INDEPENDENT_INVESTIGATION`) with bounded rounds and budget safeguards.
- **Deterministic Routing**: Transparent rule-based routing based on task classification and capabilities.

---

## Quickstart (Milestone 1)

### 1. Setup Environment
```bash
python -m venv .venv
# On Windows:
.venv\Scripts\pip install -e .
# On Linux/macOS:
.venv/bin/pip install -e .
```

### 2. Initialize a Project
```bash
fusion init --name "MyProject" --goal "Build an intelligent process manager"
```

### 3. Check Status
```bash
fusion status
```

### 4. Run a Task
```bash
fusion run "Design activity tracking architecture"
```

To see internal agent deliberation, use `--debug`:
```bash
fusion run "Investigate potential deadlock in thread pool" --debug
```

---

## Current Status: Milestone 1 (Foundation)
- [x] Provider abstraction & normalized capability model (`ProviderCapabilities`)
- [x] Shared SQLite state & 3-tier context snapshot generation
- [x] Deterministic task router & strategy selection
- [x] Deliberation engine (`DIRECT`, `EXECUTE_AND_REVIEW`, `PROPOSE_CRITIQUE_REFINE`)
- [x] Configurable mock provider for hermetic testing
- [x] Fusion CLI (`init`, `status`, `run`, `interactive`)
- [ ] Milestone 2: First real provider (Gemini CLI / API integration)
- [ ] Milestone 3: Second real provider (OpenAI / Ollama)
