# M2.3c：冻结走廊证据的离线策略评估

本阶段评估**单个决策时刻的高层观察建议**。20 个模拟场景分别提供冻结证据和人工编写的可接受动作集合；Rule 和 Qwen 各自运行并独立评分。没有采集新帧、动作执行器、图像模型或 M0/M1 接入，也不计算真实导航成功率、门数或距离。

根据本轮任务提供的 M2.3b 本地 Ollama 结果，三个真实 Qwen 请求分别返回 `INSUFFICIENT_EVIDENCE`、`VERIFY_TARGET`、`FOUND`，均未 fallback。本轮没有新增真实 Qwen 请求；FakeModel / Mock HTTP 只用于验证框架。

## 冻结场景与独立判定

文件：[m23c_corridors_v1.json](../examples/m23c_corridors_v1.json)。版本 `m2.3c-corridors-v1`，规范化 JSON 的 SHA-256：

```text
1e5a2ff373c2a5e88a6c5fed7af110cc1a67d00a44476335d91e14a4a404fda9
```

| 场景 ID | 决策时可见证据 | 可接受动作 |
| --- | --- | --- |
| ascending | EF731 → EF732，目标 EF734 | CONTINUE_ALONG_SEGMENT |
| descending | GH409 → GH407，目标 GH405 | CONTINUE_ALONG_SEGMENT |
| odd_left | 左侧 JK301 → JK303，目标 JK304 | CHECK_MISSING_OR_OTHER_SIDE |
| even_right_descending | 右侧 LM620 → LM618，目标 LM617 | CHECK_MISSING_OR_OTHER_SIDE |
| number_gap | NP510 → NP514，目标 NP512 | CHECK_MISSING_OR_OTHER_SIDE |
| nonmonotonic | QR702 → QR706 → QR704，目标 QR708 | INSUFFICIENT_EVIDENCE |
| ocr_conflict_neighbor | ST801 与同一门 ST803/ST809 冲突 | REOBSERVE |
| ocr_conflict_target | 同一门 UV222/UV228/UV222，目标 UV222 | REOBSERVE |
| single_frame_target | 单帧 WX315 | REOBSERVE |
| unassociated_target | 两帧 YZ410，未关联物理地标 | REOBSERVE |
| consistent_target | 两个不同帧一致 AA527，无人工确认 | VERIFY_TARGET |
| operator_corrects_ocr | BB633/BB638 冲突，人工已确认 BB638 | FOUND |
| empty_evidence | 目标 CC741，没有观测 | INSUFFICIENT_EVIDENCE |
| one_distinct_door | 三帧同一门 DD852，目标 DD854 | INSUFFICIENT_EVIDENCE |
| separate_segments | A 中 EE963，B 中 EE965，当前 B，目标 EE967 | INSUFFICIENT_EVIDENCE |
| different_prefix | FF201 → FF203，目标 GG205 | INSUFFICIENT_EVIDENCE |
| same_frame_repeated | 同一 frame_id 的两条 HH316 OCR | REOBSERVE |
| side_conflict_target | 同一 JJ427 地标的左右注释冲突 | REOBSERVE |
| target_absent | KK531 → KK533，目标 KK535，未见走廊尽头 | CONTINUE_ALONG_SEGMENT / INSUFFICIENT_EVIDENCE |
| direction_sign_only | 两帧前方 MN900–920 范围标牌，目标 MN914 | CONTINUE_ALONG_SEGMENT / INSUFFICIENT_EVIDENCE |

各场景的 `oracle` 有三个独立字段：`ground_truth`（模拟场景设定）、`rationale`（判定理由）、`acceptable_actions`（可接受动作）。这些内容由人工场景设计确定，加载器不调用 Rule 来生成答案。方向/奇偶动作只作为探索建议，不能把隐藏的门位置转换成“已找到”的权限。

