"""Replay the committed three-door evidence without maps or invented captures.

The field file has one observation per manually associated door. Synthetic
multi-capture tests below are separate and are not extra field evidence.
"""

import copy
from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import unittest
from unittest.mock import patch

from roomnav.m1_adapter import RoomOCRSample
from roomnav.m1_pipeline import M1Session
from roomnav.stream import StreamSession, parse_event_line
from roomnav.stream_cli import main


ROOT = Path(__file__).resolve().parents[1]
FIELD = ROOT / "docs/field-tests/2026-11-07-m1/observations.jsonl"
# Explicit associations published in the field report; never infer IDs from OCR.
ASSOCIATIONS = [
    ("frame_000001", "door_LSS101", "LSS101", "right"),
    ("frame_000029", "door_LSS124", "LSS124", "left"),
    ("frame_000161", "door_LSS119", "LSS119", "left"),
]


class M1FieldReplayTests(unittest.TestCase):
    def setUp(self):
        self.raw = FIELD.read_bytes()
        self.events = [parse_event_line(line) for line in self.raw.decode().splitlines()]

    def test_published_ids_evidence_and_timestamp_placeholders_are_preserved(self):
        self.assertEqual(len(self.events), 3)
        for event, (frame, door, text, side) in zip(self.events, ASSOCIATIONS):
            self.assertEqual(event, {
                "type": "observation", "kind": "room", "text": text,
                "segment_id": "corridor_lss", "side": side,
                "timestamp_s": 0.0, "confidence": None,
                "frame_id": f"session_01:{frame}",
                "landmark_id": f"session_01:{door}",
                "observation_id": f"session_01:{frame}:door_sign",
            })
        for field in ("frame_id", "landmark_id", "observation_id"):
            self.assertEqual(len({event[field] for event in self.events}), 3)
        self.assertEqual([event["text"] for event in self.events], ["LSS101", "LSS124", "LSS119"])

    def test_m1_ingestion_reconstructs_exact_published_events_and_stream_decisions(self):
        m1, stream = M1Session("session_01"), StreamSession("LSS119")
        for event, (frame, door, text, side) in zip(self.events, ASSOCIATIONS):
            sample = RoomOCRSample(
                session_id="session_01", frame_id=frame, region_id="door_sign",
                timestamp_s=event["timestamp_s"], segment_id=event["segment_id"],
                raw_text=text, physical_door_id=door, association="manual",
                side=side, confidence=event["confidence"],
            )
            result = m1.ingest(sample)
            self.assertEqual(result.status, "accepted")
            self.assertEqual(result.observation, event)
            self.assertNotEqual(stream.process_event(result.observation)["decision"]["action"], "FOUND")
            self.assertEqual(m1.ingest(sample).status, "duplicate")
        self.assertEqual(m1.summary(), {
            "session_id": "session_01", "accepted_count": 3, "rejected_count": 0,
            "landmark_count": 3, "unassociated_count": 0,
        })
        self.assertEqual([obs.to_dict() for obs in stream.memory.observations], [
            {key: value for key, value in event.items() if key != "type"}
            for event in self.events
        ])
        self.assertEqual(stream.memory.confirmations, {})
        self.assertEqual(FIELD.read_bytes(), self.raw)

    def test_targets_use_only_arrived_evidence_and_never_auto_confirm(self):
        expected = {
            "LSS101": ["REOBSERVE"] * 3,
            "LSS124": ["INSUFFICIENT_EVIDENCE", "REOBSERVE", "REOBSERVE"],
            "LSS119": ["INSUFFICIENT_EVIDENCE", "INSUFFICIENT_EVIDENCE", "REOBSERVE"],
            "LSS120": ["INSUFFICIENT_EVIDENCE"] * 3,
        }
        for target, actions in expected.items():
            with self.subTest(target=target):
                stream = StreamSession(target)
                for index, event in enumerate(self.events, 1):
                    reply = stream.process_event(event)
                    self.assertEqual(reply["decision"]["action"], actions[index - 1])
                    self.assertEqual(reply["memory"], {
                        "raw_observation_count": index, "landmark_count": index,
                        "unassociated_observation_count": 0, "confirmation_count": 0,
                    })
                    arrived = {item["landmark_id"] for item in self.events[:index]}
                    self.assertTrue(set(reply["decision"]["evidence_landmark_ids"]) <= arrived)
                self.assertTrue(all(lm.status == "single" for lm in stream.memory.landmarks.values()))
                self.assertEqual(stream.memory.confirmations, {})

    def test_replay_retries_do_not_manufacture_second_capture(self):
        stream = StreamSession("LSS119")
        for event in self.events:
            stream.process_event(event)
        before = copy.deepcopy(stream.memory)
        for event in self.events:
            reply = stream.process_event(event)
            self.assertEqual(reply["status"], "duplicate")
            self.assertEqual(reply["decision"]["action"], "REOBSERVE")
        self.assertEqual(stream.memory, before)

    def test_conflicting_field_id_does_not_change_recorded_evidence(self):
        stream = StreamSession("LSS119")
        for event in self.events:
            stream.process_event(event)
        before = copy.deepcopy(stream.memory)
        for changes in ({"timestamp_s": 14.0}, {"text": "LSS120"},
                        {"frame_id": "invented"}, {"landmark_id": "invented"}):
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(ValueError, "different content"):
                    stream.process_event({**self.events[-1], **changes})
                self.assertEqual(stream.memory, before)

    def test_file_and_stdin_replay_without_maps_or_network(self):
        outputs = []
        for use_stdin in (False, True):
            output, error = io.StringIO(), io.StringIO()
            with self.subTest(stdin=use_stdin), \
                    patch("roomnav.floor_scene.FloorScene.__init__", side_effect=AssertionError("Map access")), \
                    patch("roomnav.m1_scene_bridge.M1SceneBridge.__init__", side_effect=AssertionError("Scene access")), \
                    patch("socket.socket", side_effect=AssertionError("Network access")), \
                    patch("sys.stdin", io.StringIO(self.raw.decode())), \
                    redirect_stdout(output), redirect_stderr(error):
                main(["--target", "LSS119", "--input", "-" if use_stdin else str(FIELD)])
            self.assertEqual(error.getvalue(), "")
            rows = [parse_event_line(line) for line in output.getvalue().splitlines()]
            self.assertEqual(len(rows), 3)
            self.assertEqual([row["observation_id"] for row in rows], [event["observation_id"] for event in self.events])
            self.assertEqual([row["decision"]["action"] for row in rows], [
                "INSUFFICIENT_EVIDENCE", "INSUFFICIENT_EVIDENCE", "REOBSERVE",
            ])
            self.assertEqual(rows[-1]["memory"]["confirmation_count"], 0)
            outputs.append(output.getvalue())
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(FIELD.read_bytes(), self.raw)


class SyntheticNonmonotonicStreamTests(unittest.TestCase):
    def test_reliable_synthetic_nonmonotonic_sequence_abstains(self):
        # Separate synthetic namespace. These are NOT additional field captures.
        stream = StreamSession("SYN120")
        for door, text in (("d1", "SYN101"), ("d2", "SYN124"), ("d3", "SYN119")):
            for capture in range(2):
                event = RoomOCRSample(
                    session_id="synthetic", frame_id=f"{door}-f{capture}", region_id="r0",
                    timestamp_s=0.0, segment_id="synthetic", raw_text=text,
                    physical_door_id=door, association="manual",
                ).to_m2_event()
                reply = stream.process_event(event)
        self.assertEqual(reply["decision"]["action"], "INSUFFICIENT_EVIDENCE")
        self.assertIn("not strictly monotonic", reply["decision"]["reason"])
        self.assertEqual(reply["decision"]["evidence_landmark_ids"], (
            "synthetic:d1", "synthetic:d2", "synthetic:d3",
        ))
        self.assertEqual(stream.memory.confirmations, {})


if __name__ == "__main__":
    unittest.main()
