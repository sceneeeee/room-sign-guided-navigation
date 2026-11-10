"""Synthetic interface tests, not handheld field acceptance."""
from copy import deepcopy
import json
import unittest
import numpy as np

from roomnav.door_approach import ADVISORY_SCHEMA, DoorApproachPreview, replay_approach_log
from roomnav.live_approach import LiveDoorApproachSession
from roomnav.rgb_perception import Detection
from test_door_approach import frame, header, source_log
from roomnav.offline_approach import convert_detector_log


def advisory():
    return DoorApproachPreview({**header(), "schema":ADVISORY_SCHEMA})


class AdvisoryTests(unittest.TestCase):
    def test_unknown_stable_candidate_small_sign_gets_one_forward_without_review(self):
        s=advisory()
        for i in range(4):
            e=frame(i,text="")
            e['ocr'][0]['readings']=[]
            e['detections'][1]['bbox_xyxy_norm']=[.492,.25,.508,.262]
            a=s.process_event(e)['advice']
            if i<2:self.assertEqual(a['action'],'STOP_REOBSERVE')
            if i==2:self.assertEqual(a['action'],'FORWARD_SHORT')
        self.assertEqual(a['action'],'STOP_REOBSERVE')
        self.assertFalse(a['motion_authorized'])

    def test_read_is_roi_opportunity_without_room_ocr_or_identity(self):
        s=advisory()
        for i in range(3):
            e=frame(i);e['ocr'][0]['readings']=[]
            a=s.process_event(e)['advice']
        self.assertEqual(a['action'],'STOP_AND_READ')
        self.assertFalse(a['room_number_confirmed'])
        self.assertIsNone(a['physical_landmark_id'])
        self.assertEqual(a['target_evidence_stage'],'temporary_tracked_observation_target')

    def test_high_score_stable_wall_without_support_always_stops(self):
        s=advisory()
        for i in range(10):
            self.assertEqual(s.process_event(frame(i,sign=False))['advice']['action'],'STOP_REOBSERVE')

    def test_manual_selection_and_visual_confirmation_are_distinct(self):
        s=advisory();s.process_event(frame(sign=False))
        s.process_event({'type':'select_target','frame_id':frame()['frame']['frame_id'],'track_id':'track00001'})
        for i in (1,2):
            a=s.process_event(frame(i,sign=False))['advice']
        self.assertEqual(a['action'],'STOP_REOBSERVE')
        s.process_event({'type':'confirm_visual_target','frame_id':frame(2)['frame']['frame_id'],
                         'track_id':'track00001','confirmed_by':'synthetic_operator'})
        a=s.process_event(frame(3,sign=False))['advice']
        self.assertEqual(a['action'],'FORWARD_SHORT')
        self.assertEqual(a['target_evidence_stage'],'human_confirmed_visual_target')
        self.assertEqual(a['evidence_mode'],'synthetic_human_visual_confirmed')
        self.assertIsNone(a['physical_landmark_id'])

    def test_conflicts_and_poster_still_stop(self):
        for mode in ('conflict','poster','low'):
            s=advisory()
            for i in range(4):
                e=frame(i,text='Workshop' if mode=='poster' else 'LSS101',score=.1 if mode=='low' else .8)
                if mode=='conflict':e['detections'].append({'category':'door','bbox_xyxy_norm':[.3,.1,.7,.9],'score':.15})
                self.assertEqual(s.process_event(e)['advice']['action'],'STOP_REOBSERVE')

    def test_new_and_old_schema_replay(self):
        source=source_log([frame(i) for i in range(4)])
        for kwargs in ({},{'preview_schema':ADVISORY_SCHEMA}):
            rows=convert_detector_log(source,**kwargs)
            self.assertEqual(len(list(replay_approach_log(json.loads(json.dumps(rows))))),4)


class Detector:
    def detect(self,rgb):
        return [Detection('door',(.1,.1,.3,.9),.8)]


