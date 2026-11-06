"""Public contract tests for the structured, multi-agent project layer."""
import hashlib
import json
import os
import time
from pathlib import Path

import pytest

from rea_video.cli import main


COORDINATOR = "coordinator"


def _handoff(root, target, by, artifact_id="stage", artifact_rel="analysis/stage.json",
             coordinator=COORDINATOR, summary=None, next_action="继续下一阶段"):
    return main([
        "project", "handoff", str(root), "--coordinator", coordinator,
        "--to-state", target, "--by", by, "--summary", summary or f"完成 {target}",
        "--artifact", f"{artifact_id}={artifact_rel}",
        "--acceptance", f"{target}=passed:阶段产物已验证",
        "--next", next_action,
    ])


def _create_task(root, task_id, coordinator=COORDINATOR, write=None):
    return main([
        "project", "task", "create", str(root), "--coordinator", coordinator,
        "--id", task_id, "--title", task_id, "--write", write or f"work/{task_id}",
    ])


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_project_init_creates_declared_input_contract(tmp_path):
    source = tmp_path / "reference.mp4"
    source.write_bytes(b"an independent, known video payload")
    root = tmp_path / "campaign"

    assert main(["project", "init", str(root), "--reference", str(source),
                 "--title", "探店 A 店", "--profile", "store_visit"]) == 0

    project = json.loads((root / "project.json").read_text(encoding="utf-8"))
    manifest = json.loads((root / "input/input.json").read_text(encoding="utf-8"))
    copied = root / "input/reference/reference.mp4"
    assert project["state"] == "initialized"
    assert project["profile"] == "store_visit"
    assert project["inputs"]["manifest"] == "input/input.json"
    assert project["inputs"]["brief"] == "input/brief.md"
    assert (root / "input/brief.md").is_file()
    assert (root / "input/assets").is_dir()
    assert manifest["reference"]["path"] == "input/reference/reference.mp4"
    assert manifest["reference"]["sha256"] == _sha(copied)
    assert copied.read_bytes() == b"an independent, known video payload"


def test_validate_advances_initialized_project_to_input_validated(tmp_path, capsys):
    source = tmp_path / "reference.mp4"
    source.write_bytes(b"stable reference bytes")
    root = tmp_path / "campaign"
    assert main(["project", "init", str(root), "--reference", str(source), "--title", "探店"]) == 0
    capsys.readouterr()

    assert main(["project", "validate", str(root)]) == 0
    printed = capsys.readouterr().out
    result = json.loads(printed[printed.find("{"):])
    project = json.loads((root / "project.json").read_text(encoding="utf-8"))
    assert result["state"] == "input_validated"
    assert result["errors"] == []
    assert project["state"] == "input_validated"
    assert "input_validated" in (root / "HANDOFF.md").read_text(encoding="utf-8")


def _bootstrap(tmp_path, capsys=None):
    source = tmp_path / "reference.mp4"
    source.write_bytes(b"reference payload for agent handoff")
    root = tmp_path / "campaign"
    assert main(["project", "init", str(root), "--reference", str(source), "--title", "探店"]) == 0
    assert main(["project", "validate", str(root)]) == 0
    if capsys:
        capsys.readouterr()
    return root


def test_handoff_writes_machine_authoritative_json_and_generated_md(tmp_path, capsys):
    root = _bootstrap(tmp_path, capsys)
    artifact = root / "analysis/breakdown.json"
    artifact.write_text(json.dumps({"schema": "rea_video.breakdown.v1", "shots": 7}), encoding="utf-8")

    assert main(["project", "handoff", str(root), "--coordinator", "coordinator",
                 "--to-state", "decomposed", "--by", "analysis-agent",
                 "--summary", "完成 7 镜拆解", "--artifact", "breakdown=analysis/breakdown.json",
                 "--acceptance", "breakdown_exists=passed:文件与哈希已生成",
                 "--next", "复核低置信镜头", "--decision", "沿用原时间线",
                 "--open", "镜头 3 的实拍/AI 判定待确认"]) == 0

    project = json.loads((root / "project.json").read_text(encoding="utf-8"))
    handoff = json.loads((root / "handoff/0001-decomposed.json").read_text(encoding="utf-8"))
    md = (root / "handoff/0001-decomposed.md").read_text(encoding="utf-8")
    assert project["state"] == "decomposed"
    assert project["current_handoff"] == "handoff/0001-decomposed.json"
    assert project["artifacts"]["breakdown"]["sha256"] == _sha(artifact)
    assert handoff["artifacts"][0]["sha256"] == _sha(artifact)
    assert handoff["acceptance"][0]["status"] == "passed"
    assert "完成 7 镜拆解" in md and '"sequence": 1' in md
    assert "handoff/0001-decomposed.json" in (root / "HANDOFF.md").read_text(encoding="utf-8")
    assert main(["project", "validate", str(root)]) == 0


