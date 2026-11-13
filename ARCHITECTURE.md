# AgentXR 架构说明

**版本 0.14.0** · 文档最近一次随代码更新：2026-11-13 14:25:35 · 位置：`/Users/dase_mac2024/Downloads/agentxr/ARCHITECTURE.md`

> 本文件由 `scripts/update_docs.py` 根据 `docs/ARCHITECTURE.template.md` 和当前代码自动生成，**不要直接编辑 ARCHITECTURE.md**。
> 叙述部分在模板里改；标有「自动生成」的小节直接从代码抽取，代码一变就会跟着变。
> 若「过时引用检查」报警，说明模板里提到的函数或文件已经改名或删除，需要人工更新叙述。

一句话概括：**Codex 负责「看懂与提议」；脚本和服务端负责「什么时候看、带什么记忆、哪些提议可以展示给你」**。

## 一、谁负责什么

| 角色 | 负责 | 不负责 |
|---|---|---|
| **Codex** | 所有语义判断：画面里有什么、你可能想做什么（意图候选）、计划分几步、下一步具体动作、框与箭头的坐标、前后两帧的变化、动作效果是否达到预期、故障假设、需要你展示什么信息，以及「附带的问题、猜对概率、猜错代价」 | 不调用工具、不执行命令、不读文件；不决定什么时候分析、带哪几张历史图；不能直接改记忆 |
| **服务端 Python** | 决定调用时机、发送的图片和上下文、输出格式（JSON schema）；对模型输出逐项核对后降级、隐藏或冻结；存取记忆；决定是否打断你 | 基本不自己写指引内容，只有拒绝模型建议时填固定的兜底文字 |
| **浏览器页面** | 采集摄像头、压缩图片、框选局部；显示指引、箭头动画、提问卡片和反馈按钮；会话与记忆侧栏 | 不做判断 |

快速通道默认关闭（`lanes.guide`）；开启时可走本机 Ollama（`local_vlm.py`，模型见 `lanes.guide_local_model`）：提示词 `LOCAL_PROMPT`、小格式 `LOCAL_SCHEMA`，`expand_local()` 补齐为 `GUIDE_SCHEMA` 后与 Codex 输出走同一套 `reconcile_guide()` 规则；Ollama 不可用时退回 Codex。
Codex 由 `codex_rpc.py` 以 `codex app-server --listen stdio://` 常驻启动，通过 JSON-RPC 通信。启动时关闭 shell 工具、MCP 及 `config.py` 中 `codex.disable_features` 列出的功能；线程为只读沙箱、从不请求批准、不保存；内置的编程助手系统提示被换成 `lanes.base_instructions` 中的一句话。因此它在这里只是一个**按格式输出 JSON 的看图模型**。

## 二、一帧画面的处理流程

```
浏览器                          server.py                                   Codex
──────                          ─────────                                   ─────
输入任务文字（停顿 1 秒）─PATCH─▶ request_brief() ─────────────────────▶ 任务理解（第三个进程，纯文字）
                                ◀─ 解析结果：步骤/要求/第一步 ── 存为 task_brief，随后每帧都带上
定格一帧 → 主图 + 记忆缩略图     submit(): 校验
 + 框选区域的同帧裁剪 ──POST──▶  只保留最新一帧，排队中的旧帧标记作废
                                ┌─ 快速通道 _run_guide ─────────────────▶ 本地 Qwen（LOCAL_SCHEMA，compact_context）
                                │   上下文：任务 + 计划进度 + 已知信息 + 你的回答    或 Codex 新线程（GUIDE_SCHEMA）
                                │   ◀─ 流式 JSON ───────────────────────────────
                                │   reconcile_guide() → 提问规则 → 箭头过滤
                                │
                                └─ 完整通道 _run_full ─────────────────▶ 复用线程（有轮数上限）
                                    挑选图片：上一个有效动作 / 预期基线 /             大格式 SCHEMA
                                    历史任务全景 / 当前图 / 局部裁剪                  （或信息读取用 FAST_SCHEMA）
                                    ◀─ 流式 JSON ───────────────────────────────
                                    后处理规则链（见下方自动生成的顺序）
                                    写入会话存储
页面 ◀──── SSE 推送（流式片段与核对后的最终结果）
```