class LiveTests(unittest.TestCase):
    def setUp(self):
        self.now=1.
        self.s=LiveDoorApproachSession(clock=lambda:self.now)
        self.d=Detector()

    def rgb(self,t,**kwargs):
        self.now=t
        rgb=np.zeros((100,100,3),dtype=np.uint8);rgb[0,0,0]=int(t*10)
        return self.s.process_rgb(rgb,predict=kwargs.pop('predict',self.d),received_s=t,**kwargs)

    def test_detect_select_track_wait_human_feedback_new_frame(self):
        self.rgb(1)
        self.s.select_target(self.s.candidates[0].track_id,at_s=1)
        r=self.rgb(1.5);self.assertEqual(r['action'],'TURN_LEFT')
        pending=r['action_id']
        self.assertEqual(self.rgb(2)['action'],'WAIT_EXECUTION')
        self.s.feedback(pending,completed=True,safe=True,at_s=2.1)
        self.assertEqual(self.rgb(2.5)['action'],'TURN_LEFT')
        self.assertFalse(r['motion_authorized'])

    def test_reject_and_stop_latch(self):
        self.rgb(1);r=self.rgb(1.5)
        self.s.feedback(r['action_id'],completed=False,safe=False,at_s=1.6)
        self.assertEqual(self.rgb(2)['action'],'STOP_REOBSERVE')
        with self.assertRaises(ValueError):self.s.select_target('track00001',at_s=2)

    def test_timestamp_domains_and_duplicate_payload(self):
        r=self.rgb(1,capture={'timestamp_s':100,'clock_id':'camera_clock'})
        self.assertIsNone(r['timing']['camera_age_s'])
        self.assertEqual(r['timing']['server_received_s'],1)
        r=self.rgb(1.5,capture={'timestamp_s':99,'clock_id':'camera_clock'})
        self.assertEqual(r['action'],'STOP_REOBSERVE')

    def test_stale_capture_in_known_shared_clock(self):
        self.assertEqual(self.rgb(5,capture={'timestamp_s':1,'clock_id':'server_monotonic'})['action'],'STOP_REOBSERVE')

    def test_stop_during_inference_never_emits_motion(self):
        self.rgb(1)
        outer=self
        class Stopping:
            def detect(self,rgb):
                outer.s.stop()
                return outer.d.detect(rgb)
        self.assertEqual(self.rgb(1.5,predict=Stopping())['action'],'STOP_REOBSERVE')

    def test_lost_track_cancels_pending_without_fake_feedback(self):
        self.rgb(1);self.rgb(1.5)
        class Empty:
            def detect(self,rgb):return []
        self.assertEqual(self.rgb(2,predict=Empty())['action'],'STOP_REOBSERVE')
        self.assertIsNone(self.s.controller.pending_action_id)
        self.assertFalse(any(e['type']=='human_execution_feedback' for e in self.s.events))

    def test_stop_after_controller_decision_cannot_publish_old_motion(self):
        self.rgb(1)
        original=self.s.controller.step
        def interrupted(*args,**kwargs):
            decision=original(*args,**kwargs)
            self.s.stop()
            return decision
        self.s.controller.step=interrupted
        r=self.rgb(1.5)
        self.assertEqual(r['action'],'STOP_REOBSERVE')
        self.assertIsNone(r['action_id'])

    def test_gradio_receipt_includes_model_initialization_delay(self):
        from unittest.mock import patch
        from apps.rgb_guidance_demo import process_frame
        def factory():
            self.now=6.
            return self.d
        with patch('apps.rgb_guidance_demo.detector',side_effect=factory):
            r=process_frame(np.zeros((100,100,3),dtype=np.uint8),self.s)
        self.assertIn('STOP_REOBSERVE',r[4])
        event=self.s.events[0]
        self.assertEqual(event['timing']['server_received_s'],1)
        self.assertEqual(event['timing']['server_processing_finished_s'],6)

    def test_gradio_callbacks_accept_new_session(self):
        from apps.rgb_guidance_demo import process_frame, select_target, confirm_action, halt
        rgb=np.zeros((100,100,3),dtype=np.uint8)
        result=process_frame(rgb,self.s,predict=self.d,now=1)
        self.assertEqual(result[0].shape,rgb.shape)
        self.assertIn('track00001',select_target('track00001',self.s))
        self.now=1.5;rgb[0,0,0]=1
        r=process_frame(rgb,self.s,predict=self.d,now=1.5)
        self.assertIn('TURN_LEFT',r[4])
        confirm_action(self.s,False)
        self.assertTrue(self.s.stopped)
        halt(self.s)


if __name__=='__main__':unittest.main()
