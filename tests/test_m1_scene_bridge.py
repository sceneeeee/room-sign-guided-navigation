import unittest
from roomnav.m1_scene_bridge import M1SceneBridge

class M1SceneBridgeTests(unittest.TestCase):
    def test_enrichment_preserves_ocr_text_and_marks_candidate(self):
        b=M1SceneBridge()
        x=b.enrich({'type':'observation','kind':'room','text':'LSS119','frame_id':'s:f'})
        self.assertEqual(x['text'],'LSS119')
        self.assertEqual(x['scene_match']['status'],'matched')
        self.assertEqual(x['scene_match']['room_id'],'room_LSS119')
        self.assertIn('candidate', x['scene_match']['candidate_routes'][0]['status'])
        self.assertTrue(x['scene_match']['source_raster'].endswith('floor_1f_base_modified.tif'))

if __name__=='__main__': unittest.main()