### 后处理顺序（自动生成）

按源码中的调用顺序列出各通道调用的规则函数（只列项目自己的模块函数）。

- **完整通道 `Engine._run_full`**：`crop.jpeg_size()` → `temporal.select_reference()` → `effects.select_expectation()` → `inspection.select_inspection()` → `inspection.release_inspection()` → `scene_memory.select_scene()` → `crop.jpeg_size()` → `scene_memory.scene_context()` → `effects.public_expectation()` → `scene_memory.scene_context()` → `brief.brief_for_frames()` → `ask.context()` → `fast_inspection.expand_response()` → `inspection.reconcile_inspection()` → `inspection.release_inspection()` → `fast_inspection.expand_response()` → `server.normalize()` → `temporal.reconcile_temporal()` → `actions.reconcile_action()` → `diagnostics.reconcile_diagnostics()` → `scene_memory.remember_scene()` → `inspection.inspection_candidate()` → `effects.reconcile_effect()` → `inspection.reconcile_inspection()` → `effects.record_effect()` → `effects.remember_expectation()` → `temporal.remember()` → `ask.decide()` → `ask.apply()` → `visual.reconcile_cues()` → `visual.ensure_move_cue()` → `visual.reconcile_overview()` → `visual.plan_from_overview()` → `effects.public_expectation()` → `ask.commit()` → `ask.public()`
- **快速通道 `Engine._run_guide`**：`brief.brief_for_frames()` → `guide.guide_context()` → `local_vlm.compact_context()` → `local_vlm.expand_local()` → `guide.reconcile_guide()` → `ask.commit()` → `ask.public()` → `visual.plan_from_overview()`
- **快速通道核对 `guide.reconcile_guide`**：`contracts.validate()` → `contracts.check_boxes()` → `actions.project_targets()` → `ask.decide()` → `ask.apply()` → `visual.reconcile_cues()` → `visual.reconcile_overview()`

## 三、各项功能怎么实现

