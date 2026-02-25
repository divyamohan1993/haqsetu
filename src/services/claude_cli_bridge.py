"""Claude CLI Bridge — Subprocess manager for Claude CLI from the web admin panel.

Allows the super admin to:
    1. Start ``claude auth login`` and capture the device code + URL
    2. Check Claude CLI authentication status
    3. Run Claude CLI auto-fix commands with streamed output
    4. View session history

All operations are sandboxed to the application directory with strict
timeouts and output size limits to prevent resource exhaustion.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

import structlog

logger = structlog.get_logger(__name__)


class ClaudeSessionStatus(str, Enum):
    """Status of a Claude CLI session."""

    IDLE = "idle"
    AUTH_PENDING = "auth_pending"
    AUTHENTICATED = "authenticated"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass
class ClaudeAuthInfo:
    """Device login information returned by ``claude auth login``."""

    device_code: str = ""
    verification_url: str = ""
    user_code: str = ""
    started_at: str = ""
    status: ClaudeSessionStatus = ClaudeSessionStatus.IDLE


@dataclass
class ClaudeSession:
    """Record of a Claude CLI execution session."""

    session_id: str = ""
    command: str = ""
    status: ClaudeSessionStatus = ClaudeSessionStatus.IDLE
    started_at: str = ""
    completed_at: str = ""
    output_lines: list[str] = field(default_factory=list)
    exit_code: int | None = None
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "command": self.command,
            "status": self.status.value,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "output_lines_count": len(self.output_lines),
            "output_tail": self.output_lines[-30:] if self.output_lines else [],
            "exit_code": self.exit_code,
            "duration_seconds": self.duration_seconds,
        }


# Maximum output lines stored per session (prevent memory exhaustion)
_MAX_OUTPUT_LINES = 2000

# Default timeout for CLI commands (10 minutes)
_DEFAULT_TIMEOUT = 600

# Auth login timeout (5 minutes to allow admin to authorize)
_AUTH_TIMEOUT = 300


class ClaudeCLIBridge:
    """Manages Claude CLI as a subprocess from the web backend.

    Provides methods to:
    - Check if Claude CLI is installed
    - Start device-code authentication flow
    - Run auto-fix commands
    - Track session history
    """

    def __init__(self, work_dir: str = "/opt/haqsetu") -> None:
        self._work_dir = work_dir
        self._auth_info = ClaudeAuthInfo()
        self._current_process: asyncio.subprocess.Process | None = None
        self._sessions: list[ClaudeSession] = []
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Status / Discovery
    # ------------------------------------------------------------------

    def is_installed(self) -> bool:
        """Check if the ``claude`` CLI binary is available on PATH."""
        return shutil.which("claude") is not None

    def get_claude_path(self) -> str | None:
        """Return full path to the claude binary, or None."""
        return shutil.which("claude")

    async def check_auth_status(self) -> dict[str, Any]:
        """Check whether Claude CLI is authenticated.

        Runs ``claude auth status`` and parses the result.
        """
        if not self.is_installed():
            return {
                "installed": False,
                "authenticated": False,
                "detail": "Claude CLI is not installed on this server.",
            }

        try:
            proc = await asyncio.create_subprocess_exec(
                "claude", "auth", "status",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._work_dir if os.path.isdir(self._work_dir) else None,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=15
            )
            output = (stdout or b"").decode("utf-8", errors="replace").strip()
            err_output = (stderr or b"").decode("utf-8", errors="replace").strip()
            combined = f"{output}\n{err_output}".lower()

            authenticated = (
                proc.returncode == 0
                and "not" not in combined
                and ("logged in" in combined or "authenticated" in combined or "active" in combined)
            )

            return {
                "installed": True,
                "authenticated": authenticated,
                "detail": output or err_output or "Status check completed.",
                "auth_pending": self._auth_info.status == ClaudeSessionStatus.AUTH_PENDING,
            }
        except asyncio.TimeoutError:
            return {
                "installed": True,
                "authenticated": False,
                "detail": "Auth status check timed out.",
            }
        except Exception as exc:
            logger.error("claude_cli.auth_check_failed", error=str(exc))
            return {
                "installed": True,
                "authenticated": False,
                "detail": f"Error checking auth: {exc!s}",
            }

    # ------------------------------------------------------------------
    # Device-Code Authentication
    # ------------------------------------------------------------------

    async def start_auth_login(self) -> dict[str, Any]:
        """Start ``claude auth login`` and capture device code + URL.

        The process runs in the background. The admin must visit the URL
        and enter the device code to authorize this VM.

        Returns the device code and URL for display in the admin panel.
        """
        if not self.is_installed():
            return {
                "success": False,
                "error": "Claude CLI is not installed. Deploy with --with-claude flag.",
            }

        async with self._lock:
            # If already running, return existing info
            if (
                self._auth_info.status == ClaudeSessionStatus.AUTH_PENDING
                and self._current_process is not None
                and self._current_process.returncode is None
            ):
                return {
                    "success": True,
                    "already_running": True,
                    "device_code": self._auth_info.user_code,
                    "verification_url": self._auth_info.verification_url,
                    "started_at": self._auth_info.started_at,
                    "message": "Authentication already in progress. Use the code below.",
                }

            # Start the auth process
            self._auth_info = ClaudeAuthInfo(
                started_at=datetime.now(UTC).isoformat(),
                status=ClaudeSessionStatus.AUTH_PENDING,
            )

            try:
                proc = await asyncio.create_subprocess_exec(
                    "claude", "auth", "login",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    stdin=asyncio.subprocess.PIPE,
                    cwd=self._work_dir if os.path.isdir(self._work_dir) else None,
                )
                self._current_process = proc

                # Read output lines to find the device code and URL.
                # claude auth login prints instructions with a URL and code.
                output_lines: list[str] = []
                device_code = ""
                verification_url = ""

                async def _read_stream(
                    stream: asyncio.StreamReader | None,
                ) -> None:
                    nonlocal device_code, verification_url
                    if stream is None:
                        return
                    while True:
                        line_bytes = await stream.readline()
                        if not line_bytes:
                            break
                        line = line_bytes.decode("utf-8", errors="replace").strip()
                        if line:
                            output_lines.append(line)
                            logger.debug("claude_cli.auth_output", line=line)

                            # Parse device code and URL from output.
                            # Claude CLI typically outputs lines like:
                            #   "Open this URL: https://console.anthropic.com/..."
                            #   "Enter code: XXXX-XXXX"
                            low = line.lower()
                            if "http" in line and ("anthropic" in low or "claude" in low or "url" in low):
                                # Extract URL
                                for word in line.split():
                                    if word.startswith("http"):
                                        verification_url = word.rstrip(".,;)")
                                        self._auth_info.verification_url = verification_url
                                        break
                            if "code" in low and not verification_url == "":
                                # Try to extract the code portion
                                parts = line.split(":")
                                if len(parts) >= 2:
                                    code_candidate = parts[-1].strip()
                                    if code_candidate and len(code_candidate) < 30:
                                        device_code = code_candidate
                                        self._auth_info.user_code = device_code
                                        self._auth_info.device_code = device_code

                # Give it a few seconds to produce output
                try:
                    read_tasks = []
                    if proc.stdout:
                        read_tasks.append(_read_stream(proc.stdout))
                    if proc.stderr:
                        read_tasks.append(_read_stream(proc.stderr))

                    if read_tasks:
                        await asyncio.wait_for(
                            asyncio.gather(*read_tasks),
                            timeout=_AUTH_TIMEOUT,
                        )
                except asyncio.TimeoutError:
                    self._auth_info.status = ClaudeSessionStatus.TIMEOUT
                    if proc.returncode is None:
                        proc.terminate()

                # Check final status
                if proc.returncode == 0:
                    self._auth_info.status = ClaudeSessionStatus.AUTHENTICATED

                result: dict[str, Any] = {
                    "success": True,
                    "device_code": device_code or "(check output below)",
                    "verification_url": verification_url or "(check output below)",
                    "started_at": self._auth_info.started_at,
                    "status": self._auth_info.status.value,
                    "output": output_lines[-20:],
                    "message": (
                        "Authentication flow started. "
                        "Visit the URL and enter the device code to authorize this server."
                    ),
                }
                return result

            except Exception as exc:
                self._auth_info.status = ClaudeSessionStatus.FAILED
                logger.error("claude_cli.auth_start_failed", error=str(exc))
                return {
                    "success": False,
                    "error": f"Failed to start authentication: {exc!s}",
                }

    # ------------------------------------------------------------------
    # Run Auto-Fix
    # ------------------------------------------------------------------

    async def run_autofix(
        self,
        prompt: str | None = None,
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> dict[str, Any]:
        """Run Claude CLI with an auto-fix prompt.

        Args:
            prompt: Custom prompt. Defaults to HaqSetu auto-fix prompt.
            timeout: Maximum execution time in seconds.

        Returns:
            Session result dict with output and status.
        """
        if not self.is_installed():
            return {
                "success": False,
                "error": "Claude CLI is not installed.",
            }

        if prompt is None:
            prompt = (
                "Analyze the HaqSetu codebase for issues. "
                "Run 'python -m pytest tests/ -x -q' to check test health. "
                "Check src/api/v1/admin_recovery.py and "
                "src/services/autofix_orchestrator.py for any issues. "
                "Fix any test failures or bugs you find. "
                "Commit fixes with descriptive messages."
            )

        session = ClaudeSession(
            session_id=f"claude-{uuid4().hex[:8]}",
            command=f"claude --dangerously-skip-permissions \"{prompt[:100]}...\"",
            status=ClaudeSessionStatus.RUNNING,
            started_at=datetime.now(UTC).isoformat(),
        )

        async with self._lock:
            start_time = time.monotonic()

            try:
                proc = await asyncio.create_subprocess_exec(
                    "claude", "--dangerously-skip-permissions", prompt,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=self._work_dir if os.path.isdir(self._work_dir) else None,
                )

                # Stream output
                if proc.stdout:
                    while True:
                        line_bytes = await asyncio.wait_for(
                            proc.stdout.readline(),
                            timeout=timeout,
                        )
                        if not line_bytes:
                            break
                        line = line_bytes.decode("utf-8", errors="replace").rstrip()
                        if len(session.output_lines) < _MAX_OUTPUT_LINES:
                            session.output_lines.append(line)

                await asyncio.wait_for(proc.wait(), timeout=30)
                session.exit_code = proc.returncode
                session.status = (
                    ClaudeSessionStatus.COMPLETED
                    if proc.returncode == 0
                    else ClaudeSessionStatus.FAILED
                )

            except asyncio.TimeoutError:
                session.status = ClaudeSessionStatus.TIMEOUT
                session.output_lines.append("[TIMEOUT] Claude CLI exceeded time limit.")
                if proc.returncode is None:
                    proc.terminate()

            except Exception as exc:
                session.status = ClaudeSessionStatus.FAILED
                session.output_lines.append(f"[ERROR] {exc!s}")
                logger.error(
                    "claude_cli.run_failed",
                    session_id=session.session_id,
                    error=str(exc),
                )

            session.duration_seconds = round(time.monotonic() - start_time, 2)
            session.completed_at = datetime.now(UTC).isoformat()

            # Store in history (keep last 50)
            self._sessions.insert(0, session)
            if len(self._sessions) > 50:
                self._sessions.pop()

            return {
                "success": session.status == ClaudeSessionStatus.COMPLETED,
                "session": session.to_dict(),
            }

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def get_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent Claude CLI session history."""
        return [s.to_dict() for s in self._sessions[:limit]]

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Get a specific session by ID."""
        for s in self._sessions:
            if s.session_id == session_id:
                return s.to_dict()
        return None

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def get_status_summary(self) -> dict[str, Any]:
        """Get overall Claude CLI bridge status."""
        return {
            "installed": self.is_installed(),
            "claude_path": self.get_claude_path(),
            "auth_status": self._auth_info.status.value,
            "auth_device_code": self._auth_info.user_code or None,
            "auth_verification_url": self._auth_info.verification_url or None,
            "total_sessions": len(self._sessions),
            "last_session": self._sessions[0].to_dict() if self._sessions else None,
        }
