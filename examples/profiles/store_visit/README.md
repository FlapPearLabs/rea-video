# store_visit 配置示例

这是 `rea-video project init --profile store_visit` 的业务素材模板，不是可直接执行的项目。项目层仍然是通用的，`store_visit` 只提供一组推荐的输入字段、素材 ID 和验收边界。

## 使用方式

1. 先创建项目：

   ```bash
   rea-video project init campaigns/store-a --reference path/to/reference.mp4 --title "A 店探店" --profile store_visit
   ```

2. 参考 `input.example.json`，把 `assets` 中的素材声明合并到新建项目的 `input/input.json`。不要整体覆盖：参考视频路径和 SHA-256 是由 `init` 针对实际文件生成的。

3. 把真实素材放进 `campaigns/store-a/input/assets/`，为每个 `present` 素材填入实际 SHA-256，并把 `rights` 改为 `owned`、`licensed` 或 `consent`。缺少的必填素材保持 `missing`，`validate` 会阻止进入后续阶段。

4. 将 `brief.md` 中与本店不符的文案和验收项更新后放入项目的 `input/brief.md`。

5. 运行：

   ```bash
   rea-video project validate campaigns/store-a
   rea-video project status campaigns/store-a
   ```

`credentials` 数组只允许出现环境变量名，不能出现值。

关于哈希与占位符：`assets` 中的 `host-consent-template` 是仓库自带的真实文件 `input/assets/host-consent-template.txt`，其 `status` 为 `present`、`rights` 为 `owned`，`sha256` 是针对该文件计算的真实 SHA-256，用来演示 `present` 素材与真实哈希的写法。其余素材都是 `missing` / `unknown` 占位，`reference.sha256` 也是全零占位符；这些占位值只用于说明结构，不能用于真实项目——参考视频的路径与哈希由 `project init` 针对实际文件生成，真实素材的路径、授权与哈希则在登记实际素材时填写。切勿把全零 SHA-256 或占位素材声明直接当成已登记的真实素材。

已登记产物被 `apply-review` 等流程合法原地重写后如何刷新哈希，见 [docs/structured-projects.md §产物合法演进（artifact refresh）](../../../docs/structured-projects.md)。
