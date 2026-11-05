"""离线契约测试：真实 MCP 适配器 + 服务端 schema 快照，不请求远端。"""

import asyncio
from copy import deepcopy
import json
import math
from pathlib import Path
import re
import unittest

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.tools import StructuredTool
from mcp.types import CallToolResult, ImageContent, TextContent
from pydantic import BaseModel

from chart_test_support import SCENARIOS, SCHEMAS, invoke, make_tools, make_visualization
from agent.tools.chart_generator import CHART_GROUPS, _COMPACT_TABLE, _TOOL_NAMES, create_unified_chart_tool
from agent.tools.chart_validation import ChartConfigValidator


class RecordingSession:
    def __init__(self):
        self.calls = []
        self.response = CallToolResult(
            content=[TextContent(type='text', text='https://example.test/chart.png')],
            structuredContent={'image': 'https://example.test/chart.png', 'nested': {'preserved': True}},
        )
        self.error = None

    async def call_tool(self, name, arguments, **kwargs):
        self.calls.append((name, deepcopy(arguments)))
        if self.error:
            raise self.error
        return self.response


class ChartContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.session = RecordingSession()
        self.tool = make_visualization(self.session)

    async def call(self, kind, config):
        return await invoke(self.tool, {'id': 'case', 'chart_type': kind, 'chart_config': config})

    async def test_all_rendering_scenarios_route_and_preserve_content_artifact(self):
        for case in SCENARIOS:
            with self.subTest(case=case['id']):
                original = deepcopy(case)
                result = await invoke(self.tool, case)
                self.assertEqual(result.status, 'success', result.content)
                self.assertEqual(result.tool_call_id, case['id'])
                self.assertEqual(self.session.calls[-1][0], _TOOL_NAMES[case['chart_type']])
                self.assertEqual(len(result.content), 1)
                self.assertEqual(result.content[0]['type'], 'text')
                self.assertEqual(result.content[0]['text'], 'https://example.test/chart.png')
                self.assertEqual(result.artifact['structured_content'], self.session.response.structuredContent)
                self.assertEqual(case, original)
        self.assertEqual(len(self.session.calls), len(SCENARIOS))
        self.assertEqual({c['chart_type'] for c in SCENARIOS}, set(_TOOL_NAMES))

    async def test_group_defaults_are_safe_without_changing_explicit_intent(self):
        rows = [{'category': 'A', 'value': 1}]
        for kind in ('bar', 'column'):
            await self.call(kind, {'data': rows})
            self.assertEqual(self.session.calls[-1][1], {'data': rows, 'group': False, 'stack': False})
            grouped = [dict(rows[0], group='G')]
            await self.call(kind, {'data': grouped, 'group': True})
            self.assertFalse(self.session.calls[-1][1]['stack'])
            await self.call(kind, {'data': grouped, 'stack': True})
            self.assertFalse(self.session.calls[-1][1]['group'])
            await self.call(kind, {'data': grouped})
            self.assertEqual(self.session.calls[-1][1], {'data': grouped})

    async def test_invalid_configs_never_reach_mcp(self):
        cases = [
            ('bar', {}), ('bar', {'data': []}), ('bar', {'data': [{'category': 'A', 'value': '2'}]}),
            ('bar', {'data': [{'category': 'A', 'value': True}]}),
            ('bar', {'data': [{'category': 'A', 'value': 1}], 'group': True}),
            ('column', {'data': [{'category': 'A', 'value': 1}], 'stack': True}),
            ('bar', {'data': [{'category': 'A', 'value': 1, 'group': 'G'}], 'group': True, 'stack': True}),
            ('line', {'data': [{'time': 'A', 'value': 1, 'group': 'G'}, {'time': 'B', 'value': 2}]}),
            ('area', {'data': [{'time': 'A', 'value': 1}], 'stack': True}),
            ('pie', {'data': [{'category': 'A', 'value': 1}], 'innerRadius': 1.1}),
            ('histogram', {'data': [1, 2], 'binNumber': 0}),
            ('histogram', {'data': [1, 2], 'binNumber': 1.5}),
            ('liquid', {'percent': -0.1}), ('liquid', {'percent': 1.1}), ('liquid', {'data': [.5]}),
            ('liquid', {'percent': .5, 'shape': 'star'}), ('liquid', {'percent': .5, 'width': 0}),
            ('liquid', {'percent': .5, 'height': -1}), ('liquid', {'percent': .5, 'theme': 'unknown'}),
            ('liquid', {'percent': .5, 'style': {'palette': ['red']}}),
            ('dual_axes', {'categories': ['A', 'B'], 'series': [{'type': 'line', 'data': [1]}]}),
            ('waterfall', {'data': [{'category': 'A'}]}),
            ('waterfall', {'data': [{'category': 'A', 'isTotal': True, 'isIntermediateTotal': True}]}),
            ('waterfall', {'data': [{'category': 'A', 'value': 1}], 'style': {'palette': ['red']}}),
            ('mind_map', {'data': {'name': 'A'}, 'title': 'unsupported'}),
            ('organization', {'data': {'name': 'A'}, 'style': {'backgroundColor': '#fff'}}),
            ('flow_diagram', {'data': {'nodes': [{'name': 'A'}], 'edges': [{'source': 'A', 'target': 'B'}]}}),
            ('network_graph', {'data': {'nodes': [{'name': 'A'}, {'name': 'A'}], 'edges': []}}),
            ('pin_map', {'title': '位置', 'data': ['杭州'], 'theme': 'dark'}),
            ('pin_map', {'title': '位置', 'data': ['杭州'], 'markerPopup': {'type': 'video'}}),
            ('pin_map', {'title': '位置', 'data': []}),
            ('path_map', {'title': '路线', 'data': ['杭州', '宁波']}),
            ('district_map', {'title': '地图', 'data': {'name': '浙江省', 'showAllSubdistricts': False, 'subdistricts': [{'name': '杭州市'}]}}),
            ('district_map', {'title': '地图', 'data': {'name': '浙江省', 'subdistricts': [{'name': '杭州市', 'dataValue': 10}]}}),
            ('pin_map', {'title': '长' * 17, 'data': ['杭州']}),
            ('pin_map', {'title': ' ', 'data': ['杭州']}),
        ]
        for value in (math.nan, math.inf, -math.inf):
            cases.append(('bar', {'data': [{'category': 'A', 'value': value}]}))
        for kind, config in cases:
            with self.subTest(kind=kind, config=config):
                result = await self.call(kind, config)
                self.assertEqual(result.status, 'error', result.content)
                self.assertIsNone(result.artifact)
        self.assertEqual(self.session.calls, [])

    async def test_every_type_checks_its_required_fields_and_unknown_options(self):
        for case in SCENARIOS[:26]:
            kind = case['chart_type']
            schema = next(s['inputSchema'] for s in SCHEMAS if s['name'] == _TOOL_NAMES[kind])
            for field in schema['required']:
                with self.subTest(kind=kind, missing=field):
                    config = deepcopy(case['chart_config']); config.pop(field)
                    self.assertEqual((await self.call(kind, config)).status, 'error')
            config = dict(case['chart_config'], typoOption=True)
            with self.subTest(kind=kind, unknown='typoOption'):
                self.assertEqual((await self.call(kind, config)).status, 'error')
        self.assertEqual(self.session.calls, [])

    async def test_top_level_validation_is_recoverable(self):
        for args in (
            {'chart_type': 'wrong', 'chart_config': {}},
            {'chart_type': 'liquid', 'chart_config': 'percent=.5'},
            {'chart_type': 'liquid', 'chart_config': {'percent': .5}, 'unexpected': True},
            {'chart_config': {}},
        ):
            result = await self.tool.ainvoke({'type': 'tool_call', 'id': 'invalid', 'name': self.tool.name, 'args': args})
            self.assertEqual(result.status, 'error')
            self.assertEqual(result.tool_call_id, 'invalid')
        self.assertEqual(self.session.calls, [])

    async def test_remote_errors_and_timeouts_are_not_successes(self):
        self.session.response = CallToolResult(content=[TextContent(type='text', text='Renderer unavailable')], isError=True)
        result = await self.call('liquid', {'percent': .5})
        self.assertEqual(result.status, 'error')
        self.assertIn('Renderer unavailable', str(result.content))
        self.session.error = TimeoutError('https://secret-endpoint/?token=private')
        result = await self.call('liquid', {'percent': .5})
        self.assertEqual(result.status, 'error')
        self.assertIn('TimeoutError', result.content)
        self.assertNotIn('private', result.content)
        self.session.error = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.call('liquid', {'percent': .5})

    async def test_images_and_text_blocks_are_preserved(self):
        self.session.response = CallToolResult(content=[
            TextContent(type='text', text='chart'),
            ImageContent(type='image', data='aW1hZ2U=', mimeType='image/png'),
        ])
        result = await self.call('liquid', {'percent': .5})
        self.assertEqual(
            {key: result.content[1][key] for key in ('type', 'base64', 'mime_type')},
            {'type': 'image', 'base64': 'aW1hZ2U=', 'mime_type': 'image/png'},
        )

    async def test_known_dark_theme_failure_retries_once_and_discloses_fallback(self):
        class DarkFailureSession(RecordingSession):
            async def call_tool(self, name, arguments, **kwargs):
                if arguments.get('theme') == 'dark':
                    self.calls.append((name, deepcopy(arguments)))
                    raise RuntimeError("Cannot read properties of undefined (reading 'key')")
                return await super().call_tool(name, arguments, **kwargs)
        session = DarkFailureSession()
        tool = make_visualization(session)
        config = {'data': {'name': '采购', 'children': [{'name': '物料'}]}, 'theme': 'dark', 'style': {'texture': 'rough'}}
        result = await invoke(tool, {'id': 'fallback', 'chart_type': 'mind_map', 'chart_config': config})
        self.assertEqual(result.status, 'success')
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(session.calls[1][1], {**config, 'theme': 'default'})
        self.assertEqual(config['theme'], 'dark')
        self.assertIn('兼容提示', str(result.content))
        self.assertEqual(result.artifact['structured_content'], session.response.structuredContent)

        session.calls.clear()
        session.error = RuntimeError('Second render failed')
        failed = await invoke(tool, {'id': 'fallback-failed', 'chart_type': 'mind_map', 'chart_config': config})
        self.assertEqual(failed.status, 'error')
        self.assertEqual(len(session.calls), 2)

    async def test_working_dark_theme_and_unrelated_errors_do_not_retry(self):
        config = {'data': {'name': '采购'}, 'theme': 'dark'}
        result = await self.call('mind_map', config)
        self.assertEqual(result.status, 'success')
        self.assertEqual(len(self.session.calls), 1)
        self.assertEqual(self.session.calls[0][1]['theme'], 'dark')
        self.session.error = TimeoutError()
        self.assertEqual((await self.call('mind_map', config)).status, 'error')
        self.assertEqual(len(self.session.calls), 2)

    async def test_map_service_fault_is_reported_without_a_fake_image(self):
        self.session.error = RuntimeError("Cannot read properties of undefined (reading 'length')")
        result = await self.call('pin_map', {'title': '位置', 'data': ['西安钟楼']})
        self.assertEqual(result.status, 'error')
        self.assertIn('远端地图服务', result.content)
        self.assertNotIn('https://', result.content)
        self.assertEqual(len(self.session.calls), 1)

    async def test_dual_axes_supplies_distinct_series_titles_without_changing_data(self):
        config = {'categories': ['A'], 'series': [{'type': 'column', 'data': [100]}, {'type': 'line', 'data': [40], 'axisYTitle': '单价'}]}
        result = await self.call('dual_axes', config)
        self.assertEqual(result.status, 'success')
        actual = self.session.calls[-1][1]
        self.assertEqual(actual['series'][0]['axisYTitle'], '系列 1')
        self.assertEqual(actual['series'][1]['axisYTitle'], '单价')
        self.assertNotIn('axisYTitle', config['series'][0])
        for titles in [('', ''), ('数量', '数量'), ('数量', '数量', '数量 (2)')]:
            grouped = {'categories': ['A'], 'series': [
                {'type': 'line', 'data': [index + 10], 'axisYTitle': title}
                for index, title in enumerate(titles)
            ]}
            self.assertEqual((await self.call('dual_axes', grouped)).status, 'success')
            actual_series = self.session.calls[-1][1]['series']
            self.assertEqual(len({series['axisYTitle'] for series in actual_series}), len(titles))
            self.assertEqual([series['data'] for series in actual_series], [series['data'] for series in grouped['series']])

    async def test_callbacks_and_concurrent_calls_remain_isolated(self):
        class Recorder(AsyncCallbackHandler):
            def __init__(self): self.starts = []
            async def on_tool_start(self, serialized, input_str, **kwargs):
                self.starts.append((serialized['name'], kwargs.get('tags')))
        recorder = Recorder()
        results = await asyncio.gather(*[
            invoke(self.tool, case, config={'callbacks': [recorder], 'tags': ['chart-contract']})
            for case in SCENARIOS[:26]
        ])
        self.assertEqual({r.tool_call_id for r in results}, {c['id'] for c in SCENARIOS[:26]})
        self.assertEqual(len(self.session.calls), 26)
        self.assertTrue(all('chart-contract' in tags for _, tags in recorder.starts))
        self.assertEqual(len(recorder.starts), 52)

    async def test_tree_depth_is_not_silently_truncated(self):
        for kind, levels in [('treemap', 4), ('mind_map', 5), ('organization', 5), ('fishbone_diagram', 5)]:
            node = {'name': 'leaf', **({'value': 1} if kind == 'treemap' else {})}
            for _ in range(levels - 1):
                node = {'name': 'parent', 'children': [node], **({'value': 1} if kind == 'treemap' else {})}
            result = await self.call(kind, {'data': [node] if kind == 'treemap' else node})
            self.assertEqual(result.status, 'error')
        self.assertEqual(self.session.calls, [])

    def test_catalog_and_factory_are_complete(self):
        self.assertEqual(len(CHART_GROUPS), 7)
        kinds = [kind for group in CHART_GROUPS.values() for kind in group]
        self.assertEqual(len(kinds), len(set(kinds)))
        self.assertEqual(len(kinds), 26)
        original = deepcopy(SCHEMAS)
        raw = make_tools(self.session)
        tool, extra = create_unified_chart_tool(raw)
        self.assertEqual([item.name for item in extra], ['generate_spreadsheet'])
        self.assertIs(extra[0], raw[-1])
        self.assertEqual(set(tool.args['chart_type']['enum']), set(kinds))
        self.assertLess(len(_COMPACT_TABLE), 1600)
        self.assertEqual(SCHEMAS, original)
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            create_unified_chart_tool(raw + [raw[0]])
        with self.assertRaisesRegex(ValueError, 'did not provide'):
            create_unified_chart_tool([raw[-1]])
        partial, _ = create_unified_chart_tool([raw[0]])
        self.assertEqual(partial.args['chart_type']['const'], 'area')
        self.assertNotIn('Compare bar', partial.description)

    def test_pydantic_schema_and_new_server_tools(self):
        class Parameters(BaseModel):
            percent: float
        validator = ChartConfigValidator('liquid', Parameters)
        self.assertEqual(validator.prepare({'percent': .5}), {'percent': .5})
        async def future(**kwargs): return 'future'
        unknown = StructuredTool(name='generate_future_chart', description='future', args_schema={}, coroutine=future)
        _, extra = create_unified_chart_tool(make_tools(self.session) + [unknown])
        self.assertIs(extra[-1], unknown)

    async def test_reference_examples_are_executable_for_all_26_types(self):
        reference = Path(__file__).parents[1] / 'skills' / 'analyis_chart_params.md'
        documented = set()
        for block in re.findall(r'```json\n(.*?)\n```', reference.read_text(), flags=re.S):
            for kind, config in json.loads(block).items():
                with self.subTest(kind=kind):
                    self.assertNotIn(kind, documented)
                    documented.add(kind)
                    result = await self.call(kind, config)
                    self.assertEqual(result.status, 'success', result.content)
        self.assertEqual(documented, set(_TOOL_NAMES))


if __name__ == '__main__':
    unittest.main()
