"""HTTP endpoints for the education data plane (improvement plan phase 2).

Mounted under ``/api/edu`` by ``main.py``:

  POST /api/edu/login                  student_id + name (+role/teacher_code) -> token
  GET  /api/edu/me                     current user (X-Edu-Token)
  POST /api/edu/records                save one experiment record (student)
  GET  /api/edu/records                my records (student) / all (teacher)
  GET  /api/edu/records/{id}           one record
  POST /api/edu/records/{id}/submit    student submits the report
  POST /api/edu/records/{id}/review    teacher comment + subjective score
  GET  /api/edu/gradebook              teacher: best-record matrix
  GET  /api/edu/gradebook.csv          teacher: CSV export
  GET  /api/edu/stats                  teacher: usage statistics
  POST /api/edu/roster                 teacher: import class roster (text)
  GET  /api/edu/roster                 teacher: roster + missing stats
  DELETE /api/edu/roster               teacher: clear roster
  POST /api/edu/exams                  teacher: create exam (exp set + timer + seed)
  GET  /api/edu/exams                  active exams (all: full history)
  POST /api/edu/exams/{id}/end         teacher: close an exam

Auth is a bearer-style ``X-Edu-Token`` header issued at login; identity is
intentionally lightweight (student id + name) for classroom use, matching the
"one-click start" promise. Teacher role additionally requires TEACHER_CODE.
"""
from __future__ import annotations

import csv
import io
import logging
import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .edu import ROLE_TEACHER, EduStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/edu", tags=["edu"])

# 教师身份口令（部署时可经环境变量覆盖；默认值供单机课堂使用）。
TEACHER_CODE = os.environ.get("STARBRIDGE_TEACHER_CODE", "starbridge")

_store: EduStore | None = None


def init_edu_router(store: EduStore) -> None:
    global _store
    _store = store


def _store_or_503() -> EduStore:
    if _store is None:
        raise HTTPException(status_code=503, detail="edu store not initialised")
    return _store


def _auth(request: Request) -> dict:
    token = request.headers.get("X-Edu-Token", "")
    user = _store_or_503().user_of_token(token)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid or missing token")
    return user


def _require_teacher(request: Request) -> dict:
    user = _auth(request)
    if user["role"] != ROLE_TEACHER:
        raise HTTPException(status_code=403, detail="teacher role required")
    return user


# ----------------------------------------------------------------------
# 账号
# ----------------------------------------------------------------------

class LoginBody(BaseModel):
    student_id: str
    name: str
    role: str = "student"
    teacher_code: str | None = None


@router.post("/login")
async def login(body: LoginBody) -> dict:
    store = _store_or_503()
    role = body.role or "student"
    if role == ROLE_TEACHER and body.teacher_code != TEACHER_CODE:
        raise HTTPException(status_code=403, detail="invalid teacher code")
    try:
        user, token = store.login(body.student_id, body.name, role)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"token": token, "user": user}


@router.get("/me")
async def me(request: Request) -> dict:
    return {"user": _auth(request)}


# ----------------------------------------------------------------------
# 实验记录（服务端存档，对齐 2020 建设规范「下次登录可恢复」）
# ----------------------------------------------------------------------

@router.post("/records")
async def add_record(request: Request) -> dict:
    user = _auth(request)
    payload = await request.json()
    try:
        rec = _store_or_503().add_record(user, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "saved", "record": rec}


@router.get("/records")
async def list_records(request: Request) -> dict:
    user = _auth(request)
    store = _store_or_503()
    if user["role"] == ROLE_TEACHER:
        return {"records": store.all_records()}
    return {"records": store.records_of(user["student_id"])}


@router.get("/records/{rec_id}")
async def get_record(rec_id: str, request: Request) -> dict:
    user = _auth(request)
    rec = _store_or_503().get_record(rec_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="record not found")
    if user["role"] != ROLE_TEACHER and rec["student_id"] != user["student_id"]:
        raise HTTPException(status_code=403, detail="not your record")
    return {"record": rec}


@router.post("/records/{rec_id}/submit")
async def submit_record(rec_id: str, request: Request) -> dict:
    user = _auth(request)
    rec = _store_or_503().submit_record(rec_id, user["student_id"])
    if rec is None:
        raise HTTPException(status_code=404, detail="record not found")
    return {"status": "submitted", "record": rec}


