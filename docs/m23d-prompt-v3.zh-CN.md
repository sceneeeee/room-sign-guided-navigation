# M2.3d：Prompt v3 与高层搜索策略开发检查

2026-10-18，分支 `feat/m2-reasoning`。本轮实现已完成，**策略效果未达目标，等待人工验收**。真实 Qwen 共调用 5 次，无重试。没有重跑旧 20 场景，没有正式独立盲测，没有接入 M0/M1、VLM 或实机，没有合并、commit 或 push。

## 开发要求与边界

以本轮人工任务为规格：保留完整 v2 和默认行为，通过两个 CLI 显式选择 v2/v3；区分目标确认与信息获取；允许基于局部趋势、侧别、编号缺口和方向标牌提出有假设的暂定建议；保留真正证据不足时的弃权。不得复制 Rule 算法或向模型提供 Rule 动作、场景 ID、oracle、未来证据。Python 事实组装、FOUND/VERIFY_TARGET 校验和安全 fallback 不放宽。

先读源码、测试、已有实验记录再实现。未发现仓库或父目录的 `AGENTS.md`，也没有专用类型检查配置；按已有标准库、unittest、冻结证据和严格 JSON 接口约定实现。使用 implement 与 code-review 技能；本轮任务说明直接作为 Spec 来源，不配置外部 issue tracker。审查基点为开始时的 `912848664e767d3c983f4d2e081abee19a44e380`，检查工作区差异与新增文件。人工“不自动 commit/push”要求优先于 implement 技能的提交步骤。

## 先验分析：旧 20 场景 v2 记录

来源：本地原始记录 `outputs/m23c-qwen-20-v2.json`（被 git 忽略，不作为仓库链接），模型 `qwen3:4b-instruct-2507-q4_K_M`，温度 0，JSON mode，max_tokens 512，timeout 90 s。原 suite 规范化 hash 为 `1e5a2ff373c2a5e88a6c5fed7af110cc1a67d00a44476335d91e14a4a404fda9`。

逐行核对结果如下；“接受”严格使用旧 oracle，未改判定：

| 场景 | Qwen v2 动作 | 接受 |
| --- | --- | --- |
| ascending | INSUFFICIENT_EVIDENCE | 否 |
| descending | INSUFFICIENT_EVIDENCE | 否 |
| odd_left | INSUFFICIENT_EVIDENCE | 否 |
| even_right_descending | INSUFFICIENT_EVIDENCE | 否 |
| number_gap | INSUFFICIENT_EVIDENCE | 否 |
| nonmonotonic | INSUFFICIENT_EVIDENCE | 是 |
| ocr_conflict_neighbor | INSUFFICIENT_EVIDENCE | 否 |
| ocr_conflict_target | INSUFFICIENT_EVIDENCE | 否 |
| single_frame_target | INSUFFICIENT_EVIDENCE | 否 |
| unassociated_target | INSUFFICIENT_EVIDENCE | 否 |
| consistent_target | VERIFY_TARGET | 是 |
| operator_corrects_ocr | FOUND | 是 |
| empty_evidence | INSUFFICIENT_EVIDENCE | 是 |
| one_distinct_door | INSUFFICIENT_EVIDENCE | 是 |
| separate_segments | INSUFFICIENT_EVIDENCE | 是 |
| different_prefix | INSUFFICIENT_EVIDENCE | 是 |
| same_frame_repeated | INSUFFICIENT_EVIDENCE | 否 |
| side_conflict_target | REOBSERVE | 是 |
| target_absent | INSUFFICIENT_EVIDENCE | 是 |
| direction_sign_only | INSUFFICIENT_EVIDENCE | 是 |

Rule 19/20 可接受，Qwen 10/20 可接受、20/20 模型输出合法、0 fallback、17/20 弃权。10 个不可接受结果全部为弃权：5 个有探索方向的趋势/侧别/缺口场景，5 个需要重观测的场景。