| 功能 | Codex 做的 | 代码控制的 |
|---|---|---|
| **任务理解** | 只读你填的文字（不看图），输出目标、步骤与完成标志、具体要求、缺失信息、安全前提和开拍前的第一步 | `brief.py` 与 `Engine.request_brief()`：输入停顿约 1 秒后经 `PATCH /api/sessions/{id}` 触发；在独立的 Codex 进程上运行，新文字取代旧文字，相同文字不重复解析；`reconcile_brief()` 保证你填的目标和步骤原样保留；结果存入会话；文字变化后旧结果不再使用；没填步骤时 `plan_from_brief()` 生成草稿计划 |
| **双通道（快速通道默认关闭）** | 完整通道：Codex；快速通道可选（Codex 或本地 Qwen） | `server.py`：只处理最新帧；同一时间只允许一个页面使用；快速通道每轮开新线程（状态由代码显式提供），完整通道达到 `threads.full_max_turns` 后换线程，限制图片上下文长度 |
| **意图推断** | 最多 3 个候选意图，附可信度和依据 | `normalize()`：删除没有依据的候选；第一名低于 `intent_min_confidence` 或前两名相差不到 `intent_margin` 时标为「有歧义」，并把候选转成一个问题交给提问规则 |
| **下一步指引** | 动词、对象、目标位置、前提条件和验收标准 | `reconcile_action()`：可信度不足、没有定位框或画面差时降级为「先补充细节」；需要精确到孔位时，必须有计划和可读标记，否则降级为「先确认」 |
| **箭头与动画** | 箭头、旋转、按压、插拔等示意的坐标；对象框与目标位置框 | `reconcile_cues()`：模型定位了对象和目标位置却没画箭头时，`ensure_move_cue()` 自动补一条对象→目标的箭头；只在「去做」类指引、画面可用、可信度和长度都达标时绘制，数量有上限；局部裁剪的坐标换算回全景；动画由 `web/cues.js` 实现 |
| **步骤总览** | 你没给步骤时推断 3 到 8 步的计划 | `reconcile_overview()`：你给的步骤一字不改；已建立的计划不能随意改写；快速通道不能把步骤标成完成，完整通道要有可见证据才能标 |
| **前后对比** | 比较「之前」和「现在」两张图 | `select_reference()` 挑参考图，`reconcile_temporal()`：没有参考图时强制标为基线；可信度不足或没有写出具体变化时标为不确定 |
| **动作效果核验** | 判断上一步的预期结果有没有出现 | `reconcile_effect()`：发出指引时冻结预期结果，模型之后不能改写；照片早于指引时只能是「待定」；没看到动作发生不能判失败；判为不符时自动把指引改成「核对结果」 |
| **故障诊断** | 可能原因与一个检查步骤 | `reconcile_diagnostics()`：要改动物理连接的检查，必须满足领域插件规定的前提（如先断电） |
| **信息检查** | 提出需要的信息，从画面读出数值，并给出目标所在区域 | `reconcile_inspection()` 与 `fast_inspection.py`：未完成的检查请求优先于其他指引；读取时改用只读这项信息的小格式，不上传历史图。专用读取轮的回答直接归属当前请求（不要求模型回填编号），字段名由 `match_field()` 宽松匹配；读到的信息记为「已知事实」并跨帧累积。**先放大再要求**：浏览器另传一份全分辨率照片供裁剪与必要时恢复整体诊断，服务端用 `crop.py` 在上次定位到的区域裁剪后再读；仍缺且模型给出了区域时，立即裁剪重读一次。专用读取无新增字段时，在同一帧切到完整任务 schema，携带已确认事实与未决信息，并使用已有高分辨率全景（若有）；冷却期内不重复锁定同一检查。全景与裁剪的有效事实合并保留，原始响应保存在 metrics.inspection_readings。`inspection_max_attempts` 保留为其他检查路径的退出上限 |
| **场景记忆** | 判断当前画面是任务全景还是近景 | `remember_scene()`：只保存一张任务全景，近景不会替换它 |
| **提问还是直接做** | 总是给出按最可能理解的一步，并可附一个问题，标出猜对概率、猜错代价、能否撤回 | `ask.py` 的 `decide()`：P(猜错) × 猜错代价 > 打扰代价时才打断；猜错概率按本会话你的回答在线修正；10 分钟内回答过的话题不再问；涉及安全的问题必问；不重要的缺口改为可选卡片 |
| **回答与反馈** | 下一帧在上下文里看到你的回答和反馈 | `POST /api/sessions/{id}/feedback` → `record()` 校验后入库；回答后立即重新分析 |
| **会话与记忆侧栏** | — | `store.py`：存放在 `storage.dir` 下，每个会话一个目录（session.json 与 images/）；可列出、恢复、继承所选部分、逐条删除 |
| **领域插件** | 按插件提示词行事 | `domains/*.json`：按关键词自动加载，提供额外提示词、孔位提示、改动前提、读取轮规则（inspection_prompt，如电子电路只问相对关系不读孔号）和重新取景提示（inspection_hint）；核心代码与领域无关 |

## 四、写死在代码里的部分

1. **阈值都是手工设定的**：见下方自动生成的配置表。它们是对 Codex 未校准的自评分数直接切阈值，没有用数据标定过，是当前最主观的部分。
2. **固定的后处理顺序**：后面的规则可以覆盖前面的结果；这些规则只会让结果更保守（降级、隐藏、冻结），不会让模型更大胆。
3. **固定的兜底文字**：中文写死，例如「先看清操作现场」「先验证上一动作的效果」，模型建议被拒绝时显示。
4. **代码决定发哪些图、带哪些记忆**：模型无法主动要求查看旧图。
5. **领域规则**：电子电路等规则写在 `domains/*.json`，不在 Python 代码里，但仍是人工规则。

