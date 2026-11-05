# 可视化参数参考：26 类型 / 7 组

调用 `generate_chart(chart_type, chart_config)`。下方 JSON 是“类型 → chart_config”示例索引，选中一项传参，不要把整份索引当参数。
`generate_spreadsheet` 独立调用，不属于此入口。字段区分大小写；数值用有限数字，时间/类别/名称用字符串；地图 `dataValue` 特别要求字符串。

## 选项边界

| 范围 | 可选字段/默认值 |
|---|---|
| 全部 | `width/height` 正数；普通图默认 600×400，地图 1600×1000 |
| 普通图（排除地图及下方 5 种结构图） | `title`；`theme`: `default/academy/dark`；`style.backgroundColor/texture`，texture 为 `default/rough` |
| organization、mind_map、fishbone_diagram、flow_diagram、network_graph | `theme` 同上；`style` **仅 texture**；**不支持 title/坐标轴**，主题写入根节点名 |
| district_map、pin_map、path_map | `title` 必填，1–16 字；**无 theme/顶层 style/坐标轴** |
| 普通图配色 | `style.palette:[颜色,…]`；例外：liquid 用 `style.color`；waterfall 用对象 `{positiveColor,negativeColor,totalColor}` |
| area、bar、boxplot、column、histogram、line、scatter、violin、waterfall | `axisXTitle/axisYTitle` |
| dual_axes | 顶层仅 `axisXTitle`；Y 轴标题在每个 `series[].axisYTitle` |
| line、area、radar | `style.lineWidth` |
| line、boxplot、violin、dual_axes | `style.startAtZero`，默认 false |

数据数组不能为空；节点图的 `edges` 可为空。不要添加未列出的字段，封装会返回字段路径错误，不会把拼错选项静默传给 MCP。
当前远端的 5 种结构图优先用 `default/academy`；若 dark 命中已知渲染错误，封装只重试一次 default，并在结果中说明主题变化，不修改数据。

## 1. 比较与构成（5）

bar 横向，column 纵向；pie 占比，`innerRadius:0.6` 可做环形；funnel 阶段递减；waterfall 增减累计。
bar/column 的 `group/stack` 互斥；启用任一时，每行均需非空 `group` 字符串。无分组数据时封装设置两项 false；显式开启一项会把未填写的另一项设 false。分组数据未选模式则保留服务默认：bar 堆叠、column 分组。
waterfall 普通行必须有 `value`；小计 `isIntermediateTotal:true`、总计 `isTotal:true` 可省略 value，两标志不可同时为 true。

```json
{
  "bar": {"data":[{"category":"甲","value":12},{"category":"乙","value":18}]},
  "column": {"data":[{"category":"甲","value":12},{"category":"乙","value":18}]},
  "pie": {"data":[{"category":"甲","value":12},{"category":"乙","value":18}]},
  "funnel": {"data":[{"category":"访问","value":100},{"category":"询价","value":60},{"category":"下单","value":30}]},
  "waterfall": {"data":[{"category":"期初","value":100},{"category":"增加","value":30},{"category":"减少","value":-10},{"category":"合计","isTotal":true}]}
}
```

## 2. 趋势（3）

line/area 可按每行 `group` 分系列；area 的 `stack:true` 要求所有行有 group。双轴用 categories/series，**没有顶层 data**；每条 series.data 与 categories 等长。`series[].axisYTitle` 同时充当系列标识：省略/空白时补“系列 N”，重名时追加序号，避免远端崩溃或错误合并数据。最好填写不同的业务指标名。line 序列也可表示价格等绝对值，不限于小于 1 的比率。

```json
{
  "line": {"data":[{"time":"2026-01","value":12},{"time":"2026-02","value":18},{"time":"2026-03","value":9}]},
  "area": {"data":[{"time":"2026-01","value":12},{"time":"2026-02","value":18},{"time":"2026-03","value":9}]},
  "dual_axes": {"categories":["一月","二月","三月"],"series":[{"type":"column","data":[100,120,140],"axisYTitle":"数量"},{"type":"line","data":[0.1,0.12,0.15],"axisYTitle":"比率"}]}
}
```

## 3. 分布与相关（4）

boxplot/violin 输入原始样本，不是均值或四分位数；各类别提供多条记录，可按 group 分组。histogram 是纯数字数组，`binNumber` 为正整数。scatter 是 x/y 数值对，可分 group。使用 group 时所有行都应填写。