def test_validate_stops_on_tampered_reference_traversal_and_secret_fields(tmp_path):
    root = _bootstrap(tmp_path)
    reference = root / "input/reference/reference.mp4"
    original = reference.read_bytes()
    manifest_path = root / "input/input.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    reference.write_bytes(b"tampered")
    assert main(["project", "validate", str(root)]) == 2
    reference.write_bytes(original)

    manifest["api_key"] = "should-never-be-persisted"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    assert main(["project", "validate", str(root)]) == 2
    del manifest["api_key"]

    manifest["assets"] = [{"id": "escape", "kind": "other", "path": "../evil.jpg",
                           "required": False, "status": "missing", "rights": "unknown"}]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    assert main(["project", "validate", str(root)]) == 2
    manifest["assets"] = []
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    (root / "input/brief.md").unlink()
    assert main(["project", "validate", str(root)]) == 2


def test_handoff_rejects_skipped_stage_and_supports_blocked_resume(tmp_path, capsys):
    root = _bootstrap(tmp_path, capsys)
    artifact = root / "analysis/state.json"
    artifact.write_text(json.dumps({"ok": True}), encoding="utf-8")

    assert main(["project", "handoff", str(root), "--coordinator", "coordinator", "--to-state", "planned",
                 "--by", "analysis-agent", "--summary", "试图跳阶段",
                 "--artifact", "state=analysis/state.json",
                 "--acceptance", "artifact=passed:存在", "--next", "继续"]) == 2
    assert json.loads((root / "project.json").read_text(encoding="utf-8"))["state"] == "input_validated"

    assert main(["project", "handoff", str(root), "--coordinator", "coordinator", "--to-state", "blocked",
                 "--by", "analysis-agent", "--summary", "缺少门店授权", "--open", "门店肖像授权未知"]) == 0
    blocked = json.loads((root / "project.json").read_text(encoding="utf-8"))
    assert blocked["state"] == "blocked" and blocked["blocked_from"] == "input_validated"

    assert main(["project", "handoff", str(root), "--coordinator", "coordinator", "--to-state", "input_validated",
                 "--by", "coordinator", "--summary", "已补齐授权并恢复",
                 "--artifact", "state=analysis/state.json",
                 "--acceptance", "rights=passed:授权文件已确认", "--next", "开始拆解"]) == 0
    resumed = json.loads((root / "project.json").read_text(encoding="utf-8"))
    assert resumed["state"] == "input_validated" and resumed["blocked_from"] is None
    assert main(["project", "validate", str(root)]) == 0


def test_handoff_chain_reaches_delivered_one_stage_at_a_time(tmp_path):
    root = _bootstrap(tmp_path)
    artifact = root / "analysis/stage.json"
    artifact.write_text(json.dumps({"stage": "ready"}), encoding="utf-8")
    stages = ("decomposed", "reviewed", "planned", "remaking", "assembled", "verified", "delivered")
    for index, target in enumerate(stages, 1):
        if target == "remaking":
            assert main(["project", "approve", str(root), "--by", "user-owner",
                         "--scope", "7 个 AI 镜头的视频生成", "--confirmation", "用户已确认预算",
                         "--coordinator", "coordinator"]) == 0
        assert main(["project", "handoff", str(root), "--coordinator", "coordinator", "--to-state", target,
                     "--by", f"agent-{index}", "--summary", f"完成 {target}",
                     "--artifact", f"stage{index}=analysis/stage.json",
                     "--acceptance", f"{target}=passed:阶段产物已验证",
                     "--next", "继续下一阶段" if target != "delivered" else "归档"]) == 0
    project = json.loads((root / "project.json").read_text(encoding="utf-8"))
    assert project["state"] == "delivered"
    assert len(project["handoffs"]) == len(stages)
    assert "handoff/0007-delivered.json" in (root / "HANDOFF.md").read_text(encoding="utf-8")
    assert main(["project", "validate", str(root)]) == 0


