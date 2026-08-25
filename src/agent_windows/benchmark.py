from __future__ import annotations

import gzip
import json
import struct
import time

from .audio import EnergyVAD, NetworkState, profile_for


def run_local_benchmark() -> dict:
    sample=json.dumps({"messages":[{"role":"user","content":"hello "*500}]},separators=(",",":")).encode()
    compressed=gzip.compress(sample)
    vad=EnergyVAD(threshold=.01,silence_ms=40,frame_ms=20); frames=[]
    for value in ([0]*25+[1200]*25+[0]*25):
        frame=struct.pack("<320h",*([value]*320)); frames.append(vad.process(frame,timestamp_ms=len(frames)*20).speech)
    profiles={state.value:{"bitrate_bps":profile_for(state).bitrate_bps,"chunk_ms":profile_for(state).chunk_ms}
              for state in NetworkState}
    return {"request_payload_bytes":len(sample),"gzip_bytes":len(compressed),"gzip_worthwhile":len(compressed)<len(sample)*.9,
            "vad_frames_total":len(frames),"vad_speech_frames":sum(frames),"silence_removed_percent":round(100*(1-sum(frames)/len(frames)),1),
            "audio_profiles":profiles,"provider_latency_ms":None,"ttfr_ms":None,"retry_count":0,"estimated_network_state":"GOOD"}
