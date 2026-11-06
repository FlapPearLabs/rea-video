"""File-first project contracts for single-agent, handoff, and cooperative workflows.

The original rea-video breakdown pipeline is deliberately untouched. This module
owns only the surrounding project state, input manifest, handoff envelopes, and
task ownership files.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from importlib.resources import files
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable

from . import SCHEMA_VERSION

STAGES = (
    "initialized",
    "input_validated",
    "decomposed",
    "reviewed",
    "planned",
    "remaking",
    "assembled",
    "verified",
    "delivered",
)
TASK_STATES = ("pending", "in_progress", "completed", "blocked", "stale")
SECRET_KEY = re.compile(r"(?i)(api[_-]?key|access[_-]?token|token|secret|password|passwd|private[_-]?key|authorization)")
SECRET_VALUE = re.compile(
    r"(sk-[A-Za-z0-9]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{20,}|"
    r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}|"
    r"Bearer\s+[A-Za-z0-9._-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)
# Plaintext credential assignments in free text (brief.md, task outputs, assets):
# `password: hunter2`, `DB_PASSWORD=correct horse`, `api_key = ...`.
# The negative lookahead avoids flagging our own acceptance syntax `check=passed:...`.
SECRET_ASSIGNMENT = re.compile(
    # `(?<![A-Za-z0-9])` rather than `\b`: `DB_PASSWORD=...` has an underscore
    # before PASSWORD, which is a word character, so `\b` would not match.
    r"(?i)(?<![A-Za-z0-9])(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token|token)"
    r"\s*[:=]\s*(?!passed\b|failed\b)[\"']?[^\s\"',;]{4,}"
)
PROFILE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
TASK_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{1,48}$")
TEXT_SCAN_SUFFIXES = {".json", ".md", ".txt", ".yaml", ".yml", ".csv", ".srt", ".ass", ".html", ".htm"}
TEXT_SCAN_LIMIT = 1 << 20
LOCK_ATTEMPTS = 3


class ProjectError(Exception):
    """A machine-checkable contract violation that must stop the next agent."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with Path(path).open("rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
    except OSError as exc:
        raise ProjectError(f"无法计算哈希：{path}：{exc}") from exc
    return h.hexdigest()


def _read_text(path: Path) -> str:
    p = Path(path)
    if not p.is_file():
        raise ProjectError(f"缺少文件：{p}")
    try:
        return p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ProjectError(f"文件不可读：{p}：{exc}") from exc


def _read_json(path: Path) -> Any:
    p = Path(path)
    try:
        return json.loads(_read_text(p))
    except json.JSONDecodeError as exc:
        raise ProjectError(f"JSON 无效：{p}：{exc.msg}") from exc


def _atomic_write(path: Path, text: str) -> None:
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{p.name}.", dir=p.parent)
    except OSError as exc:
        raise ProjectError(f"无法写入：{p}：{exc}") from exc
    try:
        try:
            os.write(fd, text.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        # Windows keeps a briefly-open handle on the destination; retry instead of
        # leaving a valid lock/write pair half-applied.
        for attempt in range(LOCK_ATTEMPTS):
            try:
                os.replace(tmp, p)
                break
            except OSError:
                if attempt == LOCK_ATTEMPTS - 1:
                    raise
                time.sleep(0.05 * (attempt + 1))
    except OSError as exc:
        raise ProjectError(f"无法写入：{p}：{exc}") from exc
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass


def _write_json(path: Path, data: Any) -> None:
    _atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _write_text(path: Path, text: str) -> None:
    _atomic_write(path, text)


def _pid_alive(pid: int) -> bool:
    """Best-effort liveness check; unknown results are treated as alive (fail-closed)."""
    if pid <= 0:
        return False
    if os.name == "nt":
        # os.kill(pid, 0) calls TerminateProcess on Windows, so never use it here.
        try:
            import ctypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                # ERROR_ACCESS_DENIED means the process exists but cannot be
                # queried; only ERROR_INVALID_PARAMETER means it is really gone.
                return ctypes.get_last_error() == 5
            kernel32.CloseHandle(handle)
            return True
        except Exception:
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _acquire_lock(lock: Path, path: Path, token: bytes) -> int:
    try:
        stale_after = max(1, int(os.getenv("REA_VIDEO_LOCK_STALE_SECONDS", "300")))
    except ValueError:
        stale_after = 300
    # O_BINARY matters on Windows: without it os.write translates 0x0A to CRLF,
    # so a random token byte can change the lock file length and break cleanup.
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    for _ in range(LOCK_ATTEMPTS):
        try:
            fd = os.open(lock, flags)
            try:
                os.write(fd, token)
            except OSError:
                os.close(fd)
                raise
            return fd
        except FileExistsError as exc:
            try:
                raw = lock.read_bytes()
                age = time.time() - lock.stat().st_mtime
            except FileNotFoundError:
                continue
            except OSError as inner:
                raise ProjectError(f"无法读取锁文件：{lock}：{inner}") from inner
            pid = int.from_bytes(raw[:4], "little") if len(raw) >= 4 else 0
            if age > stale_after and not _pid_alive(pid):
                try:
                    # Only delete the exact lock we inspected; another process may
                    # have reclaimed it in between.
                    if lock.read_bytes() == raw:
                        lock.unlink()
                except (FileNotFoundError, OSError):
                    pass
                continue
            raise ProjectError(
                f"另一个 Agent 正在修改 {path.name}；锁文件：{lock}。"
                f"持锁进程 {pid or '未知'} 仍存活，或锁尚未超过 {stale_after} 秒，不会自动回收"
            ) from exc
        except OSError as exc:
            raise ProjectError(f"无法取得文件锁：{lock}：{exc}") from exc
    raise ProjectError(f"无法取得文件锁：{lock}；请检查残留的 .json.lock 文件")


@contextmanager
def _lock(path: Path):
    lock = Path(str(path) + ".lock")
    token = os.getpid().to_bytes(4, "little") + os.urandom(8)
    fd = _acquire_lock(lock, path, token)
    try:
        yield
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        # We created this exact lock and no other process can legitimately
        # reclaim it while we are alive (reclaim requires a dead owner PID).
        try:
            lock.unlink()
        except OSError:
            pass


def _relative_path(root: Path, value: str, label: str = "路径") -> str:
    if not isinstance(value, str) or not value or not value.strip():
        raise ProjectError(f"{label} 必须是项目内的 POSIX 相对路径：{value!r}")
    # PureWindowsPath is used explicitly: Path(value).is_absolute() is False for
    # drive-relative and UNC paths on POSIX, so it cannot be the only check.
    if ("\\" in value or ":" in value or value.startswith("/")
            or PureWindowsPath(value).is_absolute() or PurePosixPath(value).is_absolute()):
        raise ProjectError(f"{label} 必须是项目内的 POSIX 相对路径：{value!r}")
    parts = PurePosixPath(value).parts
    normalized = str(PurePosixPath(value))
    if normalized == "." or ".." in parts:
        raise ProjectError(f"{label} 不允许越出项目目录：{value!r}")
    resolved = (root / normalized).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ProjectError(f"{label} 不允许越出项目目录：{value!r}")
    return normalized


def _project_file(root: Path, value: str, label: str = "路径") -> Path:
    rel = _relative_path(root, value, label)
    return (root / rel).resolve()


def _schema(name: str) -> dict[str, Any]:
    try:
        resource = files("rea_video.project") / "schemas" / name
        return json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ModuleNotFoundError) as exc:
        raise ProjectError(f"无法读取 Schema {name}：{exc}") from exc


