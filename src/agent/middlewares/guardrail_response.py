"""把已通过安全检查的固定回复写入会话，并在业务模型执行前结束本轮。"""

from collections.abc import Mapping

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage


class GuardrailResponseMiddleware(AgentMiddleware):
    @hook_config(can_jump_to=["end"])
    def before_agent(self, state, runtime):
        context = runtime.context
        response = (
            context.get("guardrail_response") if isinstance(context, Mapping)
            else getattr(context, "guardrail_response", None)
        )
        if response is not None:
            return {"messages": [AIMessage(content=response)], "jump_to": "end"}
        return None

    @hook_config(can_jump_to=["end"])
    async def abefore_agent(self, state, runtime):
        return self.before_agent(state, runtime)
