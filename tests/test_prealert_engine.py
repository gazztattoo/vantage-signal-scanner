import unittest
from datetime import datetime,timedelta,timezone
from prealert_engine import evaluate_prealert
NOW=datetime(2026,11,3,12,0,tzinfo=timezone.utc)
BASE=dict(symbol="EUR/USD",direction="BUY",current_price=1.1008,entry_zone_low=1.1000,
 entry_zone_high=1.1005,setup_valid=True,confirmation_valid=False,invalidated=False,
 data_timestamp=NOW,now=NOW,approach_distance=0.0005)
class PrealertTests(unittest.TestCase):
 def test_approach(self):
  r=evaluate_prealert(**BASE); self.assertEqual(r["state"],"PREALERTA"); self.assertGreater(r["distance_to_zone"],0)
 def test_too_far(self):
  self.assertIsNone(evaluate_prealert(**{**BASE,"current_price":1.1020}))
 def test_stale(self):
  self.assertIsNone(evaluate_prealert(**{**BASE,"data_timestamp":NOW-timedelta(seconds=91)}))
 def test_confirm_inside_zone(self):
  r=evaluate_prealert(**{**BASE,"current_price":1.1003,"confirmation_valid":True}); self.assertEqual(r["state"],"CONFIRMADA")
 def test_no_confirm_outside_zone(self):
  self.assertEqual(evaluate_prealert(**{**BASE,"confirmation_valid":True})["state"],"PREALERTA")
 def test_cancel_invalidated(self):
  p=evaluate_prealert(**BASE); r=evaluate_prealert(**{**BASE,"invalidated":True,"previous":p}); self.assertEqual(r["state"],"CANCELADA")
 def test_cancel_price_away(self):
  p=evaluate_prealert(**BASE); r=evaluate_prealert(**{**BASE,"current_price":1.102,"previous":p}); self.assertEqual(r["state"],"CANCELADA")
 def test_expire(self):
  p=evaluate_prealert(**BASE); later=NOW+timedelta(seconds=181)
  r=evaluate_prealert(**{**BASE,"now":later,"data_timestamp":later,"previous":p}); self.assertEqual(r["state"],"EXPIRADA")
 def test_terminal_idempotent(self):
  p={**evaluate_prealert(**BASE),"state":"CANCELADA"}; self.assertEqual(evaluate_prealert(**{**BASE,"previous":p}),p)
 def test_sell(self):
  r=evaluate_prealert(**{**BASE,"direction":"SELL","current_price":1.0997}); self.assertEqual(r["direction"],"SELL")
 def test_bad_direction(self):
  with self.assertRaises(ValueError): evaluate_prealert(**{**BASE,"direction":"HOLD"})
if __name__=="__main__": unittest.main()
