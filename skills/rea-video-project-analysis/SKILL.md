---
name: rea-video-project-analysis
description: 在结构化项目中调用原版 rea-video 拆解参考视频、执行看图复核，并把 breakdown 和报告登记为正式交接。
---

# 拆解阶段

1. 先 `rea-video project validate <dir>` 和 `rea-video project status <dir>`。状态必须是 `input_validated` 或 `decomposed`，否则停止。
2. 使用原版 `rea-video` Skill 和 CLI，输出目录固定为 `<dir>/analysis`，不要重写 `breakdown.json` 结构。
3. 按原版流程执行 `review`、逐镜看图复核与 `apply-review`。VLM 是否生效以 `summary` 的 `VLM:` 行为准。
4. 检查 `analysis/breakdown.json`、`analysis/report.html`、`analysis/report.md` 和字幕产物是否存在。
5. 通过正式交接把状态推进到 `decomposed`；若已完成人工复核，可再追加一条同状态或 `reviewed` 交接。每条非阻塞交接都登记产物、SHA-256、验收结果和下一个 Agent 的动作。协调者身份读取自 `project.json` 的 `coordinator` 字段（默认 `coordinator`），每条 `handoff` 都必须用 `--coordinator coordinator` 声明它。
6. 未确认的制作方式写入 `open_questions`，不要编造确定结论；不得直接复制原片画面作为新商业素材。

示例：

```bash
rea-video project handoff <dir> --to-state decomposed --by analysis-agent --coordinator coordinator \
  --summary "完成逐镜拆解" --artifact breakdown=analysis/breakdown.json \
  --artifact report=analysis/report.html \
  --acceptance "breakdown_exists=passed:文件与哈希已登记" \
  --next "复核低置信镜头"
```
