import pickle
from typing import Any, AsyncIterator, Sequence

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    ChannelVersions,
    RunnableConfig,
    get_checkpoint_id,
)

from src.services.db import Database


class PostgresCheckpointer(BaseCheckpointSaver):
    """PostgreSQL-backed LangGraph checkpointer.

    Stores checkpoints and pending writes in PostgreSQL via the existing
    Database class. Uses pickle for serialization of arbitrary Python objects
    in channel_values and writes.
    """

    def __init__(self, db: Database) -> None:
        super().__init__()
        self._db = db

    @property
    def _pool(self):
        pool = self._db.pool
        if pool is None:
            raise RuntimeError("Database not connected")
        return pool

    # ---- async checkpoint storage ----

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        _new_versions: ChannelVersions,
    ) -> RunnableConfig:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = checkpoint["id"]
        parent_checkpoint_id = get_checkpoint_id(config)

        pickled = pickle.dumps(dict(checkpoint))
        import json
        meta_json = json.dumps(metadata, default=str)

        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO graph_checkpoints
                   (thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, checkpoint, metadata)
                   VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                   ON CONFLICT (thread_id, checkpoint_ns, checkpoint_id) DO UPDATE
                   SET parent_checkpoint_id = $4, checkpoint = $5, metadata = $6::jsonb""",
                thread_id,
                checkpoint_ns,
                checkpoint_id,
                parent_checkpoint_id,
                pickled,
                meta_json,
            )

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        _task_path: str = "",
    ) -> None:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"].get("checkpoint_id", "")

        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM graph_writes WHERE thread_id=$1 AND checkpoint_ns=$2 AND checkpoint_id=$3 AND task_id=$4",
                thread_id, checkpoint_ns, checkpoint_id, task_id,
            )
            for idx, (channel, value) in enumerate(writes):
                await conn.execute(
                    "INSERT INTO graph_writes (thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, value) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7)",
                    thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, pickle.dumps(value),
                )

    # ---- async checkpoint retrieval ----

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")

        async with self._pool.acquire() as conn:
            if checkpoint_id := get_checkpoint_id(config):
                row = await conn.fetchrow(
                    "SELECT checkpoint, metadata, parent_checkpoint_id"
                    " FROM graph_checkpoints"
                    " WHERE thread_id=$1 AND checkpoint_ns=$2 AND checkpoint_id=$3",
                    thread_id, checkpoint_ns, checkpoint_id,
                )
            else:
                row = await conn.fetchrow(
                    "SELECT checkpoint, metadata, parent_checkpoint_id"
                    " FROM graph_checkpoints"
                    " WHERE thread_id=$1 AND checkpoint_ns=$2"
                    " ORDER BY created_at DESC LIMIT 1",
                    thread_id, checkpoint_ns,
                )

            if row is None:
                return None

            checkpoint: Checkpoint = pickle.loads(row["checkpoint"])  # noqa: S301
            import json
            metadata: CheckpointMetadata = json.loads(row["metadata"])
            parent_checkpoint_id: str | None = row["parent_checkpoint_id"]

            # fetch writes
            write_rows = await conn.fetch(
                "SELECT task_id, channel, value"
                " FROM graph_writes"
                " WHERE thread_id=$1 AND checkpoint_ns=$2 AND checkpoint_id=$3"
                " ORDER BY task_id, idx",
                thread_id, checkpoint_ns, checkpoint["id"],
            )
            pending_writes = [
                (wr["task_id"], wr["channel"], pickle.loads(wr["value"]))  # noqa: S301
                for wr in write_rows
            ]

            resolved_config = {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": checkpoint["id"],
                }
            }
            parent_config = (
                {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": checkpoint_ns,
                        "checkpoint_id": parent_checkpoint_id,
                    }
                }
                if parent_checkpoint_id
                else None
            )

            return CheckpointTuple(
                config=resolved_config,
                checkpoint=checkpoint,
                metadata=metadata,
                parent_config=parent_config,
                pending_writes=pending_writes if pending_writes else None,
            )

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        _filter: dict[str, Any] | None = None,
        _before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        thread_id = config["configurable"]["thread_id"] if config else None
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "") if config else ""

        query = "SELECT checkpoint, metadata, parent_checkpoint_id, checkpoint_id FROM graph_checkpoints WHERE 1=1"
        params: list[Any] = []

        if thread_id:
            query += f" AND thread_id=${len(params) + 1}"
            params.append(thread_id)
        if checkpoint_ns is not None:
            query += f" AND checkpoint_ns=${len(params) + 1}"
            params.append(checkpoint_ns)

        query += " ORDER BY created_at DESC"
        if limit:
            query += f" LIMIT ${len(params) + 1}"
            params.append(limit)

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            for row in rows:
                checkpoint: Checkpoint = pickle.loads(row["checkpoint"])  # noqa: S301
                import json
                metadata: CheckpointMetadata = json.loads(row["metadata"])
                resolved_config = {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": checkpoint_ns,
                        "checkpoint_id": row["checkpoint_id"],
                    }
                }
                yield CheckpointTuple(
                    config=resolved_config,
                    checkpoint=checkpoint,
                    metadata=metadata,
                    parent_config=None,
                )

    async def adelete_thread(self, thread_id: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("DELETE FROM graph_writes WHERE thread_id=$1", thread_id)
            await conn.execute("DELETE FROM graph_checkpoints WHERE thread_id=$1", thread_id)

    # ---- sync adapters (delegate to async via asyncio) ----

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        raise NotImplementedError("Use aget_tuple for async operations")

    def put(self, config, checkpoint, metadata, _new_versions):
        raise NotImplementedError("Use aput for async operations")

    def put_writes(self, config, writes, task_id, _task_path=""):
        raise NotImplementedError("Use aput_writes for async operations")

    def delete_thread(self, thread_id: str) -> None:
        raise NotImplementedError("Use adelete_thread for async operations")

    def list(self, config, *, _filter=None, _before=None, limit=None):
        raise NotImplementedError("Use alist for async operations")