`target_absent` 的完整模拟走廊没有目标，但决策时未观察尽头或完整目录，因此不能要求策略知道目标不存在。继续探索与保守弃权均可接受。`direction_sign_only` 允许基于明确范围标牌继续搜索，也允许保守弃权；Rule 当前不使用该标牌，这个集合仍接受另一个策略合理利用标牌。法律性与可接受性分别评分，不能用和 Rule 一致的比例替代策略评估。

v1 作为回归基线冻结；增加场景或调整判定时创建新版本文件，并同步版本号与 hash 测试。不要为了提高某个策略得分修改旧 oracle。当前场景集合仍小，人工判定也需要后续独立复核。

本次审查修复按要求在 v1 中更换 `ascending` 编号：原 CD213/CD214 与 M2.3b Prompt 示例重合，现用 EF731/EF732，目标由 CD216 改为 EF734。编号步长、目标相对距离、奇偶关系、两个物理地标各两帧、未知侧别和无确认条件均保留，oracle 动作不变。上方 hash 是修复后的规范化 JSON hash，旧值 `79aadc8252c532c64ee801cf6903235cca30275bcc9f54c2df9138c1da5f031f` 的结果不能与新输入直接混用。回归测试从当前 system prompt 与动作指南提取并规范化门牌编号，检查所有场景的目标、OCR 文本（含标牌）与人工确认均不复用 Prompt 示例编号；另行检查 `ascending` 的推理条件。

## 输入隔离与安全校验

每个 `evidence` 只包含 `target`、`active_segment`、已有 `observations` 和已有 `confirmations`。它已经是决策时快照，**不是包含未来帧的整段 episode**。加载器拒绝额外的未来字段、错误关联、未观察地标的确认和重复 ID；它不会按时间戳过滤数据或推断确认发生时间。场景作者仍有责任确保快照中的全部事件已发生。

同一场景只构造一个不可变 `EvidenceSnapshot`，交给 `decide_rule(snapshot)` 和 `LLMPlanner.plan_snapshot(snapshot)`。Qwen 的 system/user prompt 仍使用 M2.3b v2；模型从未收到场景 ID、tags、真值、可接受动作、判定理由或 Rule 的决策。每行结果记录 `snapshot_sha256`，独立运行 Rule 和 Qwen 时可对照版本、套件 hash、选择的场景及快照 hash。

合法性直接复用 M2.3a `parse_decision()`，包括地标引用、当前走廊和 `FOUND` / `VERIFY_TARGET` 权限。既有 Rule、Prompt、后端、安全校验与 fallback 均未修改；`PlannerResult` 仅新增 `requested_action` 遥测字段，保留被拒绝回复中可解析的标准动作枚举。**独立 Rule 动作也接受相同合法性检查，但不自动修正其输出**，以免掩盖差异。

## 离线运行

在仓库根目录，使用已有虚拟环境，不需要安装 vision/UI：

```bash
.venv/bin/python -m unittest discover -s tests -p test_evaluation.py -q
.venv/bin/python -m unittest discover -s tests -q

# 默认只运行 Rule；报告 JSON 和逐场景 JSONL 可同时保存。
mkdir -p outputs
.venv/bin/python -m roomnav.eval_cli \
  --suite examples/m23c_corridors_v1.json \
  --output outputs/m23c-rule.json \
  --output-jsonl outputs/m23c-rule.jsonl

# 列出冻结快照和 hash，不构造模型，也不进行评分。
.venv/bin/python -m roomnav.eval_cli \
  --suite examples/m23c_corridors_v1.json --policy qwen --dry-run
```

也新增了 `roomnav-evaluate` 安装入口；已有 editable 安装要重新安装项目才能生成命令，直接使用 `python -m roomnav.eval_cli` 无需重新安装。`--output` 覆盖完整报告；`--output-jsonl` 追加每个 trial，带版本、hash、prompt 版本、模型配置及运行时间信息。不同运行的 JSONL 不自动合并成指标，避免无意把重复 trial 当作不同场景。

