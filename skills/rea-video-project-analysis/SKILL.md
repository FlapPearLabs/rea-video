---
name: rea-video-project-analysis
description: 在结构化项目中调用原版 rea-video 拆解参考视频、执行看图复核，并把 breakdown 和报告登记为正式交接。
---

# 拆解阶段

> 约定：示例中的 `coordinator` 是 `init` 的默认协调者名；若 `init` 用了自定义 `--coordinator NAME`，所有 `--coordinator coordinator` 必须替换为该 `NAME`（须与 `project.json` 的 `coordinator` 字段一致）。带 `...` 的命令片段仅为路由/流程说明，不是可直接复制的命令；完整参数见 `docs/structured-projects.md`。

1. 先 `rea-video project validate <dir>` 和 `rea-video project status <dir>`。状态必须是 `input_validated` 或 `decomposed`，否则停止。
2. 使用原版 `rea-video` Skill 和 CLI，输出目录固定为 `<dir>/analysis`，不要重写 `breakdown.json` 结构。
3. 按原版流程执行 `review`、逐镜看图复核与 `apply-review`。VLM 是否生效以 `summary` 的 `VLM:` 行为准。
4. 关键：`apply-review`（或重跑 `decompose`）会**原地重写** `analysis/breakdown.json`。若 `breakdown` 此前已被登记为产物，必须在任何后续 `handoff` 之前，由协调者运行 `rea-video project artifact refresh <dir> --id breakdown --coordinator coordinator --reason "apply-review 重写了 breakdown.json"`，把新的 SHA-256 以 append-only 修订记录写入 `project.json` 并重写 `HANDOFF.md`。跳过这一步，`project validate` 会因磁盘哈希与登记哈希不符而永久失败，且没有 CLI 恢复路径；仅把产物改用新 ID 无效（旧条目仍指向同一路径），也不得手工编辑 `project.json` 伪造 `artifact_revisions`。
5. 检查 `analysis/breakdown.json`、`analysis/report.html`、`analysis/report.md` 和字幕产物是否存在。
6. 通过正式交接把状态推进到 `decomposed`；若已完成人工复核，可再追加一条同状态或 `reviewed` 交接。每条非阻塞交接都登记产物、SHA-256、验收结果和下一个 Agent 的动作。协调者身份读取自 `project.json` 的 `coordinator` 字段（默认 `coordinator`），每条 `handoff` 都必须用 `--coordinator coordinator` 声明它。
7. 未确认的制作方式写入 `open_questions`，不要编造确定结论；不得直接复制原片画面作为新商业素材。

示例（首次登记 `breakdown` 的交接，完整命令）：

```bash
rea-video project handoff <dir> --to-state decomposed --by analysis-agent --coordinator coordinator \
  --summary "完成逐镜拆解" --artifact breakdown=analysis/breakdown.json \
  --artifact report=analysis/report.html \
  --acceptance "breakdown_exists=passed:文件与哈希已登记" \
  --next "复核低置信镜头"
```

若此前已登记 `breakdown`，而 `apply-review` / 重跑 `decompose` 又原地重写了它，则下次 `handoff` 之前必须先刷新（协调者专属，否则 `project validate` 永久失败）：

```bash
rea-video project artifact refresh <dir> --id breakdown --coordinator coordinator --reason "apply-review 重写了 breakdown.json"
```