def _validate_document(data: Any, schema_name: str, label: str) -> None:
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:
        raise ProjectError("缺少 jsonschema 运行依赖；请执行 pip install -e . 或 pip install --upgrade rea-video") from exc
    errors = sorted(
        Draft202012Validator(_schema(schema_name)).iter_errors(data),
        key=lambda e: tuple(str(part) for part in e.path),
    )
    if errors:
        first = errors[0]
        where = "/".join(str(x) for x in first.path) or "$"
        raise ProjectError(f"{label} 不符合 {schema_name}：{where}: {first.message}")


def _reject_secret_keys(value: Any, label: str, where: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_where = f"{where}.{key}"
            allowed_credentials = (
                key == "credentials"
                and isinstance(child, list)
                and all(isinstance(item, dict) and set(item) <= {"env"} for item in child)
            )
            if SECRET_KEY.search(str(key)) and not allowed_credentials:
                raise ProjectError(f"{label} 不允许写入凭据字段：{child_where}；只记录环境变量名")
            _reject_secret_keys(child, label, child_where)
    elif isinstance(value, list):
        for i, child in enumerate(value):
            _reject_secret_keys(child, label, f"{where}[{i}]")
    elif isinstance(value, str) and (SECRET_VALUE.search(value) or SECRET_ASSIGNMENT.search(value)):
        raise ProjectError(f"{label} 疑似包含密钥值：{where}")


def _require_coordinator(project: dict[str, Any], supplied: str, action: str) -> None:
    if supplied != project["coordinator"]:
        raise ProjectError(
            f"{action} 只能由协调者 {project['coordinator']!r} 执行；收到 {supplied!r}。"
            "这是角色声明门，不是身份认证"
        )


def _snapshot_matches(before: dict[str, Any], current: dict[str, Any]) -> bool:
    keys = ("state", "coordinator", "paid_generation", "blocked_from", "blocked_reason",
            "artifacts", "tasks", "handoffs", "current_handoff", "current_handoff_md",
            "artifact_revisions")
    return all(before.get(key) == current.get(key) for key in keys)


def _brief(title: str, profile: str) -> str:
    return (
        f"# {title}\n\n"
        "本文件给人阅读；机器权威事实保存在 `input/input.json` 与 `project.json`。\n\n"
        "## 必须保留\n\n"
        "- 参考视频的镜头顺序、时长、运镜、剪辑节奏、字幕结构和音频结构。\n\n"
        "## 本次替换\n\n"
        "- 人物、场景、素材、文案：请在 `input/input.json` 中登记素材 ID 与路径。\n\n"
        "## 边界\n\n"
        "- 只使用有权使用的肖像、门店、品牌、音乐和视频素材。\n"
        "- 不得在 JSON、Markdown、任务文件或交接中写入 API Key、Token 或密码；只记录环境变量名。\n"
        "- 付费生成必须先通过 dry run 或明确预算，再由用户确认。\n\n"
        f"业务配置：`{profile}`。\n"
    )


def init_project(root: Path, reference: Path, title: str, profile: str, coordinator: str = "coordinator") -> dict[str, Any]:
    root = Path(root).expanduser().resolve()
    reference = Path(reference).expanduser().resolve()
    if not reference.is_file():
        raise ProjectError(f"参考视频不存在：{reference}")
    if not PROFILE.fullmatch(profile):
        raise ProjectError(f"配置名只能使用小写字母、数字、下划线和连字符：{profile!r}")
    if not coordinator.strip():
        raise ProjectError("协调者名称不能为空")
    if (root / "project.json").exists():
        raise ProjectError(f"项目已存在，拒绝覆盖：{root / 'project.json'}")

    filename = reference.name
    if not filename or filename in (".", ".."):
        raise ProjectError("参考视频文件名无效")
    for name in ("input/reference", "input/assets", "analysis", "handoff", "work", "output"):
        (root / name).mkdir(parents=True, exist_ok=True)
    copied = root / "input/reference" / filename
    shutil.copy2(reference, copied)

    stamp = _now()
    project_id = re.sub(r"[^a-z0-9_-]", "-", root.name.lower()).strip("-") or "project"
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "profile": profile,
        "reference": {
            "id": "reference",
            "path": f"input/reference/{filename}",
            "media_type": "video",
            "required": True,
            "sha256": _sha256(copied),
        },
        "assets": [],
        "preserve": ["structure", "shot_timing", "camera_motion", "captions", "audio_structure"],
        "replacements": [],
        "credentials": [],
    }
    project = {
        "schema_version": SCHEMA_VERSION,
        "project_id": project_id,
        "title": title or root.name,
        "profile": profile,
        "state": "initialized",
        "coordinator": coordinator,
        "paid_generation": {
            "approved": False,
            "approved_by": None,
            "approved_at": None,
            "scope": None,
            "confirmation": None,
        },
        "blocked_from": None,
        "blocked_reason": None,
        "created_at": stamp,
        "updated_at": stamp,
        "inputs": {"manifest": "input/input.json", "brief": "input/brief.md"},
        "artifacts": {},
        "tasks": {},
        "handoffs": [],
        "artifact_revisions": [],
        "current_handoff": None,
        "current_handoff_md": "HANDOFF.md",
    }
    _validate_document(manifest, "input.schema.json", "input/input.json")
    _validate_document(project, "project.schema.json", "project.json")
    _write_json(root / "input/input.json", manifest)
    _write_text(root / "input/brief.md", _brief(project["title"], profile))
    _write_json(root / "project.json", project)
    _write_text(root / "HANDOFF.md", render_current_handoff_md(project))
    return {"root": str(root), "state": project["state"], "project": "project.json", "input": "input/input.json"}


