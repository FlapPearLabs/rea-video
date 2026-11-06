---
name: rea-video-project-remake
description: 在结构化项目中生成复刻计划、拆分并行任务、替换人物场景和素材，并在用户确认预算后拼回原时间线。
---

# 复刻阶段

> 约定：示例中的 `coordinator` 是 `init` 的默认协调者名；若 `init` 用了自定义 `--coordinator NAME`，所有 `--coordinator coordinator` 必须替换为该 `NAME`（须与 `project.json` 的 `coordinator` 字段一致）。带 `...` 的命令片段仅为路由/流程说明，不是可直接复制的命令；完整参数见 `docs/structured-projects.md`。

1. 先读取当前 `handoff/*.json`，确认拆解结论、未决问题和参考素材；运行 `project validate` 和 `project status`。
2. 根据 `brief.md` 固定镜头数量、顺序、时长、运镜、剪辑节奏、字幕和音频结构，只替换指定人物、场景、素材与文案。
3. 使用原版 `remake-plan`；先用 `remake-run --dry-run` 得到任务和费用，向用户确认预算。
4. 用户的预算确认必须先用协调者命令记录，且必须在任何进入 `remaking` 的 `handoff` 之前完成：`rea-video project approve <dir> --by user-owner --scope "复刻生成预算" --confirmation "用户已确认预算" --coordinator coordinator`。`--by` 必须填用户/人类身份，不能填协调者自己（代码拒绝自批准），且项目必须处于 `planned` 阶段。这道门禁由代码强制：未 `approve` 就执行 `project handoff --to-state remaking` 会被拒绝并返回非零退出码，无法靠手工改 MD 绕过；审批写入 `project.json` 的 `paid_generation` 字段。注意原版 `rea-video remake-run` 不受项目层拦截，仍须按本 Skill 的流程纪律在确认后才调用。
5. 并行工作必须由协调者用 `rea-video project task create` 声明（完整命令见下方示例，须带 `--coordinator coordinator` 及 `--id`、`--title`、`--write`、`--acceptance`）。执行 Agent 用 `claim` 取得租约，只写 `work/<task_id>/`，完成后用 `complete` 登记产物；失败用 `block` 并写明原因。
6. 不要在任务或交接中写入凭据值；素材授权为 `unknown` 时不得进入正式发布阶段。
7. 生成结果按原时间线 `assemble`；每条阶段交接用 `--coordinator coordinator` 声明，并登记产物哈希、验收结果、决策和下一步。
8. 若 `remake-plan` / `assemble` 的输出覆盖了已登记产物路径，须在下次 `handoff` 之前由协调者运行 `rea-video project artifact refresh <dir> --id ID --coordinator coordinator --reason "remake-plan 重写了已登记产物"` 刷新该产物的哈希（协调者专属）；跳过它，`project validate` 会因磁盘哈希与登记哈希不符而永久失败。详见 `docs/structured-projects.md`「产物合法演进」。
9. 若结构未变但人物、场景或素材不一致，创建阻塞交接，不要为了推进状态而声明通过。

示例（顺序不可颠倒，`approve` 必须先于进入 `remaking` 的 `handoff`）：

```bash
rea-video project approve <dir> --by user-owner --scope "复刻生成预算" --coordinator coordinator \
  --confirmation "用户已确认预算"
rea-video project task create <dir> --id host-assets --title "生成人物素材" --coordinator coordinator \
  --write work/host-assets --acceptance "正面照与半身照齐全"
rea-video project handoff <dir> --to-state remaking --by remake-agent --coordinator coordinator \
  --summary "复刻计划与分工就绪" --artifact plan=analysis/remake-plan.json \
  --acceptance "plan_ready=passed:计划与预算已确认" --next "生成替换素材"
```

此阶段只编排，不修改原版拆解引擎，也不把真实素材清单替换成 AI 近似结果。
