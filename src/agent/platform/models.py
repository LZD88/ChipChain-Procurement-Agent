"""OpenAI-compatible model with request-pinned, non-secret Nacos parameters.

Tool bindings, streaming, callbacks and existing clients stay in LangChain.
"""
from langchain_openai import ChatOpenAI
from pydantic import Field
from .runtime import runtime_configuration


class RuntimeChatOpenAI(ChatOpenAI):
    runtime_profile: str = Field(default="main", exclude=True)

    def _runtime_parameters(self):
        profile = getattr(runtime_configuration.snapshot().models, self.runtime_profile)
        return {"model": profile.model, "temperature": profile.temperature,
                "max_completion_tokens": profile.maxTokens}

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        payload.pop("max_tokens", None)
        payload.update(self._runtime_parameters())
        return payload

    def _get_invocation_params(self, stop=None, **kwargs):
        parameters = super()._get_invocation_params(stop=stop, **kwargs)
        parameters.pop("max_tokens", None)
        parameters.update(self._runtime_parameters())
        return parameters
