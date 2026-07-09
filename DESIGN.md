# 对话式视频剪辑工作台 — 设计文档

## 1. 产品定义

**一句话**：人在回路中的对话式剪辑。AI 做执行层苦力，人做创意决策。

**核心理念**：
- 不是"一条指令自动出成片"的黑盒
- 不是"传统时间轴上加 AI 按钮"
- 是**对话驱动**——在预览台打标记定位关键时间点，然后用自然语言告诉 AI 你要什么。半自动和全自动的区别只是你说几句

```
你在聊天里说 "帮我自动规划实现"
    → AI: 选片 → 生成 → 渲染 → 给下载链接
```

---

## 2. 架构设计

```
                         ┌─ pipelines/game-highlight.yaml ─┐
                         │  validate → audio → subtitle     │
                         │  → compose → quality_check       │
                         └────────────┬─────────────────────┘
                                      │
前端 (index.html)  ←WebSocket→  FastAPI 服务层  ←ffmpeg→  渲染引擎
    │                                 │
    ├ 视频预览 (HTML Video)           ├ NLU 解析 (LLM + 规则引擎)
    ├ 时间轴 (Canvas 只读)            ├ Session 管理 + 持久化
    ├ 对话面板                        ├ EditPlanner (标记优先)
    ├ 管道进度 UI                     ├ SkillManager (技能匹配)
    ├ LLM 配置面板                    ├ PipelineEngine (管道渲染)
    └ 导出面板                        ├ Reviewer (CHAI 质检)
                                      ├ AudioAnalyzer (BGM 节拍)
                                      └ ReferenceAnalyzer (风格复刻)
```

### 分层原则

| 层 | 职责 | 依赖方向 |
|---|---|---|
| 引擎层 | 纯逻辑：timeline 模型、ffmpeg 操作、管道质检 | 不依赖上层 |
| 服务层 | 编排：会话管理、NLU 解析、剪辑规划、BGM 分析 | 依赖引擎层 |
| 前端层 | 展示：UI、WebSocket 通信 | 依赖服务层 API |

---

## 3. 服务层核心组件

### 3.1 NLU 解析器（NLUParser）

双层解析策略：
1. **规则引擎优先** — 正则匹配常见指令（渲染/撤销/删除/提取/过渡），毫秒级响应
2. **LLM 兜底** — 复杂意图（"把击杀片段慢放到 0.5 倍再加个闪白"）走 Ollama

```python
def parse(user_input: str, context: dict) -> dict:
    # 1. compose 对话活跃中 → 直接视为 compose_answer
    if context.get("compose_active"):
        return {"action": "compose_answer", "answer": user_input}

    # 2. 规则匹配
    rule_result = self._rule_parse(user_input, context)
    if rule_result and rule_result["action"] != "unknown":
        return rule_result

    # 3. LLM 兜底
    return self._llm_parse(user_input, context)
```

### 3.2 剪辑规划器（EditPlanner）

标记优先策略：
- **有标记** → 秒级响应，直接基于标记时间点 + 用户意图生成方案
- **无标记** → 调 LLM 分析视频场景 + 运动强度生成方案
- **BGM 已加载** → 切点自动吸附到最近节拍，Drop 段硬切、低谷段淡入淡出

```python
class EditPlanner:
    def propose_plan(self, analysis_summary, user_intent, duration) -> EditPlan

class EditPlan:
    title: str              # 方案标题
    target_duration: str    # 目标时长
    vibe: str               # 风格标签
    structure: list[str]    # 段落结构
    clips: list[ClipProposal]  # 具体切点
    reasoning: str          # 决策理由
```

### 3.3 对话式自动规划（Compose Flow）

状态机驱动，3 步引导：

```
stage: "style"   →  回答风格（热血/搞笑/文艺...）
    ↓
stage: "effect"  →  回答效果（闪白/慢动作/淡入淡出...）
    ↓
stage: "pace"    →  回答节奏（快节奏/慢→快/交替...）
    ↓
stage: "done"    →  生成方案 + 自动执行 + 返回时间轴
```

支持一步到位：说"帮我自动规划实现"直接走完全流程。

### 3.4 BGM 分析器（AudioAnalyzer）

- 节拍检测 — 基于 RMS 能量 + onset detection
- Drop/Valley 段识别 — 能量分段聚类
- 支持缓存（`.bgm_cache/`），避免重复分析

