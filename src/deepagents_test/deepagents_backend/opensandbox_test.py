import asyncio
import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

from code_interpreter import CodeInterpreter, SupportedLanguage
from opensandbox import Sandbox
from opensandbox.config import ConnectionConfig
from opensandbox.models import WriteEntry

# 加载项目根目录的 .env，读取沙箱 API Key（OpenSandbox 服务要求鉴权）。
load_dotenv(Path(__file__).resolve().parents[2] / ".env", encoding="utf-8")
SANDBOX_API_KEY = "2016"

async def main() -> None:
    config = ConnectionConfig(
        domain="http://localhost:8080",
        api_key=SANDBOX_API_KEY,
        # use_server_proxy=True,
        request_timeout=timedelta(seconds=60), # 请求超时时间，默认30秒
    )

    # 1. Create a sandbox
    sandbox = await Sandbox.create(
        "sandbox-registry.cn-zhangjiakou.cr.aliyuncs.com/opensandbox/code-interpreter:v1.0.2",
        entrypoint= ["/opt/opensandbox/code-interpreter.sh"],
        env={"PYTHON_VERSION": "3.11"},
        timeout=timedelta(minutes=10), # 自动终止时间箱运行超时时间，默认10分钟，超过时间会自动终止沙箱
        connection_config=config,
    )

    async with sandbox:

        # 2. Execute a shell command
        execution = await sandbox.commands.run("echo 'Hello OpenSandbox!'")
        print(execution.logs.stdout[0].text)

        # 3. Write a file
        await sandbox.files.write_files([
            WriteEntry(path="/tmp/hello.txt", data="Hello World", mode=644)
        ])

        # 4. Read a file
        content = await sandbox.files.read_file("/tmp/hello.txt")
        print(f"Content: {content}") # Content: Hello World

        # 5. Create a code interpreter
        interpreter = await CodeInterpreter.create(sandbox)

        # 6. 执行 Python 代码（单次执行：直接传 language）
        result = await interpreter.codes.run(
              """
                  import sys
                  print(sys.version)
                  result = 2 + 2
                  result
              """,
              language=SupportedLanguage.PYTHON,
        )

        print(result.result[0].text) # 4
        print(result.logs.stdout[0].text) # 3.11.14

    # 7. Cleanup the sandbox
    # await sandbox.kill()

if __name__ == "__main__":
    asyncio.run(main())