def approve_paid_generation(root: Path, approved_by: str, scope: str,
                            confirmation: str, coordinator: str) -> dict[str, Any]:
    """Record a human budget confirmation. This is a declaration gate, not authentication."""
    root = Path(root).expanduser().resolve()
    preflight = validate_project(root, advance=False)
    if preflight["errors"]:
        raise ProjectError("前序项目校验失败：" + "；".join(preflight["errors"]))
    if not approved_by.strip() or not scope.strip() or not confirmation.strip():
        raise ProjectError("付费生成确认必须写明批准人、范围和确认内容")
    with _lock(root / "project.json"):
        project = _read_json(root / "project.json")
        _require_coordinator(project, coordinator, "确认付费生成")
        if approved_by.strip() == project["coordinator"]:
            raise ProjectError("批准人不能是协调者本人；付费生成须由用户/人类身份确认")
        if project["state"] != "planned":
            raise ProjectError(
                f"付费生成确认只能在 planned 阶段、进入 remaking 之前登记；当前状态 {project['state']}"
            )
        if project["paid_generation"]["approved"]:
            raise ProjectError("付费生成已经确认过，不能重复登记")
        stamp = _now()
        project["paid_generation"] = {
            "approved": True,
            "approved_by": approved_by,
            "approved_at": stamp,
            "scope": scope,
            "confirmation": confirmation,
        }
        project["updated_at"] = stamp
        _write_json(root / "project.json", project)
    return {"approved": True, "approved_by": approved_by, "scope": scope, "approved_at": stamp}


def refresh_artifact(root: Path, args: Any) -> dict[str, Any]:
    """Re-register the hash of a legitimately rewritten artifact.

    The original pipeline rewrites `breakdown.json` in place (`apply-review`),
    and `assemble` can overwrite registered outputs. Historical handoffs are
    append-only snapshots, so their recorded hashes cannot be edited. This is
    the only supported recovery path: the coordinator explicitly records the
    old -> new hash transition, and `validate_project` replays that chain.
    """
    root = Path(root).expanduser().resolve()
    preflight = validate_project(root, advance=False, allow_artifact_drift=True)
    if preflight["errors"]:
        raise ProjectError("前序项目校验失败：" + "；".join(preflight["errors"]))
    artifact_id = args.id
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", artifact_id):
        raise ProjectError(f"产物 ID 无效：{artifact_id!r}")
    reason = (args.reason or "产物被合法改写，重新登记哈希").strip()
    by = (args.by or args.coordinator).strip()
    before = _read_json(root / "project.json")
    with _lock(root / "project.json"):
        project = _read_json(root / "project.json")
        if not _snapshot_matches(before, project):
            raise ProjectError("项目状态已被其他协调者更新；请重新运行 project status 后再刷新")
        _require_coordinator(project, args.coordinator, "刷新产物哈希")
        entry = project["artifacts"].get(artifact_id)
        if entry is None:
            raise ProjectError(f"产物未登记，无法刷新：{artifact_id!r}")
        path_before = entry["path"]
        new_rel = _relative_path(root, args.path or path_before, "产物路径")
        new_path = _project_file(root, new_rel, "产物路径")
        if not new_path.is_file():
            raise ProjectError(f"产物文件不存在：{new_rel}")
        sha256_before = entry["sha256"]
        sha256_after = _sha256(new_path)
        if new_rel == path_before and sha256_after == sha256_before:
            raise ProjectError(
                f"产物 {artifact_id!r} 内容与路径均未变化，无需刷新；"
                "若确已改写，请确认改写落在登记的路径上"
            )
        revisions = project.get("artifact_revisions", [])
        revision = {
            "schema_version": SCHEMA_VERSION,
            "sequence": len(revisions) + 1,
            "artifact_id": artifact_id,
            "path_before": path_before,
            "path_after": new_rel,
            "sha256_before": sha256_before,
            "sha256_after": sha256_after,
            "created_at": _now(),
            "coordinator": project["coordinator"],
            "by": by,
            "reason": reason,
        }
        _reject_secret_keys(revision, "产物修订")
        project["artifact_revisions"] = revisions + [revision]
        project["artifacts"][artifact_id] = {
            "path": new_rel, "sha256": sha256_after,
            "kind": entry["kind"], "stage": entry["stage"],
        }
        project["updated_at"] = revision["created_at"]
        _validate_document(project, "project.schema.json", "project.json")
        current_handoff = _load_current_handoff(root, project)
        _write_json(root / "project.json", project)
        _write_text(root / "HANDOFF.md", render_current_handoff_md(project, current_handoff))
    return {
        "artifact": artifact_id,
        "sequence": revision["sequence"],
        "path": new_rel,
        "sha256_before": sha256_before,
        "sha256_after": sha256_after,
        "revisions": len(project["artifact_revisions"]),
    }


def render_current_handoff_md(project: dict[str, Any], handoff: dict[str, Any] | None = None) -> str:
    lines = [
        "# 当前交接",
        "",
        f"- 项目：{project['title']}",
        f"- 状态：`{project['state']}`",
        f"- 项目配置：`{project['profile']}`",
        # Anchoring these two values in the generated MD means editing only
        # project.json (coordinator takeover / revision injection) is rejected
        # by the MD-equality check in validate_project.
        f"- 协调者：`{project['coordinator']}`",
        f"- 产物修订：{len(project.get('artifact_revisions', []))} 条",
    ]
    if handoff:
        hjson = project["current_handoff"]
        hmd = str(Path(hjson).with_suffix(".md")).replace("\\", "/")
        lines += [
            f"- 交接序号：`{handoff['sequence']}`",
            f"- 执行者：`{handoff['agent']}`",
            f"- 机器权威交接：[{hjson}]({hjson})",
            f"- 人类摘要：[{hmd}]({hmd})",
            f"- 下一步：{handoff['next_actions'][0] if handoff['next_actions'] else '无'}",
        ]
    else:
        lines.append("- 当前正式交接：无")
    lines += ["", "此文件由 `project.json` 自动生成，不要手工维护另一套事实。", ""]
    return "\n".join(lines)