切点吸附逻辑：

```python
nearest = bgm.nearest_beat(start)
if abs(offset) < 0.3:  # 0.3s 以内吸附
    start += offset

# 终点落在 Drop 段 → 延长到 Drop 结束
for d in bgm.drop_sections:
    if d["start"] <= end <= d["end"] and (d["end"] - end) < 1.0:
        end = d["end"]
```

### 3.5 技能系统（SkillManager）

动态加载 `skills/` 目录下的 Markdown 技能文件。NLU 解析和规划器自动匹配触发词。

```markdown
# 触发规则（YAML frontmatter）
triggers: ["高光", "集锦", "精彩"]
always: false

# 技能内容（Markdown body）
## 高光混剪模板
- 选取运动强度 peak > 0.7 的片段
- 转场用 flash，0.3s，颜色 #FFE4B5
- 结尾渐暗不收纯黑
```

---

## 4. 引擎层核心模型

### Timeline

```python
@dataclass
class Clip:
    id: str
    source_id: str
    source_start: float
    source_end: float
    speed: float = 1.0
    volume: float = 1.0
    label: str = ""

@dataclass
class Transition:
    id: str
    effect: str           # "cut" | "flash" | "dissolve"
    duration: float = 0.3
    params: dict = {}

class Timeline:
    items: list[TimelineItem]  # 交替排列：clip → transition → clip
    total_duration: float      # 计算属性
    clip_count: int
```

### 管道引擎

```yaml
# pipelines/game-highlight.yaml
stages:
  - name: validate
    produces: validation_report
  - name: audio_prep
    requires: [validation_report]
    produces: audio_manifest
  - name: subtitle
    requires: [validation_report, audio_manifest]
    produces: subtitle_manifest
  - name: compose
    requires: [validation_report, audio_manifest, subtitle_manifest]
    produces: composition_report
  - name: quality_check
    requires: [composition_report]
    produces: qc_report
```

质检协议（CHAI）：
- **Accurate** — 每条 Finding 指向具体字段/帧号
- **Complete** — 扫描同类全貌
- **Constructive** — critical 附带修复方案

---

## 5. 会话管理

```python
class EditSession:
    id: str
    timeline: Timeline
    undo_manager: UndoManager
    media_store: MediaStore
    renderer: Renderer

    # 新增 (v0.4)
    planner: EditPlanner
    markers: list[dict]           # 用户标记时间点
    current_plan: EditPlan        # 当前剪辑方案
    compose_state: dict           # 对话引导状态机
    bgm_analysis: BgmAnalysis     # BGM 分析结果
    emotion_map: dict             # 片段情绪标签

    def propose_plan(intent)      # 生成方案（标记优先）
    def execute_plan()            # 一键执行方案
    def load_bgm(path)            # 加载 BGM + 节拍分析
```

持久化：`/tmp/conversational-editor/sessions/{session_id}.json`

---

## 6. API 设计

