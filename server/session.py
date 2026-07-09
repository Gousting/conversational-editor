"""会话管理器 — 每个编辑项目一个 session"""

import uuid
from pathlib import Path
from engine import Timeline, UndoManager, MediaStore, ProjectIO, Renderer
from .analyzer import VideoAnalyzer, VideoAnalysis
from .planner import EditPlanner, EditPlan, ClipProposal


class EditSession:
    def __init__(self, session_id: str, media_dir: str = "/tmp/conversational-editor"):
        self.id = session_id
        self.timeline = Timeline()
        self.undo_manager = UndoManager()
        self.media_store = MediaStore(f"{media_dir}/media")
        self.renderer = Renderer(f"{media_dir}")
        self.project_io = ProjectIO(f"{media_dir}/project_{session_id}.json")
        self.project_name = "未命名"
        self.video_loaded: bool = False
        self.current_source_id: str = ""
        self.source_path: str = ""
        self.analyzer = VideoAnalyzer()
        self.planner = EditPlanner()
        self.analysis: VideoAnalysis | None = None
        self.current_plan: EditPlan | None = None
        self.markers: list[dict] = []
        self.compose_state: dict = {}  # 对话式初稿状态机 {stage, style, effect, pace, ...}
        self.preview_path: str = ""  # 最新渲染产物路径
        self.render_state: str = "idle"  # idle | rendering | done | error
        self.render_progress: float = 0.0
        self.render_cancelled: bool = False
        self.bgm_path: str = ""  # 背景音乐文件路径
        self.bgm_analysis = None  # BgmAnalysis 对象
        self.emotion_map: dict[int, str] = {}  # 片段索引 → 情绪标签 (dialogue/action/calm/highlight)

    def load_video(self, filepath: str) -> dict:
        info = self.media_store.probe(filepath)
        self.video_loaded = True
        self.current_source_id = info.id
        self.source_path = info.path
        result = info.to_dict()

        # 自动分析
        try:
            self.analysis = self.analyzer.analyze(filepath)
            result["analysis"] = {
                "scenes_count": len(self.analysis.scenes),
                "highlights_count": len(self.analysis.highlights),
                "scene_summary": self.analyzer.summary(self.analysis),
                "keyframes": [s.keyframe_path for s in self.analysis.scenes[:10]],
            }
        except Exception:
            result["analysis"] = None

        return result

    def load_bgm(self, filepath: str) -> dict:
        """加载背景音乐，分析节拍和能量结构"""
        from .audio import AudioAnalyzer, save_analysis, load_analysis
        analyzer = AudioAnalyzer()
        
        # 先用缓存
        self.bgm_analysis = load_analysis(filepath)
        if self.bgm_analysis:
            self.bgm_path = filepath
            return self._bgm_result()

        # 分析
        self.bgm_analysis = analyzer.analyze(filepath)
        self.bgm_path = filepath
        save_analysis(self.bgm_analysis)
        return self._bgm_result()

    def _bgm_result(self) -> dict:
        """生成 BGM 分析摘要"""
        a = self.bgm_analysis
        if not a:
            return {"success": False, "message": "未加载 BGM"}
        return {
            "success": True,
            "duration": a.duration,
            "beat_count": len(a.beats),
            "drop_count": len(a.drop_sections),
            "valley_count": len(a.valley_sections),
            "drop_sections": a.drop_sections,
            "valley_sections": a.valley_sections,
            "beats": [{"time": b.time, "strength": b.strength, "is_drop": b.is_drop}
                      for b in a.beats[:50]],  # 只返回前 50 个防止过大
        }

    def propose_plan(self, user_intent: str) -> dict:
        """AI 生成剪辑方案 — 有标记时秒出，无标记时调 LLM"""
        # 如果有用户标记，直接基于标记生成方案（秒级响应）
        if self.markers:
            return self._quick_plan_from_markers(user_intent)

        # 无标记时走 LLM
        if not self.analysis:
            return {"success": False, "message": "请先加载视频或在预览台打标记"}

        summary = self.analyzer.summary(self.analysis)
        plan = self.planner.propose_plan(summary, user_intent, self.analysis.duration)
        self.current_plan = plan

        return {
            "success": True,
            "plan": {
                "title": plan.title,
                "vibe": plan.vibe,
                "target_duration": plan.target_duration,
                "structure": plan.structure,
                "clips": [
                    {"start": c.start, "end": c.end, "label": c.label,
                     "speed": c.speed, "transition_after": c.transition_after}
                    for c in plan.clips
                ],
                "reasoning": plan.reasoning,
            },
            "display": self.planner.format_plan_display(plan),
        }

    def _quick_plan_from_markers(self, intent: str) -> dict:
        """基于标记秒出方案 — 如果 BGM 已加载，切点自动对齐到节拍"""
        markers = sorted(self.markers, key=lambda m: m.get("start", m.get("time", 0)))
        
        # 确定风格：从 compose_state 或 intent 中提取
        vibe = self.compose_state.get("style", "")
        if not vibe:
            vibe_map = {
                "高光": "热血快节奏", "集锦": "高光混剪", "精彩": "高光混剪",
                "搞笑": "搞笑娱乐", "燃": "热血燃向", "热血": "热血燃向",
                "文艺": "文艺慢剪", "治愈": "文艺慢剪",
                "快剪": "快节奏卡点", "慢放": "慢放精彩",
                "混剪": "混剪", "卡点": "快节奏卡点",
            }
            vibe = next((v for k, v in vibe_map.items() if k in intent), "混剪")

        user_effect = self.compose_state.get("effect", "")
        user_pace = self.compose_state.get("pace", intent)

        # BGM 已加载 → 切点对齐到最近节拍
        if self.bgm_analysis and self.bgm_analysis.beats:
            clips = self._clips_aligned_to_beats(markers, user_pace, user_effect, vibe)
        else:
            clips = self._clips_freeform(markers, user_pace, user_effect, vibe)

        plan = EditPlan(
            title=intent,
            target_duration=f"约{len(clips)*3}秒",
            vibe=vibe,
            structure=[f"片段{i+1}" for i in range(len(clips))],
            clips=[ClipProposal(**c) for c in clips],
            reasoning=f"基于 {len(markers)} 个标记点{' + BGM节拍对齐' if self.bgm_analysis else ''}自动生成",
        )
        self.current_plan = plan
        return {
            "success": True,
            "plan": {
                "title": plan.title, "vibe": plan.vibe,
                "target_duration": plan.target_duration,
                "structure": plan.structure, "clips": clips,
                "reasoning": plan.reasoning,
            },
            "display": self.planner.format_plan_display(plan),
        }

    def _clips_aligned_to_beats(self, markers, pace, effect, vibe) -> list[dict]:
        """BGM 节拍对齐：每个标记切点吸附到最近的节拍/Drop 点"""
        bgm = self.bgm_analysis
        clips = []
        for i, m in enumerate(markers):
            label = m.get("label") or f"片段{i+1}"
            if "start" in m and "end" in m:
                start, end = m["start"], m["end"]
            elif "time" in m:
                start, end = max(0, m["time"] - 1.0), m["time"] + 2.0
            else:
                continue

            # 吸附到最近强拍
            nearest = bgm.nearest_beat(start)
            if nearest:
                offset = nearest.time - start
                if abs(offset) < 0.3:  # 0.3s 以内吸附
                    start += offset
                    end += offset
            # 如果终点落在 Drop 段，延长到 Drop 结束
            for d in bgm.drop_sections:
                if d["start"] <= end <= d["end"] and (d["end"] - end) < 1.0:
                    end = d["end"]
                    break

            speed = 0.5 if ("慢放" in pace or "慢" in pace) else (1.2 if ("快" in pace or "燃" in pace) else 1.0)
            trans = self._transition_for_energy(start, end, effect, vibe)
            clips.append({"start": start, "end": end, "label": label, "speed": speed, "transition_after": trans})
        return clips

    def _clips_freeform(self, markers, pace, effect, vibe) -> list[dict]:
        """无 BGM 时自由切点"""
        clips = []
        for i, m in enumerate(markers):
            label = m.get("label") or f"片段{i+1}"
            if "start" in m and "end" in m:
                start, end = m["start"], m["end"]
            elif "time" in m:
                start, end = max(0, m["time"] - 1.0), m["time"] + 2.0
            else:
                continue
            speed = 0.5 if ("慢放" in pace or "慢" in pace) else (1.2 if ("快" in pace or "燃" in pace) else 1.0)
            trans = ""
            if "闪白" in effect or "燃" in vibe or "高光" in vibe:
                trans = "flash"
            elif "淡入" in effect or "文艺" in vibe:
                trans = "dissolve"
            clips.append({"start": start, "end": end, "label": label, "speed": speed, "transition_after": trans})
        return clips

    def _transition_for_energy(self, start: float, end: float, effect: str, vibe: str) -> str:
        """基于 BGM 能量段决定转场类型"""
        bgm = self.bgm_analysis
        if not bgm:
            return ""
        # 检查片段中点落在哪个能量段
        mid = (start + end) / 2
        for d in bgm.drop_sections:
            if d["start"] <= mid <= d["end"]:
                return "cut"  # Drop 段硬切不打断节奏
        for v in bgm.valley_sections:
            if v["start"] <= mid <= v["end"]:
                return "dissolve"  # 低谷段淡入淡出
        return "flash" if ("燃" in vibe or "高光" in vibe) else ""

    def execute_plan(self) -> dict:
        """一键执行当前方案"""
        if not self.current_plan:
            return {"success": False, "message": "没有方案可执行"}

        added = []
        prev_id = None
        for c in self.current_plan.clips:
            clip = self.timeline.add_clip(
                source_id=self.current_source_id,
                start=c.start, end=c.end,
                speed=c.speed, label=c.label,
                after_item_id=prev_id,
            )
            added.append(clip.to_dict())
            prev_id = clip.id

            if c.transition_after and c.transition_after in ("flash", "dissolve"):
                self.timeline.add_transition(
                    after_item_id=clip.id,
                    effect=c.transition_after,
                    duration=0.3,
                )

        return {
            "success": True,
            "message": f"✅ 已按方案添加 {len(added)} 个片段",
            "clips": added,
            "timeline": self.timeline.to_list(),
            "total_duration": self.timeline.total_duration,
            "clip_count": self.timeline.clip_count,
        }

    def execute_action(self, action: dict) -> dict:
        """执行一个编辑操作，返回结果"""
        act = action.get("action")
        result = {"success": True, "action": act}

        try:
            if act == "add_clip":
                self.undo_manager.snapshot(self.timeline)
                clip = self.timeline.add_clip(
                    source_id=self.current_source_id,
                    start=action["start"],
                    end=action["end"],
                    speed=action.get("speed", 1.0),
                    label=action.get("label", ""),
                )
                # 如果指定位置为 last，把 clip 挪到最后
                if action.get("position") == "last":
                    self.timeline.reorder(
                        clip.id,
                        len(self.timeline.items) - 1,
                    )

                result["clip"] = clip.to_dict()
                result["clip"]["output_duration"] = clip.output_duration
                result["message"] = (
                    f"✅ 已添加片段 {clip.id} "
                    f"({clip.source_start:.1f}s → {clip.source_end:.1f}s, "
                    f"{clip.output_duration:.1f}s)"
                )
                if clip.speed != 1.0:
                    result["message"] += f" @ {clip.speed}x"

            elif act == "remove":
                self.undo_manager.snapshot(self.timeline)
                if "position" in action:
                    if action["position"] == "last":
                        if self.timeline.items:
                            last = self.timeline.items[-1]
                            self.timeline.remove_item(last.data.id)
                            result["message"] = f"✅ 已删除最后一个片段"
                        else:
                            result["success"] = False
                            result["message"] = "时间轴为空"
                            return result
                elif "index" in action:
                    idx = action["index"]
                    if 0 <= idx < len(self.timeline.items):
                        item = self.timeline.items[idx]
                        self.timeline.remove_item(item.data.id)
                        result["message"] = f"✅ 已删除第 {idx+1} 个片段"
                    else:
                        result["success"] = False
                        result["message"] = f"索引 {idx} 超出范围"
                        return result
                elif "item_id" in action:
                    ok = self.timeline.remove_item(action["item_id"])
                    if not ok:
                        result["success"] = False
                        result["message"] = f"找不到片段 {action['item_id']}"
                        return result
                    result["message"] = f"✅ 已删除片段 {action['item_id']}"
                else:
                    result["success"] = False
                    result["message"] = "删除操作缺少参数"
                    return result

            elif act == "update_clip":
                self.undo_manager.snapshot(self.timeline)
                if "index" in action:
                    idx = action["index"]
                    if 0 <= idx < len(self.timeline.items):
                        item = self.timeline.items[idx]
                        item_id = item.data.id
                    else:
                        result["success"] = False
                        result["message"] = f"索引 {idx} 超出范围"
                        return result
                else:
                    item_id = action.get("item_id", "")

                props = {k: v for k, v in action.items()
                        if k in ("speed", "volume", "label")}
                ok = self.timeline.update_clip(item_id, **props)
                if ok:
                    result["message"] = f"✅ 已更新片段 {item_id}: {props}"
                else:
                    result["success"] = False
                    result["message"] = f"找不到片段 {item_id}"

            elif act == "add_transition":
                self.undo_manager.snapshot(self.timeline)
                if not self.timeline.items:
                    result["success"] = False
                    result["message"] = "时间轴为空，无法添加过渡"
                    return result

                # 默认在最后一个 clip 后面
                last_clip = None
                for item in reversed(self.timeline.items):
                    if item.item_type == "clip":
                        last_clip = item.data
                        break

                trans = self.timeline.add_transition(
                    after_item_id=last_clip.id if last_clip else self.timeline.items[0].data.id,
                    effect=action.get("effect", "flash"),
                    duration=action.get("duration", 0.3),
                    **{k: v for k, v in action.items()
                       if k not in ("action", "effect", "duration", "after_item_id")},
                )
                if trans:
                    result["transition"] = trans.to_dict()
                    result["message"] = f"✅ 已添加 {trans.effect} 过渡 ({trans.duration}s)"
                else:
                    result["success"] = False
                    result["message"] = "添加过渡失败"

            elif act == "render":
                try:
                    sources = {self.current_source_id: self.source_path}
                    path = self.renderer.render_preview(self.timeline, sources)
                    self.preview_path = path
                    result["preview_path"] = f"/api/render-output/{self.id}"
                    result["message"] = f"📼 预览已就绪"
                except Exception as e:
                    result["success"] = False
                    result["message"] = f"渲染失败: {e}"

            elif act == "render_final":
                try:
                    sources = {self.current_source_id: self.source_path}
                    out = str(Path(self.renderer.media_dir) / "final_output.mp4")
                    path = self.renderer.render_final(self.timeline, sources, out)
                    result["final_path"] = path
                    result["message"] = f"📼 最终渲染完成: {path}"
                except Exception as e:
                    result["success"] = False
                    result["message"] = f"渲染失败: {e}"

            elif act == "undo":
                prev = self.undo_manager.undo(self.timeline)
                if prev:
                    self.timeline = prev
                    result["message"] = "↩ 已撤销"
                else:
                    result["success"] = False
                    result["message"] = "没有可撤销的操作"

            elif act == "redo":
                nxt = self.undo_manager.redo(self.timeline)
                if nxt:
                    self.timeline = nxt
                    result["message"] = "↪ 已重做"
                else:
                    result["success"] = False
                    result["message"] = "没有可重做的操作"

            elif act == "save_project":
                path = self.project_io.save(
                    self.timeline, self.undo_manager, self.project_name)
                result["message"] = f"💾 已保存: {path}"

            elif act == "query":
                result["timeline"] = self.timeline.to_list()
                result["total_duration"] = self.timeline.total_duration
                result["clip_count"] = self.timeline.clip_count

            elif act == "unknown":
                result["success"] = False
                result["message"] = action.get("reason", "无法理解指令")

            elif act == "auto_compose":
                # 自动生成初稿：启动对话式引导
                if not self.markers:
                    result["success"] = False
                    result["message"] = "请先在预览台打标记"
                else:
                    # 如果 compose_state 已完成，直接生成
                    if self.compose_state.get("stage") == "done":
                        plan_result = self._quick_plan_from_markers(self.compose_state.get("style", "AI初稿"))
                        exec_result = self.execute_plan()
                        result.update(plan_result)
                        result.update(exec_result)
                        result["message"] = f"🤖 AI初稿完成：{exec_result.get('message', '')}"
                        result["display"] = plan_result.get("display", "")
                        result["timeline"] = self.timeline.to_list()
                        self.compose_state = {}
                    else:
                        # 初始化状态机，开始对话引导
                        self.compose_state = {"stage": "style"}
                        result["action"] = "compose_guide"
                        result["compose_stage"] = self._get_compose_question()
                        result["message"] = result["compose_stage"]["question"]

            elif act == "compose_answer":
                # 对话式初稿：用户回答引导问题
                stage = self.compose_state.get("stage", "style")
                answer = action.get("answer", "")
                
                if stage == "style":
                    self.compose_state["style"] = answer
                    self.compose_state["stage"] = "effect"
                elif stage == "effect":
                    self.compose_state["effect"] = answer
                    self.compose_state["stage"] = "pace"
                elif stage == "pace":
                    self.compose_state["pace"] = answer
                    self.compose_state["stage"] = "done"
                
                if self.compose_state["stage"] == "done":
                    # 所有问题答完，生成方案并自动执行
                    plan_result = self._quick_plan_from_markers(self.compose_state.get("style", "AI初稿"))
                    exec_result = self.execute_plan()
                    result["action"] = "compose_done"
                    result["plan"] = plan_result.get("plan", {})
                    result["display"] = plan_result.get("display", "")
                    result["message"] = exec_result.get("message", "")
                    result["timeline"] = self.timeline.to_list()
                    result["clip_count"] = self.timeline.clip_count
                    self.compose_state = {}
                else:
                    result["action"] = "compose_guide"
                    result["compose_stage"] = self._get_compose_question()
                    result["message"] = result["compose_stage"]["question"]

            elif act == "propose":
                # AI 生成剪辑方案
                intent = action.get("intent", "")
                if not intent:
                    result["success"] = False
                    result["message"] = "请说明你想做什么类型的视频"
                else:
                    plan_result = self.propose_plan(intent)
                    result.update(plan_result)

            elif act == "execute_plan":
                exec_result = self.execute_plan()
                result.update(exec_result)
            elif act == "show_analysis":
                # 显示分析摘要
                if self.analysis:
                    result["message"] = self.analyzer.summary(self.analysis)
                    result["analysis"] = self.analysis.__dict__
                    result["success"] = True
                else:
                    result["success"] = False
                    result["message"] = "未加载视频或分析失败"

            else:
                result["success"] = False
                result["message"] = f"不支持的操作: {act}"

        except Exception as e:
            result["success"] = False
            result["message"] = f"执行失败: {e}"

        # 附带当前时间轴状态
        result["timeline"] = self.timeline.to_list()
        result["total_duration"] = self.timeline.total_duration
        result["clip_count"] = self.timeline.clip_count

        return result

    def get_context(self) -> dict:
        """获取当前上下文供 NLU 使用"""
        ctx = {
            "clip_count": self.timeline.clip_count,
            "total_duration": self.timeline.total_duration,
            "timeline_items": self.timeline.to_list(),
            "markers": self.markers,
        }
        if self.bgm_analysis:
            ctx["bgm_loaded"] = True
            ctx["bgm_duration"] = self.bgm_analysis.duration
            ctx["bgm_beat_count"] = len(self.bgm_analysis.beats)
            ctx["bgm_drop_sections"] = self.bgm_analysis.drop_sections
        return ctx

    def _get_compose_question(self) -> dict:
        """获取对话式引导的当前问题"""
        stage = self.compose_state.get("stage", "style")
        marker_count = len(self.markers)
        
        questions = {
            "style": {
                "stage": "style",
                "question": f"好的！我看到你有 {marker_count} 个标记片段。先告诉我，你想要什么**风格**？",
                "suggestions": ["热血燃向", "高光混剪", "搞笑娱乐", "文艺慢剪", "快节奏卡点", "暗黑酷炫"],
            },
            "effect": {
                "stage": "effect",
                "question": f"收到！想突出什么**效果**？",
                "suggestions": ["闪白转场", "慢动作特写", "淡入淡出", "抖动+音效", "速度渐变", "直接硬切"],
            },
            "pace": {
                "stage": "pace",
                "question": f"最后，整体**节奏**怎么走？",
                "suggestions": ["快节奏冲击", "慢→快递进", "慢放沉浸", "快慢交替", "随便剪"],
            },
        }
        return questions.get(stage, questions["style"])


