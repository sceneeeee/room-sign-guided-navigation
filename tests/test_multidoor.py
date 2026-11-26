import copy
import json
from pathlib import Path
import unittest

from roomnav.multidoor import MultiDoorSearchSession, State, replay
from roomnav.search_episode import FixtureEpisode, scene_item, score
from roomnav.search_policy import SearchPolicy

ROOT = Path(__file__).resolve().parents[1]
DEFINITION = json.loads((ROOT / "examples/m4/search_episode.json").read_text())


class EpisodeTests(unittest.TestCase):
    def test_handheld_mode_is_recorded_in_replay_header(self):
        session = MultiDoorSearchSession("handheld", "LSS119", origin="live_rgb", handheld_mode=True)
        self.assertTrue(session.records[0]["handheld_mode"])
        restored = replay(session.records)
        self.assertTrue(restored.handheld_mode)

    def env(self):
        return FixtureEpisode(DEFINITION, "rule", 0)

    def test_scripted_search(self):
        env = FixtureEpisode(DEFINITION, "rule", 0, scripted=True)
        s = env.run()
        self.assertEqual(s.visit_history, ["fixture:"+k for k in "ABCAD"])
        self.assertEqual(s.state, State.FOUND)
        self.assertEqual(len(s.stream.memory.landmarks), 4)
        self.assertTrue(s.stream.memory.landmarks["fixture:B"].status == "conflicted")
        self.assertEqual(s.stream.memory.landmarks["fixture:B"].label_votes, {"LSS124":2, "LSS125":1})
        self.assertEqual(s.records[-1]["input"]["type"], "confirm_room")
        rows = [r["output"] for r in s.records[1:]]
        self.assertTrue(all(not r["motion_authorized"] for r in rows))
        self.assertTrue(any(c["ocr_error"] for r in rows for c in r["candidates"]))
        self.assertTrue(any(c["association_source"]=="adjacent_wall" for r in rows for c in r["candidates"]))
        self.assertTrue(any(r["recovery_required"] for r in rows))
        self.assertTrue(any(r["advice"]["action"]=="STOP_AND_READ" for r in rows))
        self.assertEqual(replay(s.records).snapshot(), s.snapshot())
        altered = copy.deepcopy(s.records)
        altered[1]["output"]["state"] = "FOUND"
        with self.assertRaisesRegex(ValueError, "diverged"):
            replay(altered)

    def test_policy_changes_episode_and_absent_reader_cannot_find(self):
        env = self.env()
        env.run()
        self.assertEqual(env.visits, ["D"])
        seq = FixtureEpisode(DEFINITION, "sequential", 0)
        seq.run()
        self.assertEqual(seq.visits, ["A","B","C","D"])
        no_reader = dict(DEFINITION, operator_cards={})
        s = FixtureEpisode(no_reader, "rule", 0).run()
        self.assertEqual(s.state, State.FAILED)
        self.assertEqual(s.failure, "frame_budget_exhausted")

    def test_no_reader_has_no_success_denominator_and_ocr_does_not_imply_selection(self):
        env = self.env()
        env.frame(env.overview())
        out = env.frame(env.overview())
        env.command("select", track_id=out["candidates"][0]["track_id"])
        env.frame(env.overview())
        stats = score(env.session, truth=env.truth, labels=DEFINITION["evaluator"]["room_labels"],
                      observer_available=False)
        self.assertIsNone(stats["success"])
        self.assertIsNone(stats["wrong_found"])
        self.assertEqual(stats["verification_unavailable_reason"], "no_independent_reader")
        self.assertFalse(stats["found_observed"])
        self.assertEqual(stats["independent_confirmations"], 0)
        self.assertEqual(stats["verify_target_frame_ids"], [])
        self.assertEqual(stats["target_frames"]["annotated_ocr_hit"], [1, 2, 3])
        self.assertEqual(stats["target_frames"]["correct_owner_stable"], [2, 3])
        self.assertEqual(stats["target_frames"]["selected_target"], [])
        self.assertEqual(stats["target_frames"]["ocr_on_selected_target"], [])

    def test_model_attribution_compares_the_accepted_track_with_local_rule(self):
        class TestPlanner:
            backend = model = "fixture"

            def __init__(self, track):
                self.track = track

            def generate(self, **kwargs):
                result = {"action": "INSUFFICIENT_EVIDENCE", "reason": "Observe",
                          "assumptions": [], "uncertainties": [], "evidence_landmark_ids": []}
                if self.track:
                    result["next_track_id"] = self.track
                return json.dumps(result)

        for track, changed in [(None, 0), ("track00001", 1), ("track00007", 0)]:
            with self.subTest(track=track):
                env = FixtureEpisode(DEFINITION, "qwen", 2)
                env.session.policy.backend = TestPlanner(track)
                env.frame(env.overview())
                out = env.frame(env.overview())
                env.command("accept_intent", track_id=out["intent"]["suggested_track"])
                stats = score(env.session)
                self.assertEqual(stats["model_valid_calls"], 2)
                self.assertEqual(stats["model_valid_calls_without_track"], 2 if track is None else 0)
                self.assertEqual(stats["model_selection"]["accepted_intents"], 1)
                self.assertEqual(stats["model_selection"]["model_changed_choices"], changed)
                self.assertEqual(stats["model_selection"]["rule_equivalent_choices"], 1 - changed)

    def test_shared_wall_sign_and_low_confidence_competitor(self):
        env = self.env()
        items = [scene_item("door",[.05,.1,.43,.9]), scene_item("door",[.57,.1,.95,.9]),
                 scene_item("sign",[.46,.34,.54,.41],text="LSS119")]
        env.frame(items)
        out = env.frame(items)
        self.assertEqual(out["candidates"][-1]["association_status"], "ambiguous")
        track = out["candidates"][0]["track_id"]
        env.command("select", track_id=track)
        with self.assertRaises(ValueError):
            env.command("bind", track_id=track, sign_track_id=out["candidates"][-1]["track_id"],
                        physical_door_id="A", actor="reader")
        self.assertFalse(env.session.stream.memory.observations)
        self.assertEqual(env.frame(items)["advice"]["action"], "STOP_REOBSERVE")

    def test_wrong_door_duplicate_sign_and_directions_remain_raw(self):
        env = self.env()
        items = env.overview()
        for _ in range(2):
            out = env.frame(items)
        self.assertTrue(out["intent"]["direction_sign_readings"])
        env.command("select", track_id=out["candidates"][0]["track_id"])
        wrong = dict(track_id=env.session.active, sign_track_id=out["candidates"][3]["track_id"],
                     physical_door_id="A", actor="reader")
        with self.assertRaises(ValueError):
            env.command("bind", **wrong)
        out = env.frame(items + [items[1]])
        self.assertEqual(out["candidates"][1]["association_status"], "ambiguous")
        self.assertFalse(env.session.stream.memory.landmarks)

    def test_two_frame_gate_and_current_room_confirmation(self):
        env = self.env()
        items = [scene_item("door",[.3,.1,.7,.94]), scene_item("sign",[.44,.3,.55,.42],text="LSS119")]
        env.frame(items)
        env.frame(items)
        s = env.session
        bind = dict(track_id=s.active, sign_track_id=s.evidence.current[1].track_id,
                    physical_door_id="P", actor="observer")
        env.command("bind", **bind)
        confirm = dict(landmark_id="fixture:P",text="LSS119",actor="separate_read")
        with self.assertRaises(ValueError):
            env.command("confirm_room", **confirm)
        env.frame(items)
        self.assertEqual(s.state, State.VERIFY_TARGET)
        with self.assertRaises(ValueError):
            env.command("confirm_room", **dict(confirm,landmark_id="fixture:Q"))
        env.command("confirm_room", **dict(confirm,text="LSS118"))
        self.assertNotEqual(s.state, State.FOUND)
        self.assertEqual(s.stream.memory.get_confirmation("fixture:P").text, "LSS118")

    def test_verification_uses_current_door_despite_older_target_misread(self):
        env = self.env()
        view = [
            scene_item("door", [.10, .10, .40, .95]),
            scene_item("sign", [.19, .36, .28, .43], text="LSS119"),
            scene_item("door", [.60, .10, .90, .95]),
            scene_item("sign", [.69, .36, .78, .43], text="LSS119"),
        ]
        env.frame(view)
        out = env.frame(view)
        left, left_sign, right, right_sign = [c["track_id"] for c in out["candidates"]]
        env.command("select", track_id=left)
        env.command("bind", track_id=left, sign_track_id=left_sign, physical_door_id="A", actor="observer")
        env.command("next")
        env.frame(view)
        env.frame(view)
        env.command("select", track_id=right)
        env.command("bind", track_id=right, sign_track_id=right_sign, physical_door_id="B", actor="observer")
        out = env.frame(view)
        self.assertEqual(out["intent"]["decision"]["action"], "REOBSERVE")
        self.assertEqual(out["intent"]["decision"]["evidence_landmark_ids"], ["fixture:A"])
        self.assertEqual(out["state"], "VERIFY_TARGET")
        env.command("confirm_room", landmark_id="fixture:B", text="LSS119", actor="reader")
        self.assertEqual(env.session.state, State.FOUND)
        self.assertIsNone(env.session.stream.memory.get_confirmation("fixture:A"))
        self.assertEqual(replay(env.session.records).state, State.FOUND)

    def test_verification_normalizes_formats_without_combining_different_room_numbers(self):
        for second, state in [("LSS-119", "VERIFY_TARGET"), ("LSS118", "READ_SIGN")]:
            with self.subTest(second=second):
                env = self.env()
                door = scene_item("door", [.30, .10, .70, .94])
                first_view = [door, scene_item("sign", [.44, .30, .55, .42], text="LSS119")]
                env.frame(first_view)
                out = env.frame(first_view)
                track, sign = [c["track_id"] for c in out["candidates"]]
                env.command("bind", track_id=track, sign_track_id=sign, physical_door_id="P", actor="observer")
                env.frame([door, scene_item("sign", [.44, .30, .55, .42], text=second)])
                out = env.frame(first_view)
                self.assertEqual(out["state"], state)
                self.assertEqual(out["memory"]["landmarks"][0]["label_votes"], {"LSS119": 2, second: 1})
                if second == "LSS118":
                    with self.assertRaises(ValueError):
                        env.command("confirm_room", landmark_id="fixture:P", text="LSS119", actor="reader")
                else:
                    env.command("confirm_room", landmark_id="fixture:P", text="LSS119", actor="reader")
                    self.assertEqual(env.session.state, State.FOUND)

    def test_verification_holds_still_when_the_same_door_drifts_off_center(self):
        env = self.env()
        def view(offset):
            return [
                scene_item("door", [.42 + offset, .08, .79 + offset, .94]),
                scene_item("sign", [.55 + offset, .34, .67 + offset, .42], text="LSS119"),
            ]
        env.frame(view(0))
        out = env.frame(view(0))
        door, sign = [c["track_id"] for c in out["candidates"]]
        env.command("bind", track_id=door, sign_track_id=sign, physical_door_id="P", actor="observer")
        self.assertEqual(env.frame(view(0))["state"], "VERIFY_TARGET")
        out = env.frame(view(.025))
        self.assertEqual(out["state"], "VERIFY_TARGET")
        self.assertEqual(out["advice"]["action"], "STOP_AND_READ")
        self.assertIn("awaiting_independent_room_confirmation",
                      out["approach_checks"]["action_blockers"]["TURN_RIGHT"])
        self.assertIsNone(out["action_id"])
        self.assertIsNone(env.session.live.controller.pending_action_id)
        self.assertFalse(out["motion_authorized"])
        env.command("confirm_room", landmark_id="fixture:P", text="LSS119", actor="reader")
        self.assertEqual(env.session.state, State.FOUND)
        self.assertEqual(replay(env.session.records).state, State.FOUND)

    def test_equal_text_and_revisit_bindings(self):
        d = copy.deepcopy(DEFINITION)
        d["doors"][0]["reads"] = ["LSS118","LSS118"]
        s = FixtureEpisode(d,"rule",0,scripted=True).run()
        self.assertEqual(s.stream.memory.get_landmark("fixture:A").candidate_text, "LSS118")
        self.assertEqual(s.stream.memory.get_landmark("fixture:C").candidate_text, "LSS118")
        self.assertEqual(len(s.stream.memory.landmarks),4)

    def test_two_visible_doors_cannot_share_id_across_approaches(self):
        env = self.env()
        view = [
            scene_item("door", [.10, .10, .40, .95]),
            scene_item("sign", [.19, .36, .28, .43], text="LSS119"),
            scene_item("door", [.60, .10, .90, .95]),
            scene_item("sign", [.69, .36, .78, .43], text="LSS119"),
        ]
        env.frame(view)
        first = env.frame(view)
        left, left_sign, right, right_sign = [c["track_id"] for c in first["candidates"]]
        env.command("select", track_id=left)
        env.command("bind", track_id=left, sign_track_id=left_sign, physical_door_id="P", actor="observer")
        env.command("next")
        env.frame(view)
        second = env.frame(view)
        self.assertEqual([c["track_id"] for c in second["candidates"]], [left, left_sign, right, right_sign])
        env.command("select", track_id=right)
        with self.assertRaises(ValueError):
            env.command("bind", track_id=right, sign_track_id=right_sign, physical_door_id="P", actor="observer")
        with self.assertRaises(ValueError):
            env.command("confirm_room", landmark_id="fixture:P", text="LSS119", actor="reader")
        self.assertEqual(env.session.stream.memory.get_landmark("fixture:P").label_votes, {"LSS119": 1})
        self.assertNotEqual(env.session.state, State.FOUND)

        env.command("next")
        env.frame(view)
        out = env.frame(view)
        self.assertEqual(out["candidates"][0]["track_id"], left)
        env.command("select", track_id=left)
        with self.assertRaises(ValueError):
            env.command("bind", track_id=left, sign_track_id=left_sign, physical_door_id="Q", actor="observer")

        env.command("next")
        env.frame(view[2:])
        out = env.frame(view[2:])
        self.assertEqual(out["candidates"][0]["track_id"], right)
        env.command("select", track_id=right)
        with self.assertRaises(ValueError):
            env.command("bind", track_id=right, sign_track_id=right_sign, physical_door_id="P", actor="observer")
        self.assertEqual(env.session.stream.memory.get_landmark("fixture:P").label_votes, {"LSS119": 1})

    def test_revisit_can_rebind_an_id_after_track_loss(self):
        env = self.env()
        view = [
            scene_item("door", [.30, .10, .70, .94]),
            scene_item("sign", [.44, .30, .55, .42], text="LSS119"),
        ]
        env.frame(view)
        first = env.frame(view)
        door, sign = [c["track_id"] for c in first["candidates"]]
        env.command("bind", track_id=door, sign_track_id=sign, physical_door_id="P", actor="observer")
        env.frame([])
        env.command("next")
        env.frame(view)
        second = env.frame(view)
        new_door, new_sign = [c["track_id"] for c in second["candidates"]]
        self.assertNotEqual(new_door, door)
        env.command("select", track_id=new_door)
        out = env.command("bind", track_id=new_door, sign_track_id=new_sign, physical_door_id="P", actor="observer")
        self.assertEqual(out["visits"], ["fixture:P", "fixture:P"])
        self.assertEqual(out["active_landmark"], "fixture:P")
        self.assertNotEqual(out["state"], "VERIFY_TARGET")
        with self.assertRaises(ValueError):
            env.command("confirm_room", landmark_id="fixture:P", text="LSS119", actor="reader")
        self.assertEqual(env.frame(view)["state"], "VERIFY_TARGET")

    def test_plate_geometry_jump_revokes_binding_but_preserves_ocr(self):
        env = self.env()
        view = [
            scene_item("door", [.27, .10, .70, .94]),
            scene_item("sign", [.50, .30, .67, .42], text="LSS118"),
        ]
        env.frame(view)
        first = env.frame(view)
        door, sign = [c["track_id"] for c in first["candidates"]]
        env.command("bind", track_id=door, sign_track_id=sign, physical_door_id="P", actor="observer")
        shifted = [
            scene_item("door", [.41, .10, .84, .94]),
            scene_item("sign", [.44, .30, .61, .42], text="LSS119"),
        ]
        out = env.frame(shifted)
        self.assertEqual([c["track_id"] for c in out["candidates"]], [door, sign])
        self.assertIsNone(out["active_landmark"])
        self.assertEqual(out["candidates"][1]["ocr"][0]["raw_text"], "LSS119")
        self.assertEqual(out["memory"]["landmarks"][0]["label_votes"], {"LSS118": 1})
        self.assertIsNone(env.frame(shifted)["active_landmark"])

    def test_ineligible_door_geometry_drops_binding_and_does_not_vote(self):
        env = self.env()

        def view(width):
            return [
                scene_item("door", [.5 - width / 2, .10, .5 + width / 2, .94]),
                scene_item("sign", [.5 - width / 4, .30, .5 + width / 4, .42], text="LSS119"),
            ]

        env.frame(view(.08))
        out = env.frame(view(.08))
        door, sign = [c["track_id"] for c in out["candidates"]]
        env.command("bind", track_id=door, sign_track_id=sign, physical_door_id="P", actor="observer")
        out = env.frame(view(.055))
        self.assertEqual([c["track_id"] for c in out["candidates"]], [door, sign])
        self.assertIsNone(out["active_landmark"])
        self.assertEqual(out["candidates"][1]["ocr"][0]["raw_text"], "LSS119")
        self.assertEqual(out["memory"]["landmarks"][0]["label_votes"], {"LSS119": 1})
        out = env.frame(view(.08))
        self.assertIsNone(out["active_landmark"])
        self.assertNotEqual(out["state"], "VERIFY_TARGET")

    def test_lost_lock_requires_release_and_new_manual_selection(self):
        env = self.env()
        door = [scene_item("door",[.3,.1,.7,.94])]
        env.frame(door)
        env.frame(door)
        track = env.session.active
        env.frame([])
        self.assertTrue(env.session.recovery)
        with self.assertRaises(ValueError):
            env.frame(door)
        env.command("next")
        for _ in range(3):
            out = env.frame(door)
        self.assertIsNone(env.session.active)
        self.assertNotEqual(track, out["candidates"][0]["track_id"])
        with self.assertRaises(ValueError):
            env.command("select", track_id=track)

    def test_handheld_lock_recovers_short_loss_and_keeps_memory_on_manual_reselection(self):
        env = self.env()
        env.session.handheld_mode = True
        env.session.records[0]["handheld_mode"] = True
        first_door = [scene_item("door", [.30, .10, .70, .94]),
                      scene_item("sign", [.44, .32, .55, .42], text="LSS118")]
        env.frame(first_door)
        first = env.frame(first_door)
        door, sign = [c["track_id"] for c in first["candidates"]]
        env.command("bind", track_id=door, sign_track_id=sign, physical_door_id="P", actor="observer")
        second_door = [scene_item("door", [.60, .10, .90, .94]),
                       scene_item("sign", [.69, .32, .78, .42], text="LSS119")]
        lost = env.frame(second_door)
        self.assertEqual(lost["tracking_mode"], "REOBSERVE")
        self.assertEqual(lost["active_track"], door)
        self.assertNotEqual(lost["state"], State.STOPPED)
        resumed = env.frame(first_door)
        self.assertEqual(resumed["active_track"], door)
        self.assertEqual(resumed["tracking_mode"], "TARGET_LOCK")
        memory_before = env.session.stream.memory.to_reasoning_context()

        for _ in range(3):
            current = env.frame(second_door)
        other = next(c["track_id"] for c in current["candidates"] if c["category"] == "door")
        self.assertEqual(current["tracking_mode"], "REOBSERVE")
        env.command("select", track_id=other)
        self.assertEqual(env.session.active, other)
        self.assertIsNone(env.session.bound_physical)
        self.assertEqual(env.session.visit_history, ["fixture:P"])
        self.assertEqual(env.session.stream.memory.to_reasoning_context(), memory_before)
        env.command("next")
        self.assertIsNone(env.session.active)
        self.assertTrue(env.session.live.preview.auto_select)
        self.assertEqual(env.session.stream.memory.to_reasoning_context(), memory_before)

    def pending(self):
        env = self.env()
        door = [scene_item("door",[.30,.05,.70,.94])]
        env.frame(door)
        env.frame(door)
        env.command("visual_confirm", track_id=env.session.active, actor="operator")
        # Keep feedback explicit for these checks (the fixture normally acknowledges).
        original = env.command
        env.command = lambda *a, **kw: None
        out = env.frame(door)
        env.command = original
        self.assertEqual(out["advice"]["action"],"FORWARD_SHORT")
        return env,door

    def test_feedback_refusal_and_emergency_stop(self):
        env,door = self.pending()
        pending = env.session.pending_id
        with self.assertRaises(ValueError):
            env.command("feedback", action_id=pending+1, safe=True, completed=True)
        cmd = copy.deepcopy(env.session.records[-1]["input"])
        with self.assertRaises(ValueError):
            env.command("next")
        env.command("feedback", action_id=pending, safe=False, completed=False)
        self.assertEqual(env.session.state, State.STOPPED)
        with self.assertRaises(ValueError):
            env.session.apply(cmd)
        env, _ = self.pending()
        env.command("stop")
        self.assertIsNone(env.session.pending_id)
        self.assertEqual(env.session.failure, "emergency_stop")

    def test_repeated_forward_needs_feedback_and_three_new_stable_frames(self):
        env,door = self.pending()
        respond = env.command
        env.command = lambda *a, **kw: None
        stale = copy.deepcopy(env.session.records[-1]["input"])
        self.assertEqual(env.frame(door)["advice"]["action"],"WAIT_EXECUTION")
        self.assertEqual(env.session.movement_count,1)
        for count in (2,3):
            respond("feedback", action_id=env.session.pending_id, safe=True, completed=True)
            self.assertIsNone(env.session.pending_id)
            for _ in range(2):
                self.assertNotEqual(env.frame(door)["advice"]["action"],"FORWARD_SHORT")
            self.assertEqual(env.frame(door)["advice"]["action"],"FORWARD_SHORT")
            self.assertEqual(env.session.movement_count,count)
        respond("feedback", action_id=env.session.pending_id, safe=True, completed=True)
        for _ in range(4):
            self.assertNotEqual(env.frame(door)["advice"]["action"],"FORWARD_SHORT")
        self.assertEqual(env.session.apply(stale)["state"],"STOPPED")

    def test_unsafe_frame_and_image_reuse(self):
        env = self.env()
        env.frame(env.overview())
        cmd = copy.deepcopy(env.session.records[1]["input"])
        cmd["perception"]["truth"] = {"hidden": "forbidden"}
        with self.assertRaises(ValueError):
            env.session.apply(cmd)
        del cmd["perception"]["truth"]
        self.assertEqual(env.session.apply(cmd)["state"], "STOPPED")
        fresh = MultiDoorSearchSession("fixture","LSS119",origin="recorded_video")
        cmd["rgb_sha256"] = "0"*64
        fresh.apply(cmd)
        cmd = copy.deepcopy(cmd)
        from roomnav.offline_video import FrameStamp
        stamp = FrameStamp(2,2,"1/4")
        cmd.update(source_frame=stamp.to_dict(),at_s=stamp.timestamp_s)
        cmd["perception"]["frame"] = stamp.to_dict()
        self.assertEqual(fresh.apply(cmd)["state"], "STOPPED")

    def test_model_budget_image_and_replay(self):
        class TestModel:
            model, backend = "test-only", "fixture"
            def __init__(self):
                self.images = []
            def generate_with_image(self, jpeg_data_url, **kwargs):
                self.images.append(jpeg_data_url)
                return json.dumps({"action":"INSUFFICIENT_EVIDENCE","reason":"Collect another view",
                                   "assumptions":[],"uncertainties":[],"evidence_landmark_ids":[],
                                   "next_track_id":"track00007"})
        model = TestModel()
        env = FixtureEpisode(DEFINITION,"vlm-rgb",1)
        env.session.policy = SearchPolicy("vlm-rgb",budget=1,backend=model)
        s = env.run()
        self.assertEqual(len(model.images),1)
        self.assertTrue(model.images[0].startswith("data:image/jpeg;base64,"))
        self.assertEqual(s.records[1]["output"]["intent"]["suggested_track"],"track00007")
        self.assertEqual(replay(s.records).snapshot(),s.snapshot())
        result = score(s,truth=env.truth,labels={"D":"LSS888"},confirmed_handle="D")
        self.assertTrue(result["wrong_found"])
        self.assertIsNone(result["simulated_path_m"])

    def test_rgb_rois_and_missing_camera_capture(self):
        import numpy as np
        from roomnav.offline_video import FrameStamp, VideoFrame
        from roomnav.rgb_perception import Detection
        from roomnav.ocr import OCRReading
        from roomnav.search_io import rgb_command
        rgb = np.zeros((100,100,3),np.uint8)
        rgb[20:40,30:50,0] = 211
        class Proposals:
            def detect(self, image):
                return [Detection("door",(.1,.1,.6,.9),.8), Detection("door",(.65,.1,.99,.9),.8),
                        Detection("sign",(.3,.2,.5,.4),.92)]
        def read(crop):
            self.assertEqual(crop.shape,(20,20,3))
            self.assertTrue((crop[:,:,0]==211).all())
            return [OCRReading("raw?",.8)]
        frame = VideoFrame(FrameStamp(1,1,"1/2"),rgb)
        cmd,timing = rgb_command(frame,Proposals(),origin="live_rgb",received_s=.5,clock=lambda:.55,ocr=read)
        s = MultiDoorSearchSession("test","LSS119",origin="live_rgb")
        out = s.apply(cmd,timing=timing)
        self.assertEqual(len(out["candidates"]),3)
        self.assertIsNone(out["candidates"][2]["source_pts"])
        self.assertEqual(out["candidates"][2]["ocr"][0]["raw_text"],"raw?")
        self.assertEqual(out["memory"]["raw_observation_count"],0)


    def test_visible_competitor_retained_on_loss_with_and_without_pending_motion(self):
        env = self.env()
        view = [scene_item("door",[.05,.1,.30,.9]), scene_item("door",[.65,.1,.91,.9]),
                scene_item("sign",[.70,.35,.80,.42],text="LSS118")]
        env.frame(view)
        out = env.frame(view)
        env.command("select",track_id=out["candidates"][0]["track_id"])
        remaining = env.frame(view[1:])
        self.assertTrue(remaining["recovery_required"])
        self.assertEqual(remaining["candidates"][1]["ocr"][0]["raw_text"],"LSS118")
        self.assertFalse(env.session.stream.memory.observations)
        env,_ = self.pending()
        out = env.frame(view[1:])
        self.assertEqual(out["state"],"STOPPED")
        self.assertEqual(score(env.session)["track_loss"],1)

    def test_rejected_visual_confirm_does_not_change_replay(self):
        env = self.env()
        view = [scene_item("door",[.3,.1,.7,.95])]
        env.frame(view)
        env.frame(view)
        with self.assertRaises(ValueError):
            env.command("visual_confirm",track_id=env.session.active,actor="  ")
        env.frame(view)
        self.assertNotEqual(env.session.state,State.STOPPED)
        self.assertEqual(replay(env.session.records).snapshot(),env.session.snapshot())

    def test_nonconsecutive_rgb_reuse_and_postfeedback_browser_epoch(self):
        env,view = self.pending()
        live = MultiDoorSearchSession("fixture","LSS119",origin="live_rgb")
        def live_input(row, digit):
            cmd = copy.deepcopy(row["input"])
            if cmd["type"] == "frame":
                cmd.update(source_frame=None,rgb_sha256=str(digit)*64)
            return cmd
        for i,row in enumerate(env.session.records[1:],1):
            live.apply(live_input(row,i))
        pending = live.pending_id
        live.apply({"type":"feedback","completed":True,"safe":True,"at_s":live.last_at+.001,"action_id":pending})
        env.command("feedback",completed=True,safe=True,action_id=env.session.pending_id)
        env.frame(view)
        next_frame = live_input(env.session.records[-1],7)
        next_frame["after_feedback_action_id"] = pending
        replayed = replay(live.records)
        self.assertEqual(replayed.apply(dict(next_frame,after_feedback_action_id=None))["failure_reason"],
                         "pre_feedback_camera_request")
        self.assertNotEqual(live.apply(next_frame)["state"],"STOPPED")
        env.frame(view)
        repeated = live_input(env.session.records[-1],1)
        repeated["after_feedback_action_id"] = pending
        self.assertEqual(live.apply(repeated)["failure_reason"],"stale_duplicate_or_pre_feedback_frame")

    def test_model_replay_requires_response_and_preserves_calls_and_timing(self):
        class FixedModel:
            model,backend = "test-only","fixture"
            def generate(self, **kwargs):
                return '{"action":"INSUFFICIENT_EVIDENCE","reason":"Observe","assumptions":[],"uncertainties":[],"evidence_landmark_ids":[]}'
        env = FixtureEpisode(DEFINITION,"qwen",2)
        env.session.policy.backend = FixedModel()
        env.frame(env.overview())
        env.frame(env.overview())
        s = replay(env.session.records)
        self.assertEqual(s.policy.calls,env.session.policy.calls)
        self.assertEqual(s.records[-1]["timing"],env.session.records[-1]["timing"])
        corrupt = copy.deepcopy(env.session.records)
        del corrupt[1]["planner_response"]
        with self.assertRaisesRegex(ValueError,"Missing recorded"):
            replay(corrupt)

    def test_scripted_episode_through_rgb_and_current_roi_perception(self):
        """Test-only colored rectangles and an OCR alphabet exercise the real RGB ingress."""
        from unittest.mock import patch
        import numpy as np
        from roomnav.ocr import OCRReading
        from roomnav.offline_video import FrameStamp, VideoFrame
        from roomnav.rgb_perception import Detection
        from roomnav.search_io import rgb_command

        alphabet = [None, "LSS101", "LSS124", "LSS118", "LSS119", "LSS125", "?",
                    "LSS101 / LSS124 →"]
        class ColorDetector:
            def detect(self, pixels):
                found = []
                for index in sorted(np.unique(pixels[:, :, 0])):
                    if not index:
                        continue
                    ys, xs = np.where(pixels[:, :, 0] == index)
                    kind = "door" if pixels[ys[0], xs[0], 1] == 1 else "sign"
                    box = (float(xs.min())/640, float(ys.min())/360,
                           (float(xs.max())+1)/640, (float(ys.max())+1)/360)
                    found.append(Detection(kind, box, .94))
                return found
        def read_patch(crop):
            text = alphabet[int(np.median(crop[:, :, 2]))]
            if text is None:
                raise ValueError("Synthetic unreadable plate")
            return [OCRReading(text, .96)]

        env = FixtureEpisode(DEFINITION, "rule", 0, scripted=True)
        apply = env.session.apply
        def through_rgb(cmd, **kwargs):
            if cmd["type"] != "frame":
                return apply(cmd, **kwargs)
            src = cmd["perception"]
            stamp = FrameStamp.from_dict(src["frame"])
            canvas = np.zeros((360, 640, 3), np.uint8)
            canvas[0, 0, 2] = stamp.frame_index
            regions = {r["sign_index"]: r["readings"] for r in src["ocr"]}
            for i, detection in enumerate(src["detections"]):
                x, y, r, b = detection["bbox_xyxy_norm"]
                reading = regions.get(i) or []
                color = (i+1, 1 if detection["category"]=="door" else 2,
                         alphabet.index(reading[0]["raw_text"]) if reading else 0)
                canvas[round(y*360):round(b*360), round(x*640):round(r*640)] = color
            real_input, timing = rgb_command(VideoFrame(stamp, canvas), ColorDetector(),
                                             origin="synthetic", ocr=read_patch)
            return apply(real_input, rgb=canvas, timing=timing)
        with patch.object(env.session, "apply", side_effect=through_rgb):
            s = env.run()
        self.assertEqual(s.state, State.FOUND)
        self.assertEqual(s.visit_history, ["fixture:"+k for k in "ABCAD"])
        self.assertTrue(all(r["input"]["rgb_sha256"] for r in s.records[1:] if r["input"]["type"]=="frame"))
        self.assertEqual(replay(s.records).snapshot(), s.snapshot())
