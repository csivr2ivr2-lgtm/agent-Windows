import io
import struct
import tempfile
from pathlib import Path
import unittest

from agent_windows.audio import (
    AudioChunker,
    ChunkAck,
    EnergyVAD,
    NetworkState,
    OfflineAudioSpool,
    ResilientUploader,
    UploadSession,
    ffmpeg_command,
    profile_for,
)
from agent_windows.audio.transport import UploadInterrupted


class MockAudioTransport:
    def __init__(self, failures=None, duplicates=None):
        self.failures = dict(failures or {})
        self.duplicates = set(duplicates or ())
        self.opened = []
        self.sent = []

    def open(self, session_id, metadata):
        self.opened.append((session_id, metadata))

    def send_chunk(self, chunk):
        self.sent.append(chunk.sequence)
        remaining = self.failures.get(chunk.sequence, 0)
        if remaining:
            self.failures[chunk.sequence] = remaining - 1
            raise ConnectionError("network dropped")
        if chunk.sequence in self.duplicates:
            return ChunkAck(chunk.session_id, chunk.sequence, accepted=False, duplicate=True)
        return ChunkAck(chunk.session_id, chunk.sequence)

    def finish(self, session_id):
        return {"session_id": session_id, "transcript": "mocked"}


def pcm_frame(value, samples=320):
    return struct.pack(f"<{samples}h", *([value] * samples))


class AudioTests(unittest.TestCase):
    def test_chunking_is_sequential_timestamped_and_checksummed(self):
        chunks = list(AudioChunker(3, session_id="s", chunk_duration_ms=100).iter_stream(
            io.BytesIO(b"abcdefgh"), started_ms=1000
        ))
        self.assertEqual([c.sequence for c in chunks], [0, 1, 2])
        self.assertEqual([c.payload for c in chunks], [b"abc", b"def", b"gh"])
        self.assertEqual([c.timestamp_ms for c in chunks], [1000, 1100, 1200])
        self.assertFalse(chunks[0].final)
        self.assertTrue(chunks[-1].final)
        self.assertEqual(len(chunks[0].checksum), 64)

    def test_retry_only_failed_chunk(self):
        chunks = list(AudioChunker(2, session_id="s").iter_stream(io.BytesIO(b"abcdef")))
        transport = MockAudioTransport(failures={1: 1})
        result, state = ResilientUploader(transport, max_attempts=2).upload(chunks, metadata={})
        self.assertEqual(transport.sent, [0, 1, 1, 2])
        self.assertEqual(state.acknowledged, {0, 1, 2})
        self.assertEqual(result["transcript"], "mocked")

    def test_duplicate_ack_is_success(self):
        chunks = list(AudioChunker(2, session_id="s").iter_stream(io.BytesIO(b"ab")))
        _, state = ResilientUploader(MockAudioTransport(duplicates={0})).upload(chunks, metadata={})
        self.assertEqual(state.acknowledged, {0})

    def test_interrupted_upload_resumes_without_resending_acked_chunks(self):
        chunks = list(AudioChunker(2, session_id="s").iter_stream(io.BytesIO(b"abcdef")))
        first_transport = MockAudioTransport(failures={1: 5})
        with self.assertRaises(UploadInterrupted) as caught:
            ResilientUploader(first_transport, max_attempts=2).upload(chunks, metadata={})
        state = caught.exception.session
        self.assertEqual(state.acknowledged, {0})
        second_transport = MockAudioTransport()
        _, resumed = ResilientUploader(second_transport).upload(chunks, metadata={}, session=state)
        self.assertEqual(second_transport.sent, [1, 2])
        self.assertEqual(resumed.resume_from, 3)

    def test_missing_and_out_of_order_ack_tracking(self):
        state = UploadSession("s")
        state.acknowledge(0)
        state.acknowledge(2)
        self.assertEqual(state.resume_from, 1)
        self.assertFalse(state.acknowledge(2))
        self.assertTrue(state.needs(1))

    def test_vad_starts_and_ends_after_silence_boundary(self):
        vad = EnergyVAD(threshold=0.01, silence_ms=40, frame_ms=20)
        first = vad.process(pcm_frame(1000), timestamp_ms=0)
        gap = vad.process(pcm_frame(0), timestamp_ms=20)
        end = vad.process(pcm_frame(0), timestamp_ms=40)
        self.assertTrue(first.utterance_started)
        self.assertFalse(gap.utterance_ended)
        self.assertTrue(end.utterance_ended)

    def test_network_state_adapts_bitrate_chunks_vad_and_offline_spool_flag(self):
        good = profile_for(NetworkState.GOOD)
        degraded = profile_for(NetworkState.DEGRADED)
        poor = profile_for(NetworkState.POOR)
        offline = profile_for(NetworkState.OFFLINE)
        self.assertGreater(good.bitrate_bps, degraded.bitrate_bps)
        self.assertGreater(degraded.bitrate_bps, poor.bitrate_bps)
        self.assertGreater(good.chunk_ms, poor.chunk_ms)
        self.assertGreater(poor.vad_threshold, good.vad_threshold)
        self.assertTrue(offline.store_offline)

    def test_codec_negotiation_and_ffmpeg_command(self):
        pcm = profile_for(NetworkState.POOR, {"pcm_s16le"})
        self.assertEqual(pcm.codec, "pcm_s16le")
        opus = profile_for(NetworkState.GOOD, {"ogg_opus"})
        command = ffmpeg_command(opus)
        self.assertIn("libopus", command)
        self.assertNotIn("gzip", command)

    def test_offline_spool_round_trip_is_chunked_and_checksum_verified(self):
        chunks = list(AudioChunker(2, session_id="unsafe/../session").iter_stream(io.BytesIO(b"abcdef")))
        with tempfile.TemporaryDirectory() as directory:
            spool = OfflineAudioSpool(directory)
            for chunk in chunks:
                spool.put(chunk)
            restored = list(spool.iter_session("unsafe/../session"))
            self.assertEqual([chunk.payload for chunk in restored], [b"ab", b"cd", b"ef"])
            self.assertFalse(any("unsafe" in path.name for path in Path(directory).iterdir()))


if __name__ == "__main__":
    unittest.main()