最值得替换的是第 1 点：用积累的回答和反馈记录，把手工阈值换成校准值（例如保形预测）。

## 五、自动生成的事实

### 文件一览（自动生成）

| 文件 | 行数 | 说明（取自文件首行文档字符串） |
|---|---:|---|
| `actions.py` | 53 | Validate spatial action proposals before committing instructions to the UI. |
| `ask.py` | 244 | Ask-vs-act gate: decide whether an uncertainty is worth interrupting the user. |
| `board_reader.py` | 111 | Model-proposed endpoints on the labelled board grid (a 'set of marks'). |
| `breadboard.py` | 287 | Evidence-bound breadboard geometry. Independent implementation; see docs/BREADBOARD.md. |
| `brief.py` | 80 | Task understanding: a text-only Codex turn that reads what the user typed (goal / notes / steps) |
| `circuit.py` | 337 | Deterministic circuit rules on top of breadboard.evidence(): is this a working loop, and |
| `codex_rpc.py` | 140 | Persistent Codex app-server JSON-RPC transport. No shell or credential handling. |
| `config.py` | 128 | All tunable policy numbers in one place; settings.json may override any key. |
| `contracts.py` | 214 |  |
| `crop.py` | 66 | Server-side zoom: crop the full-resolution camera frame before asking the user to move. |
| `diagnostics.py` | 72 | Keep fault hypotheses separate from visible evidence and choose one check. |
| `domains.py` | 58 | Optional domain plug-ins. Core prompts and code stay task-agnostic. |
| `effects.py` | 102 | Action-effect evidence records. Expectations are frozen before later observation. |
| `fast_inspection.py` | 31 | Small visual-reading contract; task planning stays in the full observer. |
| `guide.py` | 111 | Quick guidance lane: a small contract streamed per frame while the full audit runs behind it. |
| `inspection.py` | 215 | Collect requested information before resuming whole-task diagnosis. |
| `local_vlm.py` | 194 | Local fast model (Qwen-VL via Ollama) for the quick guidance lane. |
| `scene_memory.py` | 25 | One historical task view, independent of short-lived temporal comparisons. |
| `server.py` | 1433 | Continuous latest-frame input and SSE output, backed by persistent Codex. |
| `store.py` | 228 | On-disk session memory: list / resume / inherit / delete, like a chat sidebar. |
| `temporal.py` | 32 | Bounded visual action memory, independent of Codex thread history. |
| `visual.py` | 149 | Validate animated cues and the task overview shared by both analysis lanes. |
| `web/app.js` | 815 |  |
| `web/board.js` | 187 |  |
| `web/cues.js` | 115 |  |
| `web/index.html` | 123 |  |
| `web/style.css` | 96 |  |
| `scripts/benchmark_latency.py` | 89 | Real CLI timing; no simulated results. |
| `scripts/make_board_markers.py` | 31 | Print-at-100% ArUco registration sheet. Board placement must be verified manually. |
| `scripts/smoke_cli.py` | 28 | Run a real CLI image request; no simulated response. |
| `scripts/update_docs.py` | 367 | Regenerate ARCHITECTURE.md from docs/ARCHITECTURE.template.md plus facts read from the code. |
| `observer_prompt.txt` | 53 |  |

### Codex 输出格式（自动生成）

字段顺序就是 Codex 流式生成的顺序；括号内是该字段的子字段。

