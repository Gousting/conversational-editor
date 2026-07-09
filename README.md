# 对话式视频剪辑工作台 🎬

> 人在回路中。AI 做执行层苦力，人做创意决策。

[![Version](https://img.shields.io/badge/version-0.4.0-blue)](VERSION)
[![Python](https://img.shields.io/badge/python-3.10%2B-green)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-yellow)](LICENSE)

---

## 核心理念

不是"一句话自动出片"的黑盒——那不可控。而是在预览台**打标记**定位关键点，然后**用自然语言告诉 AI 你要什么**。半自动和全自动的区别就是**你说几句**：

```
你: "这是永劫无间击杀集锦"
AI:  好的！我看到 47 个镜头。想要什么风格？
     [热血燃向] [高光混剪] [搞笑娱乐] [文艺慢剪]

你: "热血燃向"
AI:  收到！想突出什么效果？
     [闪白转场] [慢动作特写] [淡入淡出] [直接硬切]

你: "闪白转场"
AI:  最后，整体节奏怎么走？
     [快节奏冲击] [慢→快推进] [快慢交替]

你: "快节奏冲击"
AI:  ✅ 已按方案生成 15 个片段，总时长 ~52 秒
     → 自动渲染预览中...
```

或者一步到位：

```
你: "帮我自动规划实现"
AI:  ✅ 风格→选片→生成→渲染，全部搞定。预览：[下载链接]
```

---

## 功能清单

### 核心工作流
- 🎥 **视频加载** — 本地文件拖入或路径输入
- 🏷️ **手动打标** — 预览台 Shift+点击或快捷键标记关键时间点
- 💬 **对话编辑** — NLU 解析自然语言指令（添加/删除/调速/过渡/撤销/重做）
- 🤖 **对话式自动规划** — 回答 3 个问题 → AI 生成完整剪辑方案 → 一键执行
- ⚡ **一键全自动** — 说"帮我自动规划实现"直接走完：选片→生成→渲染
- 👁️ **实时预览** — 时间轴只读色块 + 播放头同步

### 智能规划
- 🧠 **标记优先** — 有标记直接秒出方案（不调 LLM），无标记才走 LLM 分析
- 🎵 **BGM 节拍对齐** — 加载 BGM 后切点自动吸附到最近强拍/Drop 段
- ⚡ **能量感知转场** — Drop 段硬切不打断节奏，低谷段淡入淡出
- 📊 **VLM 辅助分析** — 可选视觉模型分析视频场景/运动强度

### 渲染 & 导出
- 🎬 **管道渲染** — YAML 驱动的多阶段生产流水线（验证→音频→字幕→合成→质检）
- 📊 **阶段进度** — 每阶段独立状态 + Finding 质检反馈
- ⏹ **取消渲染** — 随时中止
- 📤 **多平台导出** — 抖音 9:16 / B站 16:9 / 方形 1:1 / 原比例

### 管道系统 (v0.3)
- 🔧 **声明式管道** — YAML 定义阶段、输入输出、质检标准
- ✅ **结构化质检** — 遵循 CHAI 规则（Accurate/Complete/Constructive）的 Finding 体系
- 📦 **Artifact 链** — 阶段间通过规范化 Artifact 传递数据
- 💾 **Checkpoint** — 关键节点存档，支持断点续跑

### 高级特性
- 🌈 **xfade 真转场** — filter_complex 实现，flash→fadewhite, dissolve→fade
- 🎨 **参考视频** — 加载参考视频，VLM 分析剪辑风格
- 🖐️ **风格指纹** — 时间轴顶部 L/M/S 三色条可视化节奏模式
- 🔄 **风格复刻** — 自动将参考视频的节奏应用到源素材
- 🎼 **BGM 分析** — 节拍检测、Drop 段/低谷段识别、波形可视化

### 项目 & 技能
- 💾 **自动持久化** — 每次操作后自动保存，刷新不丢失
- 📋 **项目加载** — 列出已有项目，点击恢复
- 🧠 **技能系统** — 用户偏好/剪辑规则/风格模板，可插拔 Markdown 定义

---

## 快速开始

### 环境要求

- Python 3.10+
- ffmpeg（PATH 中可用）
- （可选）Ollama — 用于 NLU 自然语言理解，离线时自动降级为规则引擎

### 安装

```bash
git clone https://github.com/Gousting/conversational-editor.git
cd conversational-editor
pip install -r requirements.txt
```

### 启动

```bash
python -m uvicorn server.main:app --host 0.0.0.0 --port 8765
```

打开浏览器访问 `http://localhost:8765`

### 配置

编辑 `config.yaml`：

```yaml
server:
  host: "0.0.0.0"
  port: 8765

ollama:
  url: "http://192.168.0.104:11434"    # Ollama 地址，Windows 宿主机 IP
  model: "qwen3.5:9b"
  fallback_enabled: true                # 离线时用规则引擎兜底

render:
  output_dir: "/tmp/conversational-editor"
  preview:
    width: 854
    height: 480
    crf: 32
  final:
    crf: 23
```

---

## 架构

```
                    ┌─ pipelines/game-highlight.yaml ─┐
                    │  validate → audio → subtitle     │
                    │  → compose → quality_check       │
                    └────────────┬─────────────────────┘
                                 │
前端 (index.html)  ←WebSocket→  FastAPI 服务层  ←ffmpeg→  渲染引擎
    │                                │
    ├ 视频预览 (HTML Video)          ├ NLU 解析 (LLM + 规则)
    ├ 时间轴 (Canvas 只读)           ├ EditPlanner (标记优先)
    ├ 对话面板                       ├ Session 管理 + 持久化
    ├ 管道进度 UI                    ├ 技能系统 (SkillManager)
    └ LLM 配置面板                   ├ 管道引擎 (PipelineEngine)
                                     ├ 质检引擎 (Reviewer)
                                     ├ BGM 分析器 (AudioAnalyzer)
                                     └ Artifact 链
```

详见 [DESIGN.md](DESIGN.md)

---

## 技术栈

| 层 | 技术 |
|---|------|
| 后端 | FastAPI + WebSocket |
| 渲染 | ffmpeg (concat demuxer + filter_complex xfade) |
| 管道 | YAML 驱动 + Artifact 链 + CHAI 质检协议 |
| NLU | Ollama / OpenAI / 规则引擎兜底 |
| 前端 | 纯 HTML/CSS/JS，零框架 |
| 持久化 | JSON 文件存储 (`/tmp/conversational-editor/sessions/`) |

---

## 技能系统

技能是 Markdown 文件，存放在 `skills/` 目录下。每个技能定义触发词、规则和上下文，NLU 解析时会自动匹配。

当前内置技能：
- `user-preferences.md` — 用户偏好（闪白暖色、片段 ≥3s、禁止纯黑结尾等）
- `rhythm-cut.md` — 节奏剪辑规则
- `highlight-montage.md` — 高光混剪模板
- `comedy-montage.md` — 搞笑混剪模板

---

## 管道系统

管道是 YAML 定义的声明式生产流水线。渲染分阶段执行，每阶段产出可检查的结构化 Artifact。

### 管道定义

`pipelines/game-highlight.yaml`：

```yaml
stages:
  - name: validate         # 验证时间轴合法性
    produces: validation_report
    review_focus:
      - 每个片段时长 ≥ 1.5 秒
      - 过渡参数合法
    success_criteria:
      - clip_count ≥ 1

  - name: audio_prep       # 音频准备
    requires: validation_report
    produces: audio_manifest
    review_focus:
      - BGM 与对白音量比 1:4 ~ 1:3

  - name: subtitle         # 字幕生成
    requires: [validation_report, audio_manifest]
    produces: subtitle_manifest

  - name: compose          # 视频合成
    requires: [validation_report, audio_manifest, subtitle_manifest]
    produces: composition_report

  - name: quality_check    # 质量检查
    requires: composition_report
    produces: qc_report
    success_criteria:
      - black_frames = 0
      - audio_present = true
```

### Artifact 类型

| Artifact | 阶段 | 关键字段 |
|----------|------|---------|
| `ValidationReport` | validate | clip_count, total_duration, passed |
| `AudioManifest` | audio_prep | tracks, bgm_volume_db, peak_level |
| `SubtitleManifest` | subtitle | subtitles, style, srt_path |
| `CompositionReport` | compose | output_path, file_size_bytes, xfade_transitions |
| `QCReport` | quality_check | black_frames, audio_present, passed |

### 质检协议

遵循 CHAI 规则（借鉴 OpenMontage）：

- **Accurate** — 每条 Finding 指向具体字段/帧号
- **Complete** — 发现一个问题后扫描同类全貌
- **Constructive** — critical 必须附带修复方案

### 自定义阶段

```python
from engine.pipeline import PipelineEngine, STAGE_HANDLERS

def my_handler(stage_config, session, inputs, checkpoint_dir):
    return MyArtifact(...)

STAGE_HANDLERS["my_stage"] = my_handler
```

---

## 版本

当前版本：**0.4.0**

- 0.4.0 — 对话式自动规划（标记优先 + BGM 节拍对齐 + 一键全自动）
- 0.3.0 — 声明式管道架构（Artifact + Reviewer + PipelineEngine + CHAI 质检）
- 0.2.0 — P0-P2 完整实现（渲染/导出/会话持久化/xfade/参考视频/风格指纹）
- 0.1.0 — MVP（视频加载 + NLU 对话 + 基础渲染）

---

## 路线图

### P3 — 音频与字幕

- [ ] **P3-1 BGM 音轨** — 添加背景音乐轨道，支持音量控制、淡入淡出
- [ ] **P3-2 音频替换** — 片段静音 + 替换音频源文件
- [ ] **P3-3 字幕轨道** — 时间轴第二层字幕轨，支持 SRT 导入导出
- [ ] **P3-4 字幕样式** — 字体/颜色/大小/位置/描边可配置
- [ ] **P3-5 字幕对话指令** — NLU 支持"在 X 秒处加字幕'xxx'"

### P4 — 时间轴交互

- [ ] **P4-1 拖拽排序** — 时间轴上拖拽调整片段顺序
- [ ] **P4-2 多选操作** — Shift/Ctrl 多选片段，批量删除/移动
- [ ] **P4-3 片段裁剪** — 拖拽片段边缘调整入点/出点
- [ ] **P4-4 片段拆分** — 播放头位置一键拆分片段
- [ ] **P4-5 缩略图波纹** — 片段色块上叠加视频缩略图

### P5 — 效果系统

- [ ] **P5-1 缩放/位移** — Ken Burns 效果（scale + pan）
- [ ] **P5-2 调色预设** — LUT 滤镜（电影/复古/清新等）
- [ ] **P5-3 文字叠加** — 画面上叠加标题/水印/标签
- [ ] **P5-4 画中画** — 多画面同屏（游戏击杀回放小窗）
- [ ] **P5-5 冻结帧** — 指定位置定格画面 + 慢放

### P6 — 工程化

- [ ] **P6-1 视频导入优化** — 大文件流式处理，避免内存爆炸
- [ ] **P6-2 多源素材** — 支持加载多个视频文件到同一个 session
- [ ] **P6-3 项目导出/导入** — 打包 project.json + 素材为 .zip 迁移
- [ ] **P6-4 Docker 部署** — Dockerfile + docker-compose
- [ ] **P6-5 端到端测试** — 完整的 WebSocket 集成测试套件
- [ ] **P6-6 GPU 加速渲染** — NVENC/VAAPI 硬件编码加速

### P7 — 智能辅助

- [ ] **P7-1 自动 B-Roll** — 根据对话文本自动搜索匹配的空镜素材
- [ ] **P7-2 节奏分析** — 自动检测源视频节奏（镜头切换频率/运动强度）
- [ ] **P7-3 情感曲线** — 根据音频响度/画面亮度生成情绪强度曲线
- [ ] **P7-4 智能粗剪** — 一句话描述 → AI 自动生成初始时间轴
- [ ] **P7-5 一键风格迁移** — 加载参考视频 → 完整复刻剪辑风格到新素材

---

## License

MIT
