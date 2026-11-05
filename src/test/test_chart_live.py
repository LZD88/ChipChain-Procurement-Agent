"""全类型、多场景真实生图验证；默认使用项目配置的 MCP 服务，数据均为测试样例。

python -B src/test/test_chart_live.py --output /tmp/chart-live
受限环境可 prepare-curl → curl --parallel --config requests.curl → verify-curl，
再下载 images.curl 并 verify-images。两阶段使用相同入口及原始 MCP 适配器；
校验时必须逐项匹配实际发出的参数，禁止把替换响应当成生图成功。
"""

import argparse
import asyncio
import json
from pathlib import Path
import re
import sys

from langchain_mcp_adapters.client import MultiServerMCPClient
from mcp.types import CallToolResult, TextContent

from chart_test_support import SCENARIOS, SCHEMAS, invoke, make_visualization
from agent.env_utils import ANALYSIS_MCP_URL
from agent.tools.chart_generator import create_unified_chart_tool


class PendingResponse(BaseException):
    """分阶段网络测试产生了新请求；尚无响应，不能算作成功或失败。"""


def read_rpc(path):
    text = path.read_text()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        events = [json.loads(line.removeprefix('data:').strip()) for line in text.splitlines() if line.startswith('data:')]
        return next(event for event in reversed(events) if 'result' in event or 'error' in event)


def write_config(path, blocks):
    path.write_text('\nnext\n'.join('\n'.join(block) for block in blocks) + '\n')
    path.chmod(0o600)


def response_urls(result):
    texts = [result.content] if isinstance(result.content, str) else [block.get('text', '') for block in result.content if isinstance(block, dict)]
    return list(dict.fromkeys(url for text in texts for url in re.findall(r'https?://[^\s<>"\)]+', text)))


class CaptureSession:
    def __init__(self, directory, case, replay=False):
        self.directory, self.case, self.replay = directory, case, replay
        self.attempt = 0

    async def call_tool(self, name, arguments, **kwargs):
        self.attempt += 1
        suffix = '' if self.attempt == 1 else f'.retry{self.attempt - 1}'
        request = {'jsonrpc': '2.0', 'id': self.case['id'], 'method': 'tools/call', 'params': {'name': name, 'arguments': arguments}}
        path = self.directory / (self.case['id'] + suffix + '.request.json')
        response_path = self.directory / (self.case['id'] + suffix + '.response.txt')
        if not self.replay:
            path.write_text(json.dumps(request, ensure_ascii=False))
            return CallToolResult(content=[TextContent(type='text', text='Request prepared')])
        if not path.exists() or json.loads(path.read_text()) != request:
            if path.exists(): path.replace(path.with_suffix('.prior.json'))
            if response_path.exists(): response_path.replace(response_path.with_suffix('.prior.txt'))
            path.write_text(json.dumps(request, ensure_ascii=False))
        if not response_path.exists(): raise PendingResponse()
        response = read_rpc(response_path)
        if response.get('id') != self.case['id']:
            raise AssertionError('Mismatched MCP response ID')
        if 'error' in response:
            raise RuntimeError(response['error'].get('message', 'MCP protocol error'))
        return CallToolResult.model_validate(response['result'])


async def prepare_curl(output, session_config, selected):
    base = [line for line in session_config.read_text().splitlines() if line.startswith(('url =', 'header ='))]
    if not any(line.startswith('url =') for line in base):
        raise ValueError('session config requires an initialized MCP URL')
    blocks = []
    for case in selected:
        result = await invoke(make_visualization(CaptureSession(output, case)), case)
        if result.status != 'success':
            raise AssertionError(f"{case['id']}: {result.content}")
        blocks.append(base + [
            'request = "POST"', 'silent', 'show-error', 'connect-timeout = 15', 'max-time = 90',
            'data-binary = ' + json.dumps('@' + str(output / (case['id'] + '.request.json'))),
            'output = ' + json.dumps(str(output / (case['id'] + '.response.txt'))),
        ])
    write_config(output / 'requests.curl', blocks)
    print(f'Prepared {len(selected)} validated MCP requests in {output}')


async def collect_results(output, selected, tool_factory):
    previous_path = output / 'results.json'
    previous = {row['id']: row for row in json.loads(previous_path.read_text())} if previous_path.exists() else {}
    records = []
    for case in selected:
        try:
            tool = await tool_factory(case)
            result = await invoke(tool, case)
            urls = response_urls(result)
            image_path = output / (case['id'] + '.image')
            if image_path.exists() and previous.get(case['id'], {}).get('urls') != urls:
                image_path.unlink()  # 响应变化后必须重取图片，不能验证上一轮的缓存。
            success = result.status == 'success' and bool(urls)
            record = {'id': case['id'], 'chart_type': case['chart_type'], 'status': 'passed' if success else 'failed', 'urls': urls}
            if '兼容提示' in str(result.content): record['compatibility_fallback'] = 'dark -> default'
            if not success:
                record['error'] = str(result.content)[:600]
        except PendingResponse:
            record = {'id': case['id'], 'chart_type': case['chart_type'], 'status': 'pending'}
        except Exception as exc:
            record = {'id': case['id'], 'chart_type': case['chart_type'], 'status': 'failed', 'error': type(exc).__name__}
        records.append(record)
        print(f"{record['status']}: {case['id']}", flush=True)
    (output / 'results.json').write_text(json.dumps(records, ensure_ascii=False, indent=2) + '\n')
    blocks = []
    for record in records:
        if record['status'] != 'passed': continue
        blocks.append([
            'url = ' + json.dumps(record['urls'][0]), 'silent', 'show-error', 'fail', 'location',
            'connect-timeout = 15', 'max-time = 60',
            'output = ' + json.dumps(str(output / (record['id'] + '.image'))),
        ])
    if blocks: write_config(output / 'images.curl', blocks)
    failed = [r['id'] for r in records if r['status'] != 'passed']
    print(f"Rendering: {len(records)-len(failed)}/{len(records)} passed")
    return bool(failed)


