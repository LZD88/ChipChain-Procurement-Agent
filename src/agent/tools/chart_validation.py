"""以服务端 schema 校验图表参数，并补足 schema 未表达的跨字段约束。"""

from __future__ import annotations

from copy import deepcopy
import math

from jsonschema import Draft7Validator
from langchain_core.tools import ToolException


def _closed_schema(schema):
    """拒绝会被服务端静默丢弃的拼错字段，包括超出树深度的 children。"""
    if isinstance(schema, list):
        return [_closed_schema(value) for value in schema]
    if not isinstance(schema, dict):
        return schema
    result = {key: _closed_schema(value) for key, value in schema.items()}
    if "properties" in result:
        result.setdefault("additionalProperties", False)
    return result


def _check_finite(value, path="chart_config"):
    if isinstance(value, float) and not math.isfinite(value):
        raise ToolException(f"{path}: number must be finite")
    if isinstance(value, dict):
        for key, item in value.items():
            _check_finite(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _check_finite(item, f"{path}[{index}]")


def _require(condition, message):
    if not condition:
        raise ToolException(message)


class ChartConfigValidator:
    def __init__(self, chart_type, schema):
        self.chart_type = chart_type
        if not isinstance(schema, dict):
            schema = schema.model_json_schema()
        schema = _closed_schema(schema)
        Draft7Validator.check_schema(schema)
        self.validator = Draft7Validator(schema)

    def prepare(self, config: dict) -> dict:
        config = deepcopy(config)
        kind = self.chart_type
        # 兼容旧参考文件误写的 treemap 单根对象，不改变树的内容。
        if kind == "treemap" and isinstance(config.get("data"), dict):
            config["data"] = [config["data"]]
        _check_finite(config)
        error = next(self.validator.iter_errors(config), None)
        if error is not None:
            path = "chart_config" + "".join(f"[{part}]" if isinstance(part, int) else f".{part}" for part in error.path)
            raise ToolException(f"{kind} {path}: {error.message[:350]}")

        for dimension in ("width", "height"):
            if dimension in config:
                _require(config[dimension] > 0, f"{dimension} must be positive")
        data = config.get("data")
        if kind in {"bar", "column", "area", "line", "boxplot", "violin", "scatter", "radar"}:
            groups = [bool(row.get("group")) for row in data]
            _require(not any(groups) or all(groups), "data[].group must be present and nonempty on every grouped row")
            if kind in {"bar", "column"}:
                _require(not (config.get("group") and config.get("stack")), "group and stack cannot both be true")
                if config.get("group"):
                    config.setdefault("stack", False)
                if config.get("stack"):
                    config.setdefault("group", False)
                if not any(groups):
                    _require(not config.get("group") and not config.get("stack"), "group/stack requires data[].group")
                    # 原始默认值 bar.stack=true / column.group=true 不适用于普通单序列。
                    config.setdefault("group", False)
                    config.setdefault("stack", False)
            if kind == "area" and config.get("stack"):
                _require(all(groups), "stack requires data[].group on every row")
        if kind == "dual_axes":
            _require(all(len(series["data"]) == len(config["categories"]) for series in config["series"]),
                     "series[].data length must equal categories length")
            # 渲染端也把轴标题当作系列标识；空值/重名会崩溃或错误合并数据。
            used_titles = set()
            for index, series in enumerate(config["series"], 1):
                title = series.get("axisYTitle", "")
                title = title if title.strip() else f"系列 {index}"
                unique_title, suffix = title, 2
                while unique_title in used_titles:
                    unique_title = f"{title} ({suffix})"
                    suffix += 1
                series["axisYTitle"] = unique_title
                used_titles.add(unique_title)
        if kind == "histogram" and "binNumber" in config:
            bins = config["binNumber"]
            _require(bins > 0 and int(bins) == bins, "binNumber must be a positive integer")
        if kind == "pie" and "innerRadius" in config:
            _require(0 <= config["innerRadius"] <= 1, "innerRadius must be between 0 and 1")
        if kind == "waterfall":
            for index, row in enumerate(data):
                subtotal, total = row.get("isIntermediateTotal"), row.get("isTotal")
                _require(not (subtotal and total), f"data[{index}]: choose either isIntermediateTotal or isTotal")
                _require(subtotal or total or "value" in row, f"data[{index}].value is required for non-total rows")
        if kind in {"flow_diagram", "network_graph"}:
            nodes = [node["name"] for node in data["nodes"]]
            _require(len(nodes) == len(set(nodes)), "data.nodes[].name must be unique")
            _require(all(edge["source"] in nodes and edge["target"] in nodes for edge in data["edges"]),
                     "data.edges source/target must reference existing node names")
        if kind in {"district_map", "pin_map", "path_map"}:
            _require(0 < len(config["title"].strip()) <= 16, "map title must contain 1 to 16 characters")
        if kind == "district_map" and data.get("subdistricts"):
            _require(data.get("showAllSubdistricts", True), "subdistricts requires showAllSubdistricts=true")
            data.setdefault("showAllSubdistricts", True)
        if kind == "pin_map" and "markerPopup" in config:
            _require(config["markerPopup"].get("type", "image") == "image", "markerPopup.type must be image")
        return config
