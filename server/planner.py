"""AI 剪辑规划器 — 根据用户意图生成剪辑方案（动态加载技能）"""

import json
from dataclasses import dataclass, field
from .llm_config import get_client
from .skills_manager import SkillManager


@dataclass
class ClipProposal:
    start: float
    end: float
    label: str
    speed: float = 1.0
    transition_after: str = ""


@dataclass
class EditPlan:
    title: str
    target_duration: str
    vibe: str
    structure: list[str]
    clips: list[ClipProposal]
    reasoning: str


SYSTEM_PROMPT = """你是视频剪辑专家。用户提供视频分析数据和剪辑意图，你给出具体的剪辑方案。

方案必须包含：
1. 标题 - 给这个剪辑取个名字
2. 风格 - 一句话描述节奏和氛围
3. 结构 - 分步骤描述每个段落
4. 具体切点 - 精确到秒的片段列表

基本规则：
- 优先选画面动感强(peak_motion高)的片段做高光
- 总时长控制在用户要求的范围内
- 不同段落之间建议过渡类型（闪白/淡入淡出/硬切）
- 变速建议：高光0.5x慢放，过渡1x正常

输出纯 JSON（不要markdown代码块）：
{
  "title": "标题",
  "target_duration": "约90秒",
  "vibe": "热血快节奏",
  "structure": ["开场(5s)", "三杀慢放(20s)", "尾声(3s)"],
  "clips": [
    {"start": 15.0, "end": 18.0, "label": "开场镜头", "speed": 1.0},
    {"start": 45.0, "end": 52.0, "label": "三杀高光", "speed": 0.5, "transition_after": "flash"},
    {"start": 120.0, "end": 125.0, "label": "收尾", "speed": 1.0, "transition_after": "dissolve"}
  ],
  "reasoning": "选择了画面动感最强的片段作为核心"
}"""


class EditPlanner:
    def __init__(self):
        self.skill_manager = SkillManager()

    def propose_plan(self, analysis_summary: str, user_intent: str,
                     duration: float) -> EditPlan:
        # 加载匹配的技能
        skills_context = self.skill_manager.get_context(user_intent)

        prompt = SYSTEM_PROMPT

        if skills_context:
            prompt += f"\n\n## 剪辑领域知识（必须优先遵守！）\n{skills_context}\n"

        prompt += f"""

视频分析：
{analysis_summary}

用户意图：{user_intent}
目标时长：从{duration:.0f}秒素材中选

请给出具体剪辑方案（纯JSON）："""

        try:
            client = get_client()
            text = client.generate(prompt, temperature=0.4, max_tokens=2048)
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(text[start:end])
                return self._parse(data)
        except:
            pass

        return self._fallback_plan(duration, user_intent)

    def _parse(self, data: dict) -> EditPlan:
        clips = []
        for c in data.get("clips", []):
            clips.append(ClipProposal(
                start=float(c["start"]),
                end=float(c["end"]),
                label=c.get("label", ""),
                speed=float(c.get("speed", 1.0)),
                transition_after=c.get("transition_after", ""),
            ))
        return EditPlan(
            title=data.get("title", "剪辑方案"),
            target_duration=data.get("target_duration", ""),
            vibe=data.get("vibe", ""),
            structure=data.get("structure", []),
            clips=clips,
            reasoning=data.get("reasoning", ""),
        )

    def _fallback_plan(self, duration: float, intent: str) -> EditPlan:
        d = duration
        clips = [
            ClipProposal(start=d*0.05, end=d*0.08, label="开场", speed=1.0),
            ClipProposal(start=d*0.35, end=d*0.45, label="核心片段", speed=0.7,
                        transition_after="flash"),
            ClipProposal(start=d*0.75, end=d*0.80, label="收尾", speed=1.0,
                        transition_after="dissolve"),
        ]
        return EditPlan(
            title="智能剪辑方案",
            target_duration=f"约{sum(c.end-c.start for c in clips):.0f}秒",
            vibe="节奏适中",
            structure=["开场", "核心", "收尾"],
            clips=clips,
            reasoning="基于时间轴均匀选取（LLM 未连接，使用规则生成）",
        )

    def format_plan_display(self, plan: EditPlan) -> str:
        lines = [
            f"## {plan.title}",
            f"*{plan.vibe} · {plan.target_duration}*",
            "",
        ]
        if plan.structure:
            lines.append("### 结构")
            for s in plan.structure:
                lines.append(f"- {s}")
            lines.append("")

        lines.append("### 具体切点")
        total = 0.0
        for i, c in enumerate(plan.clips):
            dur = (c.end - c.start) / c.speed
            total += dur
            spd = f" @ {c.speed}x" if c.speed != 1.0 else ""
            trans = f" → {c.transition_after}" if c.transition_after else ""
            lines.append(f"{i+1}. **{c.label}** {c.start:.0f}s→{c.end:.0f}s ({dur:.0f}s{spd}){trans}")

        lines.append("")
        lines.append(f"**总时长**: ~{total:.0f}秒")
        lines.append("")
        lines.append(f"> {plan.reasoning}")
        return "\n".join(lines)
