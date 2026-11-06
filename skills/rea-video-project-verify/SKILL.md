---
name: rea-video-project-verify
description: 验收结构化视频项目最终成片，检查画幅、镜头结构、字幕、音画同步、素材授权、API 凭据泄露和交接完整性，再推进 delivered。
---

# 验收阶段

1. 运行 `rea-video project validate <dir>` 与 `rea-video project status <dir>`。存在 Schema、路径、哈希、摘要或状态错误时不得交付。
2. 在独立播放器中检查成片，不依赖任务状态 `completed`：
   - 画幅、语言、全片完整播放；
   - 镜头数量、顺序、时长和结构保持；
   - 人物、门店、Logo、菜品、名称、价格、地址、字幕正确；
   - 音画同步，手机端字幕可读；
   - AI 内容标识、音乐与肖像授权符合发布要求。
3. 搜索项目 JSON、MD、任务和交接中是否出现密钥值；只应存在环境变量名。
4. 逐项记录实际费用、重做原因和未决问题。未通过的项目创建 `blocked` 交接，不得标为 `delivered`；`blocked` / `stale` 的任务只能由协调者用 `rea-video project task reclaim <dir> --id ID --coordinator coordinator` 恢复，这是唯一恢复路径，且会重新检查并行写入范围冲突。
5. 全部通过后用 `rea-video project handoff ... --coordinator coordinator` 依次进入 `assembled`、`verified`、`delivered`，每条交接附最终产物哈希和验收结果。
6. 已登记的产物 ID 不可被覆盖：内容发生变化的产物必须用新的 ID 登记，用同一 ID 重新登记不同路径或 SHA-256 会被拒绝。

示例：

```bash
rea-video project task reclaim <dir> --id host-assets --coordinator coordinator
rea-video project handoff <dir> --to-state verified --by verify-agent --coordinator coordinator \
  --summary "成片验收通过" --artifact final=output/final.mp4 \
  --acceptance "final_playable=passed:独立播放器全片通过" --next "交付"
```

交付标准是文件可独立接手，不依赖任何一段聊天上下文。
