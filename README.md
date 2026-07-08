# 对话式视频剪辑工作台 🎬

> 用文字对话替代手动拖拽时间轴。AI 执行剪辑操作，人做创意决策。

[![Version](https://img.shields.io/badge/version-0.2.0-blue)](VERSION)
[![Python](https://img.shields.io/badge/python-3.10%2B-green)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-yellow)](LICENSE)

---

## 这是什么

一个**人在回路中**的对话式视频剪辑工具。你不是给 AI 一条指令让它自动出片（那不可控），而是在预览台播放视频、打标记、然后用自然语言告诉 AI 你想要的剪辑操作——每一步可见、可回退、可调整。

```
你: "从 5 秒到 10 秒提取出来"
AI:  ✅ 已添加片段 #1 (5.0s → 10.0s, 5.0s)

你: "第一段放慢到 0.5 倍速"
AI:  ✅ 片段 #1 速度已改为 0.5x (输出 10.0s)

你: "接上 20 到 25 秒那段"
AI:  ✅ 已添加片段 #2 (20.0s → 25.0s, 5.0s)

你: "两段中间加个闪白 0.3 秒"
AI:  ✅ 已插入 flash 过渡 (0.3s, #FFE4B5 暖色柔光)
```

---

## 功能清单

### 核心工作流
- 🎥 **视频加载** — 本地文件拖入或路径输入
- 🏷️ **手动打标** — 预览台 Shift+点击时间轴标记关键点
- 💬 **对话编辑** — NLU 解析自然语言指令（添加、删除、调速、过渡、撤销/重做）
- 👁️ **实时预览** — 时间轴只读色块 + 播放头同步

### 渲染 & 导出
- 🎬 **一键渲染** — 直接渲染按钮，不经过 NLU
- 📊 **进度反馈** — ffmpeg `-progress` 实时轮询进度条
- ⏹ **取消渲染** — 随时中止
- 📤 **多平台导出** — 抖音 9:16 / B站 16:9 / 方形 1:1 / 原比例

### 高级特性
- 🌈 **xfade 真转场** — filter_complex 实现，支持 flash→fadewhite, dissolve→fade
- 🎨 **参考视频** — 加载参考视频，VLM 分析剪辑风格
- 🖐️ **风格指纹** — 时间轴顶部 L/M/S 三色条可视化节奏模式
- 🔄 **风格复刻** — 自动将参考视频的节奏应用到源素材

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
前端 (index.html)  ←WebSocket→  FastAPI 服务层  ←ffmpeg→  渲染引擎
    │                                │
    ├ 视频预览 (HTML Video)          ├ NLU 解析 (LLM + 规则)
    ├ 时间轴 (Canvas 只读)           ├ Session 管理
    ├ 对话面板                       ├ 技能系统
    └ LLM 配置面板                   └ 参考视频分析 (VLM)
```

详见 [DESIGN.md](DESIGN.md)

---

## 技术栈

| 层 | 技术 |
|---|------|
| 后端 | FastAPI + WebSocket |
| 渲染 | ffmpeg (concat demuxer + filter_complex xfade) |
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

## 版本

当前版本：**0.2.0**

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
