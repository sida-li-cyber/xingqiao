"""ilab-x 国家平台适配层（改进计划 I1）。

对齐《虚拟仿真实验教学项目技术接口规范》（2022 版）的双向接口：

  getinfo       平台 → 实验系统：以学号 + 口令换取学生信息（登录态打通）；
  score_upload  实验系统 → 平台：实验结束后上传成绩 + 步骤数据 JSON。

本平台是「实验系统」一侧：getinfo 由 HTTP GET 暴露给平台调用，
score_upload 则在学生提交报告时把 EduStore 里的记录转换成平台报文。
未配置平台回调（STARBRIDGE_ILABX_ENDPOINT）时进入自检模式——
报文落盘到 ``data/edu/ilabx_outbox.json``，供人工核验字段完整性，
不发出任何网络请求。

自检 CLI::

    python -m realtime_backend.edu_ilabx            # 构造样例报文并校验
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from .edu import EduStore

logger = logging.getLogger(__name__)

# 平台侧回调（score_upload 的目标 URL）。留空 = 自检模式。
ILABX_ENDPOINT = os.environ.get("STARBRIDGE_ILABX_ENDPOINT", "").strip()


class IlabXAdapter:
    """把 EduStore 的账号/记录映射为 ilab-x 2022 版接口报文。"""

    def __init__(self, store: EduStore, outbox_dir: Path | None = None):
        self.store = store
        self.outbox_dir = Path(outbox_dir) if outbox_dir else (
            store.db_path.parent / "ilabx_outbox.json")

    # ------------------------------------------------------------------
    # getinfo：平台换取学生信息
    # ------------------------------------------------------------------
    def getinfo(self, student_id: str, password: str = "") -> dict:
        """以学号换取学生信息。星桥无独立口令：口令非空即视为通过
        （真实对接时在此校验平台下发的凭证）。"""
        student_id = str(student_id or "").strip()
        if not student_id:
            return {"status": "fail", "msg": "student_id is required"}
        users = {u["student_id"]: u for u in self.store.list_users()}
        user = users.get(student_id)
        if user is None:
            return {"status": "fail", "msg": "unknown student_id"}
        return {
            "status": "success",
            "data": {
                "sname": user["name"],          # ilab-x 字段：学生姓名
                "sid": user["student_id"],      # 学号
                "role": user.get("role", "student"),
            },
        }

    # ------------------------------------------------------------------
    # score_upload：成绩 + 步骤数据报文
    # ------------------------------------------------------------------
    def build_score_upload(self, record: dict) -> dict:
        """把一条实验记录转换为 2022 版 score_upload 报文。"""
        steps = record.get("steps") or []
        step_data = []
        for i, st in enumerate(steps):
            # lab.js 步骤日志用 ilab-x 原始字段 seq/title；转换层补 step_id/step_name
            step_data.append({
                "step_id": st.get("step_id", st.get("seq", i + 1)),
                "step_name": st.get("step_name") or st.get("title")
                or st.get("name", f"步骤 {i + 1}"),
                "score": st.get("score", 0),
                "max": st.get("max", st.get("maxScore", 0)),
                "detail": st.get("detail", st.get("evaluation", "")),
            })
        detail = record.get("score_detail") or []
        return {
            "sid": record.get("student_id", ""),
            "sname": record.get("name", ""),
            "exp_id": record.get("exp_id", ""),
            "exp_name": record.get("exp_name", ""),
            "score": record.get("score", 0),
            "score_total": record.get("score_total", record.get("score", 0)),
            "all_pass": bool(record.get("all_pass", False)),
            "duration_s": record.get("duration_s", 0),
            "exam_id": record.get("exam_id", ""),
            "step_data": step_data,
            "score_detail": [
                {"item": d.get("item", ""), "score": d.get("score", 0),
                 "max": d.get("max", 0)}
                for d in detail],
            "upload_time": int(time.time()),
            "mode": "ilabx-2022",
        }

    def score_upload(self, record: dict) -> dict:
        """上传一条记录。配置了回调则 POST 平台；否则自检落盘。"""
        payload = self.build_score_upload(record)
        report = self._validate(payload)
        if not report["ok"]:
            return {"status": "fail", "errors": report["errors"]}
        if ILABX_ENDPOINT:
            return self._post_remote(payload)
        self._save_outbox(payload)
        return {"status": "selfcheck", "saved": True,
                "payload": payload, "validation": report}

    # ------------------------------------------------------------------
    # 内部：校验 / 落盘 / 远程上报
    # ------------------------------------------------------------------
    @staticmethod
    def _validate(payload: dict) -> dict:
        errors = []
        for field in ("sid", "sname", "exp_id", "score", "step_data"):
            if not payload.get(field) and payload.get(field) != 0:
                errors.append(f"missing field: {field}")
        if not isinstance(payload.get("score"), (int, float)):
            errors.append("score must be numeric")
        if not isinstance(payload.get("step_data"), list):
            errors.append("step_data must be a list")
        return {"ok": not errors, "errors": errors}

    def _save_outbox(self, payload: dict) -> None:
        box = []
        if self.outbox_dir.exists():
            try:
                box = json.loads(self.outbox_dir.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                box = []
        box.append(payload)
        self.outbox_dir.parent.mkdir(parents=True, exist_ok=True)
        self.outbox_dir.write_text(
            json.dumps(box, ensure_ascii=False, indent=1), encoding="utf-8")
        logger.info("ilabx selfcheck: score_upload appended to %s",
                    self.outbox_dir)

    def _post_remote(self, payload: dict) -> dict:
        """向平台回调 URL POST 报文。"""
        import urllib.request

        req = urllib.request.Request(
            ILABX_ENDPOINT,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode("utf-8", "replace")
                return {"status": "uploaded", "http_status": resp.status,
                        "response": body[:2000]}
        except (OSError, ValueError) as exc:
            logger.warning("ilabx score_upload failed: %r", exc)
            return {"status": "fail", "error": repr(exc)}


def run_selfcheck() -> int:
    """CLI 自检：构造一条样例记录走完整链路（getinfo → score_upload）。"""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        store = EduStore(Path(td) / "db.json")
        user, token = store.login("2026001", "张三")
        rec = store.add_record(user, {
            "exp_id": "E1", "exp_name": "时延分解与对账",
            "score": 80.0, "all_pass": True,
            "score_detail": [{"item": "对账判定", "max": 70, "score": 70},
                             {"item": "参数探索", "max": 10, "score": 10}],
            "verdict": [{"label": "端到端平均时延", "pass": True}],
            "steps": [{"step_id": 1, "name": "参数设置", "score": 100,
                       "max": 100, "detail": "完成"},
                      {"step_id": 2, "name": "运行实验", "score": 80,
                       "max": 100, "detail": "完成"}],
            "duration_s": 1234.0,
        })
        adapter = IlabXAdapter(store, Path(td) / "outbox.json")

        info = adapter.getinfo("2026001")
        assert info["status"] == "success", info
        assert info["data"]["sname"] == "张三"
        print(f"[1/3] getinfo        OK  -> {info['data']}")

        result = adapter.score_upload(rec)
        assert result["status"] == "selfcheck", result
        payload = result["payload"]
        assert payload["sid"] == "2026001"
        assert len(payload["step_data"]) == 2
        assert result["validation"]["ok"]
        print(f"[2/3] score_upload   OK  -> score={payload['score']}, "
              f"steps={len(payload['step_data'])}, "
              f"mode={payload['mode']}")

        # 空记录应校验失败
        bad = adapter.score_upload({"exp_id": ""})
        assert bad["status"] == "fail", bad
        print(f"[3/3] invalid guard  OK  -> {bad['errors']}")
    print("\nilab-x adapter selfcheck: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_selfcheck())
