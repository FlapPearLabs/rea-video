# 结构化视频项目与多 Agent 交接

本层是原版 rea-video 的外围工程契约：原拆解引擎、原 `breakdown.json`、原报告和原 Skill 保持不变。它解决的是"输入怎么放、当前做到哪一步、下一个 Agent 凭什么继续、多个 Agent 如何不互相覆盖"。

## 1. 目录

```text
<project>/
├── project.json              # 机器权威：状态、产物索引、任务索引、当前交接
├── HANDOFF.md                # 自动生成，始终指向当前有效交接
├── input/
│   ├── brief.md              # 人读需求：保留项、替换项、边界
│   ├── input.json            # 机器权威：参考视频、素材 ID、授权状态、哈希
│   ├── reference/            # 参考视频
│   └── assets/               # 人物、门店、菜品、Logo、音频等
├── analysis/                 # 原 rea-video 的拆解产物，建议放这里
├── handoff/                  # 每次交接的 JSON + 由 JSON 生成的 MD
├── work/<task_id>/           # 并行任务唯一允许写入的目录
└── output/                   # 最终成片与导出物
```

所有路径在 JSON 中均相对项目根目录。`project.json`、`input/input.json`、`handoff/*.json` 是机器权威；`brief.md` 只写给人看；`handoff/*.md` 和 `HANDOFF.md` 自动生成，不手工维护另一套事实。

## 2. 命令

```bash
rea-video project init <dir> --reference <video.mp4> [--title "..."] [--profile store_visit] [--coordinator NAME]
rea-video project validate <dir>
rea-video project status <dir>
rea-video project approve <dir> --by user --scope "复刻生成预算" --confirmation "用户已确认预算" --coordinator coordinator
rea-video project handoff <dir> --to-state decomposed --by analysis-agent --coordinator coordinator \
  --summary "完成 7 镜拆解" --artifact breakdown=analysis/breakdown.json \
  --acceptance "breakdown_exists=passed:文件与哈希已生成" \
  --next "复核低置信镜头"

# 已登记产物被合法原地重写（apply-review / 重跑 decompose）后，由协调者刷新其哈希：
rea-video project artifact refresh <dir> --id breakdown --coordinator coordinator --reason "apply-review 重写了 breakdown.json"

rea-video project task create <dir> --id person-assets --title "准备人物素材" --coordinator coordinator \
  --write work/person-assets --acceptance "正面照与半身照齐全"
rea-video project task claim <dir> --id person-assets --by agent-a
rea-video project task complete <dir> --id person-assets --by agent-a \
  --output work/person-assets/manifest.json
rea-video project task block <dir> --id person-assets --by agent-a --reason "肖像授权未确认"
rea-video project task reclaim <dir> --id person-assets --coordinator coordinator
```

`init` 后先运行 `validate`，成功后状态从 `initialized` 进入 `input_validated`。任何后续 Agent 开工前都应运行 `validate`；Schema、路径、哈希、摘要一致性或前序验收失败时，命令返回非零并停止。

`--coordinator` 必须与 `project.json` 中的 `coordinator` 字段一致（该字段在 `init` 时设定，默认 `coordinator`）；这是一道角色声明门禁，用来标明「以协调者身份操作」，并不是身份认证。`approve`、`handoff`、`artifact refresh`、`task create`、`task reclaim` 必须带 `--coordinator`（`init` 可选，默认 `coordinator`），而 `claim`、`complete`、`block` 是执行 Agent 动作，不带 `--coordinator`。其中 `artifact refresh` 是协调者专属动作：`--path` 仅在产物被移动到新的相对路径时使用，默认沿用当前登记路径。

若 `init` 时用了自定义 `--coordinator NAME`（而非默认 `coordinator`），本文所有示例里的 `--coordinator coordinator` 都必须替换为该 `NAME`（须与 `project.json` 的 `coordinator` 字段一致）。

## 3. 状态机

```text
initialized → input_validated → decomposed → reviewed → planned
→ remaking → assembled → verified → delivered
```

只允许同阶段重试或进入下一阶段。任何阶段可以交接为 `blocked`；恢复时只能回到被阻塞的阶段或其下一阶段，并须补齐产物、验收与下一步。历史交接只追加，不覆盖。

每次非阻塞交接（代码硬门禁，缺任一项即返回非零并拒绝交接）必须包含：

- 至少一个存在的产物及 SHA-256；
- 至少一项 `passed` 验收；
- 至少一个给下一个 Agent 的动作；
- 执行者、决策、未决问题和上一条交接指针。