def _load_current_handoff(root: Path, project: dict[str, Any],
                          loaded: dict[str, dict[str, Any]] | None = None) -> dict[str, Any] | None:
    if not project.get("current_handoff"):
        return None
    rel = project["current_handoff"]
    handoff = (loaded or {}).get(rel)
    if handoff is None:
        handoff = _read_json(_project_file(root, rel, "当前交接"))
        _validate_document(handoff, "handoff.schema.json", rel)
    return handoff


def _check_hash(root: Path, rel: str, expected: str, label: str, errors: list[str]) -> None:
    path = _project_file(root, rel, label)
    if not path.is_file():
        errors.append(f"{label} 指向的文件不存在：{rel}")
        return
    actual = _sha256(path)
    if actual != expected:
        errors.append(f"{label} 哈希不符：{rel}")


def _scan_text_file(path: Path, label: str, errors: list[str]) -> None:
    """Scan small text artefacts for credential-shaped values; skip binary media."""
    if path.suffix.lower() not in TEXT_SCAN_SUFFIXES:
        return
    try:
        if not path.is_file() or path.stat().st_size > TEXT_SCAN_LIMIT:
            return
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    try:
        _reject_secret_keys(text, label)
    except ProjectError as exc:
        errors.append(str(exc))


PAID_STAGES = ("remaking", "assembled", "verified", "delivered")


def _superseded_artifact_pairs(project: dict[str, Any]) -> set[tuple[str, str]]:
    """(path, sha256) snapshots explicitly replaced by an audited revision.

    Historical handoffs are append-only and keep the hash that was valid when
    they were written. A file legitimately rewritten by `apply-review` therefore
    no longer matches those snapshots; only hashes recorded in the revision
    chain may be exempted from the equality check.
    """
    pairs: set[tuple[str, str]] = set()
    for revision in project.get("artifact_revisions", []):
        pairs.add((revision["path_before"], revision["sha256_before"]))
        pairs.add((revision["path_after"], revision["sha256_after"]))
    return pairs


def _check_revision_chain(root: Path, project: dict[str, Any],
                          loaded: dict[str, dict[str, Any]], errors: list[str]) -> None:
    """Revision history is append-only and must form a verifiable chain."""
    revisions = project.get("artifact_revisions", [])
    if not revisions:
        return
    historical = {
        (artifact["path"], artifact["sha256"])
        for handoff in loaded.values() for artifact in handoff.get("artifacts", [])
    }
    for index, revision in enumerate(revisions, 1):
        if revision.get("sequence") != index:
            errors.append(f"产物修订序号不连续：第 {index} 条应为 {index}")
        if revision.get("coordinator") != project["coordinator"]:
            errors.append(f"产物修订协调者与 project.json 不一致：第 {index} 条")
        if revision.get("artifact_id") not in project["artifacts"]:
            errors.append(f"产物修订指向未登记产物：{revision.get('artifact_id')!r}")
        for key in ("path_before", "path_after"):
            try:
                _relative_path(root, revision[key], f"产物修订 {key}")
            except ProjectError as exc:
                errors.append(str(exc))
    chains: dict[str, list[dict[str, Any]]] = {}
    for revision in revisions:
        chains.setdefault(revision["artifact_id"], []).append(revision)
    for artifact_id, chain in chains.items():
        first = chain[0]
        if (first["path_before"], first["sha256_before"]) not in historical:
            errors.append(f"产物修订起点未锚定到历史交接：{artifact_id}")
        for previous, current in zip(chain, chain[1:]):
            if (current["sha256_before"] != previous["sha256_after"]
                    or current["path_before"] != previous["path_after"]):
                errors.append(f"产物修订链断裂：{artifact_id}")
        entry = project["artifacts"].get(artifact_id)
        if entry and (chain[-1]["sha256_after"] != entry["sha256"]
                      or chain[-1]["path_after"] != entry["path"]):
            errors.append(f"产物修订终点与当前登记不一致：{artifact_id}")


def _check_handoff_chain(project: dict[str, Any], loaded: dict[str, dict[str, Any]],
                         errors: list[str]) -> None:
    rels = project["handoffs"]
    if bool(rels) != bool(project["current_handoff"]):
        errors.append("交接链与 current_handoff 不一致")
    if rels and rels[-1] != project["current_handoff"]:
        errors.append("current_handoff 必须指向最后一条交接")
    previous_rel: str | None = None
    previous_after: str | None = None
    for index, rel in enumerate(rels, 1):
        handoff = loaded[rel]
        if handoff.get("sequence") != index:
            errors.append(f"交接序号不连续：{rel} 应为 {index}")
        if handoff.get("id") != f"handoff-{index:04d}":
            errors.append(f"交接 ID 与序号不一致：{rel}")
        if handoff.get("previous_handoff") != previous_rel:
            errors.append(f"交接链断裂：{rel} 的 previous_handoff 不正确")
        if previous_after is not None and handoff.get("from_state") != previous_after:
            errors.append(f"交接链状态不连续：{rel} 的 from_state 应为 {previous_after}")
        if handoff.get("coordinator") != project["coordinator"]:
            errors.append(f"交接协调者与 project.json 不一致：{rel}")
        target = handoff.get("to_state")
        after = handoff.get("state_after")
        if after != target and target != "blocked":
            errors.append(f"交接 state_after 与 to_state 不一致：{rel}")
        previous_rel = rel
        previous_after = after