def prepare_pending(output, session_config):
    pending = [path for path in output.glob('*.request.json') if not path.with_name(path.name.replace('.request.json', '.response.txt')).exists()]
    if not pending: return
    if session_config is None: raise ValueError('New requests pending; provide --session-config to create pending.curl')
    base = [line for line in session_config.read_text().splitlines() if line.startswith(('url =', 'header ='))]
    write_config(output / 'pending.curl', [base + [
        'request = "POST"', 'silent', 'show-error', 'connect-timeout = 15', 'max-time = 90',
        'data-binary = ' + json.dumps('@' + str(path)),
        'output = ' + json.dumps(str(path.with_name(path.name.replace('.request.json', '.response.txt')))),
    ] for path in pending])
    print(f'{len(pending)} requests need execution: curl --parallel --parallel-max 3 --config {output / "pending.curl"}')


def verify_images(output):
    from PIL import Image, ImageStat
    records = json.loads((output / 'results.json').read_text())
    cases = {case['id']: case for case in SCENARIOS}
    for record in records:
        if record['status'] != 'passed': continue
        try:
            path = output / (record['id'] + '.image')
            with Image.open(path) as image:
                image.load()
                width, height = image.size
                if width < 50 or height < 50:
                    raise ValueError('Image too small')
                if max(ImageStat.Stat(image.convert('RGB')).stddev) < 1:
                    raise ValueError('Image appears blank')
                config = cases[record['id']]['chart_config']
                scales = [actual / config[dimension] for dimension, actual in [('width', width), ('height', height)] if dimension in config]
                # 远端使用高分屏渲染；width/height 是逻辑尺寸，PNG 可统一放大。
                if scales and (any(scale not in (1, 2, 3, 4) for scale in scales) or len(set(scales)) != 1):
                    raise ValueError(f'Unexpected pixel scaling: {scales}')
                record['image'] = {'format': image.format, 'width': width, 'height': height, 'bytes': path.stat().st_size}
        except Exception as exc:
            record['status'] = 'failed'
            record['error'] = f'Image validation: {exc}'
    (output / 'results.json').write_text(json.dumps(records, ensure_ascii=False, indent=2) + '\n')
    failed = [r for r in records if r['status'] != 'passed']
    print(f"Images: {len(records)-len(failed)}/{len(records)} passed")
    for r in failed: print(r['id'], r.get('error'))
    return bool(failed)


async def download_images(output):
    import httpx

    records = json.loads((output / 'results.json').read_text())
    semaphore = asyncio.Semaphore(4)
    async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
        async def download(record):
            if record['status'] != 'passed': return
            async with semaphore:
                try:
                    response = await client.get(record['urls'][0])
                    response.raise_for_status()
                    (output / (record['id'] + '.image')).write_bytes(response.content)
                except Exception as exc:
                    print(f"Image download failed: {record['id']} ({type(exc).__name__})")
        await asyncio.gather(*(download(record) for record in records))


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['live', 'prepare-curl', 'verify-curl', 'verify-images'], default='live')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--session-config', type=Path)
    parser.add_argument('--case', action='append', help='只运行指定场景 ID；可重复')
    args = parser.parse_args()
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=True)
    selected = [case for case in SCENARIOS if not args.case or case['id'] in args.case]
    if not selected: raise ValueError('No matching scenarios')
    if args.mode == 'prepare-curl':
        await prepare_curl(output, args.session_config, selected)
        return 0
    if args.mode == 'verify-images': return int(verify_images(output))
    if args.mode == 'verify-curl':
        async def factory(case): return make_visualization(CaptureSession(output, case, replay=True))
        failed = await collect_results(output, selected, factory)
        prepare_pending(output, args.session_config)
        return int(failed)
    if not ANALYSIS_MCP_URL: raise ValueError('ANALYSIS_MCP_URL is not configured')
    client = MultiServerMCPClient({'analysis': {'url': ANALYSIS_MCP_URL, 'transport': 'streamable_http'}})
    tools = await client.get_tools(server_name='analysis')
    expected = {item['name']: item['inputSchema'] for item in SCHEMAS}
    actual = {tool.name: tool.args_schema for tool in tools}
    if actual != expected: raise AssertionError('MCP schemas changed; refresh and review schema fixtures before testing')
    tool, _ = create_unified_chart_tool(tools)
    async def factory(case): return tool
    await collect_results(output, selected, factory)
    await download_images(output)
    return int(verify_images(output))


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
