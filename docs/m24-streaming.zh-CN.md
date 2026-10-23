# M2.4：流式 Observation 接入与 Rule 帧门槛修复

本轮把已有 `Observation → LandmarkMemory.add() → decide_next()` 接成逐事件处理的 M2 接口。输入来自 JSONL 文件或标准输入，默认且唯一启用的策略是 Rule；不导入模型客户端，不发起 Ollama/Qwen 请求。未修改 M0/M1 摄像头、OCR，也未增加地图、SLAM、Nav2、深度或 VLM。真实摄像头接入与现场搜索验收仍待完成。

## 开发边界与版本

本轮先修复 M2.3d 报告中指向被忽略 `outputs/` 文件的仓库链接，改为明确的本地路径；原 173 项测试全量通过，并明确要求人工保存 M2.3d checkpoint。M2.4 在独立副本中开发，避免混入尚未保存的 M2.3d 改动；不自动 commit、push 或 merge。

修复后的 Rule 版本为 `m2.4-distinct-captures-v1`。流式输出、评估报告/每条 trial（含 JSONL）和 LLM CLI 的 Rule 对照记录均提供 `rule_version`。`prompt_version` 仍表示独立的提示配置，两者不能混用。

M2.3c suite、oracle、旧评估报告与本地原始结果均保留；Prompt v2/v3 字符串保持原文，默认提示仍为 v2。本轮只用旧 suite 做已暴露案例的修复回归，不能将新 Rule 的结果替换进旧 Rule/Qwen 对照，也不能视为独立泛化测试。

## 运行与事件格式

```bash
.venv/bin/python -m roomnav.stream_cli \
  --target FB545 --input examples/m24_stream_demo.jsonl

# --input - 或省略 --input 时，从标准输入逐行读取。
cat examples/m24_stream_demo.jsonl | \
  .venv/bin/python -m roomnav.stream_cli --target FB545
```

安装更新后的项目后也可用 `roomnav-stream`。不需要配置模型或服务地址。每行一个 JSON 对象，空行跳过；按到达顺序处理，不按时间戳排序，也不预加载未来事件。每次事件成功后立即 flush 一行 JSON。

观测事件（平铺已有 Observation 字段，只增加事件鉴别字段 `type`）：

```json
{"type":"observation","kind":"room","text":"FB541","segment_id":"corridor_FB5","frame_id":"f001","landmark_id":"run01:FB5:door01","observation_id":"run01:f001:roi0","side":"left","timestamp_s":1.0,"confidence":0.92}
```

| 字段 | 要求 | 含义 |
| --- | --- | --- |
| `type` | 必填，`observation` | 原始感知证据事件 |
| `kind` | 必填，`room` / `sign` | 门牌或指示牌，标牌文字原样保留 |
| `text` | 必填，非空字符串 | OCR 原文，不能当作人工确认 |
| `segment_id` | 必填，非空字符串 | M1 或人工提供的当前走廊标注 |
| `side` | 可选，默认 `unknown` | `left` / `right` / `front` / `unknown`；显式 null 无效 |
| `frame_id` | 可选，非空字符串或 null | 一次采集帧的标识，同帧多个 ROI 必须保持相同 ID |
| `landmark_id` | 可选，非空字符串或 null | 同一物理地标的显式关联 ID，只能来自 M1 跟踪或人工标注 |
| `observation_id` | 可选，非空字符串或 null | 单场会话唯一，用于重传去重；不同 ROI 使用不同 ID |
| `timestamp_s` | 可选，有限非负数字或 null | 采集时间；不接受布尔值或数字字符串 |
| `confidence` | 可选，有限 `[0,1]` 数字或 null | 未校准原始分数；不接受布尔值或数字字符串 |

兼容旧 Observation 的全部可选字段。缺失 `landmark_id` 时保留原始观测，增加未关联计数，但不创建地标。两个不同 ID 即使 OCR 相同，也保留为两个物理门；同一个 ID 不允许跨走廊或改变 kind。尚未支持 bbox 等未来扩展字段；未知字段拒绝，M1 适配器应只转发当前接口字段。

人工确认事件：

```json
{"type":"operator_confirmation","landmark_id":"run01:FB5:door03","text":"FB545","confirmed_by":"human-demo"}
```

`landmark_id`、`text` 必须为非空字符串；`confirmed_by` 可省略，默认 `operator`，显式提供时必须非空。只能确认已存在的 room 地标；确认不要求 OCR 已经读对，允许人工纠正错误。调用现有 `memory.confirm_label()`，不改写原始观测，仅保留每个地标最新确认。没有新增确认审计日志或确认事件 ID。

