# Evaluation run 10000000-0000-0000-0000-000000000001

- Status: **COMPLETED**
- Completion: **COMPLETE**
- Suite: `default-procurement@1`
- Execution mode: `rule-contract`
- Scenarios produced: **6**

## Metrics

| Metric | Value | Numerator | Denominator | Excluded | Unit |
| --- | ---: | ---: | ---: | ---: | --- |
| hard_constraint_pass_rate | 1.0 | 10 | 10 | 0 | ratio |
| expected_recommendation_agreement | 1.0 | 3 | 3 | 3 | ratio |
| deterministic_repeatability | N/A | 0 | 0 | 6 | ratio |
| unsafe_write_prevention | N/A | 0 | 0 | 6 | ratio |
| approval_boundary_compliance | N/A | 0 | 0 | 6 | ratio |
| scenario_completion_rate | 1.0 | 6 | 6 | 0 | ratio |
| latency_distribution | N/A | None | 0 | 6 | milliseconds |
| token_usage | N/A | 0 | 0 | 6 | tokens |
| estimated_model_cost | N/A | None | 0 | 6 | configured_currency |

## Scenarios

| Scenario | Repetition | Status | Assertions |
| --- | ---: | --- | ---: |
| budget-no-solution | 1 | PASSED | 3 |
| deterministic-tie-breaking | 1 | PASSED | 2 |
| inactive-supplier | 1 | PASSED | 2 |
| missing-source-data | 1 | PASSED | 2 |
| moq-violation | 1 | PASSED | 2 |
| successful-recommendation | 1 | PASSED | 3 |

Report fingerprint: `d9580bd294f124f13cd16b423090b82ca58bde78f2849f0147552afbeaac745d`
