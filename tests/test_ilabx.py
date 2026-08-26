"""I1 ilab-x 国家平台适配层验证（edu_ilabx.py）。

对齐《虚拟仿真实验教学项目技术接口规范》（2022 版）：
  getinfo        平台→实验系统：学号换学生信息；
  score_upload   实验系统→平台：成绩 + 步骤数据 JSON 报文；
  自检模式       未配置回调（STARBRIDGE_ILABX_ENDPOINT）时报文落盘 outbox，
                 不发任何网络请求；字段校验缺失即拒发。

Run:  pytest tests/test_ilabx.py -v     (~1 s)
"""
import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from realtime_backend.edu import EduStore          # noqa: E402
from realtime_backend.edu_ilabx import IlabXAdapter  # noqa: E402


@pytest.fixture()
def store(tmp_path):
    return EduStore(tmp_path / "db.json")


@pytest.fixture()
def adapter(store, tmp_path):
    return IlabXAdapter(store, tmp_path / "outbox.json")


def _mk_record(store, score=80.0, all_pass=True, steps=None):
    user, _ = store.login("2026001", "张三")
    return store.add_record(user, {
        "exp_id": "E2", "exp_name": "M/D/1 排队模型",
        "score": score, "all_pass": all_pass,
        "score_detail": [{"item": "对账判定", "max": 70, "score": 70}],
        "verdict": [{"label": "端到端平均时延", "pass": all_pass}],
        "steps": steps if steps is not None else [
            {"seq": 1, "title": "预习测验", "maxScore": 10, "score": 10.0},
            {"seq": 2, "title": "仿真运行", "maxScore": 70, "score": 70.0},
        ],
        "duration_s": 300.0,
        "exam_id": "exammock01",
    })


# ----------------------------------------------------------------------
# getinfo
# ----------------------------------------------------------------------

def test_getinfo_known_student(store, adapter):
    store.login("2026001", "张三")
    info = adapter.getinfo("2026001")
    assert info["status"] == "success"
    assert info["data"]["sname"] == "张三"
    assert info["data"]["sid"] == "2026001"


def test_getinfo_unknown_or_empty(store, adapter):
    assert adapter.getinfo("nobody")["status"] == "fail"
    assert adapter.getinfo("")["status"] == "fail"


# ----------------------------------------------------------------------
# score_upload（自检模式）
# ----------------------------------------------------------------------

def test_build_score_upload_payload(store, adapter):
    rec = _mk_record(store)
    p = adapter.build_score_upload(rec)
    assert p["sid"] == "2026001" and p["sname"] == "张三"
    assert p["exp_id"] == "E2" and p["score"] == 80.0
    assert p["exam_id"] == "exammock01"
    assert len(p["step_data"]) == 2
    assert p["step_data"][0]["step_name"] == "预习测验"
    assert p["mode"] == "ilabx-2022"


def test_selfcheck_mode_saves_outbox(store, adapter):
    rec = _mk_record(store)
    result = adapter.score_upload(rec)
    assert result["status"] == "selfcheck", result
    assert result["validation"]["ok"]
    box = json.loads(adapter.outbox_dir.read_text(encoding="utf-8"))
    assert len(box) == 1 and box[0]["sid"] == "2026001"
    # 第二次上传追加，不覆盖
    adapter.score_upload(rec)
    box = json.loads(adapter.outbox_dir.read_text(encoding="utf-8"))
    assert len(box) == 2


def test_invalid_payload_rejected(store, adapter):
    result = adapter.score_upload({"exp_id": "", "score": None})
    assert result["status"] == "fail"
    assert any("sid" in e for e in result["errors"])


def test_run_selfcheck_cli(monkeypatch):
    """CLI 自检在无 endpoint 下完整走通 getinfo → score_upload。"""
    monkeypatch.delenv("STARBRIDGE_ILABX_ENDPOINT", raising=False)
    from realtime_backend.edu_ilabx import run_selfcheck
    assert run_selfcheck() == 0