- **完整通道 `contracts.SCHEMA`**（20 个顶层字段）：`guidance`（mode, title, instruction, check）；`question`（topic, text, options, default_option, p_default, cost_if_wrong, reversible, observable_later）；`task_overview`（goal, goal_confidence, steps, current_step_index, plan_revision_reason）；`visual_cues`（type, label, tool, coordinate_space, x1, y1, x2, y2, radius, direction, turns, confidence）；`next_action`（status, verb, object, instruction, expected_result, precision, confidence, location_label, location_basis, location_evidence, preconditions, missing_information, targets）；`overlays`（label, kind, x, y, width, height）；`phase`；`steps`（title, status, evidence）；`activity`；`summary`；`quality`；`confidence`；`scene_scope`；`evidence`；`uncertainties`；`intent`（status, candidates, clarification）；`temporal`（state, action, changes, confidence）；`action_effect`（source, action, effect_type, expected_change, actual_observation, action_status, action_evidence, outcome_evidence, prerequisites, observability, verdict, confidence, affected_steps, verification）；`inspection`（proposal, response）；`diagnostics`（status, expected, observed, state_checks, evidence, failed_steps, causes, check, regions）
- **快速通道 `guide.GUIDE_SCHEMA`**（8 个顶层字段）：`guidance`（mode, title, instruction, check, precision, location_basis）；`question`（topic, text, options, default_option, p_default, cost_if_wrong, reversible, observable_later）；`task_overview`（goal, goal_confidence, steps, current_step_index, plan_revision_reason）；`visual_cues`（type, label, tool, coordinate_space, x1, y1, x2, y2, radius, direction, turns, confidence）；`targets`（role, label, coordinate_space, x, y, width, height, confidence）；`activity`；`quality`；`confidence`
- **信息读取 `fast_inspection.FAST_SCHEMA`**（7 个顶层字段）：`request_id`；`target`；`quality`；`status`；`confidence`；`findings`（field, value, evidence）；`regions`（role, label, coordinate_space, x, y, width, height, confidence）

### 可调参数与写死的阈值（自动生成）

默认值来自 `config.py` 的 `DEFAULTS`；`settings.json` 可以覆盖任意一项（右列显示当前覆盖值）。

| 参数 | 默认值 | settings.json 覆盖 |
|---|---|---|
| `thresholds.usable_confidence` | `0.55` |  |
| `thresholds.temporal_change_confidence` | `0.65` |  |
| `thresholds.action_confidence` | `0.7` |  |
| `thresholds.target_confidence` | `0.7` |  |
| `thresholds.cue_confidence` | `0.6` |  |
| `thresholds.intent_min_confidence` | `0.65` |  |
| `thresholds.intent_margin` | `0.15` |  |
| `thresholds.effect_confidence` | `0.65` |  |
| `thresholds.inspection_confidence` | `0.7` |  |
| `thresholds.step_done_confidence` | `0.55` |  |
| `thresholds.cue_min_length` | `0.02` |  |
| `thresholds.cue_min_radius` | `0.01` |  |
| `memory.anchor_max_age_seconds` | `300` |  |
| `memory.effect_records` | `40` |  |
| `memory.effect_context_records` | `4` |  |
| `memory.inspection_facts` | `24` |  |
| `memory.inspection_records` | `40` |  |
| `memory.inspection_max_attempts` | `4` |  |
| `memory.inspection_retry_cooldown_seconds` | `60` |  |
| `memory.inspection_region_seconds` | `20` |  |
| `memory.inspection_context_records` | `3` |  |
| `memory.guide_context_facts` | `8` |  |
| `memory.sessions_in_ram` | `32` |  |
| `memory.jobs_in_ram` | `40` |  |
| `memory.saved_records` | `24` |  |
| `threads.full_max_turns` | `8` |  |
| `threads.guide_max_turns` | `1` |  |
| `images.current_max_width` | `768` |  |
| `images.memory_max_width` | `512` |  |
| `images.detail_max_side` | `1024` |  |
| `images.jpeg_quality` | `0.8` |  |
| `images.detail_quality` | `0.9` |  |
| `images.hires_max_width` | `1920` |  |
| `images.hires_quality` | `0.85` |  |
| `lanes.guide` | `false` |  |
| `lanes.guide_backend` | `"codex"` |  |
| `lanes.guide_local_model` | `"qwen3-vl:8b-instruct"` |  |
| `lanes.ollama_url` | `"http://127.0.0.1:11434"` |  |
| `lanes.guide_local_max_tokens` | `600` |  |
| `lanes.guide_reasoning_effort` | `"low"` |  |
| `lanes.brief_reasoning_effort` | `"low"` |  |
| `lanes.inspection_reasoning_effort` | `"low"` |  |
| `lanes.base_instructions` | `"你是图像观察与操作引导助手。只根据附带图片和上下文 JSON 回答，严格按要求的 JSON schema 输出，不调用任何工具。"` |  |
| `codex.disable_features` | `["apps", "browser_use", "browser_use_external", "computer_use", "image_generation", "mu…` |  |
| `storage.dir` | `"~/.agentxr/sessions"` |  |
| `storage.persist_images` | `true` |  |
| `visual.live_cue_fade_seconds` | `10` |  |
| `visual.max_cues` | `4` |  |
| `asking.level` | `"balanced"` |  |
| `asking.interrupt_cost` | `{"quiet": 3.0, "balanced": 1.5, "careful": 0.6}` |  |
| `asking.error_cost` | `{"low": 1, "medium": 3, "high": 8, "critical": 30}` |  |
| `asking.irreversible_multiplier` | `2` |  |
| `asking.observable_discount` | `0.5` |  |
| `asking.model_weight` | `0.5` |  |
| `asking.calibration_prior` | `[1, 4]` |  |
| `asking.repeat_window_seconds` | `60` |  |
| `asking.repeat_multiplier` | `2` |  |
| `asking.answer_valid_seconds` | `600` |  |
| `asking.answers_kept` | `40` |  |
| `asking.answers_in_context` | `6` |  |