17 条弃权均没有 assumptions 和地标引用。趋势、缺口等失败记录的 reason 直接援引“没有多帧目标读数”；目标冲突、单帧和重复帧的失败则援引“没有人工确认或一致目标候选”。`unassociated_target` 还把 `found_authorized=false` 写作弃权理由。`target_absent` 与标牌场景虽然弃权获接受，也没有展示有效的探索策略。`nonmonotonic` 等场景的动作被接受，不代表其“未看到目标”的解释完整正确。

这些是可观察的输出行为，支持“把确认门槛用于探索”的行为描述。**不能从日志证明模型内部因果，也不能认定提示词是唯一原因。** 不通过猜测放宽校验，不将通过 schema 等同于策略正确。

## 版本接口与提示边界

`build_prompt(..., prompt_version="v2")`、`LLMPlanner(..., prompt_version="v2")`、`evaluate_scenario/evaluate_suite(..., prompt_version="v2")` 默认保留 v2。选择器仅接受 `v2` / `v3`，未知版本在调用模型前报错：

| 选择器 | 日志中的完整 prompt_version |
| --- | --- |
| v2（默认） | m2.3b-action-guide-v2 |
| v3 | m2.3d-search-guide-v3 |

v2 的 SYSTEM_PROMPT、ACTION_SELECTION_GUIDE 和权限拼接结果保留原文。基础提示与 v3 动作指南分别负责共同事实约束、确认/探索区别。v3 没有门牌示例或任何场景特定的编号，未调用 Rule 选择建议；模型仍只收到冻结 JSON evidence 与两个原有确认权限布尔值。

v3 要求：权限 false 只禁止对应的确认动作；下一步可检验局部假设；趋势、侧别、标牌可靠性放入 assumptions，可能失效的条件放入 uncertainties；reason 描述观察建议，不把目标位置断言为事实；具体 OCR/关联歧义可 REOBSERVE；没有方向或可重观测歧义时仍可弃权。

`PlannerResult` 和严格模式 `PlannerError` 记录实际使用的完整版本；LLM CLI、评估报告与每条 trial/JSONL 均传播版本，包括校验失败与 fallback。严格模式失败的 CLI stderr 包含 `prompt_version`，退出码为 2，不写成功 JSONL。为兼容原评估 JSONL，Rule 行中的版本表示**该次对照选择的提示配置**，其 `policy=rule` / `backend=rule` 明确表示没有模型调用。dry-run 的版本也仅表示配置，未执行模型。

```bash
# 只查看配置，不发起推理。
.venv/bin/python -m roomnav.llm_cli \
  --target EB104 --observations examples/observations_eb.jsonl \
  --prompt-version v3 --dry-run

.venv/bin/python -m roomnav.eval_cli \
  --suite examples/m23d_prompt_dev_v1.json --policy both \
  --scenario dev_local_descent --prompt-version v3 --dry-run
```

真实服务使用现有 base-url/model 参数或环境变量；删除 dry-run 并显式指定请求预算才会调用。直接用 `roomnav-llm` / `roomnav-evaluate` 入口也接受相同选项。默认版本没有切到 v3。

## 数据隔离与冻结

新文件：[m23d_prompt_dev_v1.json](../examples/m23d_prompt_dev_v1.json)。包含 9 个开发案例，规范化 hash：

```text
1a62ed0fb3180da25bcd929efaa43595e9edef6c0067bb380b088a71a1576b16
```

新案例独立编写，未从原 suite 或 Rule 动作生成 oracle；改变门牌、地标、走廊、序列长度和部分步长，包含局部递减、冲突、跨走廊、三门同侧奇偶、内部缺口、范围标牌、非单调、单门和不同前缀。与旧 suite 及 v2/v3 提示的门牌编号无重合。但开发者已经看过旧场景，且按已知失败类型编写，因此**这些是开发数据，不是独立泛化证据**。单元测试的 FakeModel 回复只检查接口，不能作为 Qwen 成绩。

