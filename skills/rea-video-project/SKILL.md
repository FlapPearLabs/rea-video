---
name: rea-video-project
description: 结构化视频项目入口。用户要建立可交接、可重跑、可并行分工的视频项目，或问 project.json、input.json、handoff、任务所有权、当前状态时，先读本 Skill 并路由到阶段 Skill。
---

# rea-video 结构化项目入口

你是协调 Agent。目标是让下一位 Agent 只凭项目文件就能接手，不依赖聊天上下文。

## 先做

1. 若项目不存在，运行 `rea-video project init <dir> --reference <video> --title "<title>" --profile <profile>`，随后 `rea-video project validate <dir>`。
2. 若项目已存在，先运行 `rea-video project validate <dir>` 和 `rea-video project status <dir>`。
3. 校验失败时停止，向用户列明阻塞项；不得手工绕过 Schema、哈希或状态机。
4. 阅读 `HANDOFF.md`，并以它指向的 `handoff/*.json` 为机器权威交接。

## 路由

- 准备输入、素材和授权：使用 `rea-video-project-input`。
- 原版视频拆解、看图复核、生成交接：使用 `rea-video-project-analysis`。
- 复刻计划、任务分工、素材生成和拼装：使用 `rea-video-project-remake`。
- 最终验收、交付、失败回滚：使用 `rea-video-project-verify`。
- 记录付费生成预算确认（用 `rea-video project approve ... --coordinator coordinator`）或从 `blocked` / `stale` 恢复任务（用 `rea-video project task reclaim ... --coordinator coordinator`）：这两项是协调者专属动作，在本入口直接用带 `--coordinator coordinator` 的命令完成。
- 只做原版单视频拆解，不涉及结构化项目：仍使用原来的 `rea-video` Skill。

## 硬规则

- `project.json` 与 `handoff/*.json` 是机器权威；MD 是生成摘要，不手工改。
- 不得把聊天记录当作交接；不得覆盖历史交接。
- 状态只允许同阶段重试或前进一阶段；任何阶段可以显式 `blocked`。
- 密钥、Token、密码不写入任何项目文件，只记录环境变量名。
- 付费模型调用前先 dry run 或提交预算，等待用户明确确认；预算确认先用 `rea-video project approve ... --coordinator coordinator` 记录，否则任何进入 `remaking` 的 `handoff` 会被代码拒绝并返回非零。
- `approve`、`handoff`、`task create`、`task reclaim` 必须带 `--coordinator coordinator`（值须等于 `project.json` 的 `coordinator` 字段，默认 `coordinator`），这是角色声明门禁；`claim`、`complete`、`block` 不带 `--coordinator`。
- 并行任务写入范围不得重叠；所有显式写盘动作都有 `--coordinator` 门，执行 Agent 只写 `work/<task_id>/`；`task reclaim ... --coordinator coordinator` 是 `blocked` / `stale` 的唯一恢复路径。唯一例外是 `project validate` 在 `initialized` 状态下的固定自动推进（写入内容不可注入，详见 `docs/structured-projects.md`）。
- `project validate` 会交叉校验 `state`、交接链末端与 `paid_generation`：直接改写根文件伪造 `remaking` 状态或协调者自批准记录都会被拒绝。不得尝试通过同时改写多个文件绕过门禁——用户确认的真实性由流程纪律保证，不是代码能验证的事实。

完整契约见仓库 `docs/structured-projects.md`，Schema 见 `rea_video/project/schemas/`。
