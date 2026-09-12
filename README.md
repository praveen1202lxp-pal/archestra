# Fusion Agent

**Provider-agnostic multi-model coding agent orchestrator.**

Fusion Agent enables autonomous AI coding agents powered by different model providers (such as OpenAI Codex CLI and Google Antigravity CLI) to collaborate dynamically while presenting a single, cohesive developer persona to the user.

---

## Why Fusion Agent?

Standalone single-model coding agents frequently suffer from two common failure modes:
1. **Unbounded Context Proliferation**: Sending entire repositories and unbounded chat histories into expensive context windows inflates latency, explodes cost, and causes cognitive degradation.
2. **Scope Creep & Test Mutation**: Standalone models often modify existing unit tests, rewrite unrelated modules, or create unrequested files to make failing builds artificially pass.

Fusion Agent addresses these challenges through:
- **One Unified Persona, Multiple Specialized Contributors**: You interact with Fusion as a single intelligent peer. Multi-agent planning, implementation, and peer review take place autonomously under the hood.
- **Strict Scope Discipline & Confinement**: Modifications are bounded to isolated Git worktrees. Protected files and test suites cannot be arbitrarily mutated.
- **Substantially Reduced Input Context**: 3-tier contextual snapshotting extracts only relevant symbols, dependency graphs, and recent execution state, eliminating massive redundant context dumps.
- **Automated Peer Review & Repair Loops**: Implementation patches are critiqued and verified by a secondary model before human review.
- **Mandatory Human-in-the-Loop Gate**: No code is ever promoted to your working branch without passing automated verification, passing peer review, and receiving explicit human confirmation.
- **Durable Crash Recovery**: Execution state and checkpoints persist in a local SQLite database (`.fusion/fusion.db`), allowing interrupted tasks to be resumed instantly.

---

## Core Architecture

```
                               ┌─────────────────────────┐
                               │   Developer (Terminal)  │
                               └────────────┬────────────┘
                                            │ fusion run "<task>"
                                            ▼
                               ┌─────────────────────────┐
                               │       Task Router       │
                               └────────────┬────────────┘
                                            │ Classify & Select Strategy
                                            ▼
                               ┌─────────────────────────┐
                               │   Deliberation Engine   │
                               │  (Multi-Model Planning) │
                               └────────────┬────────────┘
                                            │
                     ┌──────────────────────┴──────────────────────┐
                     ▼                                             ▼
          ┌─────────────────────┐                       ┌─────────────────────┐
          │   Primary Model     │                       │   Secondary Model   │
          │ (e.g. Codex CLI /   │                       │ (e.g. Antigravity   │
          │  gpt-5.6-sol)       │                       │  gemini-3.8-flash)  │
          └──────────┬──────────┘                       └──────────┬──────────┘
                     │ Code Patch                                  │ Critique & Review
                     └──────────────────────┬──────────────────────┘
                                            ▼
                               ┌─────────────────────────┐
                               │ Isolated Git Worktree   │  <-- .fusion/worktrees/task-...
                               │  Automated Verification │  <-- Runs pytest/test command
                               └────────────┬────────────┘
                                            │ Tests Pass + Review Approved
                                            ▼
                               ┌─────────────────────────┐
                               │  Human Promotion Gate   │  <-- Inspect Diff [y/N]
                               └────────────┬────────────┘
                                            │ 'y' (Promoted)
                                            ▼
                               ┌─────────────────────────┐
                               │ Base Project Repository │
                               └─────────────────────────┘
```

---

## Supported Providers

| Provider | Type | Typical Role | Default Model | Configuration Key |
| :--- | :--- | :--- | :--- | :--- |
| **OpenAI Codex CLI** | `codex_cli` | Implementation / Refactoring | `gpt-5.6-sol` | `agents.codex` |
| **Google Antigravity CLI** | `antigravity_cli` | Peer Review / Architecture | `gemini-3.8-flash-high` | `agents.antigravity` |
| **Google Gemini CLI** | `gemini_cli` | Analysis / Review | `gemini-2.5-pro` | `agents.gemini` |
| **Mock Provider** | `mock` | Hermetic Offline Testing | `mock-reasoning-v1` | `agents.mock` |

---

## Installation

### Prerequisites
- Python 3.11 or higher
- Git 2.0+
- Optional: OpenAI Codex CLI (`codex`) or Google Antigravity CLI (`agy`) installed and authenticated.

### Install from Source
```bash
git clone https://github.com/archestra/archestra.git
cd archestra

# Create and activate virtual environment
python -m venv .venv
# On Windows:
.venv\Scripts\activate
# On Linux/macOS:
source .venv/bin/activate

# Install Fusion Agent in editable development mode
pip install -e .
```

Verify installation:
```bash
fusion --version
```

---

## Quick Start

