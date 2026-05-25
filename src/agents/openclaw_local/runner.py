"""
OpenClaw Local Agent Runner — 本地化部署模式。

不使用 Docker，直接在本地运行 OpenClaw + opik-openclaw 插件，
提供完整的可观测性（token、行为链、思维链、中间结果、执行时间）。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

from dotenv import load_dotenv

from src.agents.base import AgentExecution, AgentTaskSpec, BaseAgent
from src.utils.opik_collector import OpikCollector
from src.utils.skill_installer import SkillInstaller

load_dotenv()

logger = logging.getLogger(__name__)


class OpenClawLocalAgent(BaseAgent):
    """本地化 OpenClaw Agent Runner，支持 opik 深度追踪和多 Agent 模式。"""

    def __init__(
        self,
        gateway_port: int = 18789,
        openrouter_api_key: str = "",
        openrouter_base_url: str = "https://openrouter.ai/api/v1",
        opik_enabled: bool = True,
        opik_project_name: str = "wildclaw-bench",
        opik_api_url: str = "",
        opik_api_key: str = "",
        image_model: str | None = None,
        workspace_base: str = "",
        install_skills: str = "",
        skills_config: str = "",
        skills_dir: str = "",
    ) -> None:
        self.gateway_port = gateway_port
        self.openrouter_api_key = openrouter_api_key
        self.openrouter_base_url = openrouter_base_url
        self.opik_enabled = opik_enabled
        self.opik_project_name = opik_project_name
        self.opik_api_url = opik_api_url or os.environ.get(
            "OPIK_URL_OVERRIDE", "http://localhost:5173/api"
        )
        self.opik_api_key = opik_api_key or os.environ.get("OPIK_API_KEY", "")
        self.image_model = image_model or os.environ.get("OPENCLAW_IMAGE_MODEL", "").strip()
        self.workspace_base = workspace_base or os.environ.get(
            "LOCAL_WORKSPACE_BASE", "/tmp/wildclaw_local"
        )
        self._install_skills = install_skills
        self._skills_config = skills_config
        self._skills_dir = skills_dir
        self._skill_installer = SkillInstaller()
        self._skills_installed = False
        self._collector = OpikCollector(
            api_url=self.opik_api_url,
            api_key=self.opik_api_key,
            project_name=self.opik_project_name,
        )

    @property
    def expects_gateway(self) -> bool:
        return True

    @property
    def transcript_container_path(self) -> str:
        """本地模式下，transcript 路径在 ~/.openclaw/agents/main/sessions/chat.jsonl"""
        home = Path.home()
        return str(home / ".openclaw" / "agents" / "main" / "sessions" / "chat.jsonl")

    def run_task(self, spec: AgentTaskSpec) -> AgentExecution:
        gateway_proc = None
        agent_proc = None
        elapsed_time = float(spec.timeout_seconds)

        try:
            # 1. 准备本地工作空间
            task_workspace = Path(self.workspace_base) / spec.task_id
            task_workspace.mkdir(parents=True, exist_ok=True)

            exec_path = os.path.join(spec.workspace_path, "exec")
            if os.path.isdir(exec_path):
                shutil.copytree(exec_path, str(task_workspace), dirs_exist_ok=True)

            # 2. 安装全局指定的 skills（仅首次）
            if not self._skills_installed:
                self._install_global_skills()
                self._skills_installed = True

            # 3. 设置任务级别的技能（从本地 skills/ 目录复制）
            self._setup_local_skills(spec)

            # 4. 设置模型
            self._set_model(spec.model)

            # 5. 设置 image model
            image_model = self.image_model or spec.model
            self._set_image_model(image_model)

            # 6. 注入自定义模型配置
            if spec.models_config:
                self._inject_models_config(spec.models_config)

            # 7. 确保 opik 插件已配置
            if self.opik_enabled:
                self._ensure_opik_configured()

            # 8. 执行 warmup 命令
            self._run_warmup(spec.task.get("warmup", ""), task_workspace)

            # 9. 构建环境变量
            env = self._build_env(spec)

            # 10. 启动 Gateway
            gateway_log = spec.output_dir / "gateway.log"
            gateway_log.parent.mkdir(parents=True, exist_ok=True)
            gateway_log_file = gateway_log.open("w", encoding="utf-8")

            gateway_proc = subprocess.Popen(
                ["openclaw", "gateway", "--port", str(self.gateway_port)],
                stdout=gateway_log_file,
                stderr=subprocess.STDOUT,
                env=env,
                cwd=str(task_workspace),
            )
            gateway_proc._log_file = gateway_log_file  # type: ignore[attr-defined]
            logger.info("[%s] Local gateway started PID=%s", spec.task_id, gateway_proc.pid)
            time.sleep(3)  # 等待 gateway 就绪

            # 11. 启动 Agent
            safe_prompt = spec.prompt.replace("'", "'\\''")
            agent_log = spec.output_dir / "agent.log"
            agent_log_file = agent_log.open("w", encoding="utf-8")

            start_time = time.perf_counter()
            agent_proc = subprocess.Popen(
                [
                    "openclaw", "agent",
                    "--session-id", "chat",
                    "--timeout", str(spec.timeout_seconds),
                    "--message", spec.prompt,
                ],
                stdout=agent_log_file,
                stderr=subprocess.STDOUT,
                env=env,
                cwd=str(task_workspace),
            )
            agent_proc._log_file = agent_log_file  # type: ignore[attr-defined]
            logger.info("[%s] Local agent started PID=%s", spec.task_id, agent_proc.pid)

            # 11. 等待完成或超时
            try:
                agent_proc.wait(timeout=spec.timeout_seconds)
                elapsed_time = time.perf_counter() - start_time
                logger.info(
                    "[%s] Agent finished, elapsed: %.2f seconds",
                    spec.task_id,
                    elapsed_time,
                )
            except subprocess.TimeoutExpired:
                logger.info("[%s] Agent timed out", spec.task_id)
                elapsed_time = float(spec.timeout_seconds)
                agent_proc.kill()
                agent_proc.wait()

            return AgentExecution(
                elapsed_time=elapsed_time,
                error=None,
                gateway_proc=gateway_proc,
                agent_proc=agent_proc,
            )

        except Exception as exc:
            logger.error("[%s] Local execution error: %s", spec.task_id, exc)
            return AgentExecution(
                elapsed_time=float(spec.timeout_seconds),
                error=str(exc),
                gateway_proc=gateway_proc,
                agent_proc=agent_proc,
            )

    def collect_usage(self, task_id: str, output_dir: Path, elapsed_time: float) -> dict:
        """从本地 transcript 和 Opik 收集使用指标。"""
        output_dir.mkdir(parents=True, exist_ok=True)

        # 方式一：从本地 JSONL transcript 提取基础 token 统计
        transcript_path = Path(self.transcript_container_path)
        usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
            "request_count": 0,
        }

        if transcript_path.exists():
            from src.utils.grading import extract_usage_from_jsonl
            usage = extract_usage_from_jsonl(transcript_path)
            # 拷贝 transcript 到输出目录
            shutil.copy2(str(transcript_path), str(output_dir / "chat.jsonl"))

        usage["elapsed_time"] = round(elapsed_time, 2)

        # 附加 skills 安装报告
        if self._skill_installer.results:
            usage["skills_install_report"] = self._skill_installer.get_install_report()

        # 方式二：从 Opik 获取丰富的追踪数据
        if self.opik_enabled:
            try:
                opik_metrics = self._collector.collect_task_traces(task_id)
                usage["opik_traces"] = opik_metrics
                # 写入详细的 Opik 追踪报告
                opik_report_path = output_dir / "opik_traces.json"
                opik_report_path.write_text(
                    json.dumps(opik_metrics, indent=2, ensure_ascii=False, default=str),
                    encoding="utf-8",
                )
                logger.info("[%s] Opik traces written to %s", task_id, opik_report_path)
            except Exception as exc:
                logger.warning("[%s] Failed to collect Opik traces: %s", task_id, exc)

        return usage

    def _build_env(self, spec: AgentTaskSpec) -> dict[str, str]:
        """构建子进程环境变量。"""
        env = os.environ.copy()
        env["OPENROUTER_API_KEY"] = self.openrouter_api_key
        env["OPENROUTER_BASE_URL"] = self.openrouter_base_url

        if self.opik_enabled and self.opik_api_key:
            env["OPIK_API_KEY"] = self.opik_api_key
            env["OPIK_URL_OVERRIDE"] = self.opik_api_url
            env["OPIK_PROJECT_NAME"] = self.opik_project_name

        # 注入任务自定义环境变量
        for line in spec.task.get("env", "").splitlines():
            key = line.strip()
            if not key or key.startswith("#"):
                continue
            value = os.environ.get(key, "")
            if value:
                env[key] = value

        return env

    def _install_global_skills(self) -> None:
        """
        安装通过 --install-skills / --skills-config / --skills-dir 指定的全局 skills。
        
        这些 skills 在所有任务执行前统一安装一次，不同于任务级别的 skills（从本地复制）。
        支持 zip 包、目录、ClawHub slug 三种来源。
        安装报告会写入日志和输出目录。
        """
        has_skills_to_install = (
            bool(self._install_skills) or bool(self._skills_config) or bool(self._skills_dir)
        )
        if not has_skills_to_install:
            return

        logger.info("=== Installing global skills ===")

        # 优先级：skills_dir > skills_config > install_skills
        if self._skills_dir:
            results = self._skill_installer.install_from_directory(self._skills_dir)
        elif self._skills_config:
            results = self._skill_installer.install_from_config(self._skills_config)
        elif self._install_skills:
            results = self._skill_installer.install_from_list(self._install_skills)
        else:
            return

        # 如果同时指定了多个来源，追加安装
        if self._skills_dir and self._install_skills:
            extra = self._skill_installer.install_from_list(self._install_skills)
            results.extend(extra)
        if self._skills_dir and self._skills_config:
            extra = self._skill_installer.install_from_config(self._skills_config)
            results.extend(extra)

        # 输出安装报告
        report = self._skill_installer.get_install_report()
        logger.info(
            "Skills install report: %d/%d succeeded (total %dms)",
            report["success"],
            report["total"],
            report["total_time_ms"],
        )

        # 如果有必须安装但失败的 skill，打印警告
        for detail in report["details"]:
            if not detail["success"]:
                logger.warning(
                    "  FAILED: %s — %s",
                    detail["name"],
                    detail["message"],
                )

    def _setup_local_skills(self, spec: AgentTaskSpec) -> None:
        """将技能文件复制到本地 OpenClaw skills 目录。"""
        skills_text = spec.task.get("skills", "")
        skills_path = spec.task.get("skills_path", "")
        if not skills_text or not skills_path:
            return

        home_skills = Path.home() / "skills"
        home_skills.mkdir(parents=True, exist_ok=True)

        for line in skills_text.splitlines():
            line = line.strip()
            if not line:
                continue
            src = Path(skills_path) / line.replace("\\", "/").strip("/")
            dest_name = src.name
            dest = home_skills / dest_name
            if src.is_dir():
                shutil.copytree(str(src), str(dest), dirs_exist_ok=True)
                logger.info("[%s] Skill copied: %s → %s", spec.task_id, src, dest)

    def _set_model(self, model: str) -> None:
        r = subprocess.run(
            ["openclaw", "models", "set", model],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"Model setup failed:\n{r.stderr}")
        logger.info("Model set: %s", model)

    def _set_image_model(self, model: str) -> None:
        subprocess.run(
            ["openclaw", "config", "set", "agents.defaults.imageModel.primary", model],
            capture_output=True,
            text=True,
        )
        logger.info("imageModel set: %s", model)

    def _inject_models_config(self, models_config: dict) -> None:
        """注入自定义模型配置到本地 ~/.openclaw/openclaw.json。"""
        config_path = Path.home() / ".openclaw" / "openclaw.json"
        config = {}
        if config_path.exists():
            config = json.loads(config_path.read_text(encoding="utf-8"))
        config["models"] = models_config
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        logger.info("Injected custom models config to %s", config_path)

    def _ensure_opik_configured(self) -> None:
        """确保 opik-openclaw 插件已启用且配置正确。"""
        config_path = Path.home() / ".openclaw" / "openclaw.json"
        config = {}
        if config_path.exists():
            config = json.loads(config_path.read_text(encoding="utf-8"))

        plugins = config.setdefault("plugins", {})
        entries = plugins.setdefault("entries", {})
        allow_list = plugins.setdefault("allow", [])

        if "opik-openclaw" not in allow_list:
            allow_list.append("opik-openclaw")

        opik_entry = entries.setdefault("opik-openclaw", {})
        opik_entry["enabled"] = True
        opik_entry.setdefault("hooks", {})["allowConversationAccess"] = True
        opik_config = opik_entry.setdefault("config", {})
        opik_config["enabled"] = True
        if self.opik_api_key:
            opik_config["apiKey"] = self.opik_api_key
        opik_config["apiUrl"] = self.opik_api_url
        opik_config["projectName"] = self.opik_project_name
        opik_config.setdefault("workspaceName", "default")
        opik_config.setdefault("tags", ["wildclaw-bench"])
        opik_config.setdefault("staleTraceCleanupEnabled", True)
        opik_config.setdefault("staleTraceTimeoutMs", 300000)

        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        logger.info("Opik plugin configuration ensured at %s", config_path)

    def _run_warmup(self, warmup: str, workspace: Path) -> None:
        """在本地执行 warmup 命令。"""
        if not warmup.strip():
            return
        for line in warmup.splitlines():
            cmd = line.strip()
            if not cmd or cmd.startswith("#"):
                continue
            logger.info("Warmup: %s", cmd)
            r = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                cwd=str(workspace),
            )
            if r.returncode != 0:
                raise RuntimeError(f"Warmup failed: {cmd!r}\n{r.stderr}")
