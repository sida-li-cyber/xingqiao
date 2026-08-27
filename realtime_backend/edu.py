"""教学数据存取（改进计划阶段二）：轻量账号 + 实验记录服务端存档。

单文件 JSON 持久化（``data/edu/db.json``），不引入数据库服务，
维持「一键启动」承诺。结构：

  users:   {student_id: {name, role, created}}
  tokens:  {token: student_id}          # 会话令牌（重启后仍有效）
  records: [{id, student_id, name, exp_id, exp_name, ts, status,
             score, score_detail, verdict, conclusion, params_used,
             steps, quiz, questions, review,
             attempts: [{params, score, all_pass, ts, metrics}]}]

记录状态流转：draft（自动存档）→ submitted（学生提交报告）；
教师可对记录填写评语与主观分（review），总评 = (自动分 + 主观分)/2，
未批改时总评即自动分。
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

ROLE_STUDENT = "student"
ROLE_TEACHER = "teacher"

ST_DRAFT = "draft"
ST_SUBMITTED = "submitted"


class EduStore:
    """线程安全（锁保护的读改写回）的教学数据仓库。"""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._lock = threading.Lock()
        self._db = {"users": {}, "tokens": {}, "records": []}
        self._load()

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
    def _load(self) -> None:
        if self.db_path.exists():
            try:
                self._db = json.loads(self.db_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass  # 损坏则从空库开始，避免启动失败
        self._db.setdefault("users", {})
        self._db.setdefault("tokens", {})
        self._db.setdefault("records", [])
        self._db.setdefault("exams", [])
        self._db.setdefault("roster", [])

    def _flush(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.db_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self._db, ensure_ascii=False, indent=1),
            encoding="utf-8")
        tmp.replace(self.db_path)

    # ------------------------------------------------------------------
    # 账号
    # ------------------------------------------------------------------
    def login(self, student_id: str, name: str,
              role: str = ROLE_STUDENT) -> tuple[dict, str]:
        """注册或登录，返回 (user, token)。学号即账号，姓名可更新。"""
        student_id = str(student_id).strip()
        name = str(name).strip()
        if not student_id or not name:
            raise ValueError("student_id and name are required")
        if role not in (ROLE_STUDENT, ROLE_TEACHER):
            role = ROLE_STUDENT
        with self._lock:
            user = self._db["users"].get(student_id)
            if user is None:
                user = {"student_id": student_id, "name": name,
                        "role": role, "created": int(time.time())}
                self._db["users"][student_id] = user
            else:
                user["name"] = name           # 允许改名
            token = uuid.uuid4().hex
            self._db["tokens"][token] = student_id
            self._flush()
        return dict(user), token

    def user_of_token(self, token: str) -> dict | None:
        with self._lock:
            sid = self._db["tokens"].get(token or "")
            if not sid:
                return None
            user = self._db["users"].get(sid)
            return dict(user) if user else None

    def list_users(self) -> list[dict]:
        with self._lock:
            return [dict(u) for u in self._db["users"].values()]

    # ------------------------------------------------------------------
    # 实验记录
    # ------------------------------------------------------------------
    def add_record(self, user: dict, payload: dict) -> dict:
        """保存一次实验的完整记录（参数/对账/评分/步骤日志/作答）。

        payload 携带 ``attempt``（run_experiment 结果中的本次运行摘要）
        时追加到该记录的 ``attempts[]``；旧调用方不带此字段时为空列表。
        """
        exp_id = str(payload.get("exp_id") or "").strip()
        if not exp_id:
            raise ValueError("exp_id is required")
        rec = {
            "id": uuid.uuid4().hex[:12],
            "student_id": user["student_id"],
            "name": user.get("name", ""),
            "exp_id": exp_id,
            "exp_name": str(payload.get("exp_name", "")),
            "ts": int(time.time()),
            "status": ST_DRAFT,
            "score": float(payload.get("score", 0) or 0),
            "all_pass": bool(payload.get("all_pass", False)),
            "score_detail": payload.get("score_detail") or [],
            "verdict": payload.get("verdict") or [],
            "conclusion": str(payload.get("conclusion", "")),
            "params_used": payload.get("params_used") or {},
            "steps": payload.get("steps") or [],
            "quiz": payload.get("quiz") or None,
            "questions": payload.get("questions") or {},
            "duration_s": float(payload.get("duration_s", 0) or 0),
            "exam_id": str(payload.get("exam_id", "")),
            "review": None,
            "attempts": [],
        }
        attempt = payload.get("attempt")
        if isinstance(attempt, dict):
            rec["attempts"].append(attempt)
        with self._lock:
            self._db["records"].append(rec)
            self._flush()
        return _with_attempts(rec)

    def _find(self, rec_id: str) -> dict | None:
        for rec in self._db["records"]:
            if rec["id"] == rec_id:
                return rec
        return None

    def get_record(self, rec_id: str) -> dict | None:
        with self._lock:
            rec = self._find(rec_id)
            return _with_attempts(rec) if rec else None

    def records_of(self, student_id: str) -> list[dict]:
        with self._lock:
            return [_with_attempts(r) for r in self._db["records"]
                    if r["student_id"] == student_id]

    def all_records(self) -> list[dict]:
        with self._lock:
            return [_with_attempts(r) for r in self._db["records"]]

    def submit_record(self, rec_id: str, student_id: str) -> dict | None:
        with self._lock:
            rec = self._find(rec_id)
            if rec is None or rec["student_id"] != student_id:
                return None
            rec["status"] = ST_SUBMITTED
            self._flush()
            return dict(rec)

    def review_record(self, rec_id: str, comment: str,
                      teacher_score: float | None) -> dict | None:
        """教师批改：评语 + 主观分（0-100，可为空仅评语）。"""
        with self._lock:
            rec = self._find(rec_id)
            if rec is None:
                return None
            ts = None
            if teacher_score is not None:
                ts = max(0.0, min(100.0, float(teacher_score)))
            rec["review"] = {"comment": str(comment or ""),
                             "teacher_score": ts,
                             "ts": int(time.time())}
            self._flush()
            return dict(rec)

    # ------------------------------------------------------------------
    # 成绩册与应用统计（教师端 / 申报佐证）
    # ------------------------------------------------------------------
    def gradebook(self) -> list[dict]:
        """每学生 × 实验取最佳记录（总评最高），展开为扁平行。"""
        with self._lock:
            best: dict[tuple[str, str], dict] = {}
            for rec in self._db["records"]:
                key = (rec["student_id"], rec["exp_id"])
                final = self._final_score(rec)
                if key not in best or final > self._final_score(best[key]):
                    best[key] = rec
            rows = []
            for (sid, exp_id), rec in sorted(best.items()):
                rows.append({
                    "student_id": sid,
                    "name": rec.get("name", ""),
                    "exp_id": exp_id,
                    "exp_name": rec.get("exp_name", ""),
                    "auto_score": rec.get("score", 0),
                    "final_score": self._final_score(rec),
                    "all_pass": rec.get("all_pass", False),
                    "status": rec.get("status", ST_DRAFT),
                    "record_id": rec["id"],
                    "ts": rec.get("ts", 0),
                    "exam_id": rec.get("exam_id", ""),
                    "review": rec.get("review"),
                })
            return rows

    @staticmethod
    def _final_score(rec: dict) -> float:
        auto = float(rec.get("score", 0) or 0)
        review = rec.get("review") or {}
        t = review.get("teacher_score")
        if t is None:
            return round(auto, 1)
        return round((auto + float(t)) / 2.0, 1)

    def stats(self) -> dict:
        """应用数据统计：次数 / 时长 / 平均分 / 提交率 / 成绩分布 /
        对账误差热点（申报佐证材料）。"""
        with self._lock:
            recs = self._db["records"]
            n = len(recs)
            students = {r["student_id"] for r in recs}
            per_exp: dict[str, list[float]] = {}
            for r in recs:
                per_exp.setdefault(r["exp_id"], []).append(
                    float(r.get("score", 0) or 0))
            # 成绩分布（总分）—— I2 补全
            scores = [self._final_score(r) for r in recs]
            dist = {"90-100": 0, "80-89": 0, "70-79": 0,
                    "60-69": 0, "<60": 0}
            for sc in scores:
                if sc >= 90:
                    dist["90-100"] += 1
                elif sc >= 80:
                    dist["80-89"] += 1
                elif sc >= 70:
                    dist["70-79"] += 1
                elif sc >= 60:
                    dist["60-69"] += 1
                else:
                    dist["<60"] += 1
            # 对账误差热点：按实验 × 判据统计失败次数 —— I2 补全
            hotspots: dict[str, list] = {}
            for r in recs:
                for v in (r.get("verdict") or []):
                    label = v.get("label", "")
                    if not v.get("pass"):
                        hotspots.setdefault(r["exp_id"], []).append(label)
            hotspot_rows = [
                {"exp_id": eid, "fail_count": len(labels),
                 "top_labels": _top_labels(labels)}
                for eid, labels in sorted(hotspots.items())
                if labels]
            return {
                "total_records": n,
                "total_students": len(students),
                "total_duration_s": round(
                    sum(float(r.get("duration_s", 0) or 0) for r in recs), 1),
                "submitted": sum(1 for r in recs
                                 if r.get("status") == ST_SUBMITTED),
                "avg_score": round(sum(scores) / n, 1) if n else 0.0,
                "score_distribution": dist,
                "error_hotspots": hotspot_rows,
                "per_experiment": {
                    exp: {"runs": len(sc),
                          "avg_score": round(sum(sc) / len(sc), 1),
                          "pass_rate": round(
                              sum(1 for r in recs
                                  if r["exp_id"] == exp and r.get("all_pass"))
                              / len(sc), 3)}
                    for exp, sc in sorted(per_exp.items())
                },
            }

    # ------------------------------------------------------------------
    # 班级名单（S4）：教师粘贴名单 → 仅保留学号用于校验/过滤
    # ------------------------------------------------------------------
    def set_roster(self, student_ids: list[str]) -> dict:
        """保存班级名单（学号列表），用于成绩册过滤与缺交统计。"""
        with self._lock:
            self._db["roster"] = sorted(set(str(s).strip()
                                            for s in student_ids if str(s).strip()))
            self._flush()
            return {"roster": list(self._db["roster"])}

    def get_roster(self) -> list[str]:
        with self._lock:
            return list(self._db.get("roster", []))

    def roster_stats(self) -> dict:
        """班级名单对照：已提交 / 缺交 / 平均分。"""
        with self._lock:
            roster = list(self._db.get("roster", []))
            if not roster:
                return {"roster_size": 0}
            done = set()
            total_score = 0.0
            n_done = 0
            for rec in self._db["records"]:
                sid = rec["student_id"]
                if sid in roster and rec["student_id"] not in done:
                    if rec.get("status") == ST_SUBMITTED:
                        done.add(sid)
                        total_score += self._final_score(rec)
                        n_done += 1
            roster_set = set(roster)
            missing = sorted(roster_set - done)
            return {
                "roster_size": len(roster),
                "submitted": n_done,
                "missing": missing,
                "avg_score": round(total_score / n_done, 1) if n_done else 0.0,
            }

    # ------------------------------------------------------------------
    # 考核模式（I4）：教师创建考试 → 核心种子 → 学生限时完成
    # ------------------------------------------------------------------
    def create_exam(self, name: str, exp_ids: list[str],
                    duration_s: int, seed: int,
                    params: dict | None = None) -> dict:
        """创建一场考核：冻结参数集 + 固定种子 + 限时。"""
        with self._lock:
            exam = {"id": uuid.uuid4().hex[:10], "name": str(name),
                    "exp_ids": list(exp_ids), "duration_s": int(duration_s),
                    "seed": int(seed),
                    "params": dict(params) if params else None,
                    "created": int(time.time()),
                    "active": True}
            self._db.setdefault("exams", []).append(exam)
            self._flush()
            return dict(exam)

    def get_exam(self, exam_id: str) -> dict | None:
        with self._lock:
            for ex in self._db.get("exams", []):
                if ex["id"] == exam_id:
                    return dict(ex)
            return None

    def list_exams(self) -> list[dict]:
        with self._lock:
            return [dict(ex) for ex in self._db.get("exams", [])]

    def end_exam(self, exam_id: str) -> dict | None:
        with self._lock:
            for ex in self._db.get("exams", []):
                if ex["id"] == exam_id:
                    ex["active"] = False
                    self._flush()
                    return dict(ex)
            return None


def _top_labels(labels: list[str]) -> list[dict]:
    """统计失败判据出现次数，返回 Top 3。"""
    from collections import Counter
    c = Counter(labels)
    return [{"label": lbl, "count": cnt} for lbl, cnt in c.most_common(3)]


def _with_attempts(rec: dict) -> dict:
    """旧记录无 attempts 字段时按空列表返回（向后兼容）。"""
    out = dict(rec)
    out.setdefault("attempts", [])
    return out
