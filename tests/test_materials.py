import csv
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {'device_activity.csv': (['device_id', 'household_id', 'region', 'active_date', 'activity_minutes', 'connectivity_state', 'firmware'], 180), 'subscriptions.csv': (['subscription_id', 'household_id', 'region', 'plan', 'currency', 'period_start', 'period_end', 'subscription_state'], 120), 'settlements.csv': (['settlement_id', 'channel', 'region', 'currency', 'gross_amount', 'fee_amount', 'settled_amount', 'fx_version', 'settlement_state'], 48), 'regional_costs.csv': (['region', 'period', 'support_cost', 'returns_cost', 'partner_fee', 'capacity_hours', 'cost_version'], 32), 'service_obligations.csv': (['obligation_id', 'region', 'household_id', 'service_type', 'accepted_at', 'due_at', 'completion_state', 'supplier_ref'], 60)}

class MaterialTests(unittest.TestCase):
    def test_declared_csv_contracts(self):
        for filename, (columns, count) in EXPECTED.items():
            with self.subTest(filename=filename):
                with (ROOT / "data" / filename).open(encoding="utf-8-sig", newline="") as stream:
                    reader = csv.DictReader(stream)
                    rows = list(reader)
                self.assertEqual(reader.fieldnames, columns)
                self.assertEqual(len(rows), count)
                self.assertTrue(all(None not in row for row in rows))

if __name__ == "__main__":
    unittest.main()