def _check_project_invariants(project: dict[str, Any], loaded: dict[str, dict[str, Any]],
                              errors: list[str]) -> None:
    """Bind state, handoff chain and budget approval together.

    project.json is the root of trust, so a direct file edit must not be able to
    forge entry into a paid stage or a self-approved budget record. Everything
    here is re-checked by `validate`, which every mutating command runs first.
    """
    state = project["state"]
    rels = project["handoffs"]
    if rels:
        last_after = loaded[rels[-1]].get("state_after")
        if state != last_after:
            errors.append(
                f"project.json 状态 {state!r} 与交接链末端 {last_after!r} 不一致；"
                "状态只能由交接推进，不能直接改写根文件"
            )
    elif state not in ("initialized", "input_validated"):
        errors.append(f"没有任何交接记录，却声称已进入 {state!r}")

    paid = project["paid_generation"]
    if paid["approved"]:
        approver = paid["approved_by"]
        if not approver or approver == project["coordinator"]:
            errors.append("付费生成批准人不能是协调者本人；须记录用户/人类身份")
        if not (paid["approved_at"] and paid["scope"] and paid["confirmation"]):
            errors.append("付费生成批准记录缺少批准时间、范围或确认内容")

    if state == "blocked" and project.get("blocked_from") not in STAGES:
        errors.append("blocked 项目必须记录有效的 blocked_from，不能为空或非法值")

    effective = project.get("blocked_from") if state == "blocked" else state
    if effective in PAID_STAGES and not paid["approved"]:
        errors.append(
            f"项目已进入付费生成阶段（{effective}），但 paid_generation.approved 为 false；"
            "必须先由用户确认预算并运行 project approve"
        )


def validate_project(root: Path, advance: bool = False,
                     allow_artifact_drift: bool = False) -> dict[str, Any]:
    """Full contract check.

    `allow_artifact_drift` is used ONLY by `artifact refresh`: it downgrades
    artifact hash mismatches to warnings so that refresh can repair a legitimately
    rewritten file. Every other invariant stays fail-closed.
    """
    root = Path(root).expanduser().resolve()
    errors: list[str] = []
    warnings: list[str] = []
    drift_notes: list[str] = []
    hash_errors = drift_notes if allow_artifact_drift else errors
    try:
        project = _read_json(root / "project.json")
        _validate_document(project, "project.schema.json", "project.json")
        manifest_path = _project_file(root, project["inputs"]["manifest"], "输入清单")
        manifest = _read_json(manifest_path)
        _validate_document(manifest, "input.schema.json", "input/input.json")
        _reject_secret_keys(project, "project.json")
        _reject_secret_keys(manifest, "input/input.json")
        brief = _project_file(root, project["inputs"]["brief"], "brief")
        if not brief.is_file():
            errors.append(f"brief 不存在：{project['inputs']['brief']}")
        else:
            # _scan_text_file already runs the secret check on the text content;
            # calling _reject_secret_keys here would append the same error twice.
            _scan_text_file(brief, "input/brief.md", errors)
        if project["profile"] != manifest["profile"]:
            errors.append("project.json 与 input/input.json 的配置名不一致")
        _check_hash(root, manifest["reference"]["path"], manifest["reference"]["sha256"],
                    "参考视频", errors)
        for asset in manifest["assets"]:
            rel = _relative_path(root, asset["path"], "素材路径")
            path = _project_file(root, rel, "素材路径")
            if asset["required"] and asset["status"] == "missing":
                errors.append(f"缺少必填素材：{asset['id']}")
            if asset["status"] == "present":
                if not path.is_file():
                    errors.append(f"登记为 present 的素材不存在：{asset['id']}")
                elif asset.get("sha256") and _sha256(path) != asset["sha256"]:
                    errors.append(f"素材哈希不符：{asset['id']}")
                elif not asset.get("sha256"):
                    warnings.append(f"素材 {asset['id']} 未记录 sha256")
                _scan_text_file(path, f"素材 {asset['id']}", errors)
            if asset["rights"] == "unknown":
                warnings.append(f"素材 {asset['id']} 的授权状态仍为 unknown")
        for artifact_id, artifact in project["artifacts"].items():
            _relative_path(root, artifact["path"], f"产物 {artifact_id}")
            _check_hash(root, artifact["path"], artifact["sha256"], f"产物 {artifact_id}", hash_errors)
            _scan_text_file(_project_file(root, artifact["path"], f"产物 {artifact_id}"),
                            f"产物 {artifact_id}", errors)
        tasks = _load_tasks(root, project)
        _check_task_conflicts(tasks)
        # A task file that exists on disk but is no longer indexed must not be
        # allowed to escape conflict checks (direct-edit attack model).
        work_dir = _project_file(root, "work", "任务目录") if (root / "work").is_dir() else None
        if work_dir:
            indexed = set(project["tasks"].values())
            for orphan in sorted(work_dir.glob("*/task.json")):
                rel = orphan.relative_to(root).as_posix()
                if rel not in indexed:
                    errors.append(f"存在未登记的任务文件：{rel}")
        for task_id, task in tasks.items():
            for output_rel in task.get("outputs", []):
                _relative_path(root, output_rel, f"任务产物 {task_id}")
                _scan_text_file(_project_file(root, output_rel, f"任务产物 {task_id}"),
                                f"任务产物 {task_id}", errors)
        superseded = _superseded_artifact_pairs(project)
        loaded_handoffs: dict[str, dict[str, Any]] = {}
        for handoff_rel in project["handoffs"]:
            handoff_path = _project_file(root, handoff_rel, "交接")
            handoff = _read_json(handoff_path)
            loaded_handoffs[handoff_rel] = handoff
            _validate_document(handoff, "handoff.schema.json", handoff_rel)
            _reject_secret_keys(handoff, handoff_rel)
            for input_rel in handoff["inputs"]:
                _relative_path(root, input_rel, "交接输入")
            for artifact in handoff["artifacts"]:
                _relative_path(root, artifact["path"], f"交接产物 {artifact['id']}")
                if (artifact["path"], artifact["sha256"]) not in superseded:
                    _check_hash(root, artifact["path"], artifact["sha256"],
                                f"交接产物 {artifact['id']}", hash_errors)
                _scan_text_file(_project_file(root, artifact["path"], f"交接产物 {artifact['id']}"),
                                f"交接产物 {artifact['id']}", errors)
            md_path = handoff_path.with_suffix(".md")
            if not md_path.is_file() or _read_text(md_path) != render_handoff_md(handoff):
                errors.append(f"交接摘要与 JSON 不一致：{handoff_rel}")
            _scan_text_file(md_path, f"{handoff_rel}.md", errors)
        _check_revision_chain(root, project, loaded_handoffs, errors)
        _check_handoff_chain(project, loaded_handoffs, errors)
        _check_project_invariants(project, loaded_handoffs, errors)
        current_handoff = _load_current_handoff(root, project, loaded_handoffs)
    except (ProjectError, KeyError, TypeError, ValueError, AttributeError, RuntimeError) as exc:
        errors.append(f"项目契约损坏：{exc}")
        return {"state": "unknown", "errors": errors, "warnings": warnings,
                "artifacts": 0, "tasks": {}, "handoffs": 0}

    if not errors and advance and project["state"] == "initialized":
        with _lock(root / "project.json"):
            locked_project = _read_json(root / "project.json")
            if locked_project.get("state") == "initialized" and _snapshot_matches(project, locked_project):
                locked_project["state"] = "input_validated"
                locked_project["updated_at"] = _now()
                _write_json(root / "project.json", locked_project)
                _write_text(root / "HANDOFF.md", render_current_handoff_md(locked_project, current_handoff))
            project = locked_project
            current_handoff = _load_current_handoff(root, project)

    expected_md = render_current_handoff_md(project, current_handoff)
    actual_md_path = _project_file(root, project["current_handoff_md"], "当前交接摘要")
    if not actual_md_path.is_file() or _read_text(actual_md_path) != expected_md:
        errors.append(f"HANDOFF.md 与 project.json 不一致：{project['current_handoff_md']}")
    _scan_text_file(actual_md_path, "HANDOFF.md", errors)
    if allow_artifact_drift:
        warnings.extend(drift_notes)

    return {
        "state": project["state"],
        "errors": errors,
        "warnings": warnings,
        "artifacts": len(project["artifacts"]),
        "tasks": {k: "registered" for k in project["tasks"]},
        "handoffs": len(project["handoffs"]),
    }


