"""Pitch-preserving speed change for speech (WSOLA).

time_stretch(samples, rate, sr): rate 0.8 makes speech 20% slower and 25%
longer, at the same pitch. Resampling would make the voice deeper as it slows;
this overlaps and re-joins short pieces of the signal instead.

numpy only. The voice server runs it on every clip, so it has to be quick: a
ten-second clip takes well under a second.
"""
import numpy as np


def time_stretch(x, rate: float, sr: int):
    """Return `x` (float32 mono) played at `rate` times normal speed."""
    x = np.asarray(x, dtype=np.float32)
    if x.size == 0 or abs(rate - 1.0) < 1e-3:
        return x
    rate = float(min(2.0, max(0.5, rate)))

    frame = int(sr * 0.032)            # 32 ms pieces
    frame += frame % 2
    hop_out = frame // 2               # where each piece lands in the output
    hop_in = hop_out * rate            # where it is taken from, on average
    tol = int(sr * 0.012)              # how far we may slide to find a good join
    if x.size < frame * 2:
        return x

    pad = np.concatenate([x, np.zeros(frame + tol + hop_out, dtype=np.float32)])
    window = np.hanning(frame + 1)[:-1].astype(np.float32)
    n_pieces = int(np.ceil((x.size / rate) / hop_out)) + 1
    out = np.zeros(n_pieces * hop_out + frame, dtype=np.float32)
    norm = np.zeros_like(out)

    prev = 0
    for k in range(n_pieces):
        target = int(round(k * hop_in))
        if k == 0:
            start = 0
        else:
            lo = max(0, target - tol)
            hi = min(pad.size - frame, target + tol)
            if hi <= lo:
                start = min(max(0, target), pad.size - frame)
            else:
                # the piece that would naturally follow the previous one
                ref = pad[prev + hop_out: prev + hop_out + frame]
                seg = pad[lo: hi + frame]
                score = np.correlate(seg, ref, mode="valid")
                start = lo + int(np.argmax(score))
        piece = pad[start: start + frame]
        o = k * hop_out
        out[o: o + frame] += piece * window
        norm[o: o + frame] += window
        prev = start

    norm[norm < 1e-3] = 1.0
    out = out / norm
    n_out = int(round(x.size / rate))
    return out[:n_out].astype(np.float32)
