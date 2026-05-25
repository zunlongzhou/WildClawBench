"""
Multi-Agent 编排模块 — 支持多个 Agent 协同完成复杂任务。

架构：
  Orchestrator Agent → Sub-Agent A (专项能力)
                     → Sub-Agent B (专项能力)
                     → Sub-Agent C (专项能力)

所有 Sub-Agent 的追踪数据由 opik-openclaw 自动采集（subagent_spawning/spawned/ended 事件）。
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SubAgentConfig:
    """子 Agent 配置。"""
    name: str
    model: str
    system_prompt: str = ""
    skills: list[str] = field(default_factory=list)  # skill 短名（已安装）或 clawhub slug（需安装）
    install_skills: list[str] = field(default_factory=list)  # 该 sub-agent 需要额外安装的 skills
    timeout_seconds: int = 300
    max_turns: int = 50


@dataclass
class MultiAgentConfig:
    """多 Agent 编排配置。"""
    orchestrator_model: str
    orchestrator_prompt: str = ""
    sub_agents: list[SubAgentConfig] = field(default_factory=list)
    coordination_mode: str = "sequential"  # sequential | parallel | adaptive


class MultiAgentOrchestrator:
    """
    多 Agent 编排器。

    使用 OpenClaw 的原生 sub-agent 能力，通过配置文件注册多个专项 agent，
    opik-openclaw 插件会自动追踪所有 sub-agent 的生命周期。

    使用方式:
        1. 定义 MultiAgentConfig
        2. 调用 setup() 将配置写入 OpenClaw
        3. 由 Orchestrator Agent 在运行时自动调度 sub-agents
    """

    def __init__(self, workspace_root: Path | None = None) -> None:
        self.workspace_root = workspace_root or Path.home()
        self.openclaw_config_path = self.workspace_root / ".openclaw" / "openclaw.json"

    def setup(self, config: MultiAgentConfig) -> None:
        """将多 Agent 配置写入 OpenClaw，并安装各 sub-agent 所需的 skills。"""
        # 先安装所有 sub-agent 声明需要的 skills
        self._install_sub_agent_skills(config.sub_agents)
        self._register_sub_agents(config.sub_agents)
        if config.orchestrator_prompt:
            self._set_orchestrator_prompt(config.orchestrator_prompt)
        logger.info(
            "Multi-agent setup complete: %d sub-agents registered",
            len(config.sub_agents),
        )

    def _install_sub_agent_skills(self, sub_agents: list[SubAgentConfig]) -> None:
        """安装各 sub-agent 声明需要额外安装的 skills。"""
        from src.utils.skill_installer import SkillInstaller, SkillSpec

        all_skills: list[SkillSpec] = []
        seen: set[str] = set()

        for agent in sub_agents:
            for skill_name in agent.install_skills:
                if skill_name not in seen:
                    seen.add(skill_name)
                    all_skills.append(SkillSpec(name=skill_name, required=False))

        if not all_skills:
            return

        installer = SkillInstaller()
        results = installer.install_batch(all_skills)
        success = sum(1 for r in results if r.success)
        logger.info(
            "Sub-agent skills: %d/%d installed successfully",
            success,
            len(results),
        )

    def _register_sub_agents(self, sub_agents: list[SubAgentConfig]) -> None:
        """注册子 Agent 到 OpenClaw 配置。"""
        openclaw_config = self._load_config()

        agents_config = openclaw_config.setdefault("agents", {})
        registered = agents_config.setdefault("registered", {})

        for agent in sub_agents:
            registered[agent.name] = {
                "model": agent.model,
                "systemPrompt": agent.system_prompt,
                "skills": agent.skills,
                "timeout": agent.timeout_seconds,
                "maxTurns": agent.max_turns,
            }
            logger.info("Registered sub-agent: %s (model=%s)", agent.name, agent.model)

        self._save_config(openclaw_config)

    def _set_orchestrator_prompt(self, prompt: str) -> None:
        """设置编排器的系统提示。"""
        openclaw_config = self._load_config()
        agents_config = openclaw_config.setdefault("agents", {})
        defaults = agents_config.setdefault("defaults", {})
        defaults["orchestratorPrompt"] = prompt
        self._save_config(openclaw_config)

    def _load_config(self) -> dict:
        if self.openclaw_config_path.exists():
            return json.loads(self.openclaw_config_path.read_text(encoding="utf-8"))
        return {}

    def _save_config(self, config: dict) -> None:
        self.openclaw_config_path.parent.mkdir(parents=True, exist_ok=True)
        self.openclaw_config_path.write_text(
            json.dumps(config, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @staticmethod
    def create_research_team(
        main_model: str = "openrouter/anthropic/claude-sonnet-4.6",
        researcher_model: str = "openrouter/anthropic/claude-sonnet-4.6",
        coder_model: str = "openrouter/anthropic/claude-sonnet-4.6",
        reviewer_model: str = "openrouter/google/gemini-2.5-pro",
    ) -> MultiAgentConfig:
        """
        预设配置：研究团队。

        - orchestrator: 任务分解与协调
        - researcher: 信息检索与分析
        - coder: 代码实现
        - reviewer: 代码审查与质量保证
        """
        return MultiAgentConfig(
            orchestrator_model=main_model,
            orchestrator_prompt=(
                "You are a team lead coordinating multiple specialized agents. "
                "Break down complex tasks and delegate to the appropriate sub-agent. "
                "Available sub-agents: researcher (for information gathering), "
                "coder (for implementation), reviewer (for quality assurance)."
            ),
            sub_agents=[
                SubAgentConfig(
                    name="researcher",
                    model=researcher_model,
                    system_prompt=(
                        "You are a research specialist. Your job is to gather information, "
                        "search documentation, analyze requirements, and provide comprehensive "
                        "research reports to help the team make informed decisions."
                    ),
                    skills=["ddgs-search", "academic-literature-search"],
                    timeout_seconds=180,
                ),
                SubAgentConfig(
                    name="coder",
                    model=coder_model,
                    system_prompt=(
                        "You are a senior software engineer. Your job is to write clean, "
                        "efficient, well-tested code based on specifications provided. "
                        "Follow best practices and include error handling."
                    ),
                    skills=[],
                    timeout_seconds=300,
                ),
                SubAgentConfig(
                    name="reviewer",
                    model=reviewer_model,
                    system_prompt=(
                        "You are a code reviewer and quality assurance specialist. "
                        "Review code for bugs, security issues, performance problems, "
                        "and adherence to best practices. Provide actionable feedback."
                    ),
                    skills=["skill-trust-auditor"],
                    timeout_seconds=120,
                ),
            ],
            coordination_mode="adaptive",
        )

    @staticmethod
    def create_security_audit_team(
        main_model: str = "openrouter/anthropic/claude-sonnet-4.6",
    ) -> MultiAgentConfig:
        """
        预设配置：安全审计团队。

        - orchestrator: 审计任务调度
        - scanner: 漏洞扫描
        - analyst: 安全分析
        """
        return MultiAgentConfig(
            orchestrator_model=main_model,
            orchestrator_prompt=(
                "You are a security audit team lead. Coordinate the scanner and analyst "
                "sub-agents to perform comprehensive security assessments."
            ),
            sub_agents=[
                SubAgentConfig(
                    name="scanner",
                    model=main_model,
                    system_prompt=(
                        "You are a security scanner. Identify potential vulnerabilities, "
                        "misconfigurations, and security anti-patterns in code and infrastructure."
                    ),
                    skills=["aegis-shield", "canary"],
                    timeout_seconds=240,
                ),
                SubAgentConfig(
                    name="analyst",
                    model=main_model,
                    system_prompt=(
                        "You are a security analyst. Analyze findings from the scanner, "
                        "assess risk levels, and recommend mitigations."
                    ),
                    skills=["skill-trust-auditor"],
                    timeout_seconds=180,
                ),
            ],
            coordination_mode="sequential",
        )