### 1. Run Pre-flight Diagnostics
Verify that your Python runtime, Git tooling, and coding CLIs are discovered and ready:

```bash
fusion doctor
```

Example Output:
```
Fusion Doctor

  ✓ Fusion Version         Fusion Agent v0.12.0
  ✓ Python Runtime         Python 3.12.10 (CPython)
  ✓ Git Tooling            git version 2.45.1 at C:\Program Files\Git\cmd\git.EXE
  ✓ Repository State       Git repository detected (clean)
  ✓ Storage & SQLite       State directory writable at .fusion (SQLite operational)
  ✓ Configuration          Valid config at .fusion/config.json
  ✓ Codex CLI              Codex CLI 0.153.4 online and ready (Logged in)
  ✓ Antigravity CLI        Antigravity CLI 1.2.2 online and ready
  ✓ Docker / Container     Host CLI execution mode

Summary: 8 passed, 0 warnings, 0 errors
✓ System is ready to run Fusion Agent.
```

### 2. Initialize Fusion in Your Project
Navigate to your repository and initialize Fusion:

```bash
cd /path/to/my-project
fusion init
```

This sets up:
- `.fusion/config.json`: Project-specific settings and model configurations.
- `.fusion/fusion.db`: Persistent shared state ledger.
- Automatically updates `.gitignore` to prevent runtime state from being committed.

### 3. Check Provider Readiness
```bash
fusion providers
```

### 4. Give Fusion a Coding Task
```bash
fusion run "Add input validation to the user registration endpoint"
```

Fusion will:
1. Route the task and select the optimal deliberation strategy.
2. Spin up an isolated Git worktree branch (`fusion/task-...`).
3. Generate the proposed solution using the primary model.
4. Conduct automated peer review with the secondary model.
5. Execute the project's test suite inside the isolated worktree.
6. Present the unified diff and verification results for your explicit confirmation:
   ```
   Apply verified changes? [y/N]:
   ```

---

## Command Reference

| Command | Description | Example |
| :--- | :--- | :--- |
| `fusion --help` | Display CLI help and available commands | `fusion --help` |
| `fusion --version` | Display canonical application version | `fusion --version` |
| `fusion doctor` | Non-destructive diagnostic check of environment and providers | `fusion doctor --verbose` |
| `fusion init` | Initialize Fusion Agent in the current directory | `fusion init --name "MyApp"` |
| `fusion providers` | List configured providers and inspect connectivity/latency | `fusion providers` |
| `fusion status` | Show project summary, active mode, and recent task memory | `fusion status` |
| `fusion config` | Inspect active hierarchical configuration with secrets masked | `fusion config --validate` |
| `fusion run "<task>"` | Execute an autonomous task through multi-model orchestration | `fusion run "Fix bug in auth" --debug` |
| `fusion resume <task-id>` | Resume an interrupted or crashed task from its last checkpoint | `fusion resume task-84a12b` |
| `fusion interactive` | Start an interactive terminal REPL session | `fusion interactive` |
| `fusion mcp list` | List configured Model Context Protocol tool servers | `fusion mcp list` |

---

## Configuration & Precedence

Fusion loads settings hierarchically following strict precedence:

```
CLI Arguments
  └── Project Config (.fusion/config.json)
        └── User Config (~/.fusion/config.json)
              └── Environment Variables (FUSION_*)
                    └── Built-in Defaults
```

### Environment Variables
- `FUSION_OPTIMIZATION_MODE`: `BEST_QUALITY`, `BALANCED`, `LOWEST_COST`, `FASTEST`, `LOCAL_PRIVATE`
- `FUSION_LOG_LEVEL`: `INFO`, `DEBUG`, `WARNING`, `ERROR`
- `FUSION_STORAGE_DIR`: Custom state directory name (default: `.fusion`)
- `FUSION_VERIFICATION_COMMAND`: Command used to verify changes (default: `pytest`)
- `CODEX_CLI_PATH`: Custom path to `codex` executable
- `ANTIGRAVITY_CLI_PATH`: Custom path to `agy` executable

### Example `.fusion/config.json`
```json
{
  "project_name": "MyProject",
  "optimization_mode": "BALANCED",
  "verification_command": "pytest",
  "agents": {
    "codex": {
      "provider_name": "OpenAI Codex CLI",
      "provider_type": "codex_cli",
      "model": "gpt-5.6-sol",
      "extra_params": { "reasoning_effort": "medium" }
    },
    "antigravity": {
      "provider_name": "Google Antigravity CLI",
      "provider_type": "antigravity_cli",
      "model": "gemini-3.8-flash-high",
      "extra_params": { "effort": "high" }
    }
  },
  "deliberation": {
    "max_rounds": 3,
    "max_repair_rounds": 2,
    "max_provider_calls": 8,
    "timeout_seconds": 180.0
  }
}
```

---

## Safety & Isolation Model

