from dashscope import api_key
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph, MessagesState, START, END

from agent.env_utils import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL


def mock_llm(state: MessagesState):
    # 不调用真实模型，直接返回一条 AI 消息
    return {"messages": [{"role": "ai", "content": "hello world"}]}

# 构建图
graph = StateGraph(MessagesState)
graph.add_node("mock_llm", mock_llm)
graph.add_edge(START, "mock_llm")
graph.add_edge("mock_llm", END)

config = {"configurable": {"thread_id": "111"}}

# 关键：在模块顶层导出编译后的图对象，而不是调用 asyncio.run()
compiled_graph = graph.compile(checkpointer=InMemorySaver())

result = compiled_graph.invoke(
    {"messages": [{"role": "user", "content": "hi"}]},
    config=config,
)

history = list(compiled_graph.get_state_history(config))
for i, snapshot in enumerate(history):
    # 当前检查点 id
    ckpt_id = snapshot.config["configurable"].get("checkpoint_id")

    # 父检查点 id：parent_config 可能为 None（最早的那个快照没有父节点）
    parent_cfg = snapshot.parent_config or {}
    parent_ckpt_id = parent_cfg.get("configurable", {}).get("checkpoint_id")

    print(f"\n--- 快照 {i} ---")
    print(f"step                 : {snapshot.metadata.get('step')}")
    print(f"source               : {snapshot.metadata.get('source')}")
    print(f"next                 : {snapshot.next}")
    print(f"checkpoint_id        : {ckpt_id}")
    print(f"parent_checkpoint_id : {parent_ckpt_id}")
    print(f"created_at           : {snapshot.created_at}")
    print(f"values               :")
    for msg in snapshot.values.get("messages", []):
        print(f"    [{msg.type}] {msg.content}")



from langchain.agents import create_agent


def get_weather(city: str) -> str:
    # 模拟天气查询
    """获取给定城市的天气。"""
    return f"{city}天气晴朗！"

model = init_chat_model(model="deepseek-flash", api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)

# 创建Agent
agent = create_agent(
    model=model,
    tools=[get_weather],
    checkpointer=InMemorySaver(),
    system_prompt="你是一个助手，你可以查询城市的天气。",
)


config2 = {"configurable": {"thread_id": "222"}}
# 调用Agent
resp = agent.invoke(
    {"messages": [HumanMessage("你好")]},
    config=config2
)
print(resp)

history2 = list(agent.get_state_history(config2))
for i, snapshot in enumerate(history2):
    # 当前检查点 id
    ckpt_id = snapshot.config["configurable"].get("checkpoint_id")

    # 父检查点 id：parent_config 可能为 None（最早的那个快照没有父节点）
    parent_cfg = snapshot.parent_config or {}
    parent_ckpt_id = parent_cfg.get("configurable", {}).get("checkpoint_id")

    print(f"\n--- 快照 {i} ---")
    print(f"step                 : {snapshot.metadata.get('step')}")
    print(f"source               : {snapshot.metadata.get('source')}")
    print(f"next                 : {snapshot.next}")
    print(f"checkpoint_id        : {ckpt_id}")
    print(f"parent_checkpoint_id : {parent_ckpt_id}")
    print(f"created_at           : {snapshot.created_at}")
    print(f"values               :")
    for msg in snapshot.values.get("messages", []):
        print(f"    [{msg.type}] {msg.content}")