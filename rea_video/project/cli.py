"""CLI adapters for the structured project layer. Public seam: rea-video project."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core import (
    ProjectError,
    approve_paid_generation,
    create_handoff,
    dispatch_task,
    init_project,
    refresh_artifact,
    status_project,
    validate_project,
)

DEFAULT_COORDINATOR = "coordinator"
# Nothing in this command family should ever print a traceback to an agent.
CLI_ERRORS = (ProjectError, OSError, UnicodeError, KeyError, TypeError, ValueError,
              AttributeError, RuntimeError)


def _guard(func):
    def wrapped(args: argparse.Namespace) -> int:
        try:
            return func(args)
        except CLI_ERRORS as exc:
            print(f"rea-video: {exc}", file=sys.stderr)
            return 2
    return wrapped


def _add_coordinator(parser: argparse.ArgumentParser, default: str | None = None) -> None:
    parser.add_argument(
        "--coordinator",
        required=default is None,
        default=default,
        help="协调者标识，必须等于 project.json 的 coordinator 字段（角色声明门，不是身份认证）",
    )


def _cmd_init(args: argparse.Namespace) -> int:
    result = init_project(Path(args.root), Path(args.reference), args.title,
                          args.profile, args.coordinator)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    result = validate_project(Path(args.root), advance=True)
    if result["errors"]:
        print("rea-video: 项目校验失败：\n- " + "\n- ".join(result["errors"]), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    print(json.dumps(status_project(Path(args.root)), ensure_ascii=False, indent=2))
    return 0


def _cmd_approve(args: argparse.Namespace) -> int:
    result = approve_paid_generation(Path(args.root), args.by, args.scope,
                                     args.confirmation, args.coordinator)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _cmd_handoff(args: argparse.Namespace) -> int:
    print(json.dumps(create_handoff(Path(args.root), args), ensure_ascii=False, indent=2))
    return 0


def _cmd_task(args: argparse.Namespace) -> int:
    print(json.dumps(dispatch_task(Path(args.root), args), ensure_ascii=False, indent=2))
    return 0


def _cmd_artifact(args: argparse.Namespace) -> int:
    print(json.dumps(refresh_artifact(Path(args.root), args), ensure_ascii=False, indent=2))
    return 0


def configure_project_parser(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="project_cmd", required=True)

    p_init = sub.add_parser("init", help="创建结构化项目、输入清单和 brief")
    p_init.add_argument("root")
    p_init.add_argument("--reference", required=True, help="参考视频，复制到 input/reference/")
    p_init.add_argument("--title", default="")
    p_init.add_argument("--profile", default="generic")
    _add_coordinator(p_init, default=DEFAULT_COORDINATOR)
    p_init.set_defaults(project_func=_guard(_cmd_init))

    p_validate = sub.add_parser("validate", help="校验输入、Schema、路径、哈希、交接和任务")
    p_validate.add_argument("root")
    p_validate.set_defaults(project_func=_guard(_cmd_validate))

    p_status = sub.add_parser("status", help="查看项目状态、产物和任务")
    p_status.add_argument("root")
    p_status.set_defaults(project_func=_guard(_cmd_status))

    p_approve = sub.add_parser("approve", help="登记用户对付费生成的预算确认")
    p_approve.add_argument("root")
    p_approve.add_argument("--by", required=True, help="用户/人类批准人，不能是协调者本人")
    p_approve.add_argument("--scope", required=True, help="本次确认的费用范围")
    p_approve.add_argument("--confirmation", required=True, help="用户确认的原文或凭据说明")
    _add_coordinator(p_approve)
    p_approve.set_defaults(project_func=_guard(_cmd_approve))

    p_handoff = sub.add_parser("handoff", help="生成机器权威交接 JSON 与自动摘要 MD")
    p_handoff.add_argument("root")
    p_handoff.add_argument("--to-state", required=True)
    p_handoff.add_argument("--by", required=True)
    p_handoff.add_argument("--summary", required=True)
    _add_coordinator(p_handoff)
    p_handoff.add_argument("--artifact", action="append", default=[], metavar="ID=PATH",
                           help="本阶段产物；路径相对项目根目录")
    p_handoff.add_argument("--acceptance", action="append", default=[], metavar="CHECK=passed|failed:EVIDENCE")
    p_handoff.add_argument("--next", dest="next_actions", action="append", default=[])
    p_handoff.add_argument("--decision", dest="decisions", action="append", default=[])
    p_handoff.add_argument("--open", dest="open_questions", action="append", default=[])
    p_handoff.set_defaults(project_func=_guard(_cmd_handoff))

    p_task = sub.add_parser("task", help="管理受控并行任务")
    task_sub = p_task.add_subparsers(dest="task_cmd", required=True)

    p_create = task_sub.add_parser("create", help="声明任务及唯一写入范围（仅协调者）")
    p_create.add_argument("root")
    p_create.add_argument("--id", required=True)
    p_create.add_argument("--title", required=True)
    p_create.add_argument("--stage", default="")
    p_create.add_argument("--write", dest="write_paths", action="append", required=True)
    p_create.add_argument("--input", dest="inputs", action="append", default=[])
    p_create.add_argument("--acceptance", action="append", default=[])
    _add_coordinator(p_create)
    p_create.set_defaults(project_func=_guard(_cmd_task))

    p_claim = task_sub.add_parser("claim", help="认领任务并取得限时租约")
    p_claim.add_argument("root")
    p_claim.add_argument("--id", required=True)
    p_claim.add_argument("--by", required=True)
    p_claim.add_argument("--lease-minutes", type=int, default=60)
    p_claim.set_defaults(project_func=_guard(_cmd_task))

    p_complete = task_sub.add_parser("complete", help="由任务所有者提交产物并验收")
    p_complete.add_argument("root")
    p_complete.add_argument("--id", required=True)
    p_complete.add_argument("--by", required=True)
    p_complete.add_argument("--output", dest="outputs", action="append", default=[])
    p_complete.add_argument("--summary", default="")
    p_complete.set_defaults(project_func=_guard(_cmd_task))

    p_block = task_sub.add_parser("block", help="显式阻塞任务，不静默当作完成")
    p_block.add_argument("root")
    p_block.add_argument("--id", required=True)
    p_block.add_argument("--by", required=True)
    p_block.add_argument("--reason", required=True)
    p_block.set_defaults(project_func=_guard(_cmd_task))

    p_reclaim = task_sub.add_parser("reclaim", help="回收 blocked / stale 任务（仅协调者）")
    p_reclaim.add_argument("root")
    p_reclaim.add_argument("--id", required=True)
    _add_coordinator(p_reclaim)
    p_reclaim.set_defaults(project_func=_guard(_cmd_task))

    p_artifact = sub.add_parser("artifact", help="管理已登记产物")
    artifact_sub = p_artifact.add_subparsers(dest="artifact_cmd", required=True)

    p_refresh = artifact_sub.add_parser(
        "refresh", help="重新登记被合法改写的产物哈希（仅协调者，append-only 修订）")
    p_refresh.add_argument("root")
    p_refresh.add_argument("--id", required=True, help="已登记产物 ID")
    _add_coordinator(p_refresh)
    p_refresh.add_argument("--path", default="",
                           help="可选：产物被移动到新路径时填写；默认沿用已登记路径")
    p_refresh.add_argument("--reason", default="",
                           help="合法改写的原因，例如 apply-review 重写了 breakdown.json")
    p_refresh.add_argument("--by", default="", help="执行者，默认协调者")
    p_refresh.set_defaults(project_func=_guard(_cmd_artifact))


def cmd_project(args: argparse.Namespace) -> int:
    return args.project_func(args)
