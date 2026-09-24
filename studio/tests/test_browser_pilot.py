import unittest
from studio.browser_pilot import fixture_actions

class BrowserPilotTests(unittest.TestCase):
    def test_only_fixed_fixture_actions_are_available(self):
        frame={'query':'', 'filtered':False, 'visibleItems':['Blue desk','Green chair','White lamp']}
        self.assertEqual(set(fixture_actions(frame)), {'set_query_blue','wait'})
        frame['query']='blue'; self.assertEqual(set(fixture_actions(frame)), {'apply_filter','wait'})
        frame['filtered']=True; self.assertEqual(set(fixture_actions(frame)), {'finish','wait'})
        for extra in [{'url':'https://example.com'}, {'selector':'#delete'}]:
            with self.assertRaises(ValueError):fixture_actions({**frame,**extra})
        with self.assertRaises(ValueError):fixture_actions({**frame,'visibleItems':['Unobserved external element']})
