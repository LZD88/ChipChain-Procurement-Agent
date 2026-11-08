from decimal import Decimal
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_server.tools.procurement_tools import compare_candidates


PARTS = [
    {"id": 23, "partCode": "CS-SNS-12-01", "name": "六轴惯性传感器", "model": "CS-IMU-06",
     "purchasePrice": 15.8, "supplierId": 6, "minOrderQuantity": 100, "leadTimeDays": 12},
    {"id": 24, "partCode": "CS-SNS-12-02", "name": "六轴惯性传感器", "model": "CS-IMU-06",
     "purchasePrice": 17.06, "supplierId": 1, "minOrderQuantity": 200, "leadTimeDays": 7},
]
SUPPLIERS = {
    6: {"id": 6, "name": "云栈供应链", "creditRating": "A", "status": 1},
    1: {"id": 1, "name": "晶穹电子", "creditRating": "A", "status": 1},
}
INVENTORIES = {
    23: {"partId": 23, "currentQuantity": 800, "safetyStock": 300, "warehouseLocation": "ESD-C"},
    24: {"partId": 24, "currentQuantity": 1200, "safetyStock": 300, "warehouseLocation": "ESD-C"},
}


class ProcurementCompareTests(unittest.TestCase):
    def compare(self, **overrides):
        arguments = dict(part_name="六轴惯性传感器", quantity=500, parts=PARTS,
                         suppliers=SUPPLIERS, inventories=INVENTORIES,
                         budget=Decimal("9000"), max_lead_time_days=14)
        arguments.update(overrides)
        return compare_candidates(**arguments)

    def test_budget_is_hard_constraint_and_reports_minimum_feasible_budget(self):
        result = self.compare(budget=Decimal("4000"))
        self.assertIsNone(result["recommendedPartId"])
        self.assertEqual(result["minimumFeasibleBudget"], 7900.0)
        self.assertTrue(all(not candidate["eligible"] for candidate in result["candidates"]))
        self.assertTrue(all(any("超过预算" in reason for reason in candidate["rejectedReasons"])
                            for candidate in result["candidates"]))

    def test_recommendation_uses_fixed_scores_and_stable_order(self):
        first = self.compare()
        second = self.compare(parts=list(reversed(PARTS)))
        self.assertEqual(first["recommendedPartId"], 24)
        self.assertEqual([item["partId"] for item in first["candidates"]],
                         [item["partId"] for item in second["candidates"]])
        self.assertEqual(first["decisionId"], second["decisionId"])
        self.assertEqual(first["policyVersion"], "supplier-score-v1")
        self.assertEqual(sum(first["scoreWeights"].values()), 100)

    def test_inactive_supplier_and_moq_fail_before_recommendation(self):
        suppliers = {**SUPPLIERS, 1: {**SUPPLIERS[1], "status": 0}}
        result = self.compare(suppliers=suppliers)
        self.assertEqual(result["recommendedPartId"], 23)
        inactive = next(item for item in result["candidates"] if item["partId"] == 24)
        self.assertFalse(inactive["eligible"])
        self.assertIn("供应商非合作状态", inactive["rejectedReasons"])

        too_small = self.compare(quantity=50, budget=None)
        self.assertIsNone(too_small["recommendedPartId"])
        self.assertTrue(all(any("MOQ" in reason for reason in item["rejectedReasons"])
                            for item in too_small["candidates"]))

    def test_equal_scores_use_part_id_as_final_tie_breaker(self):
        tied = [{**PARTS[0], "id": 32}, {**PARTS[0], "id": 31}]
        inventories = {31: INVENTORIES[23], 32: INVENTORIES[23]}
        result = self.compare(parts=tied, inventories=inventories, budget=None)
        self.assertEqual([item["partId"] for item in result["candidates"]], [31, 32])

if __name__ == "__main__":
    unittest.main()
