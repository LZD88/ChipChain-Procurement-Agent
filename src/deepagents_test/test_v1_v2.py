"""LangGraph v1 / v2 流式输出格式对比，可直接运行，无需 API Key。

这里的 version 是 stream / astream 的输出格式版本，不是模型版本。
create_agent() 返回的编译图也使用这套接口；这里用固定计算让输出更容易读。

配置                         v1                    v2（共同字段）
单模式                       data                  {"type": ..., "ns": ..., "data": ...}
多模式                       (mode, data)          同上
单模式 + subgraphs=True      (ns, data)            同上
多模式 + subgraphs=True      (ns, mode, data)      同上

注意：多模式指 stream_mode 传列表，哪怕列表里只有一个元素。
v2 的不同事件还可能有额外字段，如 values 事件中的 interrupts。
"""

import asyncio
from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class State(TypedDict):
    number: int


def add_one(state: State):
    return {"number": state["number"] + 1}


def build_demo_graph():
    # 子图只做一件事：输入 1，输出 2。
    child = StateGraph(State)
    child.add_node("add_one", add_one)
    child.add_edge(START, "add_one")
    child.add_edge("add_one", END)

    # 主图的 worker 节点就是这个子图，用于演示 ns 的作用。
    parent = StateGraph(State)
    parent.add_node("worker", child.compile())
    parent.add_edge(START, "worker")
    parent.add_edge("worker", END)
    return parent.compile()


async def main():
    graph = build_demo_graph()
    cases = [
        ("1. 单模式", "updates", False, "data"),
        ("2. 多模式", ["updates", "values"], False, "(mode, data)"),
        ("3. 单模式 + 子图", "updates", True, "(ns, data)"),
        ("4. 多模式 + 子图", ["updates", "values"], True, "(ns, mode, data)"),
    ]

    print("每次输入都是 {'number': 1}，计算结果都是 2；下面打印真实的流式事件。")
    print("updates = 节点返回的更新；values = 当前完整状态（包括初始状态）。")
    print("ns = 来源路径；() 表示主图，('worker:任务ID',) 表示子图。")
    print("任务 ID 每次执行都会变化，不影响业务结果。")
    print("v2 的 values 事件还带 interrupts=()，表示本例没有中断。")

    for title, stream_mode, subgraphs, v1_shape in cases:
        print(f"\n{'=' * 12} {title} {'=' * 12}")
        print(f"参数：stream_mode={stream_mode!r}, subgraphs={subgraphs}")

        print(f"\nv1：{v1_shape}")
        async for chunk in graph.astream(
            {"number": 1},
            stream_mode=stream_mode,
            subgraphs=subgraphs,
            version="v1",  # 当前默认值，显式写出便于对比。
        ):
            print(chunk)

        print("\nv2：字典，共同字段为 type（类型）、ns（来源）、data（数据）")
        async for part in graph.astream(
            {"number": 1},
            stream_mode=stream_mode,
            subgraphs=subgraphs,
            version="v2",
        ):
            print(part)
            # 无论上面是哪种配置，都使用相同的取值方式：
            # event_type = part["type"]
            # namespace = part["ns"]
            # data = part["data"]

    print("\n重点：v2 统一了事件外层结构；data 的具体内容仍由 stream_mode 决定。")
    print("如果改成 messages 模式，data 仍是 (message_chunk, metadata)。")


if __name__ == "__main__":
    asyncio.run(main())