```json
{
  "boxplot": {"data":[{"category":"甲","value":8},{"category":"甲","value":10},{"category":"甲","value":12},{"category":"甲","value":15}]},
  "violin": {"data":[{"category":"甲","value":8},{"category":"甲","value":10},{"category":"甲","value":12},{"category":"甲","value":15}]},
  "histogram": {"data":[8,10,11,11,12,13,15,16,17,20,22,25]},
  "scatter": {"data":[{"x":1,"y":2},{"x":2,"y":5},{"x":3,"y":4},{"x":4,"y":8}]}
}
```

## 4. 指标（3）

radar 用 name/value，可分 group，建议至少四个维度；word_cloud 用 text/value 权重。liquid **只需 percent，不用 data**；percent 在 0–1，75% 写 0.75；shape 为 `circle/rect/pin/triangle`，默认 circle。

```json
{
  "radar": {"data":[{"name":"价格","value":70},{"name":"质量","value":90},{"name":"交期","value":85},{"name":"服务","value":80}]},
  "liquid": {"percent":0.75},
  "word_cloud": {"data":[{"text":"采购","value":20},{"text":"质量","value":15},{"text":"交期","value":10},{"text":"成本","value":8}]}
}
```

## 5. 层级（4）

treemap 的 data 是**树数组**，每层节点必须有 name/value，最多 3 层；兼容旧文档的单根对象会自动包成数组。其余三种 data 是**单根对象**，每层需 name；schema 允许根 + 3 层子节点（共 4 层），不能继续嵌套。organization 每层可加 description；orient 为 `vertical/horizontal`，默认 vertical。

```json
{
  "treemap": {"data":[{"name":"电子","value":100,"children":[{"name":"芯片","value":60},{"name":"电阻","value":40}]}]},
  "organization": {"data":{"name":"采购","children":[{"name":"供应商"},{"name":"物料"}]}},
  "mind_map": {"data":{"name":"采购","children":[{"name":"供应商"},{"name":"物料"}]}},
  "fishbone_diagram": {"data":{"name":"采购","children":[{"name":"供应商"},{"name":"物料"}]}}
}
```

## 6. 关系（4）

sankey 用有向流量，`nodeAlign:left/right/justify/center`，默认 center。venn 的 sets 标识集合，`["A","B"]` 是交集，label 可选。flow_diagram/network_graph 用 nodes/edges；节点 name 唯一，边 source/target 必须对应已有节点名，边标签 name 可选。

```json
{
  "sankey": {"data":[{"source":"采购","target":"仓库甲","value":60},{"source":"采购","target":"仓库乙","value":40},{"source":"仓库甲","target":"产线","value":60}]},
  "venn": {"data":[{"sets":["A"],"value":20,"label":"甲"},{"sets":["B"],"value":15,"label":"乙"},{"sets":["A","B"],"value":5,"label":"交集"}]},
  "flow_diagram": {"data":{"nodes":[{"name":"申请"},{"name":"审批"},{"name":"下单"}],"edges":[{"source":"申请","target":"审批","name":"提交"},{"source":"审批","target":"下单","name":"通过"}]}},
  "network_graph": {"data":{"nodes":[{"name":"供应商"},{"name":"仓库"},{"name":"工厂"}],"edges":[{"source":"供应商","target":"仓库"},{"source":"仓库","target":"工厂"}]}}
}
```

## 7. 地图（3）

使用中国行政区全称/具体中文 POI 名，不传经纬度。
- district_map：data.name 为中国/省/市/区县；`data.style.fillColor` 为底色。下级区域 `subdistricts:[{name,dataValue?,style?:{fillColor}}]` 存在时必须 `showAllSubdistricts:true`，省略会自动补齐。数值分布 `dataType:"number"` 配一个 colors 基色；分类分布 `dataType:"enum"` 配各类别 colors；`dataLabel/dataValue/dataValueUnit` 均为字符串。
- pin_map：data 是 POI 名数组；可选 `markerPopup:{type:"image",width:40,height:40,borderRadius:8}`。
- path_map：data 是路线数组，每条路线为 `{data:[起点,途经点,终点]}`。

```json
{
  "district_map": {"title":"浙江省行政区划","data":{"name":"浙江省","showAllSubdistricts":true}},
  "pin_map": {"title":"杭州仓储位置","data":["杭州西湖","杭州东站"]},
  "path_map": {"title":"杭州配送路线","data":[{"data":["杭州东站","杭州西湖","杭州火车南站"]}]}
}
```

## 返回与纠错

保留 MCP 原始文本/图片内容与结构化 artifact；使用实际返回的图片 URL。字段错误会以 `status:error` 返回，按路径修正；网络或远端渲染异常不能视作生图成功。不要把普通统计图的 theme/style/title 批量套到地图和结构图。