Fusion enforces distinct safety boundaries for repository integrity and change governance. **Fusion does not provide a general OS sandbox.**

- **Repository & Policy Isolation (Git Worktrees)**: Autonomous edits and candidate patches execute strictly within ephemeral Git worktrees (`git worktree add`). Your active working tree and uncommitted files are never directly modified during deliberation or repair.
- **Execution Trust Model (Host-Trusted / Non-Adversarial)**: Native execution of tools, verifiers, and provider CLI subprocesses runs on the host system with the current user's privileges. This execution model is **host-trusted and non-adversarial** — it protects against accidental code destruction, dirty repository pollution, and merge conflicts, not malicious code execution.
- **Optional Container Isolation**: Where stricter isolation is required, container/Docker-based isolation may be explicitly configured and supported for isolated test execution workflows.
- **Distinct Safety Boundaries**:
  1. **Filesystem & State Partitioning**: Fusion-owned runtime state (`.fusion/fusion.db`, `.fusion/locks/`) and project files are strictly partitioned. Safe project configuration (`.fusion/config.json`) is trackable in Git while transient databases, logs, and worktrees remain ignored.
  2. **Mandatory Human Approval Gate**: Verified changes are never automatically merged to the base repository. A human must inspect the generated unified diff, review verification results, and explicitly confirm promotion (`[y/N]`).
  3. **Secret Sanitization**: All terminal logs and persistence layers route through a sensitive data filter that automatically redacts API keys (`AIza...`, `sk-...`, `Bearer...`), tokens, and credentials.

---

## Resumption & Crash Recovery

If an execution is interrupted (e.g. power loss, network dropout, user cancellation):
1. State is preserved in `.fusion/fusion.db` with checkpoint SHAs and completed step outputs.
2. View pending tasks with `fusion status`.
3. Resume immediately from the exact point of interruption:
   ```bash
   fusion resume <task-id>
   ```

---

## Evaluation & Benchmark Summary (Milestone 11)

In Milestone 11 Phase C, Fusion Agent was evaluated in a 63-run held-out comparative benchmark against standalone frontier models across 7 diverse software engineering tasks (3 repetitions each):

| System | Functional Correctness | Strict Scope Oracle | Median Input Tokens | Median Duration |
| :--- | :---: | :---: | :---: | :---: |
| **Fusion Agent** | 61.9% (13/21) | **57.1% (12/21)** | **66,980** | **70.4s** |
| **OpenAI Codex Alone** (`gpt-5.6-sol`) | **71.4% (15/21)** | 38.1% (8/21) | 370,525 | 248.9s |
| **Antigravity Alone** (`gemini-3.8-flash-high`) | **71.4% (15/21)** | 47.6% (10/21) | 284,316 | 244.3s |

### Empirical Findings:
- **Functional Correctness**: Standalone single-model baselines achieved higher overall functional correctness on the benchmark suite (Codex 15/21, Antigravity 15/21 vs. Fusion 13/21). Multi-step sequential planning remains susceptible to interface drift across step boundaries.
- **Input Context Efficiency**: Fusion used approximately **81.9% fewer median input tokens than Codex** (66,980 vs. 370,525) and approximately **76.4% fewer median input tokens than Antigravity** (66,980 vs. 284,316) by extracting bounded symbol and dependency graphs rather than ingesting entire workspaces.
- **Scope Discipline & Governance**: Standalone systems frequently modified pre-existing test suites or generated unrequested peripheral files. Fusion's scope contract enforced strict change control (Fusion 12/21 vs. Codex 8/21 and Antigravity 10/21). *Caveat: Raw strict-scope differences were partly affected by uncommunicated protected-path policies for standalone systems.*
- **Demonstrated Strengths**: Substantially reduced input context, centralized scope/change control, review/repair capability, recoverable durable orchestration, and vendor-agnostic provider abstraction.

---

## Demo Fixture

To safely test Fusion Agent without touching a production repository, explore the included calculator example:

```bash
cd examples/demo_calculator
fusion doctor
fusion init
fusion run "Add power(base, exponent) function to src/calculator.py and tests in tests/test_calculator.py"
```

See [examples/demo_calculator/README.md](examples/demo_calculator/README.md) for full instructions.

---

## Development & Testing

Run the test suite:
```bash
pytest -q
```

Run release-readiness tests:
```bash
pytest tests/test_m12_release.py -v
```

Check Git cleanliness:
```bash
git diff --check
```

---

## Roadmap

- **Milestone 13**: Portfolio showcase, public demonstration fixtures, comparative evaluation presentation, and developer onboarding materials.
- **Future Backlog (Post-M13)**: Direct remote cloud REST/gRPC service adapters (bypassing local CLI wrappers), containerized worker execution pools, and IDE plugins.

---

## License

Apache-2.0. See LICENSE for details.
