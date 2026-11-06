# rea-video：给 Agent 的说明

- 第一次使用：运行 `bash scripts/install_skill.sh`（安装 CLI 并把 skill 链接到常见 agent 目录；需要 ffmpeg、Python ≥ 3.10），再 `rea-video --help` 确认。
- 用户要拆解视频、判断制作方式、标真实素材、拆音乐音效、复刻时，**先读 `skills/rea-video/SKILL.md`**，按里面的流程做，并复核低置信镜头。
- 用户要结构化项目、输入契约、交接给下一个 Agent、并行任务或当前项目状态时，**先读 `skills/rea-video-project/SKILL.md`**，再按阶段进入 `skills/rea-video-project-*/SKILL.md`；开工前必须 `rea-video project validate` 和 `rea-video project status`。
- 仓库分工：
  - `skills/rea-video/`：原版单视频拆解 skill
  - `skills/rea-video-project*/`：结构化项目入口与输入、拆解、复刻、验收阶段 skill
  - `rea_video/`：CLI 和拆解逻辑；结构化项目层在 `rea_video/project/`，Schema 在 `rea_video/project/schemas/`；模型能力地图在 `rea_video/capabilities/`，capsule-cinema 对接脚本在 `rea_video/integrations/`
  - `examples/profiles/`：通用项目层可承载的业务配置示例
  - `docs/`：给人看的文档；`docs/structured-projects.md` 是项目层契约，`docs/dev/` 是开发文档
  - `eval/`、`tests/`：评测集和测试
- 开发约定：
  - 规则改动后跑 `python3 -m pytest -q` 和 `rea-video eval eval/labels.yaml` / `eval/holdout*.yaml`，结果更新到 `eval/RESULTS.md`
  - 结构化项目层改动先跑 `python3 -m pytest tests/test_project_workflow.py -q`，再跑全量测试；不得通过手工改 MD 绕过 JSON 权威
  - 能力地图改完跑 `python3 scripts/validate_capabilities.py --matrix`
- 不要提交任何密钥；VLM 配置通过 `--env-file` 读取。