已登记的产物 ID 不可被后续交接直接覆盖：用同一 ID 重新登记不同路径或不同 SHA-256 仍会被拒绝。当已登记文件被**合法地原地重写**（例如 `apply-review` 重写了 `breakdown.json`，或重跑 `decompose`）时，协调者必须先运行 `project artifact refresh`，由它把旧哈希记入 append-only 的修订链并登记新哈希，之后才能继续后续 `handoff`。单纯「改用新 ID」并不能解决问题——旧条目仍指向同一路径，`project validate` 仍会比对磁盘哈希而失败。`project validate` 会校验修订链自洽，且当前磁盘哈希等于最新登记哈希；未经 `refresh` 记录的内容改动仍按篡改处理并被拒绝。详见下节「产物合法演进（artifact refresh）」。

### 产物合法演进（artifact refresh）

拆解产物被登记后，SHA-256 会「钉住」磁盘文件；而原版 `apply-review` 会原地重写 `analysis/breakdown.json`，重跑 `decompose` 也会覆盖它。若不处理，这类合法迭代会让磁盘哈希与登记哈希不符，`project validate` 从此失败且没有 CLI 恢复路径。

正确流程是：在任何后续 `handoff` 之前，由协调者运行

```bash
rea-video project artifact refresh <dir> --id breakdown --coordinator coordinator --reason "apply-review 重写了 breakdown.json"
```

命令会重新计算该文件的 SHA-256，向 `project.json` 的 `artifact_revisions` **追加**一条修订记录（append-only，旧哈希保留以供审计），更新产物索引，并重写 `HANDOFF.md`。`--id` 必须是 `project.json` 的 `artifacts` 中已登记的 ID；`--path` 仅在产物被移动到新的相对路径时使用，默认沿用当前登记路径；`--reason` 写清合法迭代的原因；`--by` 为可选执行者名，默认取协调者；`--coordinator` 必填且须等于 `project.json` 的 `coordinator`（角色声明门禁，非身份认证）。

`project validate` 随后校验：修订链自洽、当前磁盘哈希等于最新登记哈希；哈希已被显式取代的历史交接快照不再参与相等性比对，因此不会误报。但**没有**经过 `refresh` 记录的内容改动仍一律视为篡改并被拒绝。修订历史 append-only、可审计。手工编辑 `project.json` 伪造 `artifact_revisions` 修订记录**不是**受支持的恢复路径，请始终通过 `artifact refresh` 完成。

### 根文件交叉校验

`project.json` 是根信任文件。直接编辑根文件不能伪造状态或预算确认：`project validate` 会把「状态、交接链、付费批准」三者绑定校验，任一不符即返回非零。所有会写盘的动作（`approve`、`handoff`、`task create`、`task reclaim`）都先跑 `validate` 作为前置 gate。

- `state` 必须等于交接链末条的 `state_after`（`initialized` / `input_validated` 且尚无交接时除外）；
- 处于 `remaking` 及之后阶段，或从这些阶段进入 `blocked` 时，`paid_generation.approved` 必须为 `true`；
- `approved` 为 `true` 时，批准人不得是协调者本人，且批准时间、范围、确认内容必须齐全；
- `HANDOFF.md` 现在还会显示 `coordinator` 与产物修订数量；因此只改 `project.json` 的 `coordinator` 字段、而不重写 `HANDOFF.md`，已无法通过 `project validate`。

因此把 `state` 直接改成 `remaking`，或写入 `approved_by: coordinator` 的自批准记录，都会被 `validate` 拒绝，也不会让后续 `handoff --to-state remaking` 通过。

边界：这是**声明式账本**的一致性校验，不是密码学签名。若有人同时改写 `project.json`、`HANDOFF.md` 与整条交接链使其完全自洽，并伪造一个非协调者的批准人身份，代码无法验证「用户是否真的确认过」——这一步仍依赖运行 `approve` 的人与用户之间的流程纪律。

### 付费生成确认

任何进入 `remaking` 的 `handoff` 之前，必须先运行 `rea-video project approve <dir> --by USER --scope "复刻生成预算" --confirmation "用户已确认预算" --coordinator coordinator`，把用户的预算确认写入 `project.json` 的 `paid_generation` 字段。这道门禁由代码强制：未 `approve` 就执行 `handoff --to-state remaking` 会被拒绝并返回非零退出码，无法靠手工改 MD 绕过。`approve` 还要求批准人不是协调者本人、项目处于 `planned` 阶段，且不能重复登记。

注意边界：项目层只拦截 `project handoff --to-state remaking`。原版 `rea-video remake-run` 属于原命令，项目层**不拦截**它；「用户确认前不调用付费生成」仍由 [rea-video-project-remake Skill](../skills/rea-video-project-remake/SKILL.md) 的流程纪律保证。要获得代码级强制，就先完成 `approve` 并把状态推进到 `remaking` 之后再开始生成。

## 4. 并行任务所有权