推理前固定 5 请求计划：本地文件 `outputs/m23d-qwen-dev-plan.json`。计划包含模型 digest、参数、suite/snapshot/system/user hash，以及受保护文件的原始字节 hash；SHA-256：`bb368694cb0971e00c7e8c21c68c816876798d4b003bf55f483078bae3f0800e`。推理前检查一致性，每次请求前先写尝试编号，完成后写结果，无重试。服务重启发生在推理命令执行前，恢复后确认只有计划文件，没有结果文件，然后才执行这 5 次请求。

旧 suite、所有已有 outputs 中的 v2/Rule 结果和 baseline.py 的字节 hash 在运行后再次对照，均未改变。原 suite/oracle 的冻结 hash 测试也保留。原 20 场景已暴露，后续即使重跑也只能标为回归。正式独立测试仍需另行设计、冻结输入和判定、记录 hash，再对照两个版本；本轮没有声称完成此测试。

## 真实 Qwen 的 5 次开发检查

Ollama `0.40.3`，Python `3.13.13`；模型 `qwen3:4b-instruct-2507-q4_K_M`，4.0B、Q4_K_M，digest `0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0`。温度 0、max_tokens 512、JSON mode、timeout 90 s。本轮提示原文冻结后未再调词；5 次请求均收到了模型回复。

完整本地记录：`outputs/m23d-qwen-dev-v2-v3.json`，SHA-256：`306406ba20afbd86cc26a970f508c770489c77af4222c04a1fffc145f900a257`。outputs 按仓库惯例被 git 忽略，因此本报告以本地路径记录、不提供无法在仓库浏览的链接；本报告保存摘要，原始结果保留在本地供验收。

| 开发案例 / 提示 | 模型请求动作 → 交付动作 | 模型合法 / 交付合法 | fallback | 模型弃权 | 模型可接受 / 交付可接受 | 延迟 s | 输入 / 输出 / 总 tokens |
| --- | --- | --- | --- | --- | --- | --- | --- |
| local_descent / v2 | INSUFFICIENT_EVIDENCE | 是 / 是 | 否 | 是 | 否 / 否 | 6.431 | 1463 / 103 / 1566 |
| local_descent / v3 | REOBSERVE | 是 / 是 | 否 | 否 | 否 / 否 | 4.162 | 1712 / 202 / 1914 |
| suspect_conflict / v2 | INSUFFICIENT_EVIDENCE | 是 / 是 | 否 | 是 | 否 / 否 | 2.111 | 1286 / 89 / 1375 |
| suspect_conflict / v3 | REOBSERVE | 是 / 是 | 否 | 否 | 是 / 是 | 5.793 | 1535 / 188 / 1723 |
| unlinked_corridors / v3 | REOBSERVE → INSUFFICIENT_EVIDENCE | 否 / 是 | 是 | null | null / 是 | 6.293 | 1436 / 202 / 1638 |

配对输入完全一致：

- `local_descent`：三个不同门 ZK864 → ZK861 → ZK858，目标 ZK855。v2 因未见目标弃权；v3 已在 assumptions 承认递减序列，却仍要求重看一致邻居，并将“目标可能邻近”作为假设。位置没有进入 facts，但没有选出 oracle 接受的继续探索动作，**未解决趋势问题**。
- `suspect_conflict`：已见邻居 VX470，以及同一门 VX476/VX478/VX476 的冲突。v2 因没有人工确认或一致候选弃权；v3 改为重观测并引用该门，动作可接受。不过其 assumptions 仍把部分重复读数称作 consistent，与冲突状态的表达不够严谨；自由文本没有经过语义真值验证。
- `unlinked_corridors`：两个不同走廊各一个非目标门，当前 WEST。v3 请求 REOBSERVE 并引用非当前走廊地标，触发 `evidence_outside_active_segment`。保留 requested_action，模型合法性 false，模型弃权/可接受性均为 null；安全回退交付可接受的 INSUFFICIENT_EVIDENCE。**这是模型失败，不能计作 v3 成功或 v3 自主弃权。**

