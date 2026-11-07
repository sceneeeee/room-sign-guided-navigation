import unittest
from pathlib import Path
import tempfile

from roomnav.floor_scene import FloorScene

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_floor_scene.json"


class FloorSceneTests(unittest.TestCase):
    def test_uses_rectified_raster_and_resolves_room(self):
        s = FloorScene(FIXTURE)
        self.assertTrue(s.source_raster.name.endswith('floor_1f_base_modified.tif'))
        self.assertEqual(s.room('syn101')['id'], 'room_SYN101')
        self.assertTrue(s.portals_for('SYN101'))

    def test_candidate_route_is_not_executable(self):
        s = FloorScene(FIXTURE)
        ctx = s.observation_context('SYN101')
        self.assertTrue(ctx['candidate_routes'])
        self.assertIn('disabled', ctx['route_execution'])

    def test_unknown_and_excluded_rooms_have_no_candidate_routes(self):
        s = FloorScene(FIXTURE)
        self.assertEqual(s.observation_context('SYN999')['status'], 'unknown_room')
        self.assertIsNone(s.room('SYN102', eligible_only=True))
        self.assertEqual(s.portals_for('SYN102'), [])
        self.assertEqual(s.candidate_routes_to('SYN102'), [])

    def test_missing_scene_raises_without_synthetic_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                FloorScene(Path(directory) / 'missing.json')

if __name__ == '__main__': unittest.main()
