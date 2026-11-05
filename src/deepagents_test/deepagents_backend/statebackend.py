from contextlib import closing
import sys
from uuid import uuid4

from deepagents import create_deep_agent
from deepagents.backends import StoreBackend
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from agent.env_utils import GLM_API_KEY, GLM_BASE_URL


def create_model(model, temperature, max_tokens=None, extra_body=None):
    return ChatOpenAI(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        openai_api_key=GLM_API_KEY,
        openai_api_base=GLM_BASE_URL,
        extra_body=extra_body or {}
    )


def create_chat_agent():
    """使用内存检查点保存聊天，使用 StoreBackend 跨会话共享虚拟文件。"""
    model = create_model(
        model="glm-4-flash",
        temperature=0.1,
        max_tokens=200,
        # extra_body={"thinking": {"type": "disabled"}},
    )

    return create_deep_agent(
        model=model,
        checkpointer=InMemorySaver(),
        backend=StoreBackend(
            store=InMemoryStore(),
            # namespace 为必填参数；固定命名空间让本次运行的会话共享文件。
            namespace=lambda _rt: ("aa",),
        ),
        system_prompt=(
            "你是一个助手，请根据用户输入的指令进行相应的操作，默认用中文回答。"
        )
    )

def run_chat(agent):
    """循环读取输入，仅将主模型的正文增量输出到控制台。"""
    config = {"configurable": {"thread_id": uuid4().hex}}
    print("StoreBackend 聊天已启动。输入 exit / quit / 退出结束，/new 开启新会话。")
    print("聊天按会话保存；虚拟文件在本次运行的所有会话间共享，退出程序后清空。\n")

    try:
        while True:
            user_input = input("你：").strip()
            if not user_input:
                continue
            if user_input.lower() in {"exit", "quit", "/exit", "/quit", "退出"}:
                break
            if user_input.lower() == "/new":
                config = {"configurable": {"thread_id": uuid4().hex}}
                print("已开启新会话。\n")
                continue

            print("助手：", end="", flush=True)
            try:
                # 每轮只提交新消息，历史由相同 thread_id 的检查点自动恢复。
                with closing(agent.stream(
                    {"messages": [{"role": "user", "content": user_input}]},
                    config=config,
                    stream_mode="messages",
                )) as stream:
                    for message, metadata in stream:
                        # messages 流还可能包含工具结果及中间件消息。
                        if isinstance(message, AIMessage) and metadata.get("langgraph_node") == "model":
                            if message.text:
                                print(message.text, end="", flush=True)
                print("\n")
            except Exception as error:
                print(f"\n本轮调用失败：{error}")
                print("可继续输入；如需清除本轮状态，请输入 /new。\n")
    except (EOFError, KeyboardInterrupt):
        print()

    print("聊天已结束。")


def main() -> int:
    try:
        agent = create_chat_agent()
    except Exception as error:
        print(f"初始化失败：{error}", file=sys.stderr)
        return 1
    run_chat(agent)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# 虚拟文件系统 ls cat read_file write_file