两个输出文件必须彼此不同，且均不得指向输入 `--suite`。CLI 在加载 suite、构造模型后端或运行策略之前检查规范化路径、符号链接目标及硬链接文件身份；冲突退出码为 2，不写报告或追加 JSONL。该检查也适用于 Rule 和 `--dry-run`。回归测试使用临时 suite，覆盖两个输出选项、相同路径、含 `..` 的路径、符号链接、硬链接及输入本身为链接的情况，确认不构造后端、不运行评估，且 suite 字节不变。

离线替换模型可直接调用 Python API：

```python
from pathlib import Path
from roomnav.evaluation import evaluate_suite, load_suite
from roomnav.llm_backend import FakeModel

suite = load_suite(Path("examples/m23c_corridors_v1.json"))
report = evaluate_suite(suite, ("rule", "qwen"), model=FakeModel())
assert all(row["backend"] in {"rule", "fake"} for row in report["records"])
```

这里的 `qwen` 是模型策略评估通道名；真实身份使用每行的 `backend` / `model`，FakeModel 不会被标为真实 Qwen 效果。

## 后续小批 Qwen 运行方式（本轮未执行）

复用 M2.3b 的 `ROOMNAV_LLM_BASE_URL`、`ROOMNAV_LLM_MODEL` 和可选 `ROOMNAV_LLM_API_KEY`。选择 `--policy qwen` 可单独评估模型，`--policy both` 可同时评估 Rule。**模型模式必须显式提供 `--scenario` 或正数 `--limit`**；每个选中场景最多一个请求，没有自动重试或重复采样。场景按冻结文件顺序执行，`--limit` 在场景筛选后生效。模型故障被保留为 trial 并按既有 Planner 显式 fallback。

```bash
# 先检查单个场景，仍然不调用模型。
.venv/bin/python -m roomnav.eval_cli \
  --suite examples/m23c_corridors_v1.json --policy both \
  --scenario ascending --dry-run

# 以下命令将向已配置服务发起一次真实请求，请在准备评估时手动执行。
.venv/bin/python -m roomnav.eval_cli \
  --suite examples/m23c_corridors_v1.json --policy both \
  --scenario ascending --json-mode --timeout 90 --max-tokens 512 \
  --output outputs/m23c-both-one.json
```

报告配置保留 served model ID、temperature、max_tokens、json_mode、timeout 和请求的策略；不记录 API Key 或 URL。不能仅凭同一模型 ID 保证权重/服务环境未变，正式对照还需要另行冻结模型版本与运行环境。

## 记录和指标定义

每行记录动作、完整决策、合法性、错误码、fallback、弃权、可接受性、耗时和服务返回的 TokenUsage。`action` 是最终交付动作（可能为 fallback）；`policy_action` 是策略本身通过校验的动作，发生 fallback 时为 null。`requested_action` 保留可解析的标准动作枚举，包括被拒绝的 `FOUND`；任意非法动作字符串、非 JSON 或含重复键的回复记为 null。拒绝的原始回复不写入日志，使用 `model_output_legal=false` 和 `validation_errors` 说明原因；传输失败没有可判断的模型输出，合法性为 null。