**该事件只能由操作员界面在明确的人为操作后提交。** OCR 适配器只产生 `observation`；`text`、`kind`、置信度或多帧一致均不会自动生成确认。此本地原型依赖可信事件生产者，不提供来源认证；`confirmed_by` 是记录字段，不是身份验证。

## 帧来源与动作权限

`Landmark.has_distinct_capture_evidence` 是 Rule 与 LLM 验证器共用的门槛，不引入循环依赖。VERIFY_TARGET 还要求当前走廊内、未人工确认、标签一致且无侧别冲突的目标地标。

1. 两个不同的非空 `frame_id` 满足帧门槛。同帧多条 OCR、不同 observation_id，甚至不同 timestamp，仍只算一帧。
2. 所有记录都有 timestamp 时，可以用不同采集时间区分没有 frame_id 的记录；有 frame_id 的记录不会再按其时间戳多算一帧。
3. 只有整个地标的所有记录均缺失 frame_id 和 timestamp 时，保留既有 LLM 验证器的旧格式兼容：两条一致记录可进入 VERIFY_TARGET。混合但不充分的元数据不能补足第二帧。**此兼容不证明来源独立；实时 M1 必须提供真实 frame_id**。

单帧、同帧重复、冲突或未关联的目标 OCR 返回 REOBSERVE；符合帧门槛的候选只返回 VERIFY_TARGET；只有明确的人工确认允许 FOUND。历史走廊的人工目标确认仍可支持 FOUND，与现有 Planner 一致；这是已记录的确认，不表示人当前就在该门前。

新观测把 `active_segment` 更新为它的 segment_id，历史 Memory 持续保留；其他动作的搜索证据仅使用当前走廊。重传与人工确认均不改变当前走廊。走廊标注仍由上游或人提供，不推断跨走廊连接。

## 幂等、错误与输出

相同 observation_id 和相同构造后的 Observation 重传：返回 `status=duplicate`，不添加 OCR 票、不增加计数、不改变当前走廊。省略可选字段与显式填写其默认值等价；ID 对应任意实际字段变化时拒绝，包括文本大小写或时间变化。未提供 observation_id 时，每次提交视作新观测。去重仅限同一个进程中的 `StreamSession`，重启后没有持久化恢复。

输入在写入前完成字段、类型、值与关联检查；冲突 ID 也在 Memory 修改前拒绝，失败事件不会占用 observation_id，不会更新走廊。JSON 重复键、NaN/Infinity、非对象、未知字段/事件类型均拒绝。CLI 遇到首个错误停止，退出码 2，stderr 输出带行号的 JSON 错误；stdout 只保留此前已成功事件的回复。事务单位是单个事件，不是整个文件。

每次有效事件（包括幂等重传确认回复）输出：

```json
{
  "event_type": "observation",
  "status": "applied",
  "observation_id": "run01:f006:roi0",
  "landmark_id": "run01:FB5:door03",
  "target": "FB545",
  "active_segment": "corridor_FB5",
  "policy": "rule",
  "rule_version": "m2.4-distinct-captures-v1",
  "memory": {
    "raw_observation_count": 7,
    "landmark_count": 3,
    "unassociated_observation_count": 0,
    "confirmation_count": 0
  },
  "decision": {
    "action": "VERIFY_TARGET",
    "reason": "The associated landmark has consistent target OCR eligible for human verification.",
    "facts": ["Landmark run01:FB5:door03 has repeated OCR text FB545."],
    "assumptions": [],
    "uncertainties": ["Matching OCR readings are correlated and may all be wrong."],
    "evidence_landmark_ids": ["run01:FB5:door03"]
  }
}
```

Decision 仍是已有 `action/reason/facts/assumptions/uncertainties/evidence_landmark_ids`；建议由人执行，不产生机器人控制命令。输出采用 JSON 转义，Unicode 文本可无损解析。

## FB541 → FB543 → FB545 验收回放

[示例事件](../examples/m24_stream_demo.jsonl) 的预期动作如下；走廊均为 `corridor_FB5`，目标均为 FB545。

