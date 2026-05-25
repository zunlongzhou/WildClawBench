"""
Skill Installer — 批量安装和管理 OpenClaw Skills。

支持：
- ClawHub 远程 skills 安装 (clawhub:@user/skill-name)
- 本地 skill 目录安装
- 从 JSON/YAML 配置文件批量安装（含 skill 级别配置）
- 安装状态检查与报告
"""
from __future__ import annotations

import json
import logging
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SkillSpec:
    """单个 Skill 的安装规格。"""
    name: str                           # clawhub:@user/skill 或本地路径
    config: dict[str, Any] = field(default_factory=dict)  # skill 级别配置
    required: bool = True               # 是否必须安装成功
    version: str = ""                   # 指定版本（空=最新）


@dataclass
class SkillInstallResult:
    """单个 Skill 的安装结果。"""
    name: str
    success: bool
    message: str = ""
    install_time_ms: int = 0


class SkillInstaller:
    """OpenClaw Skill 批量安装管理器。"""

    def __init__(self, timeout_per_skill: int = 60) -> None:
        self.timeout_per_skill = timeout_per_skill
        self._installed: list[SkillInstallResult] = []

    @property
    def results(self) -> list[SkillInstallResult]:
        return self._installed

    @property
    def all_success(self) -> bool:
        return all(r.success for r in self._installed if r.name)

    @property
    def required_success(self) -> bool:
        """所有 required=True 的 skill 都安装成功。"""
        # SkillSpec 的 required 属性在安装时已经处理
        return all(r.success for r in self._installed)

    def install_from_list(self, skills_str: str) -> list[SkillInstallResult]:
        """
        从逗号分隔的字符串安装多个 skills。
        
        Args:
            skills_str: "clawhub:@user/skill1,clawhub:@user/skill2,./local-skill"
        """
        specs = []
        for item in skills_str.split(","):
            item = item.strip()
            if not item:
                continue
            specs.append(SkillSpec(name=item))
        return self.install_batch(specs)

    def install_from_config(self, config_path: str | Path) -> list[SkillInstallResult]:
        """
        从 JSON/YAML 配置文件批量安装 skills。
        
        配置格式:
        ```json
        {
          "skills": [
            {
              "name": "clawhub:@anthropic/web-search",
              "config": {"api_key_env": "BRAVE_API_KEY"},
              "required": true
            },
            {
              "name": "clawhub:@opik/opik-openclaw",
              "required": true
            },
            {
              "name": "./skills/4/ddgs-search",
              "required": false
            }
          ]
        }
        ```
        """
        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(f"Skills config not found: {path}")

        text = path.read_text(encoding="utf-8")

        if path.suffix in (".yaml", ".yml"):
            import yaml
            data = yaml.safe_load(text)
        else:
            data = json.loads(text)

        skills_list = data if isinstance(data, list) else data.get("skills", [])
        specs = [SkillSpec(**s) if isinstance(s, dict) else SkillSpec(name=s) for s in skills_list]
        return self.install_batch(specs)

    def install_batch(self, specs: list[SkillSpec]) -> list[SkillInstallResult]:
        """批量安装 skills。"""
        logger.info("Installing %d skills...", len(specs))
        results = []

        for spec in specs:
            result = self._install_single(spec)
            results.append(result)
            self._installed.append(result)

            status = "✓" if result.success else "✗"
            logger.info(
                "  %s %s (%dms) %s",
                status,
                spec.name,
                result.install_time_ms,
                result.message if not result.success else "",
            )

        success_count = sum(1 for r in results if r.success)
        logger.info(
            "Skills installation complete: %d/%d succeeded",
            success_count,
            len(results),
        )
        return results

    def install_from_directory(self, dir_path: str | Path) -> list[SkillInstallResult]:
        """
        从一个目录批量安装所有 skill zip 包和子目录。

        典型用法：将一批 .zip skill 包放在一个目录里，一次性全部安装。
        也支持目录中混合 zip 文件和 skill 子目录。

        Args:
            dir_path: 包含 skill zip 包的目录路径
        """
        path = Path(dir_path).resolve()
        if not path.is_dir():
            raise FileNotFoundError(f"Skills directory not found: {path}")

        specs: list[SkillSpec] = []

        # 收集所有 .zip 文件
        for zip_file in sorted(path.glob("*.zip")):
            specs.append(SkillSpec(name=str(zip_file)))

        # 收集所有包含 SKILL.md 或 skill.json 的子目录（即 skill 目录）
        for sub_dir in sorted(path.iterdir()):
            if not sub_dir.is_dir():
                continue
            if (sub_dir / "SKILL.md").exists() or (sub_dir / "skill.json").exists():
                specs.append(SkillSpec(name=str(sub_dir)))

        if not specs:
            logger.warning("No skill zips or directories found in %s", path)
            return []

        logger.info("Found %d skills in %s", len(specs), path)
        return self.install_batch(specs)

    def _install_single(self, spec: SkillSpec) -> SkillInstallResult:
        """安装单个 skill。"""
        name = spec.name.strip()

        # 判断是 zip 文件、本地目录还是 ClawHub slug
        if name.endswith(".zip") or name.endswith(".tar.gz"):
            return self._install_zip(spec)
        elif name.startswith("./") or name.startswith("/") or Path(name).is_dir():
            return self._install_local(spec)
        else:
            return self._install_remote(spec)

    def _install_zip(self, spec: SkillSpec) -> SkillInstallResult:
        """从 zip/tar.gz 包安装 skill。"""
        import shutil
        import tempfile
        import zipfile
        import tarfile

        start = time.perf_counter()
        name = spec.name
        archive_path = Path(name).resolve()

        if not archive_path.exists():
            elapsed = int((time.perf_counter() - start) * 1000)
            return SkillInstallResult(
                name=name,
                success=not spec.required,
                message=f"file not found: {archive_path}",
                install_time_ms=elapsed,
            )

        # 解压到临时目录
        tmp_dir = Path(tempfile.mkdtemp(prefix="skill_"))
        try:
            if name.endswith(".zip"):
                with zipfile.ZipFile(str(archive_path), "r") as zf:
                    zf.extractall(str(tmp_dir))
            elif name.endswith(".tar.gz"):
                with tarfile.open(str(archive_path), "r:gz") as tf:
                    tf.extractall(str(tmp_dir))

            # 找到实际的 skill 根目录（可能在子目录中）
            skill_root = self._find_skill_root(tmp_dir)
            if not skill_root:
                elapsed = int((time.perf_counter() - start) * 1000)
                return SkillInstallResult(
                    name=name,
                    success=not spec.required,
                    message="no SKILL.md or skill.json found in archive",
                    install_time_ms=elapsed,
                )

            # 使用 openclaw skills install 安装本地路径
            cmd = ["openclaw", "skills", "install", str(skill_root)]
            r = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_per_skill,
            )
            elapsed = int((time.perf_counter() - start) * 1000)

            if r.returncode == 0:
                if spec.config:
                    self._configure_skill(name, spec.config)
                return SkillInstallResult(
                    name=archive_path.stem,
                    success=True,
                    message=f"installed from {archive_path.name}",
                    install_time_ms=elapsed,
                )
            else:
                if "already" in (r.stderr + r.stdout).lower():
                    return SkillInstallResult(
                        name=archive_path.stem,
                        success=True,
                        message="already installed",
                        install_time_ms=elapsed,
                    )
                return SkillInstallResult(
                    name=archive_path.stem,
                    success=not spec.required,
                    message=r.stderr.strip()[:200] or r.stdout.strip()[:200],
                    install_time_ms=elapsed,
                )
        except Exception as exc:
            elapsed = int((time.perf_counter() - start) * 1000)
            return SkillInstallResult(
                name=archive_path.stem,
                success=not spec.required,
                message=str(exc)[:200],
                install_time_ms=elapsed,
            )
        finally:
            shutil.rmtree(str(tmp_dir), ignore_errors=True)

    @staticmethod
    def _find_skill_root(extract_dir: Path) -> Path | None:
        """在解压目录中查找 skill 根目录（包含 SKILL.md 或 skill.json 的目录）。"""
        # 先检查顶层
        if (extract_dir / "SKILL.md").exists() or (extract_dir / "skill.json").exists():
            return extract_dir

        # 检查一层子目录（zip 包常见：zip 内有一个同名根文件夹）
        for child in extract_dir.iterdir():
            if child.is_dir():
                if (child / "SKILL.md").exists() or (child / "skill.json").exists():
                    return child

        # 递归查找（最多两层深度）
        for child in extract_dir.rglob("SKILL.md"):
            return child.parent
        for child in extract_dir.rglob("skill.json"):
            return child.parent

        return None

    def _install_remote(self, spec: SkillSpec) -> SkillInstallResult:
        """从 ClawHub 安装远程 skill。"""
        start = time.perf_counter()
        name = spec.name

        # 构建安装命令
        cmd = ["openclaw", "skills", "install", name]
        if spec.version:
            cmd.append(f"--version={spec.version}")

        try:
            r = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_per_skill,
            )
            elapsed = int((time.perf_counter() - start) * 1000)

            if r.returncode == 0:
                # 如果有 skill 级别配置，写入
                if spec.config:
                    self._configure_skill(name, spec.config)
                return SkillInstallResult(
                    name=name,
                    success=True,
                    message="installed",
                    install_time_ms=elapsed,
                )
            else:
                # 检查是否是已安装
                if "already installed" in r.stderr.lower() or "already installed" in r.stdout.lower():
                    return SkillInstallResult(
                        name=name,
                        success=True,
                        message="already installed",
                        install_time_ms=elapsed,
                    )
                return SkillInstallResult(
                    name=name,
                    success=not spec.required,
                    message=r.stderr.strip()[:200] or r.stdout.strip()[:200],
                    install_time_ms=elapsed,
                )
        except subprocess.TimeoutExpired:
            elapsed = int((time.perf_counter() - start) * 1000)
            return SkillInstallResult(
                name=name,
                success=not spec.required,
                message=f"timeout after {self.timeout_per_skill}s",
                install_time_ms=elapsed,
            )
        except Exception as exc:
            elapsed = int((time.perf_counter() - start) * 1000)
            return SkillInstallResult(
                name=name,
                success=not spec.required,
                message=str(exc)[:200],
                install_time_ms=elapsed,
            )

    def _install_local(self, spec: SkillSpec) -> SkillInstallResult:
        """安装本地 skill 目录。"""
        start = time.perf_counter()
        name = spec.name
        local_path = Path(name).resolve()

        if not local_path.is_dir():
            elapsed = int((time.perf_counter() - start) * 1000)
            return SkillInstallResult(
                name=name,
                success=not spec.required,
                message=f"directory not found: {local_path}",
                install_time_ms=elapsed,
            )

        # 使用 openclaw skills install 安装本地路径
        cmd = ["openclaw", "skills", "install", str(local_path)]
        try:
            r = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_per_skill,
            )
            elapsed = int((time.perf_counter() - start) * 1000)

            if r.returncode == 0:
                if spec.config:
                    self._configure_skill(name, spec.config)
                return SkillInstallResult(
                    name=name,
                    success=True,
                    message="installed from local",
                    install_time_ms=elapsed,
                )
            else:
                if "already" in (r.stderr + r.stdout).lower():
                    return SkillInstallResult(
                        name=name,
                        success=True,
                        message="already installed",
                        install_time_ms=elapsed,
                    )
                return SkillInstallResult(
                    name=name,
                    success=not spec.required,
                    message=r.stderr.strip()[:200] or r.stdout.strip()[:200],
                    install_time_ms=elapsed,
                )
        except subprocess.TimeoutExpired:
            elapsed = int((time.perf_counter() - start) * 1000)
            return SkillInstallResult(
                name=name,
                success=not spec.required,
                message="timeout",
                install_time_ms=elapsed,
            )
        except Exception as exc:
            elapsed = int((time.perf_counter() - start) * 1000)
            return SkillInstallResult(
                name=name,
                success=not spec.required,
                message=str(exc)[:200],
                install_time_ms=elapsed,
            )

    def _configure_skill(self, skill_name: str, config: dict[str, Any]) -> None:
        """为已安装的 skill 写入配置。"""
        # 提取 skill 的 short name（去掉 clawhub: 前缀等）
        short_name = skill_name
        if ":" in short_name:
            short_name = short_name.split(":", 1)[1]
        if "/" in short_name:
            short_name = short_name.rsplit("/", 1)[-1]

        for key, value in config.items():
            # 如果 value 是环境变量引用 (以 $ 或 env: 开头)，从环境读取
            if isinstance(value, str) and value.startswith("$"):
                import os
                env_key = value.lstrip("$")
                value = os.environ.get(env_key, "")

            cmd = ["openclaw", "skills", "config", "set", short_name, key, str(value)]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if r.returncode != 0:
                logger.warning(
                    "Failed to configure skill %s key %s: %s",
                    skill_name, key, r.stderr.strip()[:100],
                )

    def get_install_report(self) -> dict[str, Any]:
        """生成安装报告。"""
        return {
            "total": len(self._installed),
            "success": sum(1 for r in self._installed if r.success),
            "failed": sum(1 for r in self._installed if not r.success),
            "total_time_ms": sum(r.install_time_ms for r in self._installed),
            "details": [
                {
                    "name": r.name,
                    "success": r.success,
                    "message": r.message,
                    "install_time_ms": r.install_time_ms,
                }
                for r in self._installed
            ],
        }

    def list_installed_skills(self) -> list[str]:
        """列出当前已安装的所有 skills。"""
        try:
            r = subprocess.run(
                ["openclaw", "skills", "list"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if r.returncode == 0:
                return [line.strip() for line in r.stdout.splitlines() if line.strip()]
        except Exception as exc:
            logger.warning("Failed to list installed skills: %s", exc)
        return []