两组配对仅能描述：v2 可接受 0/2，v3 可接受 1/2。额外的 v3 检查失败且 fallback；不能把不同分母的总体比例当成公平对照。v3 的三个请求都选了 REOBSERVE，包括一个非法输出。降低弃权率没有证明策略更有效。输入/输出 token 增加；冷启动、缓存、运行顺序和小样本影响延迟，不能声称 v3 加速。

## 测试与审查

新增 `tests/test_prompt_versions.py`：版本切换、v2 原文 hash、默认兼容、CLI/JSONL 实际版本、非法版本不调用后端、事实与假设隔离、模型自带 facts 拒绝、两版本非法 FOUND/VERIFY_TARGET、确认与正确引用、同帧安全 fallback、严格模式、失败指标分离、未来/跨场景/跨走廊隔离、OCR 注入与开发编号隔离。既有测试保持不变。

验证时还对比 HEAD 与工作区 AST：`parse_decision`、`_supports_verification`、`_safe_fallback`、`_facts`、`_special_action_permissions` 均未改变。

新增 19 项测试与原有 154 项测试共 **173 项全量通过**；命令为 `.venv/bin/python -m unittest discover -s tests -q`。首次在沙箱内运行时既有本地 HTTP 仿真测试因禁止 socket 而失败，获得本机回环权限后全量通过；严格日志修复后的最终全量也通过。均为离线测试，没有额外 Ollama 请求。`compileall` 和 `git diff --check` 通过；仓库没有配置独立类型检查器。

### Standards

并行只读审查与增量复查均未发现标准违规或需要处理的结构问题，剩余 **0 项**。证据冻结、程序 facts、特权动作校验与 fallback 保持原约束；未引入依赖，遵循既有标准库与 unittest 风格。

### Spec

初审发现严格失败 CLI 漏报实际版本，已修复并补 v2/v3 两种路径测试，复查剩余接口问题 **0 项**。无范围扩张。

保留 **1 项策略验收缺口**：趋势仍重观测一致邻居，跨走廊输出非法并 fallback，奇偶/缺口/标牌的真实效果未验证。不能通过放宽校验或修改 oracle 消除此问题。报告和中英文 README 均明确标为目标部分未达成。

最终双轴结论：Standards 0 项；Spec 接口 0 项、策略效果 1 项未解决。人工验收前不把本轮视作高层策略优化成功。

## 遗留问题与验收建议

1. v3 部分修正弃权，却出现把一切不确定性转成 REOBSERVE 的倾向；局部趋势失败，证据不足检查非法，不能作为默认版本推广。
2. 本轮没有真实运行 v3 奇偶、缺口、标牌案例；提示包含这些语义、接口允许动作，不等于已验证 Qwen 能正确执行。FOUND/VERIFY_TARGET 的安全性依赖未改的 Python 校验，5 次实测没有覆盖其全部正反例。
3. 无法排除过拟合：修改针对已知失败类别，新案例仍为开发检查；没有提示中硬编码旧编号或复用旧 oracle 的证据，也没有独立盲测足以判断泛化。冻结新正式测试集应在后续实验前完成。
4. 已知 M2.2 `same_frame_repeated` 问题：Rule 按观测记录数给 VERIFY_TARGET，现有验证器按帧来源拒绝；模型 fallback 仍安全降为 REOBSERVE。本轮记录并复测此差异，**不修改 Rule v2**，保留原 19/20 对比。
5. reason/assumptions/uncertainties 仍是模型自由文本；程序保证 facts 的证据来源和特权动作门槛，无法证明自然语言假设的真实性或探索策略最优。没有多步执行和现场导航验证。

人工验收可接受版本基础设施与安全回归；策略优化仍需后续迭代。当前保留默认 v2，等待人工决定下一轮实验预算与独立测试设计。
