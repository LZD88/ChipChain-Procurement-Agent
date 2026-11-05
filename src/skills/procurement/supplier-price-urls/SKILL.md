---
name: supplier-price-urls
description: >
  供应商芯片零部件报价网址映射表。根据 ERP 系统查到的芯片零部件名和供应商名，
  查找对应的报价页面 URL，供后续爬虫技能获取最新价格。
  当需要获取外部报价数据时优先使用此技能。
---

# 供应商芯片零部件报价网址映射

## 用途

提供「零部件名称 + 供应商名称 → 报价页面 URL」的映射查询，
是外部数据采集的第一步。

## 数据
映射数据在 `data/url_mapping.yaml`。

```yaml
mappings:
  - part_name: "零部件名称（含品牌、型号、封装等描述）"
    supplier: "供应商名称"
    url: "报价页面URL"
```

> URL 指向的页面包含完整产品信息：当前售价、制造商料号、规格参数、温度等级、标准交期等。
> 爬虫技能可一次性提取全部数据，无需多次请求。

## 使用流程

1. 用 `read_file` 读取 `data/url_mapping.yaml`
2. 解析 YAML，按 MCP 工具返回的零部件名 + 供应商名查找匹配条目
3. 匹配逻辑：
   - `part_name` 包含匹配（ERP 返回的零件名包含在映射的 part_name 中，或反之）
   - `supplier` 包含匹配（ERP 返回的供应商名包含在映射的 supplier 中，或反之）
   - 两个字段同时匹配才算命中
4. 将找到的 URL 列表传给爬虫技能（如 `web-scraper`）获取最新报价和产品详情

## 示例

```
ERP 返回: part_name="32位通用微控制器", supplier="教学供应商·晶穹一号"
→ 查 url_mapping.yaml
→ 命中: part_name 包含 "32位通用微控制器" + supplier 包含 "晶穹一号"
  → "32位通用微控制器"
    supplier="教学供应商·晶穹一号"
    url="http://host.docker.internal:8082/quotes/LAB-MCU-01-01.html"
→ 传给 web-scraper 爬取该 URL → 获取价格+制造商料号+规格参数+温度等级+交期
```

## 注意事项

- 映射表由运维人员维护，Agent 只读不写
- 如未找到匹配条目 → 改用 `web_search` 搜索市场价格
- 零部件名含完整规格描述（品牌、型号、封装、温度等级），匹配时用包含关系而非精确匹配
- 同一零部件可能有多个供应商的映射条目，全部收集后交给爬虫逐一抓取
