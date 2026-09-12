"""Fusion UI Bridge Handler.

Translates incoming UI commands into existing Fusion core orchestrator and
repository operations. Translates internal signals into typed UI events.
Enforces strict filesystem path boundaries and preserves Fusion's promotion,
isolation, and verification invariants.
"""

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import fusion_agent
from fusion_agent.cli.doctor import run_doctor
from fusion_agent.config.loader import ConfigLoader
from fusion_agent.config.schema import FusionConfig, OptimizationMode
from fusion_agent.core.orchestrator import FusionOrchestrator, OrchestratorResult
from fusion_agent.memory.database import Database
from fusion_agent.memory.project_state import ProjectStateManager
from fusion_agent.models.deliberation import ReviewStatus
from fusion_agent.models.task import PromotionDisposition, TaskStatus
from fusion_agent.providers.registry import ProviderRegistry
from fusion_agent.ui_bridge.protocol import (
    BridgeEvent,
    BridgeRequest,
    BridgeResponse,
    UICommand,
    UIErrorCode,
    UIEvent,
)
from fusion_agent.workspace.promotion import PromotionEngine


class PathTraversalError(Exception):
    """Raised when a requested path falls outside the active project root."""


class UIBridgeHandler:
    """Central handler dispatching UI commands to existing Fusion APIs."""

    def __init__(
        self,
        project_root: Optional[str] = None,
        event_sink: Optional[Callable[[BridgeEvent], None]] = None,
    ):
        self.project_root: Optional[Path] = Path(project_root).resolve() if project_root else None
        self.event_sink = event_sink or (lambda ev: None)
        self.config: Optional[FusionConfig] = None
        self.orchestrator: Optional[FusionOrchestrator] = None
        self.db: Optional[Database] = None
        self.state_manager: Optional[ProjectStateManager] = None

        # In-memory tracking of active execution session for promotion
        self.active_result: Optional[OrchestratorResult] = None
        self.active_task_id: Optional[str] = None

        if self.project_root and self.project_root.exists():
            self._initialize_from_root(self.project_root)

    def emit(self, event_type: UIEvent, data: Dict[str, Any]) -> None:
        """Emit a structured event to the registered event sink."""
        event = BridgeEvent(event=event_type.value, data=data)
        self.event_sink(event)

    def _initialize_from_root(self, root: Path) -> bool:
        """Initialize or reload project configuration and orchestrator from root."""
        self.project_root = root.resolve()
        config_file = ConfigLoader.find_config_file(self.project_root)
        if config_file:
            try:
                self.config = ConfigLoader.load(config_file)
            except Exception:
                self.config = ConfigLoader.load_hierarchical(project_dir=self.project_root)
        else:
            self.config = None
            self.orchestrator = None
            self.db = None
            self.state_manager = None
            return False

        db_path = self.project_root / self.config.storage_dir / "fusion.db"
        if db_path.exists() or (self.project_root / ".fusion").exists():
            self.db = Database(db_path)
            self.state_manager = ProjectStateManager(self.db)
            self.orchestrator = FusionOrchestrator(config=self.config, database=self.db)
        else:
            self.db = None
            self.state_manager = None
            self.orchestrator = None

        return True

    def _safe_resolve(self, relative_path: str) -> Path:
        """Resolve path relative to project root and verify boundary confinement."""
        if not self.project_root:
            raise PathTraversalError("No active project loaded.")

        p = Path(relative_path)
        if p.is_absolute() or relative_path.startswith("/") or relative_path.startswith("\\"):
            target = p.resolve()
        else:
            target = (self.project_root / relative_path).resolve()

        try:
            target.relative_to(self.project_root)
        except ValueError:
            raise PathTraversalError(f"Access denied: path '{relative_path}' traverses outside project root.")

        return target

    def _is_git_clean(self) -> bool:
        """Verify if git working tree is clean."""
        if not self.project_root or not (self.project_root / ".git").exists():
            return True
        try:
            res = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(self.project_root),
                capture_output=True,
                text=True,
                timeout=5,
            )
            return len(res.stdout.strip()) == 0
        except Exception:
            return True

    def handle_request(self, req: BridgeRequest) -> BridgeResponse:
        """Dispatch an incoming BridgeRequest to the appropriate handler method."""
        cmd = req.command
        p = req.params

        try:
            if cmd == UICommand.OPEN_PROJECT.value:
                return self._cmd_open_project(req.id, p)
            elif cmd == UICommand.INIT_PROJECT.value:
                return self._cmd_init_project(req.id, p)
            elif cmd == UICommand.GET_PROJECT_STATUS.value:
                return self._cmd_get_project_status(req.id, p)
            elif cmd == UICommand.GET_PROVIDER_STATUS.value:
                return self._cmd_get_provider_status(req.id, p)
            elif cmd == UICommand.GET_MCP_STATUS.value:
                return self._cmd_get_mcp_status(req.id, p)
            elif cmd == UICommand.LIST_TASKS.value:
                return self._cmd_list_tasks(req.id, p)
            elif cmd == UICommand.GET_TASK.value:
                return self._cmd_get_task(req.id, p)
            elif cmd == UICommand.START_TASK.value:
                return self._cmd_start_task(req.id, p)
            elif cmd == UICommand.RESUME_TASK.value:
                return self._cmd_resume_task(req.id, p)
            elif cmd == UICommand.GET_DIFF.value:
                return self._cmd_get_diff(req.id, p)
            elif cmd == UICommand.APPROVE_TASK.value:
                return self._cmd_approve_task(req.id, p)
            elif cmd == UICommand.REJECT_TASK.value:
                return self._cmd_reject_task(req.id, p)
            elif cmd == UICommand.LIST_FILES.value:
                return self._cmd_list_files(req.id, p)
            elif cmd == UICommand.READ_FILE.value:
                return self._cmd_read_file(req.id, p)
            elif cmd == UICommand.WRITE_FILE.value:
                return self._cmd_write_file(req.id, p)
            elif cmd == UICommand.GET_TEST_RESULTS.value:
                return self._cmd_get_test_results(req.id, p)
            elif cmd == UICommand.GET_REVIEW_FINDINGS.value:
                return self._cmd_get_review_findings(req.id, p)
            elif cmd == UICommand.RUN_DOCTOR.value:
                return self._cmd_run_doctor(req.id, p)
            elif cmd == UICommand.SAVE_CONFIG.value:
                return self._cmd_save_config(req.id, p)
            else:
                return BridgeResponse(
                    id=req.id,
                    success=False,
                    error={
                        "code": UIErrorCode.UNKNOWN_COMMAND.value,
                        "message": f"Unrecognized bridge command: '{cmd}'",
                    },
                )
        except PathTraversalError as pte:
            return BridgeResponse(
                id=req.id,
                success=False,
                error={
                    "code": UIErrorCode.PATH_TRAVERSAL_DENIED.value,
                    "message": str(pte),
                },
            )
        except Exception as exc:
            return BridgeResponse(
                id=req.id,
                success=False,
                error={
                    "code": UIErrorCode.INTERNAL_ERROR.value,
                    "message": f"Bridge error: {str(exc)}",
                },
            )

    # --- Command Implementations ---

    def _cmd_open_project(self, req_id: Optional[str], params: Dict[str, Any]) -> BridgeResponse:
        path_str = params.get("path")
        if not path_str:
            return BridgeResponse(
                id=req_id,
                success=False,
                error={"code": UIErrorCode.INVALID_REQUEST.value, "message": "Missing 'path' parameter"},
            )

        target = Path(path_str).resolve()
        if not target.exists() or not target.is_dir():
            return BridgeResponse(
                id=req_id,
                success=False,
                error={"code": UIErrorCode.PROJECT_NOT_FOUND.value, "message": f"Directory does not exist: {path_str}"},
            )

        initialized = self._initialize_from_root(target)

        project_info = {
            "path": str(target),
            "name": self.config.project_name if self.config else target.name,
            "initialized": initialized,
            "version": fusion_agent.__version__,
            "git_clean": self._is_git_clean(),
        }

        self.emit(UIEvent.PROJECT_OPENED, project_info)
        return BridgeResponse(id=req_id, success=True, data=project_info)

    def _cmd_init_project(self, req_id: Optional[str], params: Dict[str, Any]) -> BridgeResponse:
        if not self.project_root:
            return BridgeResponse(
                id=req_id,
                success=False,
                error={"code": UIErrorCode.PROJECT_NOT_FOUND.value, "message": "No project opened."},
            )

        fusion_dir = self.project_root / ".fusion"
        fusion_dir.mkdir(parents=True, exist_ok=True)

        project_name = params.get("name") or self.project_root.name
        config = FusionConfig.default_starter_config(project_name=project_name)
        config.project_root = str(self.project_root)

        ConfigLoader.save(config, self.project_root)

        # Initialize SQLite database
        db = Database(fusion_dir / "fusion.db")
        db.connect()
        db.close()

        self._initialize_from_root(self.project_root)

        return BridgeResponse(
            id=req_id,
            success=True,
            data={
                "project_name": project_name,
                "project_root": str(self.project_root),
                "initialized": True,
            },
        )

    def _cmd_get_project_status(self, req_id: Optional[str], params: Dict[str, Any]) -> BridgeResponse:
        if not self.project_root:
            return BridgeResponse(
                id=req_id,
                success=True,
                data={"opened": False},
            )

        data = {
            "opened": True,
            "project_name": self.config.project_name if self.config else self.project_root.name,
            "project_root": str(self.project_root),
            "initialized": bool(self.config and self.orchestrator),
            "version": fusion_agent.__version__,
            "optimization_mode": self.config.optimization_mode.value if self.config else "BALANCED",
            "verification_command": self.config.verification_command if self.config else "pytest",
            "git_clean": self._is_git_clean(),
            "config": self.config.to_safe_dict() if self.config else None,
        }
        return BridgeResponse(id=req_id, success=True, data=data)

    def _cmd_get_provider_status(self, req_id: Optional[str], params: Dict[str, Any]) -> BridgeResponse:
        providers = []
        if self.config and self.config.agents:
            for agent_id, agent_cfg in self.config.agents.items():
                try:
                    prov = ProviderRegistry.create(
                        name=agent_cfg.provider_name,
                        provider_type=agent_cfg.provider_type,
                        config=agent_cfg.to_dict(),
                    )
                    prov.initialize()
                    health = prov.health_check()
                    providers.append({
                        "id": agent_id,
                        "name": agent_cfg.provider_name,
                        "type": agent_cfg.provider_type,
                        "model": agent_cfg.model,
                        "healthy": health.healthy,
                        "message": health.message,
                        "latency_ms": round(health.latency_ms, 1),
                    })
                except Exception as exc:
                    providers.append({
                        "id": agent_id,
                        "name": agent_cfg.provider_name,
                        "type": agent_cfg.provider_type,
                        "model": agent_cfg.model,
                        "healthy": False,
                        "message": str(exc),
                        "latency_ms": 0.0,
                    })
        return BridgeResponse(id=req_id, success=True, data={"providers": providers})

    def _cmd_get_mcp_status(self, req_id: Optional[str], params: Dict[str, Any]) -> BridgeResponse:
        servers = []
        if self.orchestrator and self.orchestrator.mcp_registry:
            for s_id, s_cfg in self.orchestrator.mcp_registry.list_servers().items():
                servers.append({
                    "id": s_id,
                    "name": s_cfg.name,
                    "command": s_cfg.command,
                    "enabled": s_cfg.enabled,
                })
        return BridgeResponse(id=req_id, success=True, data={"mcp_servers": servers})

    def _cmd_list_tasks(self, req_id: Optional[str], params: Dict[str, Any]) -> BridgeResponse:
        if not self.db or not self.state_manager:
            return BridgeResponse(id=req_id, success=True, data={"tasks": []})

        limit = params.get("limit", 20)
        try:
            conn = self.db.connect()
            rows = conn.execute(
                "SELECT id, title, status, created_at, completed_at, last_checkpoint_sha, promotion_disposition "
                "FROM tasks ORDER BY created_at DESC LIMIT ?;",
                (limit,),
            ).fetchall()

            tasks = []
            for r in rows:
                tasks.append({
                    "id": r["id"],
                    "title": r["title"],
                    "status": r["status"],
                    "created_at": r["created_at"],
                    "completed_at": r["completed_at"],
                    "last_checkpoint_sha": r["last_checkpoint_sha"],
                    "promotion_disposition": r["promotion_disposition"],
                    "recoverable": r["status"] in ("RUNNING", "INTERRUPTED", "RECOVERABLE", "RESUMING"),
                })
            return BridgeResponse(id=req_id, success=True, data={"tasks": tasks})
        except Exception as exc:
            return BridgeResponse(id=req_id, success=False, error={"code": UIErrorCode.INTERNAL_ERROR.value, "message": str(exc)})

    def _cmd_get_task(self, req_id: Optional[str], params: Dict[str, Any]) -> BridgeResponse:
        task_id = params.get("task_id")
        if not task_id or not self.db:
            return BridgeResponse(id=req_id, success=False, error={"code": UIErrorCode.INVALID_REQUEST.value, "message": "Missing task_id"})

        try:
            conn = self.db.connect()
            task_row = conn.execute("SELECT * FROM tasks WHERE id = ?;", (task_id,)).fetchone()
            if not task_row:
                return BridgeResponse(id=req_id, success=False, error={"code": UIErrorCode.TASK_NOT_FOUND.value, "message": f"Task {task_id} not found"})

            stages = conn.execute(
                "SELECT * FROM provider_stage_runs WHERE task_id = ? ORDER BY started_at ASC;",
                (task_id,),
            ).fetchall()

            reviews = conn.execute(
                "SELECT * FROM reviews WHERE task_id = ? ORDER BY created_at ASC;",
                (task_id,),
            ).fetchall()

            data = {
                "id": task_row["id"],
                "title": task_row["title"],
                "status": task_row["status"],
                "created_at": task_row["created_at"],
                "completed_at": task_row["completed_at"],
                "promotion_disposition": task_row["promotion_disposition"],
                "last_checkpoint_sha": task_row["last_checkpoint_sha"],
                "stages": [dict(s) for s in stages],
                "reviews": [dict(r) for r in reviews],
            }
            return BridgeResponse(id=req_id, success=True, data=data)
        except Exception as exc:
            return BridgeResponse(id=req_id, success=False, error={"code": UIErrorCode.INTERNAL_ERROR.value, "message": str(exc)})

    def _cmd_start_task(self, req_id: Optional[str], params: Dict[str, Any]) -> BridgeResponse:
        instruction = params.get("instruction")
        if not instruction:
            return BridgeResponse(id=req_id, success=False, error={"code": UIErrorCode.INVALID_REQUEST.value, "message": "Missing 'instruction'"})

        if not self.orchestrator:
            return BridgeResponse(id=req_id, success=False, error={"code": UIErrorCode.PROJECT_NOT_FOUND.value, "message": "Project not initialized."})

        # Check clean repository boundary
        if not self._is_git_clean():
            return BridgeResponse(
                id=req_id,
                success=False,
                error={
                    "code": UIErrorCode.DIRTY_REPOSITORY.value,
                    "message": (
                        "Repository contains uncommitted changes. Fusion requires a clean repository "
                        "before autonomous execution. Commit or stash your changes before running this task."
                    ),
                },
            )

        self.emit(UIEvent.TASK_STARTED, {"instruction": instruction})

        def on_status_callback(event_type: str, data: dict):
            self._translate_orchestrator_event(event_type, data)

        try:
            result = self.orchestrator.run_task(instruction, on_status=on_status_callback)
            self.active_result = result
            self.active_task_id = result.task.id

            task_summary = {
                "task_id": result.task.id,
                "status": result.task.status.value,
                "diff": result.diff,
                "verification_passed": result.verification_result.passed if result.verification_result else False,
                "review_approved": result.review_result.status == ReviewStatus.APPROVED if result.review_result else False,
                "final_answer": result.final_answer,
            }

            if result.diff and result.verification_result and result.verification_result.passed:
                self.emit(UIEvent.DIFF_READY, {"diff": result.diff, "task_id": result.task.id})
                self.emit(UIEvent.APPROVAL_REQUIRED, {
                    "task_id": result.task.id,
                    "eligible": True,
                    "verification": {
                        "passed": result.verification_result.passed,
                        "exit_code": result.verification_result.exit_code,
                        "duration_seconds": result.verification_result.duration_seconds,
                    },
                    "review": {
                        "status": result.review_result.status.value if result.review_result else "UNKNOWN",
                        "comments": result.review_result.comments if result.review_result else "",
                    },
                })

            self.emit(UIEvent.TASK_COMPLETED, task_summary)
            return BridgeResponse(id=req_id, success=True, data=task_summary)
        except Exception as exc:
            self.emit(UIEvent.TASK_FAILED, {"error": str(exc)})
            return BridgeResponse(id=req_id, success=False, error={"code": UIErrorCode.INTERNAL_ERROR.value, "message": str(exc)})

    def _cmd_resume_task(self, req_id: Optional[str], params: Dict[str, Any]) -> BridgeResponse:
        task_id = params.get("task_id")
        if not task_id or not self.orchestrator:
            return BridgeResponse(id=req_id, success=False, error={"code": UIErrorCode.INVALID_REQUEST.value, "message": "Missing task_id"})

        self.emit(UIEvent.TASK_STARTED, {"task_id": task_id, "action": "resume"})

        def on_status_callback(event_type: str, data: dict):
            self._translate_orchestrator_event(event_type, data)

        try:
            result = self.orchestrator.resume_task(task_id, on_status=on_status_callback)
            self.active_result = result
            self.active_task_id = result.task.id

            task_summary = {
                "task_id": result.task.id,
                "status": result.task.status.value,
                "diff": result.diff,
                "verification_passed": result.verification_result.passed if result.verification_result else False,
                "review_approved": result.review_result.status == ReviewStatus.APPROVED if result.review_result else False,
                "final_answer": result.final_answer,
            }
            self.emit(UIEvent.TASK_COMPLETED, task_summary)
            return BridgeResponse(id=req_id, success=True, data=task_summary)
        except Exception as exc:
            self.emit(UIEvent.TASK_FAILED, {"task_id": task_id, "error": str(exc)})
            return BridgeResponse(id=req_id, success=False, error={"code": UIErrorCode.INTERNAL_ERROR.value, "message": str(exc)})

    def _cmd_get_diff(self, req_id: Optional[str], params: Dict[str, Any]) -> BridgeResponse:
        diff = self.active_result.diff if self.active_result else None
        return BridgeResponse(id=req_id, success=True, data={"diff": diff or ""})

    def _cmd_approve_task(self, req_id: Optional[str], params: Dict[str, Any]) -> BridgeResponse:
        if not self.active_result or not self.active_result.workspace_session:
            return BridgeResponse(
                id=req_id,
                success=False,
                error={"code": UIErrorCode.PROMOTION_INELIGIBLE.value, "message": "No active task workspace session available for promotion."},
            )

        res = self.active_result
        eligible = (
            (res.verification_result is not None and res.verification_result.passed)
            and (res.review_result is not None and res.review_result.status == ReviewStatus.APPROVED)
            and bool(res.diff)
        )
        if not eligible:
            return BridgeResponse(
                id=req_id,
                success=False,
                error={"code": UIErrorCode.PROMOTION_INELIGIBLE.value, "message": "Changes not eligible for promotion: tests must pass and peer review must approve."},
            )

        promo = PromotionEngine()
        promo_res = promo.promote(res.workspace_session)
        if promo_res.success:
            if self.state_manager:
                self.state_manager.update_task_promotion(res.task.id, PromotionDisposition.PROMOTED)
            return BridgeResponse(
                id=req_id,
                success=True,
                data={"message": promo_res.message, "promoted": True},
            )
        else:
            if self.state_manager:
                self.state_manager.update_task_promotion(res.task.id, PromotionDisposition.BLOCKED)
            return BridgeResponse(
                id=req_id,
                success=False,
                error={"code": UIErrorCode.PROMOTION_FAILED.value, "message": promo_res.message},
            )

    def _cmd_reject_task(self, req_id: Optional[str], params: Dict[str, Any]) -> BridgeResponse:
        if not self.active_result or not self.active_result.workspace_session:
            return BridgeResponse(
                id=req_id,
                success=True,
                data={"message": "No active session to discard.", "discarded": True},
            )

        promo = PromotionEngine()
        promo.discard(self.active_result.workspace_session)
        if self.state_manager and self.active_result.task:
            self.state_manager.update_task_promotion(self.active_result.task.id, PromotionDisposition.DECLINED)

        return BridgeResponse(
            id=req_id,
            success=True,
            data={"message": "Isolated changes discarded cleanly.", "discarded": True},
        )

    def _cmd_list_files(self, req_id: Optional[str], params: Dict[str, Any]) -> BridgeResponse:
        subpath = params.get("subpath", "")
        target_dir = self._safe_resolve(subpath)

        if not target_dir.exists() or not target_dir.is_dir():
            return BridgeResponse(id=req_id, success=False, error={"code": UIErrorCode.FILE_NOT_FOUND.value, "message": f"Not a directory: {subpath}"})

        ignored_names = {
            ".git",
            "__pycache__",
            ".pytest_cache",
            ".venv",
            "node_modules",
            ".gemini",
        }

        recursive = params.get("recursive", True)
        entries = []

        if recursive:
            for item in sorted(target_dir.rglob("*"), key=lambda x: (not x.is_dir(), str(x).lower())):
                rel_parts = item.relative_to(self.project_root).parts
                if any(p in ignored_names for p in rel_parts):
                    continue
                if any(p.startswith(".fusion") and p != ".fusion" for p in rel_parts):
                    continue
                if item.name.endswith((".db", ".db-wal", ".db-shm", ".pyc")):
                    continue

                rel_path = str(item.relative_to(self.project_root)).replace("\\", "/")
                entries.append({
                    "name": item.name,
                    "path": rel_path,
                    "is_dir": item.is_dir(),
                    "size": item.stat().st_size if item.is_file() else None,
                })
        else:
            for item in sorted(target_dir.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                if item.name in ignored_names:
                    continue
                if item.name.startswith(".fusion") and item.name != ".fusion":
                    continue
                if item.name.endswith((".db", ".db-wal", ".db-shm", ".pyc")):
                    continue

                rel_path = str(item.relative_to(self.project_root)).replace("\\", "/")
                entries.append({
                    "name": item.name,
                    "path": rel_path,
                    "is_dir": item.is_dir(),
                    "size": item.stat().st_size if item.is_file() else None,
                })

        return BridgeResponse(id=req_id, success=True, data={"files": entries, "subpath": subpath})

    def _cmd_read_file(self, req_id: Optional[str], params: Dict[str, Any]) -> BridgeResponse:
        filepath = params.get("filepath", "")
        target = self._safe_resolve(filepath)

        if not target.exists() or not target.is_file():
            return BridgeResponse(id=req_id, success=False, error={"code": UIErrorCode.FILE_NOT_FOUND.value, "message": f"File not found: {filepath}"})

        try:
            content = target.read_text(encoding="utf-8", errors="replace")
            rel_path = str(target.relative_to(self.project_root)).replace("\\", "/")
            return BridgeResponse(id=req_id, success=True, data={"filepath": rel_path, "content": content})
        except Exception as exc:
            return BridgeResponse(id=req_id, success=False, error={"code": UIErrorCode.INTERNAL_ERROR.value, "message": str(exc)})

    def _cmd_write_file(self, req_id: Optional[str], params: Dict[str, Any]) -> BridgeResponse:
        filepath = params.get("filepath", "")
        content = params.get("content", "")
        target = self._safe_resolve(filepath)

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        rel_path = str(target.relative_to(self.project_root)).replace("\\", "/")
        return BridgeResponse(id=req_id, success=True, data={"filepath": rel_path, "bytes_written": len(content)})

    def _cmd_get_test_results(self, req_id: Optional[str], params: Dict[str, Any]) -> BridgeResponse:
        if self.active_result and self.active_result.verification_result:
            vr = self.active_result.verification_result
            return BridgeResponse(
                id=req_id,
                success=True,
                data={
                    "passed": vr.passed,
                    "exit_code": vr.exit_code,
                    "duration_seconds": vr.duration_seconds,
                    "stdout": vr.stdout,
                    "stderr": vr.stderr,
                },
            )
        return BridgeResponse(id=req_id, success=True, data={"passed": None, "message": "No verification result in session."})

    def _cmd_get_review_findings(self, req_id: Optional[str], params: Dict[str, Any]) -> BridgeResponse:
        if self.active_result and self.active_result.review_result:
            rr = self.active_result.review_result
            return BridgeResponse(
                id=req_id,
                success=True,
                data={
                    "status": rr.status.value,
                    "reviewer_agent": rr.reviewer_agent,
                    "comments": rr.comments,
                },
            )
        return BridgeResponse(id=req_id, success=True, data={"status": None, "message": "No review result in session."})

    def _cmd_run_doctor(self, req_id: Optional[str], params: Dict[str, Any]) -> BridgeResponse:
        if not self.project_root:
            return BridgeResponse(id=req_id, success=False, error={"code": UIErrorCode.PROJECT_NOT_FOUND.value, "message": "No project opened."})

        report = run_doctor(self.project_root)
        checks = [
            {
                "name": c.name,
                "status": c.status.value,
                "message": c.message,
                "remediation": c.remediation,
            }
            for c in report.checks
        ]
        return BridgeResponse(
            id=req_id,
            success=True,
            data={
                "is_healthy": report.is_healthy,
                "passed_count": report.passed_count,
                "warn_count": report.warn_count,
                "fail_count": report.fail_count,
                "checks": checks,
            },
        )

    def _cmd_save_config(self, req_id: Optional[str], params: Dict[str, Any]) -> BridgeResponse:
        if not self.project_root or not self.config:
            return BridgeResponse(id=req_id, success=False, error={"code": UIErrorCode.PROJECT_NOT_FOUND.value, "message": "No project opened."})

        cfg_updates = params.get("config", {})
        if "optimization_mode" in cfg_updates:
            self.config.optimization_mode = OptimizationMode(cfg_updates["optimization_mode"])
        if "verification_command" in cfg_updates:
            self.config.verification_command = cfg_updates["verification_command"]

        ConfigLoader.save(self.config, self.project_root)
        return BridgeResponse(id=req_id, success=True, data={"config": self.config.to_safe_dict()})

    # --- Event Translation ---

    def _translate_orchestrator_event(self, event_type: str, data: dict) -> None:
        """Translate raw orchestrator callback signals into structured UI events."""
        if event_type == "status":
            self.emit(UIEvent.LOG_MESSAGE, {"message": data.get("message", "")})
            msg = data.get("message", "")
            if "indexing repository" in msg.lower():
                self.emit(UIEvent.CONTEXT_BUILT, {"message": msg})
            elif "executing automated verification" in msg.lower():
                self.emit(UIEvent.VERIFICATION_STARTED, {"message": msg})
            elif "verification passed" in msg.lower() or "verification failed" in msg.lower():
                self.emit(UIEvent.VERIFICATION_COMPLETED, {"message": msg})
            elif "peer diff review" in msg.lower():
                self.emit(UIEvent.REVIEW_STARTED, {"message": msg})
            elif "repair" in msg.lower():
                self.emit(UIEvent.REPAIR_STARTED, {"message": msg})
        elif event_type == "routing_decision":
            self.emit(UIEvent.TASK_ASSESSED, {
                "strategy": data.get("strategy"),
                "primary": data.get("primary"),
                "secondary": data.get("secondary"),
                "role_assignments": data.get("role_assignments", {}),
                "rationale": data.get("rationale", ""),
            })
        elif event_type == "deliberation_step":
            self.emit(UIEvent.STEP_STARTED, {"step": data.get("step", "")})
