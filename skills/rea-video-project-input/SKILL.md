---
name: rea-video-project-input
description: 初始化结构化视频项目，登记参考视频、人物/场景/产品素材、授权状态、保留项、替换项和环境变量名，并通过 project validate。
---

# 输入阶段

1. 确认参考视频及素材均有权使用；不要把他人肖像、音乐或无授权 footage 当作可复刻素材。
2. 调用 `rea-video project init`，把参考视频复制到 `input/reference/`。
3. 编辑 `input/brief.md`，写清必须保留的结构、允许替换的元素和禁止事项。
4. 编辑 `input/input.json`：每个素材必须有稳定 `id`、`kind`、相对 `path`、`required`、`status` 和 `rights`。`present` 素材写入实际 SHA-256；缺失素材标 `missing`。
5. 只记录凭据的环境变量名，不能记录值。不得添加 `api_key`、`token`、`secret`、`password` 等字段。
6. 运行 `rea-video project validate <dir>`。通过后状态应为 `input_validated`；若仍有错误，停止并报告，不进入拆解。
7. 用 `rea-video project handoff <dir> --to-state input_validated ... --coordinator coordinator` 仅在需要留下同阶段重试记录时使用；`--coordinator` 须等于 `project.json` 的 `coordinator` 字段（默认 `coordinator`）。正常首次推进由 `validate` 完成。

业务配置示例见 `examples/profiles/store_visit/`；不要整体复制示例 `input.json`，参考视频哈希必须由 `init` 针对真实文件生成。
