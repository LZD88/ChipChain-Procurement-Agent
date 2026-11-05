"""从 NeMo 配置目录加载输入护栏；每次检查独立创建 rails，不共享用户上下文。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from agent.env_utils import get_env

DEFAULT_GUARDRAILS_DIR = Path(__file__).resolve().parent / "guardrails"
logger = logging.getLogger(__name__)


class GuardSettings(BaseModel):
    """项目运行参数；拒绝拼错字段和无效值，避免配置失效后静默放行。"""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    base_url_env_var: str = Field(min_length=1)
    max_input_chars: int = Field(gt=0)
    timeout_seconds: float = Field(gt=0)
    reject_control_characters: bool
    max_resume_decisions: int = Field(gt=0)
    blocked_message: str = Field(min_length=1)
    direct_response_flows: list[str] = Field(default_factory=list)


class InputPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    blocked_requests: list[str] = Field(min_length=1)
    allowed_requests: list[str] = Field(min_length=1)


class InputSafetyError(Exception):
    """可安全展示给客户端的错误，不携带用户原文或模型原始输出。"""

    def __init__(self, code: str, message: str, status_code: int):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _invalid_input(message: str) -> InputSafetyError:
    return InputSafetyError("input_invalid", message, 422)


def _unavailable() -> InputSafetyError:
    return InputSafetyError(
        "input_check_unavailable", "输入安全检查暂时不可用，请稍后重试。", 503,
    )


def _parse_verdict(output: str) -> tuple[bool, list]:
    # 仅明确的 ALLOW 可放行；空输出、说明文字或格式异常均不放行。
    return isinstance(output, str) and output.strip() == "ALLOW", []


def _load_configuration(config_path: Path):
    os.environ.setdefault("NEMO_GUARDRAILS_NO_USAGE_STATS", "1")
    from nemoguardrails import RailsConfig

    config = RailsConfig.from_path(str(config_path))
    settings = GuardSettings.model_validate(config.custom_data["input_guard"])
    policy = InputPolicy.model_validate(config.custom_data["input_policy"])
    if any(not rule.strip() for rule in policy.blocked_requests + policy.allowed_requests):
        raise ValueError("安全规则不能是空字符串")
    if "self check input" not in config.rails.input.flows:
        raise ValueError("必须保留 self check input，可在其前后追加输入检查流程")
    response_flows = settings.direct_response_flows
    if any(not name.strip() or name == "self check input" for name in response_flows):
        raise ValueError("直接回复流程不能是空名称或 self check input")
    if response_flows and config.rails.input.parallel:
        raise ValueError("直接回复流程必须在安全检查之后串行执行")
    # 所有拦截规则先执行，避免固定回复提前 stop 而跳过后续安全规则。
    flows = config.rails.input.flows
    config.rails.input.flows = (
        [name for name in flows if name not in response_flows]
        + [name for name in flows if name in response_flows]
    )
    prompts = [prompt for prompt in config.prompts or [] if prompt.task == "self_check_input"]
    if not prompts or any(prompt.output_parser != "strict_input_verdict" for prompt in prompts):
        raise ValueError("self_check_input 必须配置 strict_input_verdict 输出解析器")
    if len(config.models) != 1 or config.models[0].type != "main" or config.models[0].engine != "openai":
        raise ValueError("当前审查模型适配器要求一个 engine=openai、type=main 的模型")
    model = config.models[0]
    if not model.model or not model.api_key_env_var or model.mode != "chat":
        raise ValueError("审查模型必须配置名称、api_key_env_var，并使用 chat 模式")
    if {"api_key", "openai_api_key"} & model.parameters.keys():
        raise ValueError("审查模型密钥请使用 api_key_env_var 配置")
    return config, settings, policy


@lru_cache(maxsize=8)
def _configured_model(model_json: str, api_key: str, base_url: str | None):
    """仅复用无会话状态的模型客户端；规则和 rails 实例不缓存。"""
    from langchain_openai import ChatOpenAI

    model = json.loads(model_json)
    parameters = dict(model["parameters"])
    if not parameters.get("base_url") and not parameters.get("openai_api_base"):
        if not base_url:
            raise ValueError("审查模型服务地址未配置")
        parameters["base_url"] = base_url
    return ChatOpenAI(model=model["model"], api_key=api_key, **parameters)


def _build_rails(model, user_id: str, config, settings: GuardSettings, policy: InputPolicy):
    from nemoguardrails.integrations.langchain.llm_adapter import LangChainLLMAdapter
    from nemoguardrails.rails.llm.llmrails import LLMRails

    if model is None:
        model_config = config.models[0]
        api_key = get_env(model_config.api_key_env_var)
        if not api_key:
            raise ValueError("审查模型密钥未配置")
        model = _configured_model(
            model_config.model_dump_json(), api_key, get_env(settings.base_url_env_var),
        )
    # 模型已按 YAML 创建，通过 LangChain 适配器注入；避免 NeMo 再创建一次。
    config.models = []

    async def initialize():
        rails = LLMRails(config, llm=LangChainLLMAdapter(model))
        rails.register_output_parser(_parse_verdict, name="strict_input_verdict")
        rails.register_prompt_context("input_policy", policy.model_dump())
        rails.register_prompt_context("authenticated_user_id", json.dumps(user_id, ensure_ascii=False))
        return rails

    # SDK 初始化会取得事件循环；工作线程内显式管理，避免留下未关闭的循环。
    return asyncio.run(initialize())


class InputGuard:
    def __init__(self, model=None, *, config_path: Path | str = DEFAULT_GUARDRAILS_DIR,
                 timeout: float | None = None):
        self.model = model
        self.config_path = Path(config_path)
        self.timeout = timeout

    async def _configuration(self):
        try:
            # 每次读取配置：规则、提示词、流程和运行参数修改后，下次请求生效。
            return await asyncio.to_thread(_load_configuration, self.config_path)
        except Exception as exc:
            logger.warning("输入护栏配置加载失败：%s", type(exc).__name__)
            raise _unavailable() from None

    async def check_text(self, text: str, *, user_id: str) -> str | None:
        """检查输入；命中已配置的回复流程时返回固定文案，否则返回 None。"""
        if not isinstance(text, str) or not text.strip():
            raise _invalid_input("输入不能为空。")
        config, settings, policy = await self._configuration()
        if len(text) > settings.max_input_chars:
            raise _invalid_input(f"输入过长，请控制在 {settings.max_input_chars} 个字符以内。")
        if settings.reject_control_characters and any(ord(char) < 32 and char not in "\t\n\r" for char in text):
            raise _invalid_input("输入包含不支持的控制字符，请删除后重试。")

        try:
            timeout = self.timeout if self.timeout is not None else settings.timeout_seconds
            async with asyncio.timeout(timeout):
                rails = await asyncio.to_thread(_build_rails, self.model, user_id, config, settings, policy)
                from nemoguardrails.rails.llm.options import RailStatus, RailType
                result = await rails.check_async(
                    [{"role": "user", "content": text}], rail_types=[RailType.INPUT],
                )
        except Exception as exc:
            # 不记录异常正文，模型服务异常可能含用户输入或连接凭据。
            logger.warning("输入安全检查不可用：%s", type(exc).__name__)
            raise _unavailable() from None

        if result.status == RailStatus.BLOCKED:
            # NeMo 将 stop 统一标为 BLOCKED；仅显式声明的回复流程视为正常回复。
            if result.rail in settings.direct_response_flows:
                if not isinstance(result.content, str) or not result.content.strip():
                    raise _unavailable()
                return result.content
            raise InputSafetyError("input_blocked", settings.blocked_message, 403)
        # 不采用 SDK 返回的改写文本；异常或意外结果不会进入业务 Agent。
        if result.status != RailStatus.PASSED or result.content != text:
            raise _unavailable()

    async def check_resume(self, resume: dict, *, user_id: str) -> None:
        if not isinstance(resume, dict):
            raise _invalid_input("恢复请求格式不正确。")
        if set(resume) == {"supplement"}:
            await self.check_text(resume["supplement"], user_id=user_id)
            return
        if set(resume) == {"decisions"}:
            decisions = resume["decisions"]
            if (
                isinstance(decisions, list) and decisions
                and all(isinstance(item, dict) and set(item) == {"type"}
                        and item["type"] in ("approve", "reject") for item in decisions)
            ):
                _, settings, _ = await self._configuration()
                if len(decisions) <= settings.max_resume_decisions:
                    return
        # 仅接受现有界面的两类输入，不允许夹带编辑后的工具参数或自由文本指令。
        raise _invalid_input("请提交补充信息，或选择同意/拒绝；不能修改审批参数。")


@lru_cache(maxsize=1)
def get_input_guard() -> InputGuard:
    return InputGuard()