def test_tasks_enforce_write_scopes_owner_and_completion(tmp_path, capsys):
    root = _bootstrap(tmp_path, capsys)
    assert main(["project", "task", "create", str(root), "--coordinator", "coordinator", "--id", "asset-a",
                 "--title", "准备人物素材", "--write", "work/asset-a",
                 "--acceptance", "人物正面照与半身照齐全"]) == 0
    assert main(["project", "task", "create", str(root), "--coordinator", "coordinator", "--id", "asset-b",
                 "--title", "准备门店素材", "--write", "work/asset-a"]) == 2
    assert main(["project", "task", "claim", str(root), "--id", "asset-a", "--by", "agent-a"]) == 0
    assert main(["project", "task", "claim", str(root), "--id", "asset-a", "--by", "agent-b"]) == 2
    assert main(["project", "task", "complete", str(root), "--id", "asset-a", "--by", "agent-b"]) == 2

    output = root / "work/asset-a/person-front.txt"
    output.write_text("person reference registered", encoding="utf-8")
    assert main(["project", "task", "complete", str(root), "--id", "asset-a",
                 "--by", "agent-a", "--output", "work/asset-a/person-front.txt",
                 "--summary", "人物参考素材已登记"]) == 0
    task = json.loads((root / "work/asset-a/task.json").read_text(encoding="utf-8"))
    assert task["state"] == "completed" and task["owner"] == "agent-a"

    capsys.readouterr()
    assert main(["project", "status", str(root)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["tasks"] == {"asset-a": "completed"}


def test_expired_task_lease_is_visible_as_stale_not_silently_completed(tmp_path, capsys):
    root = _bootstrap(tmp_path, capsys)
    assert main(["project", "task", "create", str(root), "--coordinator", "coordinator", "--id", "asset-c",
                 "--title", "准备菜品素材", "--write", "work/asset-c"]) == 0
    assert main(["project", "task", "claim", str(root), "--id", "asset-c", "--by", "agent-a"]) == 0
    task_path = root / "work/asset-c/task.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    task["lease_expires_at"] = "2000-01-01T00:00:00Z"
    task_path.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")

    capsys.readouterr()
    assert main(["project", "status", str(root)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["tasks"]["asset-c"] == "stale"
    assert any("租约已过期" in warning for warning in status["warnings"])

    assert main(["project", "task", "claim", str(root), "--id", "asset-c", "--by", "agent-b"]) == 0
    (root / "work/asset-c/dish.txt").write_text("dish reference", encoding="utf-8")
    assert main(["project", "task", "complete", str(root), "--id", "asset-c",
                 "--by", "agent-b", "--output", "work/asset-c/dish.txt"]) == 0


def test_handoff_rejects_tampered_artifacts_and_md_and_secret_values(tmp_path, capsys):
    root = _bootstrap(tmp_path, capsys)
    artifact = root / "analysis/breakdown.json"
    artifact.write_text(json.dumps({"shots": 3}), encoding="utf-8")
    assert main(["project", "handoff", str(root), "--coordinator", "coordinator", "--to-state", "decomposed",
                 "--by", "analysis-agent", "--summary", "完成拆解",
                 "--artifact", "breakdown=analysis/breakdown.json",
                 "--acceptance", "breakdown=passed:存在", "--next", "复核"]) == 0
    original = artifact.read_bytes()
    md_path = root / "handoff/0001-decomposed.md"
    original_md = md_path.read_text(encoding="utf-8")

    artifact.write_bytes(b"tampered")
    assert main(["project", "validate", str(root)]) == 2
    artifact.write_bytes(original)

    md_path.write_text(original_md + "\n手工补充了另一套事实\n", encoding="utf-8")
    assert main(["project", "validate", str(root)]) == 2
    md_path.write_text(original_md, encoding="utf-8")
    assert main(["project", "validate", str(root)]) == 0

    assert main(["project", "handoff", str(root), "--coordinator", "coordinator", "--to-state", "reviewed",
                 "--by", "review-agent", "--summary", "sk-abcdefghijklmnopqrstuvwxyz",
                 "--artifact", "breakdown=analysis/breakdown.json",
                 "--acceptance", "review=passed:完成", "--next", "规划"]) == 2
    assert json.loads((root / "project.json").read_text(encoding="utf-8"))["state"] == "decomposed"


def test_store_visit_example_and_stage_skills_follow_declared_contract():
    from jsonschema import Draft202012Validator

    repo = Path(__file__).resolve().parents[1]
    schema = json.loads((repo / "rea_video/project/schemas/input.schema.json").read_text(encoding="utf-8"))
    example = json.loads((repo / "examples/profiles/store_visit/input.example.json").read_text(encoding="utf-8"))
    assert list(Draft202012Validator(schema).iter_errors(example)) == []
    assert "REA_VLM_API_KEY" in (repo / "examples/profiles/store_visit/input.example.json").read_text(encoding="utf-8")
    assert "api_key" not in (repo / "examples/profiles/store_visit/input.example.json").read_text(encoding="utf-8")

    for name in ("rea-video-project", "rea-video-project-input", "rea-video-project-analysis",
                 "rea-video-project-remake", "rea-video-project-verify"):
        skill = repo / "skills" / name / "SKILL.md"
        assert skill.is_file()
        text = skill.read_text(encoding="utf-8")
        assert text.startswith("---\nname:")
        if "project handoff" in text:
            assert "--coordinator" in text, f"{name} 的 handoff 示例缺少协调者声明"
        if "task create" in text:
            assert "--coordinator" in text, f"{name} 的 task create 示例缺少协调者声明"
        if "project approve" in text:
            assert "--by coordinator" not in text, f"{name} 的 approve 示例用了协调者自批准"
    assert (repo / "docs/structured-projects.md").is_file()
    docs = (repo / "docs/structured-projects.md").read_text(encoding="utf-8")
    assert "--by coordinator" not in docs
    assert "项目层**不拦截**" in docs  # 不得宣称 remake-run 被代码拦截


def test_initial_state_requires_validate_and_task_completion_requires_output(tmp_path):
    source = tmp_path / "reference.mp4"
    source.write_bytes(b"initial state payload")
    root = tmp_path / "campaign"
    assert main(["project", "init", str(root), "--reference", str(source), "--title", "探店"]) == 0
    artifact = root / "analysis/state.json"
    artifact.write_text(json.dumps({"ok": True}), encoding="utf-8")

    assert main(["project", "handoff", str(root), "--coordinator", "coordinator", "--to-state", "input_validated",
                 "--by", "input-agent", "--summary", "试图绕过 validate",
                 "--artifact", "state=analysis/state.json",
                 "--acceptance", "state=passed:存在", "--next", "拆解"]) == 2
    assert main(["project", "validate", str(root)]) == 0

    assert main(["project", "task", "create", str(root), "--coordinator", "coordinator", "--id", "asset-d",
                 "--title", "准备素材", "--write", "work/asset-d"]) == 0
    assert main(["project", "task", "claim", str(root), "--id", "asset-d", "--by", "agent-a"]) == 0
    assert main(["project", "task", "complete", str(root), "--id", "asset-d", "--by", "agent-a"]) == 2
    assert json.loads((root / "work/asset-d/task.json").read_text(encoding="utf-8"))["state"] == "in_progress"


def test_coordinator_gate_rejects_wrong_identity_and_missing_declaration(tmp_path, capsys):
    root = _bootstrap(tmp_path, capsys)
    (root / "analysis/stage.json").write_text('{"ok": true}', encoding="utf-8")

    assert _handoff(root, "decomposed", "analysis-agent", coordinator="worker-agent") == 2
    assert json.loads((root / "project.json").read_text(encoding="utf-8"))["state"] == "input_validated"
    with pytest.raises(SystemExit):
        main(["project", "handoff", str(root), "--to-state", "decomposed",
              "--by", "analysis-agent", "--summary", "缺少协调者声明"])
    with pytest.raises(SystemExit):
        main(["project", "task", "create", str(root), "--id", "asset-x",
              "--title", "缺少协调者声明", "--write", "work/asset-x"])
    assert _handoff(root, "decomposed", "analysis-agent") == 0


def test_remaking_requires_recorded_user_budget_approval(tmp_path, capsys):
    root = _bootstrap(tmp_path, capsys)
    (root / "analysis/stage.json").write_text('{"ok": true}', encoding="utf-8")
    assert _handoff(root, "decomposed", "agent-1", "a1") == 0
    assert _handoff(root, "reviewed", "agent-2", "a2") == 0
    assert _handoff(root, "planned", "agent-3", "a3") == 0

    approve = ["project", "approve", str(root), "--coordinator", COORDINATOR,
               "--scope", "7 个 AI 镜头", "--confirmation", "用户已确认预算"]
    assert _handoff(root, "remaking", "agent-4", "a4") == 2
    assert main(approve + ["--by", COORDINATOR]) == 2  # 协调者不能自己批准自己
    assert main(approve + ["--by", "user-owner"]) == 0
    assert main(approve + ["--by", "user-owner"]) == 2  # 不能重复登记
    paid = json.loads((root / "project.json").read_text(encoding="utf-8"))["paid_generation"]
    assert paid["approved"] is True and paid["approved_by"] == "user-owner"
    assert _handoff(root, "remaking", "agent-4", "a4") == 0
    assert main(["project", "validate", str(root)]) == 0


def test_approve_is_rejected_before_planned_stage(tmp_path, capsys):
    root = _bootstrap(tmp_path, capsys)
    assert main(["project", "approve", str(root), "--by", "user-owner", "--scope", "预算",
                 "--confirmation", "用户已确认", "--coordinator", COORDINATOR]) == 2


def test_task_reclaim_is_the_only_recovery_for_blocked_tasks(tmp_path, capsys):
    root = _bootstrap(tmp_path, capsys)
    assert _create_task(root, "asset-r") == 0
    assert main(["project", "task", "claim", str(root), "--id", "asset-r", "--by", "agent-a"]) == 0
    assert main(["project", "task", "block", str(root), "--id", "asset-r", "--by", "agent-a",
                 "--reason", "肖像授权未确认"]) == 0
    assert main(["project", "task", "claim", str(root), "--id", "asset-r", "--by", "agent-b"]) == 2
    assert main(["project", "task", "reclaim", str(root), "--id", "asset-r",
                 "--coordinator", "worker-agent"]) == 2
    assert main(["project", "task", "reclaim", str(root), "--id", "asset-r",
                 "--coordinator", COORDINATOR]) == 0
    assert main(["project", "task", "claim", str(root), "--id", "asset-r", "--by", "agent-b"]) == 0
    task = json.loads((root / "work/asset-r/task.json").read_text(encoding="utf-8"))
    assert task["state"] == "in_progress" and task["owner"] == "agent-b"


def test_expired_task_lease_can_only_be_reclaimed_by_coordinator(tmp_path, capsys):
    root = _bootstrap(tmp_path, capsys)
    assert _create_task(root, "asset-s") == 0
    assert main(["project", "task", "claim", str(root), "--id", "asset-s", "--by", "agent-a"]) == 0
    task_path = root / "work/asset-s/task.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    task["lease_expires_at"] = "2000-01-01T00:00:00Z"
    task_path.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")

    assert main(["project", "task", "complete", str(root), "--id", "asset-s",
                 "--by", "agent-a", "--output", "work/asset-s/x.txt"]) == 2
    assert main(["project", "task", "reclaim", str(root), "--id", "asset-s",
                 "--coordinator", COORDINATOR]) == 0
    assert main(["project", "task", "claim", str(root), "--id", "asset-s", "--by", "agent-b"]) == 0


def test_stale_lock_from_dead_process_is_reclaimed_but_live_lock_is_not(tmp_path, capsys):
    root = _bootstrap(tmp_path, capsys)
    lock = root / "project.json.lock"
    ancient = time.time() - 10_000
    command = ["project", "task", "create", str(root), "--coordinator", COORDINATOR,
               "--id", "lock-test", "--title", "锁测试", "--write", "work/lock-test"]

    lock.write_bytes(os.getpid().to_bytes(4, "little"))
    os.utime(lock, (ancient, ancient))
    assert main(command) == 2  # 持锁进程仍存活，绝不回收
    assert not (root / "work/lock-test/task.json").exists()

    # Windows PID 一定是 4 的倍数，这个奇数不可能对应活进程。
    lock.write_bytes((999_999).to_bytes(4, "little"))
    os.utime(lock, (ancient, ancient))
    assert main(command) == 0
    assert (root / "work/lock-test/task.json").is_file()
    assert not lock.exists()


def test_sequential_tasks_and_handoffs_do_not_lose_registrations(tmp_path, capsys):
    root = _bootstrap(tmp_path, capsys)
    (root / "analysis/stage.json").write_text('{"ok": true}', encoding="utf-8")
    assert _create_task(root, "asset-x") == 0
    assert _create_task(root, "asset-y") == 0
    assert _handoff(root, "decomposed", "agent-1", "a1") == 0
    assert _handoff(root, "reviewed", "agent-2", "a2") == 0

    project = json.loads((root / "project.json").read_text(encoding="utf-8"))
    assert set(project["tasks"]) == {"asset-x", "asset-y"}
    assert project["handoffs"] == ["handoff/0001-decomposed.json", "handoff/0002-reviewed.json"]
    assert (root / "handoff/0001-decomposed.json").is_file()
    assert (root / "handoff/0002-reviewed.json").is_file()
    assert main(["project", "validate", str(root)]) == 0


def test_registered_artifact_ids_are_immutable(tmp_path, capsys):
    root = _bootstrap(tmp_path, capsys)
    (root / "analysis/stage.json").write_text('{"v": 1}', encoding="utf-8")
    (root / "analysis/other.json").write_text('{"v": 2}', encoding="utf-8")
    assert _handoff(root, "decomposed", "agent-1", "same") == 0
    original_sha = _sha(root / "analysis/stage.json")

    assert _handoff(root, "reviewed", "agent-2", "same", "analysis/other.json") == 2
    project = json.loads((root / "project.json").read_text(encoding="utf-8"))
    assert project["artifacts"]["same"]["sha256"] == original_sha
    assert _handoff(root, "reviewed", "agent-2", "v2", "analysis/other.json") == 0


def test_handoff_chain_tampering_is_detected(tmp_path, capsys):
    root = _bootstrap(tmp_path, capsys)
    (root / "analysis/stage.json").write_text('{"ok": true}', encoding="utf-8")
    assert _handoff(root, "decomposed", "agent-1", "a1") == 0
    assert _handoff(root, "reviewed", "agent-2", "a2") == 0

    second = root / "handoff/0002-reviewed.json"
    handoff = json.loads(second.read_text(encoding="utf-8"))
    handoff["sequence"] = 99
    second.write_text(json.dumps(handoff, ensure_ascii=False, indent=2), encoding="utf-8")
    assert main(["project", "validate", str(root)]) == 2
    handoff["sequence"] = 2
    second.write_text(json.dumps(handoff, ensure_ascii=False, indent=2), encoding="utf-8")
    assert main(["project", "validate", str(root)]) == 0


def test_broken_project_files_and_secret_texts_fail_closed_without_traceback(tmp_path, capsys):
    root = _bootstrap(tmp_path, capsys)
    brief = root / "input/brief.md"
    brief.write_text(brief.read_text(encoding="utf-8") + "\nsk-abcdefghijklmnopqrstuvwxyz\n",
                     encoding="utf-8")
    capsys.readouterr()
    assert main(["project", "validate", str(root)]) == 2
    err = capsys.readouterr().err
    assert "密钥" in err and "Traceback" not in err

    (root / "project.json").write_text("{ not json", encoding="utf-8")
    capsys.readouterr()
    assert main(["project", "validate", str(root)]) == 2
    assert "Traceback" not in capsys.readouterr().err

    capsys.readouterr()
    assert main(["project", "validate", str(tmp_path / "does-not-exist")]) == 2
    assert "Traceback" not in capsys.readouterr().err


def test_store_visit_example_has_a_present_asset_with_a_real_hash():
    repo = Path(__file__).resolve().parents[1]
    base = repo / "examples/profiles/store_visit"
    example = json.loads((base / "input.example.json").read_text(encoding="utf-8"))
    present = [asset for asset in example["assets"] if asset["status"] == "present"]
    assert present, "示例至少需要一个 present 素材与真实 SHA-256"
    for asset in present:
        path = base / asset["path"]
        assert path.is_file(), path
        assert asset["sha256"] == _sha(path)


def test_custom_coordinator_name_from_project_json_is_enforced(tmp_path, capsys):
    source = tmp_path / "reference.mp4"
    source.write_bytes(b"custom coordinator payload")
    root = tmp_path / "campaign"
    assert main(["project", "init", str(root), "--reference", str(source),
                 "--title", "探店", "--coordinator", "lead-agent"]) == 0
    assert main(["project", "validate", str(root)]) == 0
    (root / "analysis/stage.json").write_text('{"ok": true}', encoding="utf-8")
    assert json.loads((root / "project.json").read_text(encoding="utf-8"))["coordinator"] == "lead-agent"

    assert _handoff(root, "decomposed", "agent-1", coordinator="coordinator") == 2
    assert _handoff(root, "decomposed", "agent-1", coordinator="lead-agent") == 0
    assert main(["project", "validate", str(root)]) == 0


def test_lock_token_with_newline_byte_is_cleaned_up(tmp_path, capsys, monkeypatch):
    # Windows text-mode os.open used to translate 0x0A into CRLF, making the
    # on-disk token one byte longer and leaving an orphan lock behind.
    from rea_video.project import core
    monkeypatch.setattr(core.os, "urandom", lambda n: b"\x0a" * n)

    root = _bootstrap(tmp_path, capsys)
    (root / "analysis/stage.json").write_text('{"ok": true}', encoding="utf-8")
    assert _handoff(root, "decomposed", "agent-1") == 0
    assert not (root / "project.json.lock").exists()
    assert main(["project", "validate", str(root)]) == 0


def test_forged_state_or_self_approved_budget_fails_validation(tmp_path, capsys):
    root = _bootstrap(tmp_path, capsys)
    (root / "analysis/stage.json").write_text('{"ok": true}', encoding="utf-8")
    for target, artifact_id in (("decomposed", "a1"), ("reviewed", "a2"), ("planned", "a3")):
        assert _handoff(root, target, "agent-x", artifact_id) == 0

    project_path = root / "project.json"
    original = json.loads(project_path.read_text(encoding="utf-8"))

    # P1-1: editing the root file alone must not fake entry into a paid stage.
    forged = json.loads(json.dumps(original))
    forged["state"] = "remaking"
    project_path.write_text(json.dumps(forged, ensure_ascii=False, indent=2), encoding="utf-8")
    capsys.readouterr()
    assert main(["project", "validate", str(root)]) == 2
    err = capsys.readouterr().err
    assert "交接链末端" in err or "付费生成阶段" in err, err

    # P1-2: a self-approved budget record must be rejected even though
    # HANDOFF.md does not depend on paid_generation and needs no edit.
    forged = json.loads(json.dumps(original))
    forged["paid_generation"] = {
        "approved": True, "approved_by": COORDINATOR,
        "approved_at": "2026-01-01T00:00:00Z",
        "scope": "伪造预算", "confirmation": "伪造确认",
    }
    project_path.write_text(json.dumps(forged, ensure_ascii=False, indent=2), encoding="utf-8")
    capsys.readouterr()
    assert main(["project", "validate", str(root)]) == 2
    assert "批准人不能是协调者本人" in capsys.readouterr().err
    assert _handoff(root, "remaking", "agent-x", "a4") == 2  # preflight gate too

    # Restore, then prove the legitimate path still works.
    project_path.write_text(json.dumps(original, ensure_ascii=False, indent=2), encoding="utf-8")
    assert main(["project", "validate", str(root)]) == 0
    assert main(["project", "approve", str(root), "--by", "user-owner",
                 "--scope", "7 个 AI 镜头", "--confirmation", "用户已确认预算",
                 "--coordinator", COORDINATOR]) == 0
    assert _handoff(root, "remaking", "agent-x", "a4") == 0
    assert main(["project", "validate", str(root)]) == 0


def test_blocked_from_a_paid_stage_still_requires_budget_approval(tmp_path, capsys):
    root = _bootstrap(tmp_path, capsys)
    (root / "analysis/stage.json").write_text('{"ok": true}', encoding="utf-8")
    assert _handoff(root, "decomposed", "agent-x", "a1") == 0

    project_path = root / "project.json"
    forged = json.loads(project_path.read_text(encoding="utf-8"))
    forged["state"] = "blocked"
    forged["blocked_from"] = "remaking"
    project_path.write_text(json.dumps(forged, ensure_ascii=False, indent=2), encoding="utf-8")
    capsys.readouterr()
    assert main(["project", "validate", str(root)]) == 2
    assert "付费生成阶段" in capsys.readouterr().err
