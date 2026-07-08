# 对话式视频剪辑工作台 — 设计文档

## 1. 产品定义

**一句话**：用文字对话替代手动拖拽时间轴，AI 执行剪辑操作，人做创意决策。

**与现有竞品的核心差异**：
- 不是"一条指令自动出成片"（CutAI 路线）
- 不是"传统时间轴上加 AI 按钮"（Adobe 路线）
- 是**人在回路中的对话式时间轴构建**——每步操作可见、可回退、可调整

---

## 2. 架构设计

```
┌────────────────────────────────────────────────────┐
│                   前端层 (Presentation)               │
│  ┌──────────┐  ┌──────────────┐  ┌──────────────┐ │
│  │ 视频预览   │  │ 时间轴可视化   │  │ 对话面板      │ │
│  │ (Player)  │  │ (只读色块)    │  │ (指令+历史)   │ │
│  └──────────┘  └──────────────┘  └──────────────┘ │
│                                                     │
│  通信: WebSocket（实时） + REST（素材管理）              │
└─────────────────────┬──────────────────────────────┘
                      │
┌─────────────────────▼──────────────────────────────┐
│                   服务层 (Application)                │
│  ┌──────────────────────────────────────────────┐  │
│  │              WebSocket Handler                │  │
│  │   消息路由 / 会话管理 / 进度推送                │  │
│  └──────────────────┬───────────────────────────┘  │
│                     │                                │
│  ┌──────────────────▼───────────────────────────┐  │
│  │              会话管理器 (SessionManager)        │  │
│  │   每个项目一个 session，持有 timeline + history │  │
│  └──────────────────┬───────────────────────────┘  │
│                     │                                │
│  ┌──────────────────▼───────────────────────────┐  │
│  │              指令解析器 (NLUParser)             │  │
│  │   自然语言 → 编辑操作 JSON                       │  │
│  │   依赖: Ollama (qwen3.6-27b)                   │  │
│  └──────────────────┬───────────────────────────┘  │
│                     │                                │
│  ┌──────────────────▼───────────────────────────┐  │
│  │              操作执行器 (ActionExecutor)         │  │
│  │   操作 JSON → 修改 timeline → 触发渲染           │  │
│  └──────────────────┬───────────────────────────┘  │
└─────────────────────┼──────────────────────────────┘
                      │
┌─────────────────────▼──────────────────────────────┐
│                   引擎层 (Engine)                      │
│                                                     │
│  ┌───────────────┐  ┌───────────────┐              │
│  │  Timeline     │  │  Renderer     │              │
│  │  · clips[]    │  │  · ffmpeg     │              │
│  │  · add/remove │  │  · concat     │              │
│  │  · reorder    │  │  · transitions│              │
│  │  · export JSON│  │  · speed ramp │              │
│  └───────────────┘  └───────────────┘              │
│                                                     │
│  ┌───────────────┐  ┌───────────────┐              │
│  │  MediaStore   │  │  ProjectIO    │              │
│  │  · 视频索引    │  │  · save/load  │              │
│  │  · 缩略图生成  │  │  · JSON 格式  │              │
│  └───────────────┘  └───────────────┘              │
│                                                     │
│  纯 Python 库，零服务依赖，可独立测试和复用             │
└─────────────────────────────────────────────────────┘
```

### 分层原则

| 层 | 职责 | 依赖方向 |
|---|---|---|
| 引擎层 | 纯逻辑：timeline 模型、ffmpeg 操作、JSON IO | 不依赖上层 |
| 服务层 | 编排：会话管理、NLP 解析、操作路由 | 依赖引擎层 |
| 前端层 | 展示：UI、WebSocket 通信 | 依赖服务层 API |

**关键约束**：引擎层可以被前端直接 import（桌面版），也可以被 FastAPI 调用（网页版），接口完全一致。

---

## 3. 引擎层核心模型（语言无关的数据结构）

### Timeline

