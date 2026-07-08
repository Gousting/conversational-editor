"""指令解析器 — 自然语言 → 编辑操作 JSON

策略: LLM 解析优先，规则引擎兜底
"""

import json
import re
from typing import Optional
from .llm_config import get_client, get_config


SYSTEM_PROMPT = """你是视频剪辑指令解析器。用户说自然语言，你输出操作 JSON。

如果上下文中包含 markers（用户标记的时间点），优先根据标记来理解用户意图：
- 标记格式: @12.5s 精彩击杀, @30.2s 慢动作
- 用户可能说"把标记之间的片段提取出来"、"在标记2处加闪白"等

支持的操作类型:
1. add_clip — 从源视频提取片段
   {"action": "add_clip", "start": 32.0, "end": 35.5, "speed": 1.0, "label": "三杀"}
   {"action": "add_clip", "start": 75.0, "end": 78.0}  (speed默认1.0)

2. remove — 删除片段
   {"action": "remove", "item_id": "abc123"}
   {"action": "remove", "index": 2}  (删除第3个，从0开始)
   {"action": "remove", "position": "last"}  或 "first"

3. update_clip — 修改片段属性
   {"action": "update_clip", "item_id": "abc123", "speed": 0.5}
   {"action": "update_clip", "item_id": "abc123", "label": "慢动作"}

4. reorder — 调整顺序
   {"action": "reorder", "item_id": "abc123", "new_index": 0}

5. add_transition — 添加过渡
   {"action": "add_transition", "after_item_id": "abc123", "effect": "flash", "duration": 0.3}
   支持的 effect: "flash"(闪白), "dissolve"(淡入淡出), "cut"(硬切)
   flash 默认颜色 #FFE4B5

6. render — 渲染预览
   {"action": "render"}

7. undo / redo
   {"action": "undo"}
   {"action": "redo"}

8. save_project / load_project
   {"action": "save_project"}
   {"action": "load_project", "path": "/path/to/project.json"}

9. query — 查询当前状态（非编辑操作）
   {"action": "query", "target": "timeline"}

规则:
- 时间格式支持: "1:05"=65秒, "0:32"=32秒, "32秒"=32, 纯数字=秒
- "放慢0.5倍"→ speed=0.5, "加速2倍"→ speed=2.0
- "闪白"→ effect="flash", "淡入淡出"→ effect="dissolve"
- "删掉最后一个"→ remove position="last"
- "把第2和第3换位置"→两次 reorder
- 如果用户说"接到后面"但没指定具体时间，提示需要时间参数
- 如果指令无法解析，返回 {"action": "unknown", "reason": "..."}

IMPORTANT: 只输出纯 JSON，不要任何其他文字！"""


