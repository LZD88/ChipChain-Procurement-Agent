"""Cheap capability routing after the input guard; never an authorization grant.

Explicit rules decide first; unresolved questions can use a bounded classifier.
A routing mistake
cannot grant the restricted agent filesystem, execution or write tools.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Literal


@dataclass(frozen=True)
class RequestRoute:
    mode: Literal["lightweight", "sandbox"]
    reason: str


# Exact names, not prefixes: newly registered tools must be reviewed explicitly.
READ_ONLY_TOOLS = frozenset({
    "supplier_get", "supplier_query", "part_query", "part_search",
    "part_by_supplier", "inventory_query", "inventory_warning", "order_search_details",
})

_WORKSPACE = re.compile(
    r"执行|运行|代码|脚本|命令|终端|沙箱|文件|附件|上传|下载|导入|导出|保存|写入|"
    r"删除|清空|清理|安装|部署|爬取|抓取|浏览器|报表|报告|图表|绘图|画图|制图|"
    r"python|shell|powershell|bash|javascript|sql|excel|csv|xlsx|pdf|word|ppt|"
    r"```|https?://|[/\\][\w.-]+[/\\]|(?a:\brm\s+-|\b(?:sudo|curl|wget|subprocess|select)\b|"
    r"\b(?:eval|exec)\s*\(|\bpip\s+install|\bnpm\s+|\border_(?:create|update)\b)",
    re.IGNORECASE,
)
_WRITE = re.compile(
    r"下单|创建|新建|修改|更新|调整|变更|扣减|增加|减少|取消|作废|撤销|提交|"
    r"审批|批准|确认|付款|支付|发货|收货|入库|出库|采购|补货|记住|以后|偏好|"
    r"(?a:\b(create|update|delete|insert|drop|alter|approve|cancel|purchase|buy|pay)\b)",
    re.IGNORECASE,
)
_COMPLEX = re.compile(r"分析|比价|比较|对比|推荐|评估|筛选|预测|规划|方案|批量|自动|所有数据")
_CHAT = re.compile(
    r"(?:请问[，,\s]*)?(?:你好|您好|嗨|哈[喽啰]|早上好|下午好|晚上好|谢谢[你您]?|多谢|再见|"
    r"hi|hello|hey|thanks|thank you|bye|你是谁|你叫什么(?:名字)?|"
    r"(?:你(?:都)?(?:能|可以|会)(?:帮我|为我)?|(?:能|可以)(?:帮我|为我))"
    r"(?:做|干|提供)(?:些什么|什么|哪些)(?:事情|事|服务|帮助)?|"
    r"(?:你|你们)?(?:有(?:什么|哪些)|都有哪些)(?:功能|能力|用途)|"
    r"我叫什么(?:名字)?)(?:呢|呀|啊|吗)?",
    re.IGNORECASE,
)
_KNOWLEDGE = re.compile(
    r"^(?:请)?(?:解释|介绍|说明|科普|什么是|什么叫)|(?:是什么|是啥|是什么意思|有什么区别)[?？。!！]*$"
)
_READ_INTENT = re.compile(r"查询|查看|查一下|查下|查查|看一下|检查|列出|搜索|检索|有哪些|有多少|多少钱|多少|预警")
_DOMAIN = re.compile(r"库存|供应商|物料|零部件|订单|交期|单价|价格|合作状态")
_FOLLOWUP = re.compile(r"(?:请)?(?:继续|接着做|接着来|照上面做|按上面做|就这么办|按你说的做)[吧啊呀]?[!！?？。.\s]*")


def route_request(message: str, *, enabled: bool = True, resume: bool = False) -> RequestRoute:
    if resume:
        return RequestRoute("sandbox", "resume")
    if not enabled:
        return RequestRoute("sandbox", "disabled")
    text = unicodedata.normalize("NFKC", message or "").strip()
    if not text:
        return RequestRoute("sandbox", "empty")
    if len(text) > 4096:
        return RequestRoute("sandbox", "input_limit")
    if _FOLLOWUP.fullmatch(text):
        return RequestRoute("sandbox", "contextual")
    # Inspect the entire input before considering a model fallback; never
    # truncate a suffix which may contain a write or execution instruction.
    if _WORKSPACE.search(text):
        return RequestRoute("sandbox", "workspace")
    if _WRITE.search(text.replace("采购订单", "订单")):
        return RequestRoute("sandbox", "business_write")
    if _COMPLEX.search(text):
        return RequestRoute("sandbox", "complex")
    if len(text) > 300:
        return RequestRoute("sandbox", "uncertain")
    if _CHAT.fullmatch(text.rstrip("!！?？。~～. ")) or _KNOWLEDGE.search(text):
        return RequestRoute("lightweight", "simple_chat")
    if _READ_INTENT.search(text) and _DOMAIN.search(text):
        return RequestRoute("lightweight", "readonly")
    return RequestRoute("sandbox", "uncertain")