```python
@dataclass
class Clip:
    id: str
    source_path: str          # 源视频路径
    source_start: float       # 源视频起始时间（秒）
    source_end: float         # 源视频结束时间（秒）
    speed: float = 1.0        # 变速倍率
    volume: float = 1.0       # 音量
    label: str = ""           # 用户标签

@dataclass
class Transition:
    id: str
    type: str                 # "cut" | "flash" | "dissolve" | "wipe"
    duration: float = 0.3
    params: dict = {}         # 过渡参数（颜色、方向等）

@dataclass
class TimelineItem:
    """时间轴上的一个单元，可以是 Clip 或 Transition"""
    item_type: str            # "clip" | "transition"
    data: Clip | Transition

@dataclass
class Timeline:
    items: list[TimelineItem]
    fps: float = 30.0
    total_duration: float = 0.0  # 计算属性
```

### 项目文件格式（Project JSON）

```json
{
  "version": "1.0",
  "meta": {
    "name": "我的剪辑",
    "created": "2026-07-08T10:00:00",
    "modified": "2026-07-08T11:30:00"
  },
  "sources": [
    {"id": "src1", "path": "/videos/game.mp4", "fps": 60, "duration": 3600.0}
  ],
  "timeline": {
    "items": [
      {"type": "clip", "source_id": "src1", "start": 32.0, "end": 35.5, "speed": 0.5},
      {"type": "transition", "effect": "flash", "duration": 0.3, "color": "#FFE4B5"},
      {"type": "clip", "source_id": "src1", "start": 75.0, "end": 78.2, "speed": 1.0}
    ]
  },
  "history": []
}
```

### 操作定义（引擎层 API）

```python
class TimelineEngine:
    def add_clip(self, source_id, start, end, **kwargs) -> str  # 返回 clip_id
    def remove_item(self, item_id)
    def reorder(self, item_id, new_index)
    def update_clip(self, clip_id, **kwargs)  # 改 speed/volume/label
    def add_transition(self, after_item_id, type, duration, **params)
    def snapshot(self) -> dict  # 导出可序列化的状态
    def render_preview(self, output_path) -> str  # 低分辨率预览
    def render_final(self, output_path) -> str   # 完整渲染
    def undo(self)
    def redo(self)
```

---

## 4. 初版定稿（MVP v1.0）

### 范围

| 包含 | 不含（后续版本） |
|------|-----------------|
| 加载单个视频 | 多源素材管理 |
| 对话式剪辑操作 | AI 自动分析画面/语义搜索 |
| cut / splice / speed / flash | 复杂转场 / 字幕 / BGM / 调色 |
| 时间轴可视化（只读色块） | 可拖拽时间轴 |
| WebSocket 实时交互 | 多人协作 |
| 项目 save/load JSON | 导出视频格式选择 |
| 操作 undo/redo | 操作历史分支 |

### 支持的自然语言指令

```
✅ "从 0:32 到 0:35 提取出来"
✅ "把这后面接上 1:15 到 1:20"
✅ "中间加个 0.3 秒闪白"
✅ "这段放慢到 0.5 倍"
✅ "删掉最后一个片段"
✅ "把第 2 段和第 3 段换位置"
✅ "渲染预览"
✅ "撤销"
✅ "保存项目"
```

### 对话流程示例

```
用户: 加载 game.mp4
AI:   已加载 game.mp4（60fps，60分钟）

用户: 从 0:32 到 0:35 提取
AI:   ✅ 已添加片段 #1（0:32 → 0:35，3.0s）
     [时间轴显示色块 #1]

用户: 这段放慢 0.5 倍
AI:   ✅ 片段 #1 速度改为 0.5x（6.0s）

用户: 再接上 1:15 到 1:20
AI:   ✅ 已添加片段 #2（1:15 → 1:20，5.0s）
     [时间轴追加色块 #2]

用户: 渲染预览
AI:   🔄 渲染中...
     📼 预览视频已就绪（11.0s）
```

### 前端 UI 布局

```
┌──────────────────────────────────────────────┐
│  Logo    对话式剪辑工作台         [保存] [导出]  │
├──────────────┬───────────────────┬───────────┤
│              │                   │           │
│   视频预览    │   时间轴可视化      │  素材列表  │
│              │                   │           │
│  [▶️ 播放]   │  ██ ▓▓ ████ ▓▓   │  game.mp4 │
│              │  #1  t  #2  t     │           │
│              │                   │           │
│              │                   │           │
├──────────────┴───────────────────┴───────────┤
│  💬 输入指令...                        [发送]  │
│                                              │
│  历史:                                       │
│  > 从 0:32 到 0:35 提取                      │
│  ✅ 已添加片段 #1（3.0s）                     │
│  > 这段放慢 0.5 倍                            │
│  ✅ 片段 #1 速度 0.5x                        │
└──────────────────────────────────────────────┘
```