- `approve`、`handoff`、`artifact refresh`、`task create`、`task reclaim` 必须带 `--coordinator`，且其值须等于 `project.json` 的 `coordinator` 字段；`claim`、`complete`、`block` 不带 `--coordinator`。这些是协调者专属动作，代码会据此校验身份声明。
- 协调 Agent 用 `task create`（须带 `--coordinator coordinator`，完整参数见 §2 命令块）声明任务、输入、验收条件和写入范围；
- 所有显式写入（`approve`、`handoff`、`artifact refresh`、`task create`、`task reclaim`）都有 `--coordinator` 角色声明门（代码强制）；执行 Agent 只写 `work/<task_id>/` 以及自己的 `task.json`；
- 唯一例外是 `project validate` 的一次性自动推进：仅当项目仍为 `initialized` 且全部校验通过时，由校验器自己把状态写为 `input_validated` 并重写 `HANDOFF.md`；写入内容固定、调用方无法注入，此后 `validate` 只读。这是让 `initialized → input_validated` 不依赖手动交接的必要例外，不计入执行 Agent 的写权限；
- 活跃任务之间的写入范围不得相同或嵌套；
- 执行 Agent 用 `claim` 取得限时租约，只有持有者能 `complete` 或 `block`；
- 租约过期在 `status` 中显示 `stale`，不会自动变成完成；
- `task reclaim <dir> --id ID --coordinator coordinator` 是任务脱离 `blocked` / `stale` 的唯一恢复路径，且为协调者专属；它会重新检查并行写入范围冲突，冲突未消除时拒绝回收。

## 5. 与原版拆解流程对接

1. `project init` + `project validate` 建立项目；
2. 使用原版 `rea-video decompose <project>/input/reference/... -o <project>/analysis ...` 生成 `analysis/breakdown.json` 等产物；
3. 使用原版 `review` / `apply-review` 完成看图复核；注意 `apply-review` 会原地重写 `analysis/breakdown.json`；
4. 若 `breakdown` 等产物此前已登记、而第 3 步又原地重写了它，先由协调者运行 `rea-video project artifact refresh <dir> --id breakdown --coordinator coordinator --reason "apply-review 重写了 breakdown.json"` 刷新哈希，再进行后续 `handoff`；跳过这一步会让 `project validate` 永久失败（详见「产物合法演进」）。原版 `remake-plan` / `assemble` 的输出若覆盖了已登记产物路径，同样需要先 `artifact refresh`（协调者专属）。
5. 对 `analysis/breakdown.json`、`analysis/report.html` 等重要产物执行 `project handoff`（完整命令见 §2 命令块，须带 `--coordinator coordinator` 及 `--to-state`、`--by`、`--artifact`、`--acceptance`、`--next`），状态推进到 `decomposed` 或 `reviewed`；
6. 使用原版 `remake-plan` / `remake-run --dry-run` 取得预算；用户的预算确认必须用 `rea-video project approve <dir> --by USER --scope "复刻生成预算" --confirmation "用户已确认预算" --coordinator coordinator` 记录（写入 `project.json` 的 `paid_generation`）。未 `approve` 时，`project handoff --to-state remaking` 会被代码拒绝并返回非零；原版 `remake-run` 不受项目层拦截，须按 Skill 流程纪律在确认后才调用；
7. 复刻素材用 `task` 分工，完成后按原时间线 `assemble`；
8. 验收后通过带 `--coordinator coordinator` 的 `rea-video project handoff`（完整参数见 §2 命令块）依次进入 `assembled`、`verified`、`delivered`。

## 6. 安全与商业素材

- 只使用有权使用的肖像、门店、品牌、音乐和视频素材；
- `rights` 为 `unknown` 只警告，但必填素材缺失、登记为存在却找不到文件、哈希变化都会阻止后续阶段；
- API Key、Token、密码不得写入项目文件、交接、任务或日志；只记录环境变量名，例如 `REA_VLM_API_KEY`；
- 密钥扫描覆盖范围：`project.json`、交接、`work/<task_id>/` 任务输出、`input/assets/` 中 `present` 素材，以及 `password: hunter2`、`DB_PASSWORD=...` 这类明文凭据赋值；但这是启发式扫描（heuristic），不是保证，不能替代「不写入凭据值」的纪律；
- 付费生成须先 dry run 或给出预算并得到用户确认，且用 `rea-video project approve <dir> --by USER --scope "复刻生成预算" --confirmation "用户已确认预算" --coordinator coordinator` 记录（见「付费生成确认」）；
- 付费生成门禁是**账本/状态门禁**：只拦截 `project handoff --to-state remaking`，原版 `rea-video remake-run` 不由项目层拦截，「用户确认前不调用付费生成」仍靠流程纪律；
- 发布前遵守平台的 AI 内容标识和素材授权要求。

## 7. 兼容性

新增能力位于 `rea_video/project/`，CLI 入口为 `rea-video project ...`。原命令、原报告、原 `skills/rea-video` 和 `breakdown.json` 结构均未改变。`jsonschema` 是第一轮新增的唯一运行依赖；Schema 位于 `rea_video/project/schemas/`。
