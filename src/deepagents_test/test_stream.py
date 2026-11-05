import asyncio
import os
from langchain.agents import create_agent
from langchain_core.messages import AIMessageChunk
from langchain_deepseek import ChatDeepSeek
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool

from agent.env_utils import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL


@tool
def get_weather(city: str) -> str:
    """Get weather for a given city."""
    return f"It's always sunny in {city}!"


# 使用真实模型
llm = ChatOpenAI(
        api_key=DEEPSEEK_API_KEY,
        model="deepseek-flash",
        base_url=DEEPSEEK_BASE_URL
    )

agent = create_agent(
    llm,
    tools=[get_weather],
    system_prompt="You are a helpful assistant.",
)

full_message = None
chunk_index = 0

# v1 单模式直接返回 (token, metadata)，没有外层 (stream_mode, data)。
for token, metadata in agent.stream(
    {"messages": [{"role": "user", "content": "What is the weather in Boston and SF?"}]},
    stream_mode="messages",
    version="v1",
):
    # 在类型过滤前观察所有消息，包括空片段、工具调用片段和工具结果。
    chunk_index += 1
    print(
        f"\n[片段 {chunk_index}] type={type(token).__name__} "
        f"node={metadata.get('langgraph_node')} step={metadata.get('langgraph_step')}\n"
        f"token={token!r}",
        flush=True,
    )

    if not isinstance(token, AIMessageChunk):
        continue

    # 1. 聚合所有块
    full_message = token if full_message is None else full_message + token

    # 可选：实时观察碎片化参数
    if token.tool_call_chunks:
        for tc in token.tool_call_chunks:
            if tc.get("args"):
                print(f"[参数碎片] index={tc.get('index')} args={tc['args']!r}")

    # 2. 关键判断：最后一个块被聚合后，tool_calls 即为完整对象
    if token.chunk_position == "last":
        print("\n>>> chunk_position='last'，工具调用参数已解析完成。")
        if full_message.tool_calls:
            for call in full_message.tool_calls:
                print(f"    name={call['name']!r}, args={call['args']!r}, id={call['id']!r}")
        else:
            print("    本轮没有工具调用（纯文本回复）。")
        full_message = None  # 重置，准备下一轮 LLM 调用

# async def test():
#    async for event in agent.astream_events(
#             {"messages": [{"role": "user", "content": "上海天气怎么样？"}]},
#             version="v2",
#         ):
#             kind = event["event"]
#
#             # 1. 文本 token
#             if kind == "on_chat_model_stream":
#                 chunk = event["data"]["chunk"]
#                 if chunk.content:
#                     print("model", chunk.content)
#             elif kind == "on_tool_start":
#                 print("tool_start",event.get("name"), event.get("run_id"))
#             elif kind == "on_tool_end":
#                 output = event["data"].get("output", "")
#                 print("tool_end", output)
#
# if __name__ == '__main__':
#     asyncio.run(test())