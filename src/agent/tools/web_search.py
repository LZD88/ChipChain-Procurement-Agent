"""
自定义网络搜索工具。

基于博查 Web Search API，供主 Agent 和所有子 Agent 使用。
"""

import requests
from langchain_core.tools import tool

from agent.env_utils import BOCHA_API_KEY


@tool("web_search", parse_docstring=True)
def web_search(
    query: str,
    freshness: str = "noLimit",
    count: int = 5,
    include: str = "",
    exclude: str = "",
) -> str:
    """使用博查 Web Search 搜索全网信息，返回包含来源链接的 Markdown 文本。

    适用于：市场行情调研、供应商背景调查、物料价格趋势查询、行业资讯获取等。

    Args:
        query: 搜索关键词或自然语言问题。
        freshness: 时间范围：noLimit、oneDay、oneWeek、oneMonth、oneYear，
            也可用 YYYY-MM-DD 或 YYYY-MM-DD..YYYY-MM-DD。
        count: 返回的网页数量，1 到 50，默认 5。
        include: 只搜索这些网站域名，多个域名用逗号分隔；留空表示不限。
        exclude: 排除这些网站域名，多个域名用逗号分隔；留空表示不排除。

    Returns:
        搜索结果字符串，每条包含标题、简介、链接、网站及发布时间。
    """
    if not query.strip():
        return "搜索查询不能为空。"
    if not 1 <= count <= 50:
        return "搜索结果数量 count 必须在 1 到 50 之间。"

    api_key = BOCHA_API_KEY
    if not api_key:
        return "未配置 BOCHA_API_KEY，请在环境变量或项目根目录 .env 中设置。"

    payload = {"query": query.strip(), "summary": True, "freshness": freshness, "count": count}
    if include:
        payload["include"] = include
    if exclude:
        payload["exclude"] = exclude
    try:
        response = requests.post(
            "https://api.bocha.cn/v1/web-search",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()
    except ValueError:
        return "博查搜索返回了无法解析的 JSON。"
    except requests.RequestException as exc:
        return f"博查搜索请求失败：{exc}".replace(api_key, "[REDACTED]")

    if not isinstance(result, dict):
        return "博查搜索返回的数据格式异常。"
    if result.get("code") not in (200, "200"):
        message = result.get("msg") or result.get("message") or "未知错误"
        return f"博查搜索失败（{result.get('code')}）：{message}".replace(api_key, "[REDACTED]")
    data = result.get("data") or {}
    if not isinstance(data, dict):
        return "博查搜索返回的数据格式异常。"
    web_pages = data.get("webPages") or {}
    if not isinstance(web_pages, dict):
        return "博查搜索返回的网页列表格式异常。"
    pages = web_pages.get("value") or []
    if not isinstance(pages, list):
        return "博查搜索返回的网页列表格式异常。"

    results = []
    for page in pages[:count]:
        if not isinstance(page, dict):
            continue
        results.append(
            f"标题：{page.get('name') or '无标题'}\n"
            f"简介：{page.get('summary') or page.get('snippet') or '无简介'}\n"
            f"链接：{page.get('url') or '无链接'}\n"
            f"网站：{page.get('siteName') or '未知网站'}\n"
            f"发布时间：{page.get('datePublished') or '未知'}"
        )
    return "\n\n".join(results) if results else "未找到相关网页，请调整查询词或搜索范围。"
