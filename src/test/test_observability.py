from types import SimpleNamespace

from prometheus_client import generate_latest

from api_view.observability import FIRST_TOKEN, REQUEST_DURATION, StreamObservation


def test_stream_observation_records_token_usage_and_cost(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_INPUT_USD_PER_M_TOKENS", "1")
    monkeypatch.setenv("DEEPSEEK_OUTPUT_USD_PER_M_TOKENS", "2")
    observation = StreamObservation("test").start()
    token = SimpleNamespace(
        usage_metadata={"input_tokens": 100, "output_tokens": 25},
        response_metadata={"model_name": "deepseek-test"},
    )
    observation.token(token, "测试")
    observation.finish("success")
    metrics = generate_latest().decode()
    assert 'agent_requests_total{endpoint="test",status="success"}' in metrics
    assert 'agent_llm_input_tokens_total{model="deepseek-test"}' in metrics
    assert 'agent_llm_output_tokens_total{model="deepseek-test"}' in metrics


def test_latency_histograms_cover_slow_agent_requests():
    from prometheus_client import CollectorRegistry, Histogram

    # 覆盖超过旧 10 秒上限的样本；90 秒请求不能全部落入 +Inf 桶。
    for metric in (REQUEST_DURATION, FIRST_TOKEN):
        registry = CollectorRegistry()
        histogram = Histogram("latency_test", "test", buckets=metric._upper_bounds[:-1], registry=registry)
        histogram.observe(90)
        samples = {sample.labels["le"]: sample.value for family in registry.collect()
                   for sample in family.samples if sample.name.endswith("_bucket")}
        assert samples["60.0"] == 0
        assert samples["120.0"] == 1
        assert samples["600.0"] == 1
