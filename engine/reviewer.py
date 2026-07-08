"""质检引擎 — 结构化审查协议

遵循 CHAI 规则（Accurate / Complete / Constructive）：
- Accurate: 每条 finding 指向具体字段/帧号
- Complete: 发现一个问题后扫描同类全貌
- Constructive: critical 必须附带 proposed_fix

使用方法:
    reviewer = Reviewer()
    findings = reviewer.review(artifact, review_focus, success_criteria)
"""

from __future__ import annotations

from .artifact import Artifact, Finding, Severity


class Reviewer:
    """无状态的审查引擎。输入 artifact + 审查规则，输出结构化 finding 列表。"""

    def review(
        self,
        artifact: Artifact,
        review_focus: list[str],
        success_criteria: list[str],
        playbook_rules: dict | None = None,
    ) -> list[Finding]:
        """执行完整审查流程。

        Args:
            artifact: 阶段产出的 artifact
            review_focus: 管道 YAML 中定义的审查焦点
            success_criteria: 可通过/不可通过的硬性标准
            playbook_rules: 可选的风格剧本规则（调色板、转场类型等）

        Returns:
            按 severity 排序的 finding 列表 (critical > suggestion > nitpick > investigation)
        """
        findings: list[Finding] = []

        # Step 1: Schema 校验
        findings.extend(artifact.validate())

        # Step 2: 逐条审查 review_focus
        for criterion in review_focus:
            results = self._check_criterion(artifact, criterion, playbook_rules)
            findings.extend(results)

        # Step 3: 检查 success_criteria（硬性门禁）
        findings.extend(self._check_success_criteria(artifact, success_criteria))

        # Step 4: Complete 原则 — 同类问题全貌扫描
        findings = self._expand_to_class(findings, artifact)

        # Step 5: Constructive 原则 — critical 必须有修复建议
        findings = self._ensure_constructive(findings)

        # 排序：critical → suggestion → nitpick → investigation
        order = {Severity.critical: 0, Severity.suggestion: 1, Severity.nitpick: 2, Severity.investigation: 3}
        findings.sort(key=lambda f: order.get(f.severity, 99))

        return findings

    def should_block(self, findings: list[Finding]) -> bool:
        """有 critical finding 就应该阻塞"""
        return any(f.severity == Severity.critical for f in findings)

    def summary(self, findings: list[Finding]) -> str:
        """生成人类可读的审查摘要"""
        if not findings:
            return "✅ 全部通过"
        by_sev = {}
        for f in findings:
            by_sev.setdefault(f.severity.value, []).append(f)
        parts = []
        for sev in ["critical", "suggestion", "nitpick", "investigation"]:
            if sev in by_sev:
                icons = {"critical": "❌", "suggestion": "⚠️", "nitpick": "💡", "investigation": "🔍"}
                parts.append(f"{icons[sev]} {len(by_sev[sev])} {sev}")
        return ", ".join(parts)

    # ─── 内部方法 ───────────────────────────────────────────────────

    def _check_criterion(
        self, artifact: Artifact, criterion: str, playbook_rules: dict | None
    ) -> list[Finding]:
        """根据审查焦点字符串生成 finding。

        这里用关键词匹配做轻量级检查。对于需要深度分析的规则
        （如 "所有过渡正确"），由具体的 artifact.validate() 处理。
        """
        findings = []
        d = artifact.to_dict()

        # 时长检查
        if "duration" in criterion.lower() or "时长" in criterion:
            total = d.get("total_duration", d.get("expected_duration", d.get("output_duration", 0)))
            if total <= 0:
                findings.append(Finding(Severity.critical, criterion, artifact.stage_name,
                    "总时长为 0", "检查时间轴片段"))

        # 片段数量
        if "clip" in criterion.lower() and "count" in criterion.lower():
            count = d.get("clip_count", 0)
            if count == 0:
                findings.append(Finding(Severity.critical, criterion, artifact.stage_name,
                    "无片段", "至少添加 1 个片段"))
            elif count < 2:
                findings.append(Finding(Severity.suggestion, criterion, artifact.stage_name,
                    "仅 1 个片段，混剪通常需要 3+ 片段",
                    "继续添加更多片段或切换为单片段模式"))

        # 文件存在性
        if "exists" in criterion.lower() or "存在" in criterion:
            paths = [d.get("output_path", ""), d.get("srt_path", ""), d.get("bgm_path", "")]
            for p in paths:
                if p and not __import__("os").path.exists(p):
                    findings.append(Finding(Severity.critical, criterion, artifact.stage_name,
                        f"文件不存在: {p}", "检查渲染输出路径"))

        # 过渡检查
        if "transition" in criterion.lower() or "过渡" in criterion:
            trans_count = d.get("transition_count", d.get("xfade_transitions", -1))
            if trans_count == 0 and d.get("clip_count", 1) > 1:
                findings.append(Finding(Severity.nitpick, criterion, artifact.stage_name,
                    "多个片段间无过渡效果", "添加 dissolve 或 flash 过渡"))

        # 音频检查
        if "audio" in criterion.lower() or "音频" in criterion or "sound" in criterion.lower():
            if not d.get("audio_present", True):
                findings.append(Finding(Severity.critical, criterion, artifact.stage_name,
                    "无音频轨道", "检查音频混音参数"))
            peak = d.get("peak_level", 0)
            if peak > 0:
                findings.append(Finding(Severity.suggestion, criterion, artifact.stage_name,
                    f"音频峰值 {peak}dB 超过 0dB，可能削波", "降低总输出增益或加限幅器"))

        return findings

    def _check_success_criteria(self, artifact: Artifact, criteria: list[str]) -> list[Finding]:
        """硬性门禁检查"""
        findings = []
        d = artifact.to_dict()

        for criterion in criteria:
            # 简单的模式匹配
            if "clip_count" in criterion:
                try:
                    target = int(criterion.split("≥")[1].strip()) if "≥" in criterion else 1
                    if d.get("clip_count", 0) < target:
                        findings.append(Finding(Severity.critical, criterion, artifact.stage_name,
                            f"片段数 {d.get('clip_count', 0)} < {target}",
                            f"添加至少 {target} 个片段"))
                except (ValueError, IndexError):
                    pass

            if "duration" in criterion and "match" in criterion:
                expected = d.get("expected_duration", 0)
                actual = d.get("output_duration", 0)
                if expected > 0 and abs(actual - expected) / expected > 0.01:
                    findings.append(Finding(Severity.critical, criterion, artifact.stage_name,
                        f"时长不匹配: 预期 {expected:.1f}s, 实际 {actual:.1f}s",
                        "检查渲染参数"))

        return findings

    def _expand_to_class(self, findings: list[Finding], artifact: Artifact) -> list[Finding]:
        """Complete 原则：发现一个问题后，扫描同类全貌。

        例如：发现一个片段时长不足，检查所有片段。
        """
        d = artifact.to_dict()

        # 如果发现时长相关 finding，检查所有片段的子结构
        has_duration_finding = any("时长" in f.detail or "duration" in f.criterion for f in findings)

        if has_duration_finding and "subtitles" in d:
            for i, sub in enumerate(d.get("subtitles", [])):
                dur = sub.get("end", 0) - sub.get("start", 0)
                if dur < 0.3 and not any(f"字幕 #{i+1}" in f.location for f in findings):
                    findings.append(Finding(Severity.suggestion, "complete_scan",
                        f"字幕 #{i+1}", f"同类问题：显示时长仅 {dur:.1f}s",
                        "延长到至少 0.5 秒"))

        return findings

    def _ensure_constructive(self, findings: list[Finding]) -> list[Finding]:
        """Constructive 原则：critical 没有 proposed_fix 则降级为 investigation。"""
        for f in findings:
            if f.severity == Severity.critical and not f.proposed_fix:
                f.severity = Severity.investigation
                f.detail = f"[降级] 无法定位修复方案: " + f.detail
        return findings