settings.json 中还有这些服务器启动参数：`fast_inspection`、`proxy`、`reasoning_effort`

### HTTP 接口与能力（自动生成）

- 服务端版本：`0.14.0`
- 固定路径：`/api/analyze`、`/api/breadboard/profiles`、`/api/breadboard/read`、`/api/breadboard/rectify`、`/api/cancel`、`/api/domains`、`/api/events`、`/api/health`、`/api/jobs/`、`/api/sessions`
- 会话子路径 `/api/sessions/{id}/…`：`feedback`、`images`、`memory`、`records`
- `/api/health` 声明的能力：`intent-guidance`、`temporal-action-memory`、`precise-actions`、`visual-diagnostics`、`action-effect-records`、`inspection-focus`、`task-scene-memory`、`fast-inspection`、`guide-lane`、`visual-cues`、`task-overview`、`session-store`、`domains`、`ask-gate`、`user-feedback`、`task-brief`、`auto-zoom`、`local-guide`、`board-circuit`、`board-read`

### 领域插件（自动生成）

- `domains/electronics.json`：电子电路 / 面包板；关键词 13 个；改动前提 1 条；预设 1 个

### 测试（自动生成，未运行，只统计）

- `tests/test_ask.py`：15 个测试函数
- `tests/test_board_reader.py`：5 个测试函数
- `tests/test_breadboard.py`：18 个测试函数
- `tests/test_circuit.py`：10 个测试函数
- `tests/test_effects.py`：11 个测试函数
- `tests/test_fast_inspection.py`：2 个测试函数
- `tests/test_inspection.py`：17 个测试函数
- `tests/test_lanes.py`：20 个测试函数
- `tests/test_scene_memory.py`：4 个测试函数
- `tests/test_server.py`：37 个测试函数
- `tests/test_visual.py`：8 个测试函数
- `tests/test_board_frontend.cjs`：前端测试（`node tests/test_board_frontend.cjs`）
- `tests/test_frontend.cjs`：前端测试（`node tests/test_frontend.cjs`）
- 合计 147 个 Python 测试函数；运行：`python3 -W error::ResourceWarning -m unittest discover -s tests`

