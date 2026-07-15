from __future__ import annotations

import time
from importlib.resources import files
from pathlib import Path
from typing import Any

import aiosqlite

from summary_service.models import Job, JobStatus, NewJob


class QueueFullError(RuntimeError):
    pass


class Repository:
    def __init__(self, database_path: str | Path, *, busy_timeout_ms: int = 5000) -> None:
        self.database_path = str(database_path)
        self.busy_timeout_ms = busy_timeout_ms

    async def _connect(self) -> aiosqlite.Connection:
        connection = await aiosqlite.connect(self.database_path, isolation_level=None)
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA journal_mode=WAL")
        await connection.execute("PRAGMA foreign_keys=ON")
        await connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        await connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    async def migrate(self) -> None:
        migration = files("summary_service.migrations").joinpath("001_initial.sql")
        connection = await self._connect()
        try:
            await connection.executescript(migration.read_text(encoding="utf-8"))
        finally:
            await connection.close()

    async def scalar(self, sql: str) -> Any:
        connection = await self._connect()
        try:
            cursor = await connection.execute(sql)
            row = await cursor.fetchone()
            return row[0] if row else None
        finally:
            await connection.close()

    async def create_job(
        self,
        new_job: NewJob,
        *,
        max_active: int = 100,
        now_ms: int | None = None,
    ) -> tuple[Job, bool]:
        timestamp = now_ms if now_ms is not None else int(time.time() * 1000)
        connection = await self._connect()
        try:
            await connection.execute("BEGIN IMMEDIATE")
            if new_job.idempotency_key is not None:
                cursor = await connection.execute(
                    "SELECT * FROM jobs WHERE client_id=? AND idempotency_key=?",
                    (new_job.client_id, new_job.idempotency_key),
                )
                existing = await cursor.fetchone()
                if existing:
                    await connection.commit()
                    return self._job(existing), False

            cursor = await connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')"
            )
            active = (await cursor.fetchone())[0]
            if active >= max_active:
                await connection.rollback()
                raise QueueFullError("active job queue is full")

            cursor = await connection.execute(
                """
                INSERT INTO jobs (
                    token_hash, client_id, idempotency_key, status, input_text,
                    text_bytes, prompt_version, created_at_ms
                ) VALUES (?, ?, ?, 'queued', ?, ?, ?, ?)
                RETURNING *
                """,
                (
                    new_job.token_hash,
                    new_job.client_id,
                    new_job.idempotency_key,
                    new_job.input_text,
                    new_job.text_bytes,
                    new_job.prompt_version,
                    timestamp,
                ),
            )
            row = await cursor.fetchone()
            await connection.commit()
            return self._job(row), True
        except Exception:
            if connection.in_transaction:
                await connection.rollback()
            raise
        finally:
            await connection.close()

    async def claim_next(
        self,
        owner: str,
        *,
        now_ms: int,
        lease_seconds: int,
    ) -> Job | None:
        connection = await self._connect()
        try:
            await connection.execute("BEGIN IMMEDIATE")
            cursor = await connection.execute(
                """
                UPDATE jobs
                SET status='running', lease_owner=?, lease_expires_at_ms=?, heartbeat_at_ms=?,
                    started_at_ms=COALESCE(started_at_ms, ?)
                WHERE id=(
                    SELECT id FROM jobs WHERE status='queued' ORDER BY created_at_ms, id LIMIT 1
                ) AND status='queued'
                RETURNING *
                """,
                (owner, now_ms + lease_seconds * 1000, now_ms, now_ms),
            )
            row = await cursor.fetchone()
            await connection.commit()
            return self._job(row) if row else None
        except Exception:
            if connection.in_transaction:
                await connection.rollback()
            raise
        finally:
            await connection.close()

    async def heartbeat(
        self,
        job_id: int,
        owner: str,
        *,
        now_ms: int,
        lease_seconds: int,
    ) -> bool:
        return await self._conditional_update(
            """
            UPDATE jobs SET heartbeat_at_ms=?, lease_expires_at_ms=?
            WHERE id=? AND status='running' AND lease_owner=?
            """,
            (now_ms, now_ms + lease_seconds * 1000, job_id, owner),
        )

    async def complete_success(
        self,
        job_id: int,
        owner: str,
        summary: str,
        *,
        completed_at_ms: int,
        expires_at_ms: int,
        attempts: int = 1,
    ) -> bool:
        return await self._conditional_update(
            """
            UPDATE jobs SET status='succeeded', summary=?, attempts=?, completed_at_ms=?,
                expires_at_ms=?, lease_owner=NULL, lease_expires_at_ms=NULL, heartbeat_at_ms=NULL
            WHERE id=? AND status='running' AND lease_owner=?
            """,
            (summary, attempts, completed_at_ms, expires_at_ms, job_id, owner),
        )

    async def complete_failure(
        self,
        job_id: int,
        owner: str,
        error_code: str,
        error_message: str,
        *,
        completed_at_ms: int,
        expires_at_ms: int,
        attempts: int,
    ) -> bool:
        return await self._conditional_update(
            """
            UPDATE jobs SET status='failed', error_code=?, error_message=?, attempts=?,
                completed_at_ms=?, expires_at_ms=?, lease_owner=NULL,
                lease_expires_at_ms=NULL, heartbeat_at_ms=NULL
            WHERE id=? AND status='running' AND lease_owner=?
            """,
            (
                error_code,
                error_message,
                attempts,
                completed_at_ms,
                expires_at_ms,
                job_id,
                owner,
            ),
        )

    async def recover_expired_leases(self, *, now_ms: int) -> int:
        return await self._update_count(
            """
            UPDATE jobs SET status='queued', lease_owner=NULL, lease_expires_at_ms=NULL,
                heartbeat_at_ms=NULL
            WHERE status='running' AND lease_expires_at_ms < ?
            """,
            (now_ms,),
        )

    async def expire_terminal_jobs(self, *, now_ms: int, delete_at_ms: int) -> int:
        return await self._update_count(
            """
            UPDATE jobs SET status='expired', input_text=NULL, summary=NULL,
                error_message=NULL, delete_at_ms=?
            WHERE status IN ('succeeded','failed') AND expires_at_ms <= ?
            """,
            (delete_at_ms, now_ms),
        )

    async def delete_expired_jobs(self, *, now_ms: int) -> int:
        return await self._update_count(
            "DELETE FROM jobs WHERE status='expired' AND delete_at_ms <= ?",
            (now_ms,),
        )

    async def get_by_id(self, job_id: int) -> Job | None:
        return await self._fetch_one("SELECT * FROM jobs WHERE id=?", (job_id,))

    async def get_by_token(self, client_id: str, token_hash: str) -> Job | None:
        return await self._fetch_one(
            "SELECT * FROM jobs WHERE client_id=? AND token_hash=?",
            (client_id, token_hash),
        )

    async def count_by_status(self) -> dict[JobStatus, int]:
        connection = await self._connect()
        try:
            cursor = await connection.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status")
            return {JobStatus(row[0]): row[1] for row in await cursor.fetchall()}
        finally:
            await connection.close()

    async def _fetch_one(self, sql: str, values: tuple[Any, ...]) -> Job | None:
        connection = await self._connect()
        try:
            cursor = await connection.execute(sql, values)
            row = await cursor.fetchone()
            return self._job(row) if row else None
        finally:
            await connection.close()

    async def _conditional_update(self, sql: str, values: tuple[Any, ...]) -> bool:
        return await self._update_count(sql, values) == 1

    async def _update_count(self, sql: str, values: tuple[Any, ...]) -> int:
        connection = await self._connect()
        try:
            cursor = await connection.execute(sql, values)
            await connection.commit()
            return cursor.rowcount
        finally:
            await connection.close()

    @staticmethod
    def _job(row: aiosqlite.Row) -> Job:
        values = dict(row)
        values["status"] = JobStatus(values["status"])
        return Job(**values)