def _visible_task_state(task: dict[str, Any]) -> str:
    if task["state"] == "in_progress" and task.get("lease_expires_at"):
        try:
            if _parse_time(task["lease_expires_at"]) < datetime.now(timezone.utc):
                return "stale"
        except ValueError:
            return "stale"
    return task["state"]


def status_project(root: Path) -> dict[str, Any]:
    root = Path(root).expanduser().resolve()
    project = _read_json(root / "project.json")
    _validate_document(project, "project.schema.json", "project.json")
    tasks: dict[str, str] = {}
    warnings: list[str] = []
    for task_id, task_rel in project["tasks"].items():
        try:
            task = _read_json(_project_file(root, task_rel, f"任务 {task_id}"))
            _validate_document(task, "task.schema.json", f"任务 {task_id}")
            state = _visible_task_state(task)
            tasks[task_id] = state
            if state == "stale":
                warnings.append(f"任务 {task_id} 的租约已过期，须由协调者显式回收")
        except (ProjectError, KeyError, TypeError, ValueError) as exc:
            tasks[task_id] = "missing"
            warnings.append(f"任务 {task_id} 文件不可读：{exc}")
    return {
        "project_id": project["project_id"],
        "state": project["state"],
        "current_handoff": project["current_handoff"],
        "current_handoff_md": project["current_handoff_md"],
        "artifacts": len(project["artifacts"]),
        "handoffs": len(project["handoffs"]),
        "tasks": tasks,
        "warnings": warnings,
    }


def render_handoff_md(handoff: dict[str, Any]) -> str:
    """Human-readable summary plus an exact JSON mirror that validation compares."""
    lines = [
        f"# 交接 {handoff['sequence']:04d}：{handoff['state_after']}",
        "",
        handoff["summary"],
        "",
        f"- 执行者：`{handoff['agent']}`；协调者：`{handoff['coordinator']}`",
        f"- 状态：`{handoff['from_state']}` → `{handoff['state_after']}`",
        f"- 上一条交接：`{handoff['previous_handoff'] or '无'}`",
        "",
        "## 给下一个 Agent 的动作",
        "",
    ]
    lines += ([f"{i}. {item}" for i, item in enumerate(handoff["next_actions"], 1)]
              or ["- 无（阻塞交接）"])
    lines += ["", "## 验收", ""]
    lines += ([f"- {item['check']}：`{item['status']}` — {item['evidence']}"
               for item in handoff["acceptance"]] or ["- 无"])
    lines += ["", "## 产物（SHA-256 前缀）", ""]
    lines += ([f"- `{item['id']}` → `{item['path']}`（`{item['sha256'][:12]}…`）"
               for item in handoff["artifacts"]] or ["- 无"])
    if handoff["decisions"]:
        lines += ["", "## 决策", ""] + [f"- {item}" for item in handoff["decisions"]]
    if handoff["open_questions"]:
        lines += ["", "## 未决问题", ""] + [f"- {item}" for item in handoff["open_questions"]]
    lines += [
        "",
        "此摘要由同名 JSON 自动生成。机器权威事实以 JSON 为准，不要手工改写本文件。",
        "",
        "```json",
        json.dumps(handoff, ensure_ascii=False, indent=2),
        "```",
        "",
    ]
    return "\n".join(lines)


def _parse_artifact_spec(root: Path, spec: str, stage: str) -> dict[str, Any]:
    if "=" in spec:
        artifact_id, rel = spec.split("=", 1)
    else:
        rel = spec
        artifact_id = Path(rel).stem
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", artifact_id):
        raise ProjectError(f"产物 ID 无效：{artifact_id!r}")
    rel = _relative_path(root, rel, "产物路径")
    path = _project_file(root, rel, "产物路径")
    if not path.is_file():
        raise ProjectError(f"产物不存在：{rel}")
    kind = "analysis" if rel.startswith("analysis/") else "output" if rel.startswith("output/") else "other"
    return {"id": artifact_id, "path": rel, "sha256": _sha256(path), "kind": kind, "stage": stage}


def _parse_acceptance(specs: Iterable[str]) -> list[dict[str, str]]:
    results = []
    for spec in specs:
        if "=" not in spec or ":" not in spec:
            raise ProjectError(f"验收格式应为 CHECK=passed|failed:EVIDENCE：{spec!r}")
        check, remainder = spec.split("=", 1)
        status, evidence = remainder.split(":", 1)
        if status not in ("passed", "failed"):
            raise ProjectError(f"验收状态只能是 passed 或 failed：{status!r}")
        results.append({"check": check, "status": status, "evidence": evidence})
    return results