class SessionManager:
    def __init__(self):
        self.sessions: dict[str, EditSession] = {}
        self._storage_dir = Path("/tmp/conversational-editor/sessions")
        self._storage_dir.mkdir(parents=True, exist_ok=True)

    def create_session(self) -> EditSession:
        sid = str(uuid.uuid4())[:8]
        session = EditSession(sid)
        self.sessions[sid] = session
        return session

    def get_session(self, session_id: str) -> EditSession | None:
        # 先查内存
        if session_id in self.sessions:
            return self.sessions[session_id]
        # 再尝试从磁盘恢复
        return self._load_session(session_id)

    def get_or_create(self, session_id: str) -> EditSession:
        s = self.get_session(session_id)
        if s:
            return s
        s = EditSession(session_id)
        self.sessions[session_id] = s
        return s

    def _save_session(self, session: EditSession):
        """持久化 session 到磁盘"""
        data = {
            "id": session.id,
            "source_path": session.source_path,
            "current_source_id": session.current_source_id,
            "video_loaded": session.video_loaded,
            "timeline": session.timeline.to_list(),
            "markers": session.markers,
            "preview_path": session.preview_path,
            "project_name": session.project_name,
        }
        filepath = self._storage_dir / f"{session.id}.json"
        import json
        filepath.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    def _load_session(self, session_id: str) -> EditSession | None:
        """从磁盘恢复 session"""
        filepath = self._storage_dir / f"{session_id}.json"
        if not filepath.exists():
            return None
        try:
            import json
            data = json.loads(filepath.read_text())
            s = EditSession(session_id)
            s.source_path = data.get("source_path", "")
            s.current_source_id = data.get("current_source_id", "")
            s.video_loaded = data.get("video_loaded", False)
            s.markers = data.get("markers", [])
            s.preview_path = data.get("preview_path", "")
            s.project_name = data.get("project_name", "未命名")
            if data.get("timeline"):
                s.timeline = Timeline.from_list(data["timeline"])
            self.sessions[session_id] = s
            return s
        except Exception:
            return None