class ReviewBody(BaseModel):
    comment: str = ""
    teacher_score: float | None = None


@router.post("/records/{rec_id}/review")
async def review_record(rec_id: str, body: ReviewBody,
                        request: Request) -> dict:
    _require_teacher(request)
    rec = _store_or_503().review_record(rec_id, body.comment,
                                        body.teacher_score)
    if rec is None:
        raise HTTPException(status_code=404, detail="record not found")
    return {"status": "reviewed", "record": rec}


# ----------------------------------------------------------------------
# 成绩册与应用统计（教师端）
# ----------------------------------------------------------------------

@router.get("/gradebook")
async def gradebook(request: Request) -> dict:
    _require_teacher(request)
    return {"rows": _store_or_503().gradebook()}


@router.get("/gradebook.csv")
async def gradebook_csv(request: Request):
    _require_teacher(request)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["student_id", "name", "exp_id", "exp_name", "auto_score",
                "final_score", "all_pass", "status", "exam_id", "record_id",
                "ts"])
    for row in _store_or_503().gradebook():
        w.writerow([row["student_id"], row["name"], row["exp_id"],
                    row["exp_name"], row["auto_score"], row["final_score"],
                    row["all_pass"], row["status"], row.get("exam_id", ""),
                    row["record_id"], row["ts"]])
    data = buf.getvalue().encode("utf-8-sig")   # BOM 供 Excel 直接打开
    return StreamingResponse(
        io.BytesIO(data), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 "attachment; filename=starbridge_gradebook.csv"})


@router.get("/stats")
async def stats(request: Request) -> dict:
    _require_teacher(request)
    return _store_or_503().stats()


# ----------------------------------------------------------------------
# 班级名单（S4）：文本粘贴导入 + 缺交统计
# ----------------------------------------------------------------------

class RosterBody(BaseModel):
    text: str = ""


@router.post("/roster")
async def set_roster(body: RosterBody, request: Request) -> dict:
    _require_teacher(request)
    # 每行 "学号 姓名" 或纯学号；取首列为学号（逗号/Tab 先归一为空格）。
    ids = []
    for line in (body.text or "").splitlines():
        fields = (line.replace(",", " ").replace("\t", " ")).split()
        if fields:
            ids.append(fields[0])
    return _store_or_503().set_roster(ids)


@router.get("/roster")
async def get_roster(request: Request) -> dict:
    _require_teacher(request)
    store = _store_or_503()
    return {"roster": store.get_roster(), "stats": store.roster_stats()}


@router.delete("/roster")
async def clear_roster(request: Request) -> dict:
    _require_teacher(request)
    return _store_or_503().set_roster([])


# ----------------------------------------------------------------------
# 考核模式（I4）：教师创建考试（冻结实验 + 限时 + 种子）
# ----------------------------------------------------------------------

class ExamBody(BaseModel):
    name: str
    exp_ids: list[str]
    duration_min: float = 30
    seed: int = 1234
    params: dict | None = None          # 冻结参数集；None = 实验默认值


@router.post("/exams")
async def create_exam(body: ExamBody, request: Request) -> dict:
    _require_teacher(request)
    if not body.exp_ids:
        raise HTTPException(status_code=400, detail="exp_ids is required")
    if body.duration_min <= 0 or body.duration_min > 180:
        raise HTTPException(status_code=400,
                            detail="duration_min must be in (0, 180]")
    exam = _store_or_503().create_exam(
        body.name, body.exp_ids, int(body.duration_min * 60), body.seed,
        body.params)
    return {"status": "created", "exam": exam}


@router.get("/exams")
async def list_exams(request: Request) -> dict:
    user = _auth(request)          # 学生也要看进行中的考试
    exams = _store_or_503().list_exams()
    if user["role"] != ROLE_TEACHER:
        exams = [e for e in exams if e.get("active")]
    return {"exams": exams}


@router.post("/exams/{exam_id}/end")
async def end_exam(exam_id: str, request: Request) -> dict:
    _require_teacher(request)
    exam = _store_or_503().end_exam(exam_id)
    if exam is None:
        raise HTTPException(status_code=404, detail="exam not found")
    return {"status": "ended", "exam": exam}
