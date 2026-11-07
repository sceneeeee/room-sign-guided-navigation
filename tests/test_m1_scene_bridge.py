import unittest
from pathlib import Path

from roomnav.floor_scene import FloorScene
from roomnav.m1_scene_bridge import M1SceneBridge
from roomnav.stream import StreamSession

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_floor_scene.json"


class M1SceneBridgeTests(unittest.TestCase):
    def test_enrichment_preserves_ocr_text_and_marks_candidate(self):
        b=M1SceneBridge(FloorScene(FIXTURE))
        original = {'type':'observation','kind':'room','text':'SYN101','frame_id':'s:f',
                    'landmark_id':'s:door1','observation_id':'s:f:r0','segment_id':'synthetic'}
        x=b.enrich(original)
        self.assertEqual({key: x[key] for key in original}, original)
        self.assertNotIn('scene_match', original)
        self.assertEqual(x['scene_match']['status'],'matched')
        self.assertEqual(x['scene_match']['room_id'],'room_SYN101')
        self.assertIn('candidate', x['scene_match']['candidate_routes'][0]['status'])
        self.assertTrue(x['scene_match']['source_raster'].endswith('floor_1f_base_modified.tif'))
        self.assertIn('disabled', x['scene_match']['route_execution'])

    def test_scene_enrichment_cannot_enter_strict_rgb_reasoning(self):
        stream = StreamSession('SYN101')
        enriched = M1SceneBridge(FloorScene(FIXTURE)).enrich({
            'type':'observation','kind':'room','text':'SYN101','segment_id':'synthetic',
        })
        with self.assertRaisesRegex(ValueError, 'Unexpected event fields'):
            stream.process_event(enriched)
        self.assertEqual(stream.memory.observations, [])
        self.assertEqual(stream.memory.confirmations, {})

if __name__=='__main__': unittest.main()
