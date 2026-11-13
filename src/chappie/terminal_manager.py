"""Terminal process manager and multiprocessing worker lifecycle for Chappie.

Manages isolated Python child processes per terminal session to maintain in-memory
project context and execute reasoning tasks safely within project boundaries.
"""

from __future__ import annotations

import asyncio
import logging
import multiprocessing as mp
from multiprocessing.connection import Connection
import os
from datetime import UTC, datetime
from typing import Any, Callable, Coroutine

from chappie.config import Settings, get_settings

logger = logging.getLogger(__name__)


class TerminalWorkerHandle:
    """Encapsulates active worker child process and IPC connection."""

    def __init__(
        self,
        session_id: str,
        process: mp.Process,
        parent_conn: Connection,
        pid: int,
        project_name: str,
        project_root: str,
    ) -> None:
        self.session_id = session_id
        self.process = process
        self.parent_conn = parent_conn
        self.pid = pid
        self.project_name = project_name
        self.project_root = project_root
        self.created_at = datetime.now(UTC)


def _worker_entrypoint(
    child_conn: Connection,
    handshake_dict: dict[str, Any],
    settings_dict: dict[str, Any],
) -> None:
    """Entry point for worker child process running in an isolated address space."""
    try:
        asyncio.run(_worker_async_main(child_conn, handshake_dict, settings_dict))
    except Exception as exc:
        logger.error("Fatal error in terminal child worker process: %s", exc, exc_info=True)
        try:
            child_conn.send({"status": "error", "error_message": str(exc)})
        except Exception:
            pass


async def _worker_async_main(
    child_conn: Connection,
    handshake_dict: dict[str, Any],
    settings_dict: dict[str, Any],
) -> None:
    """Asynchronous main loop for the child worker process."""
    from chappie.agent import CodingAgent
    from chappie.knowledge import EmbeddingClient, KnowledgeBase
    from chappie.models import AgentTaskRequest
    from chappie.profiles import ProfileStorage
    from chappie.projects import ProjectStorage
    from chappie.volume_manager import VolumeManager
    from chappie.workspace_client import WorkspaceClient

    # Reconstitute Settings
    settings = Settings.model_validate(settings_dict)

    session_id = handshake_dict["session_id"]
    project_name = handshake_dict["project_name"]
    project_root = handshake_dict["project_root"]
    chappie_context = handshake_dict.get("chappie_context", "")
    response_language = handshake_dict.get("response_language", "en")
    enable_web_search = handshake_dict.get("enable_web_search", False)

    # Initialize microservice clients and agent within child process
    embedder = EmbeddingClient(
        settings.embedder_url,
        timeout=settings.embedder_timeout_seconds,
    )
    knowledge = KnowledgeBase(settings, embedder)
    workspace = WorkspaceClient(settings)
    volumes = VolumeManager(settings)
    profiles = ProfileStorage(settings.profiles_file_path)
    projects = ProjectStorage(
        settings.projects_file_path,
        projects_root=settings.projects_root_path,
        data_root=settings.chappie_data_path,
    )

    agent = CodingAgent(
        settings,
        knowledge,
        workspace=workspace,
        volumes=volumes,
        projects=projects,
        profiles=profiles,
    )

    # Register local directory project in worker storage
    try:
        project_context = projects.open_directory_project(
            root_path=project_root,
            name=project_name,
            volume_manager=volumes,
        )
        proj_id = project_context.id
    except Exception as exc:
        logger.warning(
            "Could not open directory project via storage: %s. Using session project ID.",
            exc,
        )
        proj_id = f"term_proj_{session_id[:8]}"

    # Seed in-memory session with uploaded .chappie context if available
    agent.init_session(session_id)
    if chappie_context:
        agent.append_session_turn(
            session_id=session_id,
            user_prompt="[INITIAL_PROJECT_CONTEXT_LOADED]",
            raw_output=chappie_context,
            project_id=proj_id,
        )

    # Send Ready signal to master agent process
    child_conn.send({
        "status": "ready",
        "session_id": session_id,
        "pid": os.getpid(),
    })

    # Event loop listening for queries over IPC pipe
    loop = asyncio.get_running_loop()
    while True:
        # Await command from pipe without blocking event loop
        msg = await loop.run_in_executor(None, child_conn.recv)
        if not msg or msg.get("type") == "terminate":
            logger.info("Worker process for session %s received termination command.", session_id)
            break

        msg_type = msg.get("type", "query")
        if msg_type == "query":
            query_id = msg.get("query_id", "q_unknown")
            prompt = msg.get("prompt", "")

            try:
                task_req = AgentTaskRequest(
                    task=prompt,
                    session_id=session_id,
                    project_id=proj_id,
                    enable_web_search=enable_web_search,
                    response_language=response_language,
                    save_project_context=True,
                )

                # Run reasoning loop
                agent_resp = await agent.run(task_req)

                # Send response payload
                response_payload = {
                    "type": "response",
                    "query_id": query_id,
                    "status": "complete",
                    "answer": agent_resp.answer,
                    "rationale": agent_resp.rationale,
                    "files": [f.model_dump() for f in agent_resp.files],
                    "error_message": None,
                }
                child_conn.send(response_payload)

            except Exception as e:
                logger.error("Error during agent query execution in worker: %s", e, exc_info=True)
                child_conn.send({
                    "type": "response",
                    "query_id": query_id,
                    "status": "error",
                    "answer": "",
                    "rationale": [],
                    "files": [],
                    "error_message": str(e),
                })


