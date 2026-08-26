"""教学数据面测试（阶段二 S1-S4 + 阶段三 I2/S4/I4）。

对真实后端进程做 HTTP 全链路验证（模式同 test_file_e2e.py，但不需要仿真核心）：

  1.  学生注册/登录 -> token；无 token 访问被 401 拒绝
  2.  实验记录服务端存档（对账表 + 步骤日志 + 评分明细），登录后拉回
  3.  学生提交报告 -> 状态 submitted
  4.  教师口令错误 403；正确口令登录教师端
  5.  教师批改（评语 + 主观分）-> 总评 = (自动分 + 主观分) / 2
  6.  成绩册（每学生×实验取最佳记录）与 CSV 导出
  7.  应用统计（次数 / 时长 / 平均分 / 成绩分布 / 误差热点）
  8.  班级名单：文本导入 → 缺交统计 → 清空
  9.  考核模式：教师创建 → 学生可见 → 记录绑定 exam_id → 结束后不可见

Run:  python tests/test_edu_api.py        (~10 s, spawns backend only)
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parent.parent             # dayilixiang-v3/

# 系统代理会劫持对 127.0.0.1 的 HTTP 请求（502），测试内全程旁路。
os.environ.setdefault("no_proxy", "127.0.0.1,localhost")
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")

HOST = "127.0.0.1"
PORT = 8771
HTTP = f"http://{HOST}:{PORT}"


def _req(path, method="GET", body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Edu-Token"] = token
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(HTTP + path, data=data, headers=headers,
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            raw = r.read()
            ct = r.headers.get("Content-Type", "")
            return r.status, (raw if "csv" in ct or "octet" in ct
                              else json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def start_backend(db_path):
    env = dict(os.environ)
    env["STARBRIDGE_EDU_DB"] = str(db_path)
    env["no_proxy"] = env["NO_PROXY"] = "127.0.0.1,localhost"
    return subprocess.Popen(
        [sys.executable, "-m", "realtime_backend.run",
         "--host", HOST, "--port", str(PORT), "--log-level", "warning"],
        cwd=str(PROJECT_ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def wait_health(timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{HTTP}/health", timeout=2) as r:
                if json.loads(r.read()).get("status") == "ok":
                    return
        except Exception:
            time.sleep(0.3)
    raise RuntimeError("backend did not become healthy")


def make_steps():
    """步骤日志样例：字段对齐 ilab-x 2022 版步骤数据结构。"""
    now = int(time.time() * 1000)
    return [
        {"seq": 1, "title": "预习测验", "startTime": now - 60000,
         "endTime": now - 40000, "timeUsed": 20, "maxScore": 10,
         "score": 10.0, "repeatCount": 1,
         "scoringModel": "考察点：核心概念与理论公式",
         "evaluation": "测验得分 10/10"},
        {"seq": 2, "title": "参数设置", "startTime": now - 40000,
         "endTime": now - 30000, "timeUsed": 10, "maxScore": 10,
         "score": 10, "repeatCount": 2,
         "scoringModel": "考察点：参数影响理解", "evaluation": "非默认参数"},
        {"seq": 3, "title": "仿真运行", "startTime": now - 30000,
         "endTime": now - 5000, "timeUsed": 25, "maxScore": 70,
         "score": 70.0, "repeatCount": 1,
         "scoringModel": "考察点：理论-实测对账", "evaluation": "对账全部通过"},
    ]


def main():
    db = Path(tempfile.mkdtemp(prefix="starbridge_edu_test_")) / "db.json"
    proc = start_backend(db)
    try:
        wait_health()

        sid = "S" + uuid.uuid4().hex[:8]
        tid = "T" + uuid.uuid4().hex[:8]

        # ---- S1 学生登录 + 无鉴权拒绝 ----
        code, data = _req("/api/edu/records")
        assert code == 401, f"unauthenticated should be 401, got {code}"

        code, data = _req("/api/edu/login", "POST",
                          {"student_id": sid, "name": "测试学生"})
        assert code == 200 and data["token"], data
        tok = data["token"]
        assert data["user"]["role"] == "student"

        code, data = _req("/api/edu/me", token=tok)
        assert code == 200 and data["user"]["student_id"] == sid

        # ---- S2 服务端存档（对账表 + 步骤日志 + 评分明细） ----
        record_payload = {
            "exp_id": "E2", "exp_name": "M/D/1 排队模型",
            "score": 80.0, "all_pass": True,
            "score_detail": [
                {"item": "对账判定", "max": 70, "score": 70.0},
                {"item": "参数探索", "max": 10, "score": 10},
            ],
            "verdict": [{"label": "端到端平均时延", "theory": 24.0,
                         "measured": 23.6, "unit": "ms", "pass": True}],
            "conclusion": "Wq 理论 12.0 ms；实测吻合。",
            "params_used": {"rho": 0.8, "window_s": 120},
            "steps": make_steps(),
            "quiz": {"score": 10.0, "max": 10, "n_correct": 3, "n_total": 3},
            "questions": {"0": "预热消除瞬态偏差", "1": "发散", "2": ""},
            "duration_s": 95,
        }
        code, data = _req("/api/edu/records", "POST", record_payload, tok)
        assert code == 200 and data["status"] == "saved", data
        rec_id = data["record"]["id"]
        assert data["record"]["status"] == "draft"
        # 步骤日志原样落库（ilab-x 字段结构）
        assert data["record"]["steps"][2]["maxScore"] == 70

        code, data = _req("/api/edu/records", token=tok)
        assert code == 200
        assert any(r["id"] == rec_id for r in data["records"]), \
            "登录后可恢复存档（2020 规范）"

        # ---- S3 学生提交报告 ----
        code, data = _req(f"/api/edu/records/{rec_id}/submit", "POST", {}, tok)
        assert code == 200 and data["record"]["status"] == "submitted"

        # ---- S1/S4 教师口令校验 ----
        code, _ = _req("/api/edu/login", "POST",
                       {"student_id": tid, "name": "测试教师",
                        "role": "teacher", "teacher_code": "wrong"})
        assert code == 403, f"wrong teacher code should be 403, got {code}"

        code, data = _req("/api/edu/login", "POST",
                          {"student_id": tid, "name": "测试教师",
                           "role": "teacher", "teacher_code": "starbridge"})
        assert code == 200, data
        ttok = data["token"]

        # 学生不得访问成绩册
        code, _ = _req("/api/edu/gradebook", token=tok)
        assert code == 403

        # ---- S3 教师批改 -> 总评 = (自动分 + 主观分)/2 ----
        code, data = _req(f"/api/edu/records/{rec_id}/review", "POST",
                          {"comment": "对账严谨，结论清晰", "teacher_score": 90},
                          ttok)
        assert code == 200 and data["record"]["review"]["teacher_score"] == 90

        # ---- S4 成绩册与 CSV 导出 ----
        code, data = _req("/api/edu/gradebook", token=ttok)
        assert code == 200
        row = next(r for r in data["rows"] if r["record_id"] == rec_id)
        assert row["auto_score"] == 80.0
        assert row["final_score"] == 85.0          # (80 + 90) / 2
        assert row["status"] == "submitted"

        code, csv_bytes = _req("/api/edu/gradebook.csv", token=ttok)
        assert code == 200
        assert sid.encode() in csv_bytes

        # ---- 应用统计（I2：申报佐证字段 + 成绩分布 + 误差热点） ----
        code, data = _req("/api/edu/stats", token=ttok)
        assert code == 200
        assert data["total_records"] >= 1
        assert data["per_experiment"]["E2"]["runs"] >= 1
        dist = data["score_distribution"]
        assert sum(dist.values()) == data["total_records"]
        assert dist["80-89"] == 1              # final 85.0 → 80-89 段
        hot = data["error_hotspots"]
        assert hot == []                       # 该记录对账全通过
        # 一条对账失败记录 → 热点出现
        code, data = _req("/api/edu/records", "POST", {
            "exp_id": "E1", "exp_name": "时延分解与对账", "score": 40.0,
            "all_pass": False, "duration_s": 60,
            "verdict": [{"label": "端到端平均时延", "pass": False}],
        }, tok)
        assert code == 200
        code, data = _req("/api/edu/stats", token=ttok)
        hot = {h["exp_id"]: h for h in data["error_hotspots"]}
        assert hot["E1"]["fail_count"] == 1
        assert hot["E1"]["top_labels"][0]["label"] == "端到端平均时延"

        # ---- 班级名单（S4）：导入 → 缺交统计 → 清空 ----
        code, data = _req("/api/edu/roster", "POST",
                          {"text": f"{sid} 测试学生\nS999X 李四\n, ,\n"},
                          ttok)
        assert code == 200 and len(data["roster"]) == 2, data
        code, data = _req("/api/edu/roster", token=ttok)
        st = data["stats"]
        assert st["roster_size"] == 2
        assert st["missing"] == ["S999X"]       # sid 已提交；李四缺交
        assert st["submitted"] == 1
        code, _ = _req("/api/edu/roster", token=tok)     # 学生无权看名单
        assert code == 403
        code, data = _req("/api/edu/roster", "DELETE", {}, ttok)
        assert code == 200 and data["roster"] == []

        # ---- 考核模式（I4）：创建 → 学生可见 → 记录绑定 → 结束 ----
        code, _ = _req("/api/edu/exams", "POST",
                       {"name": "期末实测", "exp_ids": []}, ttok)
        assert code == 400                     # 空 exp_ids 拒绝
        code, data = _req("/api/edu/exams", "POST",
                          {"name": "期末实测", "exp_ids": ["E1", "E2"],
                           "duration_min": 45, "seed": 777,
                           "params": {"pkt_bytes": 6000}}, ttok)
        assert code == 200, data
        exam = data["exam"]
        assert exam["active"] and exam["duration_s"] == 2700

        code, data = _req("/api/edu/exams", token=tok)
        assert code == 200
        active = [e for e in data["exams"] if e["active"]]
        assert exam["id"] in [e["id"] for e in active]

        # 考核模式下学生产生的记录应携带 exam_id（成绩册可追溯）
        code, data = _req("/api/edu/records", "POST", {
            "exp_id": "E1", "exp_name": "时延分解与对账",
            "score": 90.0, "all_pass": True, "duration_s": 120,
            "exam_id": exam["id"],
        }, tok)
        assert code == 200 and data["record"]["exam_id"] == exam["id"]
        code, data = _req("/api/edu/gradebook", token=ttok)
        exam_row = next(r for r in data["rows"] if r["exam_id"] == exam["id"])
        assert exam_row["auto_score"] == 90.0

        code, data = _req(f"/api/edu/exams/{exam['id']}/end", "POST", {},
                          ttok)
        assert code == 200 and not data["exam"]["active"]
        code, data = _req("/api/edu/exams", token=tok)
        assert exam["id"] not in [e["id"] for e in data["exams"]
                                  if e["active"]]

        # 教师可看全部记录；他人记录对学生不可见
        code, data = _req(f"/api/edu/records/{rec_id}", token=tok)
        assert code == 200
        other = "S" + uuid.uuid4().hex[:8]
        code, data = _req("/api/edu/login", "POST",
                          {"student_id": other, "name": "他人"})
        code, data = _req(f"/api/edu/records/{rec_id}", token=data["token"])
        assert code == 403

        print("EDU-API  all checks passed "
              f"(record {rec_id}, final=85.0, exam={exam['id']}, db={db})")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