def _transition(project: dict[str, Any], target: str, open_questions: list[str], summary: str) -> tuple[str, str | None, str | None]:
    current = project["state"]
    if target != "blocked" and target not in STAGES:
        raise ProjectError(f"未知状态：{target!r}")
    if current == "initialized" and target != "blocked":
        raise ProjectError("请先运行 rea-video project validate，不能通过 handoff 绕过输入校验")
    if target == "blocked":
        reason = open_questions[0] if open_questions else summary
        return "blocked", current, reason
    if current == "blocked":
        previous = project.get("blocked_from")
        if previous not in STAGES:
            raise ProjectError("blocked 项目缺少有效的 blocked_from，无法恢复")
        i = STAGES.index(previous)
        if target not in (previous, STAGES[i + 1] if i + 1 < len(STAGES) else previous):
            raise ProjectError(f"blocked 项目只能恢复到 {previous} 或其下一阶段：{target!r}")
        return target, None, None
    if current == "delivered" and target != current:
        raise ProjectError("delivered 是终态，不能自动跳到其他阶段")
    i = STAGES.index(current)
    if target == current:
        return current, None, None
    if i + 1 >= len(STAGES) or STAGES[i + 1] != target:
        raise ProjectError(f"非法状态跳转：{current} → {target}；允许同阶段重试或进入下一阶段")
    return target, None, None


def create_handoff(root: Path, args: Any) -> dict[str, Any]:
    root = Path(root).expanduser().resolve()
    preflight = validate_project(root, advance=False)
    if preflight["errors"]:
        raise ProjectError("前序项目校验失败：" + "；".join(preflight["errors"]))

    project = _read_json(root / "project.json")
    _require_coordinator(project, args.coordinator, "生成正式交接")
    target = args.to_state
    if target == "remaking" and not project["paid_generation"]["approved"]:
        raise ProjectError("进入 remaking 前必须先由用户确认预算，运行 rea-video project approve")
    decisions = list(args.decisions)
    open_questions = list(args.open_questions)
    next_actions = list(args.next_actions)
    acceptance = _parse_acceptance(args.acceptance)
    artifacts = [_parse_artifact_spec(root, spec, target) for spec in args.artifact]
    state_after, blocked_from, blocked_reason = _transition(project, target, open_questions, args.summary)
    if target != "blocked":
        if not artifacts:
            raise ProjectError("完成阶段交接必须至少登记一个产物")
        if not acceptance:
            raise ProjectError("完成阶段交接必须至少登记一项验收")
        if any(item["status"] == "failed" for item in acceptance):
            raise ProjectError("存在未通过的验收，不能声明阶段完成；请改用 --to-state blocked")
        if not next_actions:
            raise ProjectError("完成阶段交接必须写明下一个 Agent 的动作")
    if state_after == "blocked" and not open_questions:
        open_questions = [blocked_reason or args.summary]

    sequence = len(project["handoffs"]) + 1
    handoff_rel = f"handoff/{sequence:04d}-{target}.json"
    if _project_file(root, handoff_rel, "交接").exists():
        raise ProjectError(f"交接文件已存在，拒绝覆盖：{handoff_rel}")
    input_paths = [project["inputs"]["manifest"], project["inputs"]["brief"]]
    input_paths += [artifact["path"] for artifact in project["artifacts"].values()]
    inputs = list(dict.fromkeys(input_paths))
    handoff = {
        "schema_version": SCHEMA_VERSION,
        "id": f"handoff-{sequence:04d}",
        "sequence": sequence,
        "created_at": _now(),
        "from_state": project["state"],
        "to_state": target,
        "state_after": state_after,
        "agent": args.by,
        "coordinator": project["coordinator"],
        "summary": args.summary,
        "inputs": inputs,
        "artifacts": artifacts,
        "decisions": decisions,
        "open_questions": open_questions,
        "next_actions": next_actions,
        "acceptance": acceptance,
        "previous_handoff": project["current_handoff"],
    }
    _validate_document(handoff, "handoff.schema.json", handoff_rel)
    _reject_secret_keys(handoff, handoff_rel)

    with _lock(root / "project.json"):
        locked_project = _read_json(root / "project.json")
        if not _snapshot_matches(project, locked_project):
            raise ProjectError("项目状态已被其他协调者更新；请重新运行 project status 后再交接")
        for artifact in artifacts:
            prior = locked_project["artifacts"].get(artifact["id"])
            if prior and (prior["path"] != artifact["path"] or prior["sha256"] != artifact["sha256"]):
                raise ProjectError(
                    f"产物 ID {artifact['id']!r} 已登记且内容或路径不同；历史交接只追加不覆盖，请改用新 ID"
                )
        for artifact in artifacts:
            locked_project["artifacts"][artifact["id"]] = {
                "path": artifact["path"], "sha256": artifact["sha256"],
                "kind": artifact["kind"], "stage": artifact["stage"],
            }
        locked_project["handoffs"].append(handoff_rel)
        locked_project["current_handoff"] = handoff_rel
        locked_project["state"] = state_after
        locked_project["blocked_from"] = blocked_from
        locked_project["blocked_reason"] = blocked_reason
        locked_project["updated_at"] = handoff["created_at"]
        _write_json(root / handoff_rel, handoff)
        _write_text(root / str(Path(handoff_rel).with_suffix(".md")), render_handoff_md(handoff))
        _write_json(root / "project.json", locked_project)
        _write_text(root / "HANDOFF.md", render_current_handoff_md(locked_project, handoff))
    return {"handoff": handoff_rel, "summary": str(Path(handoff_rel).with_suffix(".md")).replace("\\", "/"),
            "state": state_after, "artifacts": [a["id"] for a in artifacts]}


def _scope_key(value: str) -> str:
    """Canonical POSIX form so `work//a`, `work/./a` and `work/a` collide."""
    return str(PurePosixPath(value)).rstrip("/")


def _scope_overlap(a: str, b: str) -> bool:
    a, b = _scope_key(a), _scope_key(b)
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def _is_active_task(state: str) -> bool:
    return state != "completed"


def _load_tasks(root: Path, project: dict[str, Any]) -> dict[str, dict[str, Any]]:
    tasks = {}
    for task_id, rel in project["tasks"].items():
        task = _read_json(_project_file(root, rel, f"任务 {task_id}"))
        _validate_document(task, "task.schema.json", f"任务 {task_id}")
        tasks[task_id] = task
    return tasks


def _check_task_conflicts(tasks: dict[str, dict[str, Any]], candidate: dict[str, Any] | None = None) -> None:
    entries = [(task_id, task, scope) for task_id, task in tasks.items()
               if _is_active_task(_visible_task_state(task)) for scope in task["write_paths"]]
    if candidate:
        entries += [(candidate["task_id"], candidate, scope) for scope in candidate["write_paths"]]
    for i, (left_id, _, left) in enumerate(entries):
        for right_id, _, right in entries[i + 1:]:
            if left_id != right_id and _scope_overlap(left, right):
                raise ProjectError(f"并行任务写入范围冲突：{left_id}:{left} 与 {right_id}:{right}")


