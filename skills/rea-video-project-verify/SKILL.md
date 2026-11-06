---
name: rea-video-project-verify
description: 验收结构化视频项目最终成片，检查画幅、镜头结构、字幕、音画同步、素材授权、API 凭据泄露和交接完整性，再推进 delivered。
---

# 验收阶段

> 约定：示例中的 `coordinator` 是 `init` 的默认协调者名；若 `init` 用了自定义 `--coordinator NAME`，所有 `--coordinator coordinator` 必须替换为该 `NAME`（须与 `project.json` 的 `coordinator` 字段一致）。带 `...` 的命令片段仅为路由/流程说明，不是可直接复制的命令；完整参数见 `docs/structured-projects.md`。

1. 运行 `rea-video project validate <dir>` 与 `rea-video project status <dir>`。存在 Schema、路径、哈希、摘要或状态错误时不得交付。
2. 在独立播放器中检查成片，不依赖任务状态 `completed`：
   - 画幅、语言、全片完整播放；
   - 镜头数量、顺序、时长和结构保持；
   - 人物、门店、Logo、菜品、名称、价格、地址、字幕正确；
   - 音画同步，手机端字幕可读；
   - AI 内容标识、音乐与肖像授权符合发布要求。
3. 搜索项目 JSON、MD、任务和交接中是否出现密钥值；只应存在环境变量名。密钥扫描还覆盖 `work/<task_id>/` 任务输出、`input/assets/` 的 `present` 素材以及 `password: hunter2`、`DB_PASSWORD=...` 这类明文赋值，但它是启发式扫描（heuristic），不是保证。
4. 核对 `project.json` 的 `artifact_revisions` 修订链自洽、旧哈希被 append-only 保留，并确认没有任何已登记产物被静默原地覆盖（磁盘哈希须等于最新登记哈希）；未经 `artifact refresh` 记录的改动即为篡改，`validate` 会拒绝。
5. 逐项记录实际费用、重做原因和未决问题。未通过的项目创建 `blocked` 交接，不得标为 `delivered`；`blocked` / `stale` 的任务只能由协调者用 `rea-video project task reclaim <dir> --id ID --coordinator coordinator` 恢复，这是唯一恢复路径，且会重新检查并行写入范围冲突。
6. 全部通过后用 `rea-video project handoff`（完整命令见下方示例，须带 `--coordinator coordinator` 及 `--to-state`、`--by`、`--artifact`、`--acceptance`、`--next`）依次进入 `assembled`、`verified`、`delivered`，每条交接附最终产物哈希和验收结果。
7. 已登记产物被合法原地重写时，必须先由协调者运行 `rea-video project artifact refresh <dir> --id ID --coordinator coordinator --reason "验收前重写了已登记产物"` 刷新哈希，再进行后续 `handoff`；用同一 ID 重新登记不同 SHA-256 仍会被拒绝，且不得手工编辑 `project.json` 伪造修订记录。

示例：

```bash
rea-video project task reclaim <dir> --id host-assets --coordinator coordinator
rea-video project handoff <dir> --to-state verified --by verify-agent --coordinator coordinator \
  --summary "成片验收通过" --artifact final=output/final.mp4 \
  --acceptance "final_playable=passed:独立播放器全片通过" --next "交付"
```

交付标准是文件可独立接手，不依赖任何一段聊天上下文。