class NLUParser:
    def __init__(self):
        self._rule_parse = self._rule_parse

    def parse(self, user_input: str, context: dict = None) -> dict:
        """解析用户输入为操作 dict

        Args:
            user_input: 用户自然语言输入
            context: 可选上下文 {"timeline_items": [...], "clip_count": N}

        Returns:
            操作 dict，action 字段为操作类型
        """
        # 如果 compose 对话活跃中，用户输入直接视为 compose_answer
        if context and context.get("compose_active"):
            answer = user_input.strip()
            return {"action": "compose_answer", "answer": answer}

        # 先尝试规则匹配
        rule_result = self._rule_parse(user_input, context or {})
        if rule_result and rule_result.get("action") != "unknown":
            return rule_result

        # 规则无法匹配时用 LLM
        try:
            cfg = get_config()
            if cfg.check_available() == "online":
                return self._llm_parse(user_input, context)
        except:
            pass
            return {"action": "unknown",
                    "reason": f"无法解析指令，Ollama 也不在线。试试更明确的说法。",
                    "user_input": user_input}

    def _rule_parse(self, text: str, ctx: dict) -> Optional[dict]:
        """正则规则快速匹配常见模式"""
        import re
        t = text.strip()

        # 渲染预览
        if re.search(r'渲染|预览|preview|render', t, re.I):
            if re.search(r'最终|导出|完整|final', t):
                return {"action": "render_final"}
            return {"action": "render"}

        # 撤销 / 重做
        if re.match(r'^(撤销|回退|撤回|undo)$', t):
            return {"action": "undo"}
        if re.match(r'^(重做|前进|redo)$', t):
            return {"action": "redo"}

        # 保存 / 加载
        if re.search(r'^保存', t):
            return {"action": "save_project"}
        if re.search(r'^加载', t):
            # 提取路径
            m = re.search(r'加载\s+(.+)', t)
            path = m.group(1).strip() if m else ""
            return {"action": "load_project", "path": path}

        # 查询时间轴
        if re.search(r'查看|显示.*时间轴|当前.*状态|timeline|现在.*什么', t):
            return {"action": "query", "target": "timeline"}

        # AI 方案相关
        if re.search(r'auto_compose|自动初稿|自动生成|自动剪辑|AI.*初稿|智能.*生成', t):
            return {"action": "auto_compose"}
        if re.search(r'^执行方案|^应用方案|^按方案|^确认.*方案', t):
            return {"action": "execute_plan"}
        if re.search(r'^[给帮].*方案|^推荐.*方案|^怎么剪|^帮我.*剪|^怎么做|^我想.*做|^我能.*做', t):
            return {"action": "propose", "intent": t}
        # 任何包含具体风格/主题意图的，也触发 propose
        if re.search(r'高光|集锦|精彩|搞笑|燃|热血|文艺|治愈|快剪|慢放|混剪|卡点', t):
            return {"action": "propose", "intent": t}

        # 删除
        if m := re.search(r'删[除掉]\s*最?后\s*(一?[个段条])?', t):
            return {"action": "remove", "position": "last"}
        if m := re.search(r'删[除掉]\s*第\s*(\d+)\s*(个|段)', t):
            idx = int(m.group(1)) - 1  # 用户说"第1个"=index 0
            return {"action": "remove", "index": idx}

        # 提取片段: "从 X 到 Y" 或 "X-Y" 或 "X ~ Y"
        time_range = None
        # 格式: 1:05 或 0:32 或 32秒 或 32
        def parse_time(s: str) -> Optional[float]:
            s = s.strip()
            if ':' in s:
                parts = s.split(':')
                return float(parts[0]) * 60 + float(parts[1])
            s = s.replace('秒', '').strip()
            try:
                return float(s)
            except:
                return None

        # "从 0:32 到 0:35"
        for pat in [
            r'从\s*(\S+)\s*[到至\-~]\s*(\S+)',
            r'(\S+)\s*[到至\-~]\s*(\S+)',
        ]:
            m = re.search(pat, t)
            if m:
                start = parse_time(m.group(1))
                end = parse_time(m.group(2))
                if start is not None and end is not None and end > start:
                    time_range = (start, end)
                    break

        if time_range:
            start, end = time_range
            result = {"action": "add_clip", "start": start, "end": end}

            # 提取标签
            if m := re.search(r'(.{1,20})$', t):
                label = m.group(1).strip()
                # 过滤掉明显不是标签的
                if label and not re.match(r'^[从把在的]|^提取|^保留|^留下|^截取', label):
                    result["label"] = label

            # 提取变速
            if m := re.search(r'([0-9.]+)\s*倍', t):
                result["speed"] = float(m.group(1))
            elif re.search(r'慢放|放慢|减[速慢]', t):
                if m := re.search(r'([0-9.]+)', t):
                    spd = float(m.group(1))
                    result["speed"] = spd if spd < 1 else 1/spd

            # 提取插入位置: "接在...后面" "加到...之后"
            if re.search(r'接[到在].*后[面头]|加[到入].*后|追加', t):
                result["position"] = "last"

            return result

        # 修改片段
        # 修改片段
        if clip_count := ctx.get("clip_count", 0):
            idx = None
            from .nlu_fix import chinese_num_to_int
            # 支持 "第1段" 和 "第一段"
            if m := re.search(r'第\s*(\d+|[一二三四五六七八九十]+)\s*[个段]', t):
                idx_str = m.group(1)
                try:
                    idx = int(idx_str) - 1
                except ValueError:
                    idx = chinese_num_to_int(idx_str) - 1
            elif re.search(r'这段|当前|这个', t):
                idx = clip_count - 1  # 默认最后添加的

            if idx is not None and idx < clip_count:
                props = {}
                if m := re.search(r'([0-9.]+)\s*倍', t):
                    props["speed"] = float(m.group(1))
                elif re.search(r'慢放|放慢', t):
                    if m := re.search(r'([0-9.]+)', t):
                        props["speed"] = float(m.group(1))
                if props:
                    return {"action": "update_clip", "index": idx, **props}

        # 过渡
        if re.search(r'闪白|闪光|flash', t, re.I):
            dur = 0.3
            if m := re.search(r'([0-9.]+)\s*秒', t):
                dur = float(m.group(1))
            color = "#FFE4B5"
            if m := re.search(r'#([0-9A-Fa-f]{6})', t):
                color = "#" + m.group(1)
            return {"action": "add_transition", "effect": "flash",
                    "duration": dur, "color": color}

        if re.search(r'淡[入出]|dissolve|fade', t, re.I):
            dur = 0.5
            if m := re.search(r'([0-9.]+)\s*秒', t):
                dur = float(m.group(1))
            return {"action": "add_transition", "effect": "dissolve",
                    "duration": dur}

        return None

    def _llm_parse(self, user_input: str, context: Optional[dict]) -> dict:
        """用 LLM 解析"""
        ctx_str = ""
        if context:
            items = context.get("timeline_items", [])
            if items:
                clips = [i for i in items if i.get("type") == "clip"]
                ctx_str = f"\n当前时间轴有 {len(clips)} 个片段:\n"
                for c in clips:
                    d = c.get("data", {})
                    ctx_str += f"  [{d.get('id','?')}] {d.get('label','未命名')} | "
                    ctx_str += f"源{d.get('source_start',0)}-{d.get('source_end',0)}s "
                    ctx_str += f"速度{d.get('speed',1)}x\n"

            markers = context.get("markers", [])
            if markers:
                ctx_str += "\n用户标记的时间点:\n"
                for i, m in enumerate(markers):
                    ctx_str += f"  标记{i+1}: @{m['time']:.1f}s"
                    if m.get('label'):
                        ctx_str += f" {m['label']}"
                    ctx_str += "\n"

        prompt = f"""{SYSTEM_PROMPT}

{ctx_str}
用户: {user_input}

输出:"""

        try:
            client = get_client()
            response_text = client.generate(prompt, temperature=0.1, max_tokens=256)
            json_start = response_text.find("{")
            json_end = response_text.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                return json.loads(response_text[json_start:json_end])
            if not response_text:
                return {"action": "unknown",
                        "reason": "LLM 返回为空，请用更明确的方式表达",
                        "user_input": user_input}
            return {"action": "unknown",
                    "reason": f"LLM 返回非 JSON: {response_text[:100]}",
                    "user_input": user_input}
        except Exception as e:
            return {"action": "unknown", "reason": f"LLM 调用失败: {e}",
                    "user_input": user_input}
