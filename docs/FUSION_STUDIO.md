# Fusion Studio — Desktop IDE & Presentation Layer

**Fusion Studio** is a lightweight, desktop IDE-style graphical environment built on top of the existing **Fusion Agent** orchestration engine. It serves as a visual control plane and rich code workspace while preserving Python as the sole source of truth for orchestration, workspace safety, and verification.

---

## 1. Architecture Overview

Fusion Studio adopts a strict decoupled architecture:

```
┌─────────────────────────────────────────────────────────────┐
│                 Fusion Studio (Angular 18)                  │
│  Project Explorer │ Monaco Code/Diff Editor │ Task Stepper │
└──────────────────────────────┬──────────────────────────────┘
                               │ JSON IPC / Events
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                   Desktop Shell (Tauri v2)                  │
│       Native Window Shell │ Stdio JSON-RPC Process Host     │
└──────────────────────────────┬──────────────────────────────┘
                               │ Stdio JSON-RPC Protocol
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                 Fusion UI Bridge (Python)                   │
│   fusion_agent.ui_bridge.handler.UIBridgeHandler            │
│   fusion_agent.ui_bridge.server.UIBridgeServer              │
└──────────────────────────────┬──────────────────────────────┘
                               │ Direct Internal Python APIs
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                     Fusion Agent Core                       │
│  Orchestrator │ Worktrees │ Verification │ Review │ State   │
└─────────────────────────────────────────────────────────────┘
```

### Architectural Principles
1. **No Frontend Orchestration**: The Angular UI and Tauri Rust shell contain zero autonomous routing or decision logic.
2. **Single Source of Truth**: The existing Python core controls dynamic routing, git worktree isolation, automated pytest verification, cross-model critique, SQLite persistence, and promotion boundaries.
3. **No Unsafe REST Services**: Communication uses a clean stdio newline-delimited JSON protocol over local process pipes, preventing exposed local network ports.
4. **Strict Confinement**: All file read/write operations validate path boundaries against `project_root`, preventing path traversal outside the repository.
5. **Human-in-the-Loop Promotion**: Diff review, test diagnostics, and peer reviewer critiques must be inspected and explicitly approved (`APPROVE_TASK` / `REJECT_TASK`) to trigger worktree branch promotion.

---

## 2. UI Bridge Protocol

The bridge layer lives under `fusion_agent/ui_bridge/` and provides two primary communication primitives:

### Commands (GUI → Bridge)
| Command | Arguments | Purpose |
|---|---|---|
| `OPEN_PROJECT` | `path: string` | Validates directory, initializes state manager, checks git clean status. |
| `GET_PROJECT_STATUS` | None | Returns git status, branch, clean status, and .fusion directory presence. |
| `GET_PROVIDER_STATUS` | None | Evaluates Codex and Antigravity health and detection status. |
| `GET_MCP_STATUS` | None | Returns active MCP servers and registered tool gateways. |
| `RUN_DOCTOR` | None | Executes non-generative pre-flight environment diagnostics. |
| `LIST_TASKS` | `limit?: number` | Queries SQLite task database for previous executions. |
| `GET_TASK` | `task_id: string` | Retrieves full task record (steps, diff, verification, review). |
| `START_TASK` | `prompt: string` | Begins autonomous pipeline (assessment, context, codex, test, review). |
| `RESUME_TASK` | `task_id: string` | Recovers a failed/paused task from SQLite state. |
| `GET_DIFF` | `task_id?: string` | Fetches git patch comparing candidate branch to base. |
| `APPROVE_TASK` | `task_id: string` | Promotes worktree changes into main working tree. |
| `REJECT_TASK` | `task_id: string` | Aborts task and discards isolated candidate worktree. |
| `LIST_FILES` | `subpath?: string` | Traverses repository while honoring ignores and security filters. |
| `READ_FILE` | `path: string` | Safely reads file contents for Monaco editor tabs. |
| `WRITE_FILE` | `path: string, content: string` | Saves user file edits from Monaco editor. |

### Structured Events (Bridge → GUI)
Events are streamed in real time to drive the reactive visual stepper:
- `PROJECT_OPENED`
- `TASK_STARTED`
- `TASK_ASSESSED`
- `PLAN_CREATED`
- `STEP_STARTED`
- `CONTEXT_BUILT`
- `PROVIDER_STARTED`
- `PROVIDER_COMPLETED`
- `PATCH_CREATED`
- `VERIFICATION_STARTED`
- `VERIFICATION_COMPLETED`
- `REVIEW_STARTED`
- `REVIEW_FINDING`
- `REPAIR_STARTED`
- `DIFF_READY`
- `APPROVAL_REQUIRED`
- `TASK_COMPLETED`
- `TASK_FAILED`
- `TASK_RECOVERABLE`

---

## 3. Visual Components & Layout

Fusion Studio features a high-density, dark-mode-first developer layout:

1. **Project Explorer (Left Sidebar)**:
   - Expandable directory tree with icons for Python, Markdown, JSON, and generic files.
   - Respects `.fusion`, `.git`, `__pycache__`, and temporary worktree filter rules.
   - Dirty-repo badge alerts developer if manual edits prevent autonomous execution.
2. **Monaco Editor Workspace (Center)**:
   - Multi-tab file editing with syntax highlighting, line numbers, and keyboard shortcuts (`Ctrl+S`).
   - Dirty tab indicator (`•`) and tab closing.
   - Integrated Monaco Diff Viewer for inspecting changes side-by-side.
3. **Fusion Task Panel (Right)**:
   - Prompt input for autonomous agent dispatch.
   - Orchestration timeline stepper tracking stage transitions without leaking chain-of-thought.
   - Tabs for **Timeline**, **Candidate Diff**, **Verification Results**, and **Reviewer Findings**.
   - Mandatory **[Approve Changes]** and **[Reject]** promotion buttons.
4. **Activity & History (Collapsible Panel)**:
   - Lists tasks from SQLite database (`#task_id`, timestamp, prompt, status badge).
   - Instant task inspection and **[Resume Task]** for recoverable runs.
5. **Diagnostics Drawer**:
   - Environment doctor checking Python version, Git status, directory structure, and provider binaries.
   - Masked credentials ensuring no tokens or keys are exposed to the UI.
6. **Bottom Status Bar**:
   - Status indicators: `Fusion 0.12.0`, optimization mode (`BALANCED`), Provider health (`Codex ✓`, `Gemini ✓`), Git status (`Git clean`), Python version (`Python 3.12.x`).

---

## 4. Running Fusion Studio

### Starting the Python UI Bridge Directly
To run the stdio JSON-RPC server from the terminal:
```bash
python -m fusion_agent.cli.main studio-bridge --dir .
```

### Running Angular Web / Development UI
```bash
cd fusion-studio
npm install
npm start
```
The browser interface will be available at `http://localhost:4200/`. When running outside Tauri, the built-in development fallback bridge provides simulated responses and telemetry.

### Running Tauri Desktop Shell
Prerequisites for native desktop compilation:
- **Rust toolchain** (`rustc` & `cargo` via [rustup.rs](https://rustup.rs/))
- **Visual Studio Build Tools with C++ / MSVC**
- **Microsoft Edge WebView2** (pre-installed on Windows 10/11)

To run the Tauri desktop app in dev mode:
```bash
cd fusion-studio
npm run tauri:dev
```

To build a standalone production desktop installer:
```bash
cd fusion-studio
npm run tauri:build
```
