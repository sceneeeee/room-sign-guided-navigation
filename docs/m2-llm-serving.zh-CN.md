# M2.3b：连接真实预训练 LLM（OpenAI-compatible API）

## 当前能力与边界

本阶段新增**可连接真实文本模型的 API 客户端**。它调用 OpenAI-compatible Chat Completions 接口（`POST /v1/chat/completions`），例如已启动的 Qwen 本地推理服务或其他兼容网关。它不负责下载、训练、量化或启动模型，不负责实时摄像头取流。

**模型调用仅发生于用户显式执行 `roomnav-llm` 且未加 `--dry-run` 时。** 无需 API Key 即可运行不设鉴权的本地服务器；有鉴权的服务器使用 `ROOMNAV_LLM_API_KEY` 环境变量，切勿提交密钥到 Git 仓库。

M2.3a 的 `EvidenceSnapshot`、严格 JSON 解析、`FOUND`/`VERIFY_TARGET` 证据校验、超时与显式回退**保持不变**。遇到无法解析的模型输出、服务不可用或模型越权时，`llm_result.fallback_used=true`，并有 `error_kind` 和 `validation_errors`；绝对不能把回退结果当作模型成功。

## 安装与首次演练（WSL）

在本项目 `feat/m2-reasoning` 分支：

```bash
cd ~/projects/room-sign-guided-navigation
.venv/bin/python -m pip install -e .
.venv/bin/python -m unittest discover -s tests -v
```

先在不运行 GPU、不触网的情况下检查样例和 Rule-based 对照：

```bash
roomnav-llm --dry-run --target EB104 --observations examples/observations_eb.jsonl
```

启动一个你有权限使用、已载入预训练模型的 OpenAI-compatible 服务，并确认它的模型 ID。URL **必须是 API base**，例如 `http://127.0.0.1:8000/v1`，而非已经包含 `chat/completions` 的完整路径。

```bash
export ROOMNAV_LLM_BASE_URL=http://127.0.0.1:8000/v1
export ROOMNAV_LLM_MODEL='这里替换成服务端实际暴露的模型 ID'
# 只有服务端需要鉴权时才设置：
# export ROOMNAV_LLM_API_KEY='仅在本机环境中设置，不提交 Git'

roomnav-llm --target EB104 --observations examples/observations_eb.jsonl \
  --timeout 30 --max-tokens 1024 --output-jsonl results-m23b.jsonl
```

需要比较两个模型时，分别修改 `ROOMNAV_LLM_MODEL`（或传 `--model`），保持**同一份 JSONL 观测**、当前走廊和目标不变。可以先用 Instruct 版本，再用 Thinking 版本。记录包括 Rule-based 与 LLM 的决策、实际回退状态、耗时和（若服务返回）token usage。**是否与规则一致不等于策略正确**；尚需要冻结的 Ground Truth 与模拟搜索任务评估。

对于 Qwen Thinking，模型可能使用较多输出 token；可增大 `--max-tokens`。一些推理服务把思考内容放入独立 `reasoning_content`，另一些可能将 `<think>` 放在 `content`；若不是严格 JSON，当前 Planner 会**安全回退**而不是通过正则截取 JSON 伪造成功。只在支持 JSON mode 的服务上尝试 `--json-mode`，否则服务器可能返回 HTTP 错误。

## 8GB 显卡的可选首次部署：Ollama + Qwen3-4B

如果在自己的 GPU 电脑上使用 Ollama，优先选择官方模型目录中的 **Qwen3-4B-Instruct-2507 Q4_K_M**（模型包约 2.5 GB，实际运行显存还取决于上下文与推理设置）。这只是推荐的本地部署方案，**尚未在项目机器上验证**。

先确保 [Ollama](https://ollama.com/) 已安装且服务已启动。在安装了 Ollama 的终端运行：

```bash
ollama pull qwen3:4b-instruct-2507-q4_K_M
ollama list
```

当服务已在端口 `11434` 监听时，**在运行本项目的 WSL 终端**执行：

```bash
curl --max-time 10 http://127.0.0.1:11434/v1/models
export ROOMNAV_LLM_BASE_URL=http://127.0.0.1:11434/v1
export ROOMNAV_LLM_MODEL=qwen3:4b-instruct-2507-q4_K_M
roomnav-llm --target EB104 --observations examples/observations_eb.jsonl --timeout 60 --max-tokens 1024
```

如果 Ollama 在 Windows、而项目在 WSL2，`localhost` 的可达性取决于网络模式和端口转发，**先以 curl 是否成功为准**；不要通过关闭防火墙或全网开放推理端口来凑通。Ollama 的本地 OpenAI-compatible 接口支持 `/v1/chat/completions`，本项目不需要安装 OpenAI Python SDK。

首次成功后再测试 `qwen3:4b-thinking-2507-q4_K_M`（官方目录同样约 2.5 GB），为 Thinking 版本调整 `--max-tokens` / `--temperature` 并检查输出是否符合严格 JSON。模型包大小不等于 GPU 峰值显存。目录：[Ollama Qwen3 tags](https://ollama.com/library/qwen3/tags)，[Ollama OpenAI compatibility](https://github.com/ollama/ollama/blob/main/docs/api/openai-compatibility.mdx)。

## MacBook 与 GPU 之间的连接

服务如果运行在远程 GPU 电脑上，建议**仅绑定远程 localhost，再使用 SSH 端口转发**。在 MacBook / WSL 上建立转发之后，可将 `ROOMNAV_LLM_BASE_URL` 指向转发后的 `http://127.0.0.1:8000/v1`。如果跨不可信网络直接连接，使用 HTTPS 和经过鉴权的端点，避免把视频 OCR 与实验观测以明文发送。

## 安全与资源限制

- 此 API 客户端的 `generate()` 带 socket 超时；M2.3a Planner 额外设置等待上限，同一后端最多一个调用，全进程最多四个未完成调用。Python 无法强行杀死卡住的线程。服务器需要自行限制推理时间。
- 单次 HTTP 回复最多读取 1 MiB；服务器异常与响应体不写入异常信息，密钥不会出现在 `repr(model)` 或实验日志。
- `facts` 由程序根据已观察 Memory 生成，模型无法直接写入事实；模型给出的自由文本解释仍只是建议，不可作为已确认的地图事实。
- 只使用到当前决策时刻为止的观测；JSONL 文件不能包含未来帧，代码**不会**自动按时间倒带。
- 本阶段的离线模拟 HTTP 测试只能证明适配器协议和数据约束；**没有获得用户真实 GPU 模型服务的响应之前，不宣称真实 Qwen 推理或导航实验已成功。**

## 与 M2.3a 的关系

- `src/roomnav/openai_compatible.py`：可配置 OpenAI-compatible Chat Completions 传输，API 错误脱敏与 usage 采集。
- `src/roomnav/llm_cli.py`：`roomnav-llm` 命令读取同一观测快照，执行 Rule-based 与可替换 LLM 对照，可选 JSONL 结果记录。
- `tests/test_openai_compatible.py`：不联网的 HTTP 仿真测试。
- M2.3a 的 `LLMPlanner`、`FakeModel` 和规则基线保持不变。
