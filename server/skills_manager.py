"""技能管理器 — 加载/匹配/注入剪辑领域知识

技能文件格式 (Markdown + YAML frontmatter):

---
name: 高光合集
description: 游戏高光混剪规则
triggers: [高光, 集锦, 精彩时刻, 击杀, 连杀, 三杀, 四杀, 五杀]
always: false
---

## 高光合集剪辑规则

...规则正文...
"""

import os
import re
import yaml
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class Skill:
    name: str
    description: str
    triggers: list[str] = field(default_factory=list)
    always: bool = False
    body: str = ""         # 规则正文（YAML frontmatter 后面的 markdown）
    file_path: str = ""

    def match(self, text: str) -> bool:
        """检查用户意图是否匹配此技能"""
        if self.always:
            return True
        text_lower = text.lower()
        return any(t.lower() in text_lower for t in self.triggers)


class SkillManager:
    def __init__(self, skills_dir: str = None):
        if skills_dir is None:
            skills_dir = str(Path(__file__).parent.parent / "skills")
        self.skills_dir = Path(skills_dir)
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.skills: dict[str, Skill] = {}
        self.load_all()

    def load_all(self):
        """扫描目录，加载所有 .md 文件"""
        self.skills.clear()
        if not self.skills_dir.exists():
            return
        for f in sorted(self.skills_dir.glob("*.md")):
            try:
                skill = self._parse_skill(f)
                self.skills[skill.name] = skill
            except Exception as e:
                print(f"[SkillManager] 跳过 {f.name}: {e}")

    def _parse_skill(self, filepath: Path) -> Skill:
        """解析 YAML frontmatter + markdown body"""
        text = filepath.read_text(encoding="utf-8")

        # Extract YAML frontmatter
        fm_match = re.match(r'^---\s*\n(.*?)\n---\s*\n', text, re.DOTALL)
        if not fm_match:
            raise ValueError(f"缺少 YAML frontmatter")

        meta = yaml.safe_load(fm_match.group(1))
        body = text[fm_match.end():].strip()

        return Skill(
            name=meta.get("name", filepath.stem),
            description=meta.get("description", ""),
            triggers=meta.get("triggers", []),
            always=meta.get("always", False),
            body=body,
            file_path=str(filepath),
        )

    def match(self, intent: str) -> list[Skill]:
        """根据用户意图匹配技能列表"""
        matched = []
        for skill in self.skills.values():
            if skill.match(intent):
                matched.append(skill)
        return matched

    def get_context(self, intent: str = "") -> str:
        """获取应注入 prompt 的技能上下文（markdown 格式）"""
        always_skills = [s for s in self.skills.values() if s.always]
        matched = [s for s in self.skills.values() if s.match(intent) and not s.always]

        # 去重（always 和 matched 可能有重叠）
        all_skills = list({s.name: s for s in always_skills + matched}.values())

        if not all_skills:
            return ""

        parts = []
        for s in all_skills:
            parts.append(f"## {s.name}\n{s.body}")

        return "\n\n---\n\n".join(parts)

    def list_skills(self) -> list[dict]:
        """返回技能列表（不含 body）"""
        return [
            {
                "name": s.name,
                "description": s.description,
                "triggers": s.triggers,
                "always": s.always,
                "file_path": s.file_path,
            }
            for s in self.skills.values()
        ]

    def create_or_update(self, name: str, description: str,
                         triggers: list[str], always: bool,
                         body: str) -> Skill:
        """创建或更新技能文件"""
        filename = re.sub(r'[^\w\-]', '-', name).strip('-') + ".md"
        filepath = self.skills_dir / filename

        # Build frontmatter
        fm = {
            "name": name,
            "description": description,
            "triggers": triggers,
            "always": always,
        }
        content = "---\n"
        content += yaml.dump(fm, allow_unicode=True, default_flow_style=False)
        content += "---\n\n"
        content += body.strip() + "\n"

        filepath.write_text(content, encoding="utf-8")
        self.load_all()
        return self.skills.get(name)

    def delete(self, name: str) -> bool:
        """删除技能文件"""
        skill = self.skills.get(name)
        if not skill:
            return False
        Path(skill.file_path).unlink(missing_ok=True)
        self.load_all()
        return True

    def reload(self):
        """重新加载所有技能"""
        self.load_all()
