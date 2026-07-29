"""HTTP endpoints for the file-transfer data plane (Milestone A, phase B).

Mounted under ``/api/files`` by ``main.py``:

  POST   /api/files/upload          multipart upload -> stored + sliced
  GET    /api/files                 list tracked transfers (progress/state)
  GET    /api/files/{file_id}       one transfer's record
  GET    /api/files/{file_id}/download   verified reassembled bytes
  DELETE /api/files/{file_id}       forget + remove from disk

The actual network transfer is triggered separately over WebSocket
(``file_send`` / ``file_cancel`` commands), which the backend enriches with
``total_bytes`` / ``chunk_size`` and forwards to the simulation core.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .files import ST_COMPLETE, FileStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/files", tags=["files"])

# Bound to the shared FileStore instance in main.py via init_files_router().
_store: FileStore | None = None


def init_files_router(store: FileStore) -> None:
    global _store
    _store = store


def _store_or_503() -> FileStore:
    if _store is None:
        raise HTTPException(status_code=503, detail="file store not initialised")
    return _store


@router.post("/upload")
async def upload_file(file: UploadFile = File(...)) -> dict:
    """Store an uploaded file, sliced into simulation-sized chunks."""
    store = _store_or_503()
    data = await file.read()
    try:
        rec = store.add_upload(file.filename or "unnamed.bin", data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return rec.public_dict()


@router.get("")
async def list_files() -> dict:
    store = _store_or_503()
    return {"files": store.list()}


@router.get("/{file_id}")
async def get_file(file_id: str) -> dict:
    store = _store_or_503()
    rec = store.get(file_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="file not found")
    return rec.public_dict()


@router.get("/{file_id}/download")
async def download_file(file_id: str):
    """Serve the reassembled bytes once the transfer is verified complete."""
    store = _store_or_503()
    rec = store.get(file_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="file not found")
    if rec.state != ST_COMPLETE:
        raise HTTPException(
            status_code=409,
            detail=f"transfer not complete (state={rec.state})")
    path = store.reassembled_path(file_id)
    if not path.exists():
        raise HTTPException(status_code=410, detail="reassembled data missing")
    return FileResponse(
        path,
        filename=rec.name,
        media_type="application/octet-stream",
    )


@router.delete("/{file_id}")
async def delete_file(file_id: str) -> dict:
    store = _store_or_503()
    if not store.delete(file_id):
        raise HTTPException(status_code=404, detail="file not found")
    return {"status": "deleted", "file_id": file_id}