### 过时引用检查（自动生成）

✓ 叙述中提到的函数、文件、常量和配置项都还在代码里。（只检查名字是否存在，不检查描述的行为是否仍然准确。）

### 变更记录（自动生成）

每次检测到被跟踪文件变化时记一条（最近 30 条，新的在前）。

- 2026-11-13 14:25:35：修改 `README.md`
- 2026-11-13 13:58:08：修改 `docs/ARCHITECTURE.template.md`
- 2026-11-13 13:58:08：修改 `README.md`
- 2026-11-13 13:54:06：修改 `tests/test_frontend.cjs`
- 2026-11-13 13:53:47：修改 `start.command`
- 2026-11-13 13:53:47：修改 `observer_prompt.txt`
- 2026-11-13 13:53:47：修改 `web/style.css`
- 2026-11-13 13:53:47：修改 `web/app.js`
- 2026-11-13 13:53:46：修改 `web/index.html`
- 2026-11-13 13:53:46：修改 `web/index.html`
- 2026-11-13 13:53:46：修改 `web/board.js`
- 2026-11-13 13:51:34：修改 `board_reader.py`
- 2026-11-13 13:51:34：修改 `board_reader.py`
- 2026-11-13 13:51:20：新增 `tests/test_board_reader.py`
- 2026-11-13 13:50:54：修改 `tests/fake_app_server.py`
- 2026-11-13 13:50:54：修改 `server.py`
- 2026-11-13 13:50:54：修改 `server.py`
- 2026-11-13 13:50:54：修改 `server.py`
- 2026-11-13 13:50:54：修改 `server.py`
- 2026-11-13 13:50:53：修改 `server.py`
- 2026-11-13 13:50:53：新增 `board_reader.py`
- 2026-11-13 13:49:07：修改 `breadboard.py`
- 2026-11-13 13:48:36：修改 `tests/test_circuit.py`
- 2026-11-13 13:48:36：修改 `circuit.py`
- 2026-11-13 13:48:20：修改 `tests/test_circuit.py`
- 2026-11-13 13:48:09：修改 `circuit.py`
- 2026-11-13 13:48:09：修改 `circuit.py`
- 2026-11-13 13:47:49：新增 `tests/test_circuit.py`
- 2026-11-13 13:47:49：修改 `breadboard.py`
- 2026-11-13 13:47:13：新增 `circuit.py`

## 面包板几何路径

`breadboard.py` 和 `web/board.js` 提供冻结帧的六点/ArUco 标定、独立复核、源图孔距门槛、端点确认、节点合并与测量比对。`contracts.prepare()` 重算用户提供的板面证据，`server._run_full()` 将同帧原图、编号图和连接表送入完整任务通道。详见 [BREADBOARD.md](docs/BREADBOARD.md)。

0.14 在其上加了三层：`board_reader.py`（`Engine.request_board_read()` 在文字通道上让 Codex 对编号图用孔名提议端点，`reconcile_read()` 核对孔名与置信度，页面逐个采用）、`circuit.py`（`analyze()`：从已确认端点算回路闭合/短路/接反/缺电阻/元件被绕过，未闭合时单步修复搜索给出「从哪个孔移到哪个孔」）、照片叠加（已确认端点、候选、建议移动箭头）。分工不变：模型只读标号与转述规则结论，连接关系由代码判定。

| 功能 | Codex 做的 | 代码控制的 |
|---|---|---|
| **读端点候选** | 在编号图上用孔名回答每根线端、每只脚在哪 | `reconcile_read()` 丢弃不在孔格里的孔名、低置信度和重复项；候选必须由用户采用 |
| **回路判断** | 转述 `board_evidence.circuit` 的结论和下一步 | `circuit.analyze()` 全部确定性：节点并查集、通路搜索、规则与单步修复排序 |