| 行 | 新事件 | 原始观测数 | 地标数 | 确认数 | Rule 动作 |
| --- | --- | --- | --- | --- | --- |
| 1 | FB541 / f001 / door01 | 1 | 1 | 0 | INSUFFICIENT_EVIDENCE |
| 2 | FB541 / f002 / door01 | 2 | 1 | 0 | INSUFFICIENT_EVIDENCE |
| 3 | FB543 / f003 / door02 | 3 | 2 | 0 | INSUFFICIENT_EVIDENCE |
| 4 | FB543 / f004 / door02 | 4 | 2 | 0 | CONTINUE_ALONG_SEGMENT |
| 5 | FB545 / f005 / door03 | 5 | 3 | 0 | REOBSERVE |
| 6 | 同一 f005 的另一条 FB545 OCR | 6 | 3 | 0 | REOBSERVE |
| 7 | FB545 / 独立 f006 / door03 | 7 | 3 | 0 | VERIFY_TARGET |
| 8 | 操作员明确确认 door03=FB545 | 7 | 3 | 1 | FOUND |

第 4 行的建议包含局部编号趋势、走廊方向与遍历顺序的假设，不将目标位置写成事实。第 6 行证明 OCR 条数增加不能绕过帧门槛；第 7 行依然没有 FOUND。

## 测试与代码审查记录

新增 `tests/test_stream.py`，覆盖帧来源、物理门关联、原始未关联观测、ID 重传/冲突、跨走廊、确认纠错及 FOUND、无效输入的完整 Memory 不变、JSON 严格解码、文件/标准输入端到端回放与无网络默认路径。原来断言 Rule 缺陷存在的测试改为检查修复行为，suite/oracle 与旧报告不改。

验证结果：新增 21 项测试，加上原有 173 项，**194 项全量通过**。在隔离副本设置 `PYTHONPATH=src` 后使用项目现有虚拟环境运行 `python -m unittest discover -s tests -q`；既有本机 HTTP 仿真测试需要沙箱外回环端口权限，全量测试已获该权限并通过。`compileall` 也通过，项目没有独立类型检查器配置。未调用 Ollama/Qwen。

冻结的 M2.3c suite 规范化 SHA-256 仍为 `1e5a2ff373c2a5e88a6c5fed7af110cc1a67d00a44476335d91e14a4a404fda9`。新 Rule 在相同 oracle 下 **20/20 合法且可接受，fallback 0**；`same_frame_repeated` 现为 REOBSERVE。旧 Rule 的历史结果仍是 19/20，没有修改旧报告。这是已知缺陷修复后的已暴露 suite 回归，不是新泛化成绩，也没有重跑 Qwen。

新生成的本地记录为 `outputs/m24-rule-regression-v1.json` 与 `outputs/m24-stream-demo-v1.jsonl`，使用独占创建模式写入，不覆盖旧文件；与其他 outputs 一样被 git 忽略。隔离交付时它们保存在隔离副本下。文件/标准输入的 8 行回放均与上表逐项一致。

审查基点是 `912848664e767d3c983f4d2e081abee19a44e380` 上已有 M2.3d 工作区加链接修正的字节快照，审查只覆盖新增 M2.4 差异。按 code-review skill 并行只读审查：**Standards 0 项文档规范违反、0 项值得报告的启发式结构问题；Spec 0 项需修复问题**。已补齐审查时尚待录入的测试与回归记录。原 suite、开发数据、旧报告和原始 outputs 的字节 hash，以及两版提示的 AST hash 均已对照，全部保持一致。原分支的 M2.3d 暂存内容也保持原样。

保留的接口限制：会话状态不持久化；确认只保存最新值；旧格式无帧元数据的兼容不能证明来源独立。M1 现场接入应提供真实帧来源，并由可信操作员入口提交确认。真实感知/现场多步搜索未在本轮验证。

## 下一步 M1 接入

M1 只需在每次采集后构造上述 observation 对象，通过标准输入管道、JSONL 日志，或同进程 `StreamSession.process_event()` 提交。不需要改摄像头或 OCR 模型；适配器从跟踪器/人工标注取得 landmark_id 和 segment_id，并为同帧 ROI 共用 frame_id。没有可靠关联就省略 landmark_id，禁止用相同 OCR 字符串当门的 ID。

每个 ROI 分配稳定的 observation_id，网络重试保持全部字段不变；重新采集须使用新 frame_id/observation_id。逐行读取 JSON 回复，展示动作、facts、假设和不确定性；人工确认按钮单独提交 operator_confirmation。先录制现场 JSONL 供确定性回放，再进行人执行建议的现场验收。

Qwen 实时策略可在这个 Observation/Memory 边界上另接现有 `build_evidence_snapshot` / `LLMPlanner`，后续必须显式启用；本轮没有策略开关或自动模型请求。
