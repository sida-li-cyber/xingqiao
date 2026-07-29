"""Backend data plane for user file transfers (Milestone A, phase B).

The discrete-event simulation core models a file as a stream of abstract
chunks (``packet_sim.FileTransfer``) routed hop-by-hop like ordinary packets,
with timeout-driven selective-repeat ARQ. It never touches the real payload
bytes. This module is the counterpart that *does* hold the bytes:

  1. ``POST /api/files/upload`` stores the uploaded file, sliced on disk into
     fixed-size chunk part-files (``chunks/<seq>.part``) whose boundaries match
     the simulation's ``chunk_size`` exactly. The SHA-256 of the whole file is
     recorded.
  2. The core reports ``file_chunk_delivered`` events (file_id + seq) over its
     WebSocket. ``FileStore.on_chunk_delivered`` marks the chunk received.
  3. Once every chunk has been delivered, the part-files are concatenated into
     ``reassembled.bin`` and its SHA-256 is verified against the upload. Only a
     verified file becomes downloadable.

This proves end-to-end that the bytes "travelled" through the simulated
network: the download serves bytes reassembled purely from DES delivery events.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Must match packet_sim's default chunk size so on-disk part boundaries line up
# with the abstract chunks the DES routes. Kept as the single source of truth
# on the backend side; file_send forwards it to the core explicitly.
CHUNK_SIZE = 16384

# Safety caps (see docs/file-transfer-design.md §7).
MAX_FILE_BYTES = 100 * 1024 * 1024      # 100 MB per file
MAX_CONCURRENT_FILES = 64               # tracked transfers

# Transfer states (mirror the sim core's FT_* but as strings for the API).
ST_STORED = "STORED"            # uploaded, not yet sent into the simulation
ST_TRANSFERRING = "TRANSFERRING"
ST_COMPLETE = "COMPLETE"
ST_CANCELLED = "CANCELLED"
ST_FAILED = "FAILED"            # reassembly hash mismatch (should never happen)


@dataclass
class FileRecord:
    file_id: str
    name: str
    total_bytes: int
    chunk_size: int
    total_chunks: int
    sha256: str
    src: str | None = None
    dst: str | None = None
    state: str = ST_STORED
    received: set[int] = field(default_factory=set)
    reassembled_sha256: str | None = None
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None

    @property
    def received_bytes(self) -> int:
        out = 0
        for seq in self.received:
            start = seq * self.chunk_size
            out += min(self.chunk_size, max(0, self.total_bytes - start))
        return out

    @property
    def progress(self) -> float:
        return (self.received_bytes / self.total_bytes) if self.total_bytes else 1.0

    def public_dict(self) -> dict[str, Any]:
        return {
            "file_id": self.file_id,
            "name": self.name,
            "total_bytes": self.total_bytes,
            "chunk_size": self.chunk_size,
            "total_chunks": self.total_chunks,
            "received_chunks": len(self.received),
            "received_bytes": self.received_bytes,
            "progress": round(self.progress, 4),
            "sha256": self.sha256,
            "src": self.src,
            "dst": self.dst,
            "state": self.state,
            "reassembled_sha256": self.reassembled_sha256,
            "verified": self.reassembled_sha256 is not None
            and self.reassembled_sha256 == self.sha256,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }


class FileStore:
    """Holds uploaded bytes on disk and reassembles them from DES deliveries."""

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.records: dict[str, FileRecord] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------

    def _dir(self, file_id: str) -> Path:
        return self.base_dir / file_id

    def _chunks_dir(self, file_id: str) -> Path:
        return self._dir(file_id) / "chunks"

    def _part_path(self, file_id: str, seq: int) -> Path:
        return self._chunks_dir(file_id) / f"{seq:06d}.part"

    def reassembled_path(self, file_id: str) -> Path:
        return self._dir(file_id) / "reassembled.bin"

    def _meta_path(self, file_id: str) -> Path:
        return self._dir(file_id) / "meta.json"

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def add_upload(self, name: str, data: bytes,
                   chunk_size: int = CHUNK_SIZE) -> FileRecord:
        """Slice an uploaded blob into chunk part-files and record metadata."""
        total = len(data)
        if total == 0:
            raise ValueError("empty file")
        if total > MAX_FILE_BYTES:
            raise ValueError(
                f"file too large ({total} bytes > {MAX_FILE_BYTES})")
        with self._lock:
            if len(self.records) >= MAX_CONCURRENT_FILES:
                raise ValueError(
                    f"too many tracked files (>={MAX_CONCURRENT_FILES})")
            file_id = uuid.uuid4().hex[:12]
            while (self.base_dir / file_id).exists():
                file_id = uuid.uuid4().hex[:12]

            sha = hashlib.sha256(data).hexdigest()
            total_chunks = max(1, -(-total // chunk_size))
            rec = FileRecord(
                file_id=file_id, name=name, total_bytes=total,
                chunk_size=chunk_size, total_chunks=total_chunks, sha256=sha)

            chunks_dir = self._chunks_dir(file_id)
            chunks_dir.mkdir(parents=True, exist_ok=True)
            for seq in range(total_chunks):
                start = seq * chunk_size
                self._part_path(file_id, seq).write_bytes(
                    data[start:start + chunk_size])
            self._write_meta(rec)
            self.records[file_id] = rec
            logger.info(
                f"Stored upload '{name}' as {file_id}: {total} bytes, "
                f"{total_chunks} chunks, sha256={sha[:12]}…")
            return rec

    # ------------------------------------------------------------------
    # Transfer lifecycle (driven by WS messages)
    # ------------------------------------------------------------------

    def mark_transferring(self, file_id: str, src: str, dst: str) -> FileRecord | None:
        with self._lock:
            rec = self.records.get(file_id)
            if rec is None:
                return None
            rec.src = src
            rec.dst = dst
            if rec.state == ST_STORED:
                rec.state = ST_TRANSFERRING
            self._write_meta(rec)
            return rec

    def cancel(self, file_id: str) -> FileRecord | None:
        with self._lock:
            rec = self.records.get(file_id)
            if rec is None:
                return None
            if rec.state in (ST_TRANSFERRING, ST_STORED):
                rec.state = ST_CANCELLED
                self._write_meta(rec)
            return rec

    def on_chunk_delivered(self, file_id: str, seq: int) -> FileRecord | None:
        """Record one DES chunk delivery; reassemble + verify when complete."""
        with self._lock:
            rec = self.records.get(file_id)
            if rec is None:
                return None
            if rec.state not in (ST_TRANSFERRING, ST_STORED):
                return rec
            if 0 <= seq < rec.total_chunks:
                rec.received.add(seq)
            if len(rec.received) >= rec.total_chunks:
                self._reassemble_locked(rec)
            return rec

    def on_complete(self, file_id: str) -> FileRecord | None:
        """The core signalled all chunks delivered — ensure reassembly ran."""
        with self._lock:
            rec = self.records.get(file_id)
            if rec is None:
                return None
            if rec.state == ST_TRANSFERRING and \
                    len(rec.received) >= rec.total_chunks:
                self._reassemble_locked(rec)
            return rec

    def _reassemble_locked(self, rec: FileRecord) -> None:
        """Concatenate delivered part-files and verify the SHA-256 (lock held)."""
        out_path = self.reassembled_path(rec.file_id)
        h = hashlib.sha256()
        try:
            with open(out_path, "wb") as out:
                for seq in range(rec.total_chunks):
                    part = self._part_path(rec.file_id, seq)
                    blob = part.read_bytes()
                    out.write(blob)
                    h.update(blob)
        except OSError as e:
            logger.error(f"Reassembly failed for {rec.file_id}: {e}")
            rec.state = ST_FAILED
            self._write_meta(rec)
            return
        digest = h.hexdigest()
        rec.reassembled_sha256 = digest
        if digest == rec.sha256:
            rec.state = ST_COMPLETE
            rec.completed_at = time.time()
            logger.info(
                f"File {rec.file_id} ('{rec.name}') reassembled & verified "
                f"({rec.total_bytes} bytes, sha256={digest[:12]}…)")
        else:
            rec.state = ST_FAILED
            logger.error(
                f"File {rec.file_id} hash MISMATCH: upload={rec.sha256[:12]}… "
                f"reassembled={digest[:12]}…")
        self._write_meta(rec)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get(self, file_id: str) -> FileRecord | None:
        return self.records.get(file_id)

    def list(self) -> list[dict[str, Any]]:
        return [r.public_dict() for r in self.records.values()]

    def delete(self, file_id: str) -> bool:
        import shutil
        with self._lock:
            rec = self.records.pop(file_id, None)
            if rec is None:
                return False
        d = self._dir(file_id)
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
        logger.info(f"Deleted file {file_id}")
        return True

    # ------------------------------------------------------------------
    # Persistence (metadata only; chunks live on disk)
    # ------------------------------------------------------------------

    def _write_meta(self, rec: FileRecord) -> None:
        meta = rec.public_dict()
        try:
            self._meta_path(rec.file_id).write_text(
                json.dumps(meta, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except OSError as e:
            logger.error(f"Failed to write meta for {rec.file_id}: {e}")
