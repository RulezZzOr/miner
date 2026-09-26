import threading
import unittest
from types import SimpleNamespace
from studio.browser_pilot import BrowserPilot, fixture_actions

class BrowserPilotTests(unittest.TestCase):
    def test_only_fixed_fixture_actions_are_available(self):
        frame={'query':'', 'filtered':False, 'visibleItems':['Blue desk','Green chair','White lamp']}
        self.assertEqual(set(fixture_actions(frame)), {'set_query_blue','wait'})
        frame['query']='blue'; self.assertEqual(set(fixture_actions(frame)), {'apply_filter','wait'})
        frame['filtered']=True; self.assertEqual(set(fixture_actions(frame)), {'finish','wait'})
        for extra in [{'url':'https://example.com'}, {'selector':'#delete'}]:
            with self.assertRaises(ValueError):fixture_actions({**frame,**extra})
        with self.assertRaises(ValueError):fixture_actions({**frame,'visibleItems':['Unobserved external element']})

    def test_busy_inference_is_reported_as_a_conflict(self):
        frame={'query':'', 'filtered':False, 'visibleItems':['Blue desk']}
        studio=SimpleNamespace(lock=threading.RLock(), runs={'run':{'status':'running'}}, decision_lab=SimpleNamespace(active=lambda:False))
        pilot=BrowserPilot(studio)
        with self.assertRaises(ValueError) as caught:pilot.choose({'frame':frame})
        self.assertEqual(caught.exception.status, 409)
        studio.runs.clear(); pilot.lock.acquire()
        try:
            with self.assertRaises(ValueError) as caught:pilot.choose({'frame':frame})
            self.assertEqual(caught.exception.status, 409)
        finally:pilot.lock.release()