### 技术选型 MVP

| 组件 | 选型 | 理由 |
|------|------|------|
| 后端框架 | FastAPI + uvicorn | 异步 + WebSocket 原生支持 |
| 前端 | 单 HTML + 原生 JS + 少量 CSS | 零构建，直接 serve |
| 通信 | WebSocket | 实时双向，操作→预览延迟最低 |
| 视频播放 | `<video>` 标签 | 浏览器原生，H.264 |
| 时间轴 | Canvas 2D | 色块绘制，无需 SVG 库 |
| 渲染 | ffmpeg subprocess | 稳定可靠，concat demuxer |
| NLU | Ollama qwen3.6-27b | 本地已有，中文理解力够 |
| 存储 | JSON 文件 | 项目同级目录，Git 友好 |

---

## 5. 目录结构

```
conversational-editor/
├── engine/                    # 引擎层（纯 Python，零依赖）
│   ├── __init__.py
│   ├── timeline.py           # Timeline / Clip / Transition 模型
│   ├── renderer.py           # ffmpeg 渲染封装
│   ├── media.py              # 视频元数据提取、缩略图
│   └── project.py            # JSON 读写
│
├── server/                    # 服务层
│   ├── __init__.py
│   ├── main.py               # FastAPI + WebSocket 入口
│   ├── session.py            # SessionManager（多项目管理）
│   ├── nlu.py                # 指令解析（Ollama prompt）
│   ├── executor.py           # 操作执行 + undo/redo 栈
│   └── schemas.py            # Pydantic 请求/响应模型
│
├── web/                       # 前端层
│   ├── index.html            # 单页应用
│   ├── css/
│   │   └── style.css
│   └── js/
│       ├── app.js            # 主入口
│       ├── player.js         # 视频播放控制
│       ├── timeline.js       # Canvas 时间轴渲染
│       ├── chat.js           # 对话面板 + WebSocket
│       └── state.js          # 前端状态管理（镜像 Timeline）
│
├── config.yaml               # 服务器配置（Ollama IP、端口等）
├── requirements.txt
└── README.md
```

---

## 6. 后续优化路线

### v1.1 — 编辑能力增强
- 字幕：Whisper 识别 → 时间轴编辑 → 烧录
- BGM：选择/截取/音量混合
- 更多转场：dissolve、wipe、slide
- 片段复制/分割
- 导出格式选择（mp4/gif）

### v1.2 — 智能辅助
- AI 画面分析：选中帧 → minicpm-v 描述 → 辅助定位素材
- 语义搜索：「找到有击杀的片段」
- 自动节拍检测：BGM 配切点
- 片段缩略图预览（hover 时间轴）

### v1.3 — 工作流增强
- 多源素材管理（加载多个视频）
- 操作历史可视化分支
- 快捷键绑定（手动操作 escape hatch）
- 渲染队列（后台批量）

### v2.0 — 桌面版（Tauri 封装）
- 引擎层直接复用（Python 通过 sidecar 调用）
- 前端复用，Tauri WebView 加载
- 原生文件对话框、系统通知
- 本地 GPU 加速渲染

### v2.x — 社区方向
- EDITSTYLE.md（可分享的剪辑风格模板）
- MCP Server（让其他 AI Agent 调剪辑能力）
- 插件系统（自定义转场/效果）

---

## 7. 风险与应对

| 风险 | 应对 |
|------|------|
| NLU 解析不准 | 指令失败时展示解析结果让用户确认/修正；提供快捷按钮 fallback |
| ffmpeg 渲染慢 | 预览用低分辨率（480p）proxy；最终渲染异步后台 |
| 浏览器视频解码限制 | 素材自动转码为 H.264 baseline；大文件分段加载 |
| Ollama 离线 | 规则引擎 fallback（关键词匹配基础指令） |
| 竞品加速追赶 | 聚焦「精确剪辑级对话」差异化，不在自动成片赛道竞争 |

---

**初版预计工时**：3-5 天（引擎 1 天 + 服务层 1 天 + 前端 2 天 + 联调 1 天）
