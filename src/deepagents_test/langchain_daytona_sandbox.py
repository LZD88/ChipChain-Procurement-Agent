import os

#sandbox 沙箱
from daytona import Daytona, DaytonaConfig
from deepagents import create_deep_agent
from dotenv import load_dotenv
from langchain_daytona import DaytonaSandbox
from langchain_openai import ChatOpenAI

from agent.env_utils import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL

load_dotenv()

# Define the configuration
config = DaytonaConfig(api_key="dtn_1fe6a82765e04565ec908f7b9745870357960c884e4d9943f8a612b4750d45f9")

# Initialize the Daytona client
daytona = Daytona(config)

# Create the Sandbox instance
sandbox = daytona.create()

#虚拟文件系统
backend = DaytonaSandbox(sandbox=sandbox)

llm = ChatOpenAI(
        api_key=DEEPSEEK_API_KEY,
        model="deepseek-flash",
        base_url=DEEPSEEK_BASE_URL
    )

agent = create_deep_agent(
    model=llm,
    backend=backend,
    system_prompt="""你是一个专业的Python编码助手，拥有在一个完全隔离的沙箱环境中执行命令和操作文件
    的能力，你可以使用`execute`工具运行任何shell命令，使用`write_file`创建和编辑文件，使用`read_file`查看文件内容，
    使用清晰、安全的代码回应用户的请求。"""
)

user_request = """请完成以下任务：
1. 在沙箱的/home/daytona/目录下，创建一个名为'hello_sandbox.py'的Python文件
2. 文件内容应打印'Hello from the secure sandbox!'以及当前工作目录
3. 运行这个Python脚本，并告诉我输出结果"""


result = agent.invoke(
    {
        "messages": [
            {
                "role": "user",
                "content": user_request,
            }
        ]
    }
)

print(result["messages"][-1].content)

# sandbox.delete()