def _within_scope(path: str, scope: str) -> bool:
    return path == scope or path.startswith(scope.rstrip("/") + "/")


def dispatch_task(root: Path, args: Any) -> dict[str, Any]:
    root = Path(root).expanduser().resolve()
    preflight = validate_project(root, advance=False)
    if preflight["errors"]:
        raise ProjectError("前序项目校验失败：" + "；".join(preflight["errors"]))
    project = _read_json(root / "project.json")
    if args.task_cmd in ("create", "reclaim"):
        _require_coordinator(project, args.coordinator, f"任务动作 {args.task_cmd}")
    task_id = args.id
    if not TASK_ID.fullmatch(task_id):
        raise ProjectError(f"任务 ID 无效：{task_id!r}")
    existing = _load_tasks(root, project)

    if args.task_cmd == "create":
        if task_id in project["tasks"]:
            raise ProjectError(f"任务已存在：{task_id}")
        write_paths = [_relative_path(root, value, "任务写入范围") for value in args.write_paths]
        prefix = f"work/{task_id}"
        for path in write_paths:
            if not _within_scope(path, prefix):
                raise ProjectError(f"任务只能写自己的目录：{prefix}/，收到 {path!r}")
        inputs = [_relative_path(root, value, "任务输入") for value in args.inputs]
        stamp = _now()
        task = {
            "schema_version": SCHEMA_VERSION,
            "task_id": task_id,
            "title": args.title,
            "stage": args.stage or project["state"],
            "state": "pending",
            "owner": None,
            "write_paths": write_paths,
            "inputs": inputs,
            "acceptance": list(args.acceptance),
            "outputs": [],
            "summary": None,
            "reason": None,
            "created_at": stamp,
            "updated_at": stamp,
            "lease_expires_at": None,
        }
        _validate_document(task, "task.schema.json", f"任务 {task_id}")
        _reject_secret_keys(task, f"任务 {task_id}")
        _check_task_conflicts(existing, task)
        task_rel = f"work/{task_id}/task.json"
        with _lock(root / "project.json"):
            locked_project = _read_json(root / "project.json")
            if not _snapshot_matches(project, locked_project):
                raise ProjectError("项目状态已被其他协调者更新；请重新运行 project status 后再创建任务")
            locked_tasks = _load_tasks(root, locked_project)
            if task_id in locked_project["tasks"]:
                raise ProjectError(f"任务已存在：{task_id}")
            _check_task_conflicts(locked_tasks, task)
            if _project_file(root, task_rel, "任务文件").exists():
                raise ProjectError(f"任务文件已存在，拒绝覆盖：{task_rel}")
            _write_json(root / task_rel, task)
            locked_project["tasks"][task_id] = task_rel
            locked_project["updated_at"] = _now()
            _write_json(root / "project.json", locked_project)
        return {"task": task_id, "state": "pending", "write_paths": write_paths}

    if task_id not in existing:
        raise ProjectError(f"任务不存在：{task_id}")
    task = existing[task_id]
    task_rel = project["tasks"][task_id]
    task_path = _project_file(root, task_rel, "任务文件")
    with _lock(task_path):
        task = _read_json(task_path)
        visible = _visible_task_state(task)
        stamp = _now()
        if args.task_cmd == "claim":
            if visible not in ("pending", "stale"):
                raise ProjectError(f"任务当前由 {task['owner']!r} 持有，状态 {visible}，不能再次认领")
            if args.lease_minutes <= 0:
                raise ProjectError("租约分钟数必须大于 0")
            task["state"] = "in_progress"
            task["owner"] = args.by
            task["lease_expires_at"] = (datetime.now(timezone.utc) + timedelta(minutes=args.lease_minutes)).isoformat(timespec="seconds").replace("+00:00", "Z")
            task["updated_at"] = stamp
        elif args.task_cmd == "complete":
            if visible == "stale":
                raise ProjectError("任务租约已过期，须由协调者显式回收后重跑")
            if visible != "in_progress" or task["owner"] != args.by:
                raise ProjectError("只有当前持租约的 Agent 能提交任务")
            outputs = [_relative_path(root, value, "任务产物") for value in args.outputs]
            if not outputs:
                raise ProjectError("完成任务必须至少登记一个产物")
            for output in outputs:
                if not any(_within_scope(output, scope) for scope in task["write_paths"]):
                    raise ProjectError(f"产物不在任务声明的写入范围内：{output}")
                if not _project_file(root, output, "任务产物").is_file():
                    raise ProjectError(f"任务产物不存在：{output}")
            task["state"] = "completed"
            task["outputs"] = outputs
            task["summary"] = args.summary
            task["reason"] = None
            task["lease_expires_at"] = None
            task["updated_at"] = stamp
        elif args.task_cmd == "block":
            if visible == "stale":
                raise ProjectError("任务租约已过期，不能由原 Agent 标记阻塞")
            if visible != "in_progress" or task["owner"] != args.by:
                raise ProjectError("只有当前持租约的 Agent 能阻塞任务")
            task["state"] = "blocked"
            task["reason"] = args.reason
            task["lease_expires_at"] = None
            task["updated_at"] = stamp
        elif args.task_cmd == "reclaim":
            if visible not in ("blocked", "stale"):
                raise ProjectError(f"只有 blocked 或 stale 任务可以回收，当前状态 {visible}")
            task["state"] = "pending"
            task["owner"] = None
            task["outputs"] = []
            task["summary"] = None
            task["reason"] = None
            task["lease_expires_at"] = None
            task["updated_at"] = stamp
            # Reactivating a scope can collide with a task created while this one
            # was blocked; re-check under the task lock before persisting.
            others = {key: value for key, value in _load_tasks(root, project).items() if key != task_id}
            _check_task_conflicts(others, task)
        else:
            raise ProjectError(f"未知任务动作：{args.task_cmd!r}")
        _validate_document(task, "task.schema.json", f"任务 {task_id}")
        _reject_secret_keys(task, f"任务 {task_id}")
        _write_json(task_path, task)
    return {"task": task_id, "state": task["state"], "owner": task["owner"]}