| 字段/指标 | 定义 |
| --- | --- |
| legal / legality_rate | 最终动作通过既有 JSON/证据安全校验的比例；不等于策略有效 |
| model_output_legal_rate | 通过模型输出校验的次数 / 有输出可校验的次数（正常回复和 validation_error）；不把传输失败当格式错误，另列 model_response_count |
| policy_acceptable_rate | 非 fallback、合法且属于独立可接受动作集合的次数 / 全部 trial；模型失败仍占分母 |
| effective_acceptable_rate | 最终交付动作合法且可接受的次数 / 全部 trial；包括 fallback，必须与模型本身得分分开 |
| fallback_rate | 使用既有 Rule fallback 的次数 / 全部 trial |
| abstention_rate | 策略自身返回 INSUFFICIENT_EVIDENCE 的次数 / 全部 trial；fallback 不伪装成模型弃权 |
| effective_abstention_rate | 最终交付 INSUFFICIENT_EVIDENCE 的次数 / 全部 trial |
| coverage | 非 fallback、合法且没有弃权的次数 / 全部 trial |
| selective_acceptable_rate | coverage 中可接受动作的比例；必须同时看 coverage，防止靠大量弃权掩盖失败 |
| elapsed_s | 整个策略调用的本地墙钟时间，含 prompt / 校验 / fallback，排除加载场景、评分及写报告；汇总 mean / p50 / 最近秩 p95 |
| planner_elapsed_s | 既有 Planner 的模型等待及解析校验耗时，供诊断，与整体时间分开 |
| usage / tokens | 服务报告的 prompt/completion/total；每个字段独立汇总 sum 和 reported_count，不用缺失值补 0 或推算 total |

`REOBSERVE` 是有明确目标的再次采集动作，不算弃权。合法的弃权只有在 oracle 接受时才得分。无样本的条件比例为 null。非法模型 JSON 若仍得到正常 API 回复，可保留该次 usage；超时、busy/capacity 或后端错误不读取可能迟到/陈旧的 usage。自定义 `TextModel` 如需报告用量，应在每次回复时赋值新的 `TokenUsage` 对象，评估器会忽略与调用前相同的旧对象；未知用量保持 null。Rule 的 token 字段为 null（不适用）。

汇总按策略分别给出总体、每个 tag、请求/通过校验/最终交付的动作分布、后端错误分布、模型校验错误码分布和最终合法性错误分布。tags 可重叠，不能把 tag 样本数相加当作独立场景总数。这些是描述性结果，没有置信区间或统计显著性声明。

## 当前离线结果与遗留工作

Rule 在 v1 的 20 个场景中，19 个动作合法且可接受（95%），6 次弃权（30%），fallback 为 0。唯一不合格的 `same_frame_repeated` 暴露既有 M2.2 按记录数算重复而 M2.3a 按帧来源算多帧的差异：Rule 原样返回 `VERIFY_TARGET`，评估记录 `legal=false` / `verify_requires_cited_consistent_target`。模型无效输出在同场景的 fallback 则沿用既有 M2.3a 安全降级，交付 `REOBSERVE`。此处未改 Rule 或安全门槛。

33 项离线评估测试覆盖冻结 hash、独立判定、标牌动作与 Rule 不同但可接受、提示隔离、未来确认不泄漏、非法特权动作及其枚举记录、fallback 分数隔离、缺失/部分/陈旧 TokenUsage、耗时与比例、按 tag 汇总、CLI 请求预算、JSON/JSONL 记录与冲突输出路径，以及本次新增的 Prompt 编号隔离、递增场景条件和 suite 不可覆盖检查。原有 121 项与评估测试最终共 154 项通过。基线验证时原有并发容量测试曾出现一次时序失败，未修改其逻辑；重新运行基线通过。

后续需人工复核 oracle、小批评估真实 Qwen 并按 hash 对齐输入，保留完整错误/成本记录。再设计有动作执行、信息预算与停止条件的多步环境，才能评估目标未出现时的终止行为、搜索成功率、门数和距离。当前文本模型、粗粒度动作与 20 个手工场景无法证明真实走廊策略最优。

## Standards 审查

标准轴未发现文档约定违规或需要处理的结构问题。初审指出陈旧 TokenUsage 可能误归属、两个输出路径可能混写，已分别用调用前对象比较和路径/硬链接校验修复并测试。复查无剩余问题。

## Spec 审查

需求轴初审指出被拒绝的模型动作无法统计、自定义模型的旧 usage 可能被计入本次调用，已补齐标准动作遥测及用量隔离。复查确认场景真值未进入提示，安全门槛与 fallback 未改，无剩余需求问题。

最终审查：Standards 0 项剩余问题；Spec 0 项剩余问题。