### REST

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/upload` | 上传视频文件 |
| POST | `/api/load-video` | 加载本地视频路径 |
| GET | `/api/sessions` | 列出已保存项目 |
| GET | `/api/session/{id}` | 获取项目信息 |
| GET | `/api/frame/{id}?time=N` | 提取帧截图 |
| GET | `/api/thumbnails/{id}` | 获取缩略图条带 |
| POST | `/api/markers/{id}` | 同步标记数据 |
| POST | `/api/render/{id}` | 触发管道渲染 |
| GET | `/api/render/status/{id}` | 渲染进度 |
| POST | `/api/render/cancel/{id}` | 取消渲染 |
| POST | `/api/export/{id}` | 导出视频（多平台） |
| POST | `/api/load-bgm/{id}` | 加载 BGM |
| GET | `/api/bgm-beats/{id}` | 获取节拍数据 |
| POST | `/api/analyze-reference` | 分析参考视频 |
| POST | `/api/replicate-style` | 风格复刻 |
| GET/POST | `/api/config` | LLM 配置 |
| GET/POST | `/api/skills` | 技能管理 |

### WebSocket

```
ws://host:8765/ws/{session_id}
```

消息类型：
| type | 方向 | 说明 |
|------|------|------|
| `session_ready` | S→C | 连接建立 |
| `user_message` | S→C | 用户消息回显 |
| `edit_result` | S→C | 操作执行结果 + 时间轴状态 |
| `compose_guide` | S→C | 对话引导问题 + 建议按钮 |
| `compose_done` | S→C | 自动规划完成 |
| `preview_ready` | S→C | 预览视频就绪 |
| `error` | S→C | 错误信息 |

---

## 7. 对话指令支持

```
✅ "从 0:32 到 0:35 提取出来"
✅ "这段放慢到 0.5 倍"
✅ "中间加个 0.3 秒闪白"
✅ "删掉最后一个片段"
✅ "渲染预览"
✅ "撤销"
✅ "保存项目"
✅ "这是永劫无间击杀集锦"          → 触发自动规划
✅ "帮我自动规划实现"               → 一键全自动
✅ "热血燃向" / "闪白转场" / "快节奏冲击"  → 对话式引导回答
```

---

## 8. 技术选型

| 组件 | 选型 | 理由 |
|------|------|------|
| 后端框架 | FastAPI + uvicorn | 异步 + WebSocket 原生支持 |
| 前端 | 单 HTML + 原生 JS + CSS | 零构建，直接 serve |
| 通信 | WebSocket | 实时双向 |
| 视频播放 | `<video>` 标签 | 浏览器原生 H.264 |
| 时间轴 | Canvas 2D | 色块绘制 |
| 渲染 | ffmpeg subprocess | 稳定可靠 |
| NLU | Ollama (qwen3.5:9b) + 规则引擎 | 本地推理，离线兜底 |
| 存储 | JSON 文件 | Git 友好 |
| BGM 分析 | librosa + numpy | Python 生态 |

---

## 9. 目录结构

```
conversational-editor/
├── engine/                         # 引擎层（纯 Python）
│   ├── __init__.py
│   ├── timeline.py                 # Timeline / Clip / Transition
│   ├── renderer.py                 # ffmpeg 渲染
│   ├── media.py                    # 视频元数据、缩略图
│   ├── project.py                  # JSON 读写
│   ├── pipeline.py                 # 管道引擎
│   ├── reviewer.py                 # CHAI 质检
│   └── artifact.py                 # Artifact 数据模型
│
├── server/                         # 服务层
│   ├── __init__.py
│   ├── main.py                     # FastAPI + WebSocket 入口
│   ├── session.py                  # SessionManager + EditSession
│   ├── nlu.py                      # NLU 解析（LLM + 规则）
│   ├── nlu_fix.py                  # 中文数字解析辅助
│   ├── planner.py                  # AI 剪辑规划器
│   ├── skills_manager.py           # 技能系统
│   ├── analyzer.py                 # 视频场景分析
│   ├── reference_analyzer.py       # 参考视频风格分析
│   ├── audio.py                    # BGM 音频分析
│   ├── llm_config.py               # LLM 配置管理
│   ├── model_fetch.py              # 模型列表获取
│   └── schemas.py                  # Pydantic 模型
│
├── web/
│   └── index.html                  # 单页应用
│
├── skills/                         # 技能定义
│   ├── user-preferences.md
│   ├── rhythm-cut.md
│   ├── highlight-montage.md
│   └── comedy-montage.md
│
├── pipelines/                      # 渲染管道
│   └── game-highlight.yaml
│
├── config.yaml
├── requirements.txt
└── README.md
```

---

## 10. 版本历史

| 版本 | 内容 |
|------|------|
| 0.4.0 | 对话式自动规划（标记优先 + BGM 节拍对齐 + 一键全自动） |
| 0.3.0 | 声明式管道架构（Artifact + Reviewer + PipelineEngine + CHAI 质检） |
| 0.2.0 | P0-P2 完整实现（渲染/导出/持久化/xfade/参考视频/风格指纹） |
| 0.1.0 | MVP（视频加载 + NLU 对话 + 基础渲染） |

---

## 11. 风险与应对

| 风险 | 应对 |
|------|------|
| NLU 解析不准 | 规则引擎优先匹配常见模式；失败时 LLM 兜底 |
| ffmpeg 渲染慢 | 预览用低分辨率 proxy；最终渲染异步后台 |
| Ollama 离线 | 规则引擎 fallback；标记秒出方案不依赖 LLM |
| BGM 节拍检测不准 | 缓存分析结果；提供手动微调入口 |