class TerminalProcessManager:
    """Master manager maintaining child worker processes for terminal sessions."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.workers: dict[str, TerminalWorkerHandle] = {}
        self._lock = asyncio.Lock()

    async def start_session(self, handshake_data: dict[str, Any]) -> TerminalWorkerHandle:
        """Launches a dedicated worker child process and waits for ready acknowledgment."""
        session_id = handshake_data["session_id"]

        async with self._lock:
            # Terminate existing worker for same session if any
            if session_id in self.workers:
                await self._terminate_handle(self.workers[session_id])

            parent_conn, child_conn = mp.Pipe(duplex=True)
            settings_dict = self.settings.model_dump()

            process = mp.Process(
                target=_worker_entrypoint,
                args=(child_conn, handshake_data, settings_dict),
                daemon=True,
            )
            process.start()

            # Wait for ready signal from worker
            loop = asyncio.get_running_loop()
            try:
                ready_msg = await asyncio.wait_for(
                    loop.run_in_executor(None, parent_conn.recv),
                    timeout=15.0,
                )
            except Exception as exc:
                logger.error("Failed to start terminal child worker: %s", exc)
                process.terminate()
                process.join(timeout=1.0)
                parent_conn.close()
                child_conn.close()
                raise RuntimeError(f"Terminal child worker initialization failed: {exc}") from exc

            if ready_msg.get("status") != "ready":
                process.terminate()
                process.join(timeout=1.0)
                parent_conn.close()
                child_conn.close()
                err = ready_msg.get("error_message", "Unknown initialization error")
                raise RuntimeError(f"Terminal worker failed to initialize: {err}")

            handle = TerminalWorkerHandle(
                session_id=session_id,
                process=process,
                parent_conn=parent_conn,
                pid=ready_msg.get("pid", process.pid or 0),
                project_name=handshake_data["project_name"],
                project_root=handshake_data["project_root"],
            )
            self.workers[session_id] = handle
            logger.info(
                "Started terminal worker child process PID=%s for session %s (Project: %s)",
                handle.pid,
                session_id,
                handle.project_name,
            )
            return handle

    async def query_session(
        self,
        session_id: str,
        query_data: dict[str, Any],
        token_callback: Callable[[str], Coroutine[Any, Any, None]] | None = None,
    ) -> dict[str, Any]:
        """Sends query prompt to worker process over IPC and returns response."""
        handle = self.workers.get(session_id)
        if not handle or not handle.process.is_alive():
            raise RuntimeError(f"No active worker process found for session {session_id}")

        loop = asyncio.get_running_loop()

        # Send query to child process
        handle.parent_conn.send(query_data)

        # Await response from child process
        while True:
            msg = await loop.run_in_executor(None, handle.parent_conn.recv)
            msg_type = msg.get("type")

            if msg_type == "token" and token_callback:
                await token_callback(msg.get("token", ""))
            elif msg_type == "response":
                return msg
            elif msg.get("status") == "error":
                return {
                    "type": "response",
                    "query_id": query_data.get("query_id", ""),
                    "status": "error",
                    "answer": "",
                    "rationale": [],
                    "files": [],
                    "error_message": msg.get("error_message", "Unknown error in worker"),
                }

    async def terminate_session(self, session_id: str) -> None:
        """Terminates worker child process cleanly upon client disconnect."""
        async with self._lock:
            handle = self.workers.pop(session_id, None)
            if handle:
                await self._terminate_handle(handle)

    async def _terminate_handle(self, handle: TerminalWorkerHandle) -> None:
        """Sends termination command, waits for exit, or forces SIGTERM."""
        logger.info("Terminating terminal worker child process PID=%s (session: %s)...", handle.pid, handle.session_id)
        try:
            # 1. Graceful command via IPC pipe
            if handle.process.is_alive():
                try:
                    handle.parent_conn.send({"type": "terminate"})
                except Exception:
                    pass

            # 2. Wait up to 1.5 seconds for clean exit
            loop = asyncio.get_running_loop()
            try:
                await asyncio.wait_for(
                    loop.run_in_executor(None, handle.process.join, 1.5),
                    timeout=2.0,
                )
            except asyncio.TimeoutError:
                pass

            # 3. Force SIGTERM / kill if still alive
            if handle.process.is_alive():
                handle.process.terminate()
                handle.process.join(timeout=1.0)

            if handle.process.is_alive():
                handle.process.kill()
                handle.process.join(timeout=1.0)

        except Exception as exc:
            logger.warning("Error during worker termination: %s", exc)
        finally:
            try:
                handle.parent_conn.close()
            except Exception:
                pass
            logger.info("Terminal worker PID=%s terminated successfully.", handle.pid)

    async def shutdown_all(self) -> None:
        """Terminates all active child processes on application shutdown."""
        async with self._lock:
            for session_id, handle in list(self.workers.items()):
                await self._terminate_handle(handle)
            self.workers.clear()
