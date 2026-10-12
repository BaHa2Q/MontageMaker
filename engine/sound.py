"""Sound design: riser, freeze boom, scan, orbit pad, whip; music low-passed during the freeze; game audio under it."""
import numpy as np
from scipy import signal
from scipy.io import wavfile

SR = 48000


def _read_wav(path):
    sr, x = wavfile.read(path)
    dtype = x.dtype
    if np.issubdtype(dtype, np.integer):
        if dtype == np.uint8:
            x = (x.astype(np.float32) - 128.0) / 128.0
        else:
            x = x.astype(np.float32) / float(max(abs(np.iinfo(dtype).min), np.iinfo(dtype).max))
    else:
        x = x.astype(np.float32)
    if x.ndim == 1:
        x = np.stack([x, x], 1)
    if sr != SR:
        x = signal.resample_poly(x, SR, sr, axis=0)
    return x[:, :2]


def make_soundtrack(total, t_ramp, t_freeze, t_resume, orbit_end, music_path, game_path, game_segments, out_path,
                    music_gain=0.55, fx_gain=0.75, game_gain=0.6, custom_effects=None):
    N = int(total * SR) + SR
    L = np.zeros(N); R = np.zeros(N)
    rng = np.random.default_rng(3)

    def add(sig, t0, pan=0.0, gain=1.0):
        if t0 < 0:
            skip = int(-t0 * SR)
            sig = sig[skip:]
            if np.ndim(pan):
                pan = pan[skip:]
        i0 = max(0, int(t0 * SR))
        if i0 >= N:
            return
        sig = sig[: N - i0]
        gl = np.cos((pan + 1) * np.pi / 4) * np.sqrt(2); gr = np.sin((pan + 1) * np.pi / 4) * np.sqrt(2)
        if np.ndim(pan):
            gl, gr = gl[: len(sig)], gr[: len(sig)]
        L[i0:i0 + len(sig)] += sig * gain * gl; R[i0:i0 + len(sig)] += sig * gain * gr

    def noise(dur, f0, f1, bw=1.0, curve=1.0):
        n = int(dur * SR)
        f, t, Z = signal.stft(rng.normal(0, 1, n), SR, nperseg=1024)
        fc = np.exp(np.log(f0) + (np.log(f1) - np.log(f0)) * np.clip(t / dur, 0, 1) ** curve)
        mask = np.exp(-0.5 * ((np.log2(np.maximum(f, 1))[:, None] - np.log2(fc)[None, :]) / (bw / 2)) ** 2)
        y = signal.istft(Z * mask, SR, nperseg=1024)[1][:n]
        return y / (np.abs(y).max() + 1e-9)

    def reverb(x, decay=1.4, mix=0.35):
        n = int(decay * SR)
        ir = rng.normal(0, 1, n) * np.exp(-np.arange(n) / SR * 5.0 / decay)
        ir = signal.sosfilt(signal.butter(2, 6000, 'low', fs=SR, output='sos'), ir)
        ir /= np.abs(ir).sum() ** 0.5 * 20
        wet = signal.fftconvolve(x, ir)
        out = np.zeros(len(x) + n); out[: len(x)] += x * (1 - mix); out[: len(wet)] += wet[: len(out)] * mix * 8
        return out

    # 1) riser into the freeze
    d = max(t_freeze - t_ramp + 0.05, 0.2)
    rise = noise(d, 300, 5000, 1.2, 2.0); tt = np.arange(len(rise)) / SR
    rise *= (tt / d) ** 2.2
    tone = np.sin(2 * np.pi * np.cumsum(180 + 520 * (tt / d) ** 2) / SR) * 0.25 * (tt / d) ** 2
    add(rise * 0.55 + tone, t_ramp, gain=0.8)
    # 2) impact
    n = int(2.2 * SR); tt = np.arange(n) / SR
    boom = np.tanh(np.sin(2 * np.pi * np.cumsum(62 * np.exp(-tt * 1.6) + 30) / SR) * np.exp(-tt / 0.75) * 1.8) * 0.9
    click = rng.normal(0, 1, n) * np.exp(-tt / 0.012) * 0.6
    shimmer = noise(2.2, 7000, 9000, 1.0) * np.exp(-tt / 0.35) * 0.35
    add(reverb(boom + click + shimmer, 2.0, 0.3), t_freeze - 0.005)
    # 3) scan
    ts, td = t_freeze + 0.22, 0.9
    n = int(td * SR); tt = np.arange(n) / SR
    fs_ = 420 + 1300 * (tt / td)
    sq = np.sign(np.sin(2 * np.pi * np.cumsum(fs_) / SR)) * 0.5 + np.sin(2 * np.pi * np.cumsum(fs_ * 2) / SR) * 0.3
    sq = signal.sosfilt(signal.butter(2, 3500, 'low', fs=SR, output='sos'), sq)
    env = np.minimum(1, tt / 0.05) * np.minimum(1, (td - tt) / 0.15)
    add(reverb(sq * (0.6 + 0.4 * np.sign(np.sin(2 * np.pi * 28 * tt))) * env * 0.22, 1.0, 0.35), ts)
    for k, f0 in enumerate([1760, 2349, 2637]):
        tb = np.arange(int(0.09 * SR)) / SR
        add(np.sin(2 * np.pi * f0 * tb) * np.exp(-tb / 0.03) * 0.18, ts + td + 0.02 + k * 0.07, pan=0.3 * (k - 1))
    # 4) orbit pad + moving air
    t0, t1 = t_freeze + 0.3, t_freeze + orbit_end
    dur = max(t1 - t0, 0.5)
    n = int((dur + 0.6) * SR); tt = np.arange(n) / SR
    pad = np.zeros(n)
    for f0, a in ((55, 0.5), (82.41, 0.35), (110.0, 0.3), (164.8, 0.15), (220.0, 0.08)):
        pad += a * (np.sin(2 * np.pi * f0 * tt) + np.sin(2 * np.pi * f0 * 1.004 * tt + 1.3))
    pad = signal.sosfilt(signal.butter(2, 900, 'low', fs=SR, output='sos'), pad)
    penv = np.clip(tt / 0.9, 0, 1) * np.clip((dur + 0.4 - tt) / 0.6, 0, 1)
    add(pad * penv * 0.16, t0)
    add(noise(n / SR, 500, 1400, 1.5) * penv * 0.12, t0, pan=np.clip(-0.7 + 1.4 * (tt / dur), -0.8, 0.8))
    # 5) whip + resume hit
    ws = t_freeze + orbit_end - 0.07
    wd = max(t_resume - ws + 0.1, 0.2)
    wh = noise(wd, 3500, 350, 1.3, 0.8); tt = np.arange(len(wh)) / SR
    add(wh * np.sin(np.pi * np.clip(tt / wd, 0, 1)) ** 1.5, ws, pan=np.linspace(0.7, -0.7, len(wh)), gain=0.9)
    n = int(0.9 * SR); tt = np.arange(n) / SR
    kick = np.sin(2 * np.pi * np.cumsum(120 * np.exp(-tt * 18) + 45) / SR) * np.exp(-tt / 0.22)
    snap = noise(0.9, 2500, 2500, 2.0) * np.exp(-tt / 0.05) * 0.5
    add(reverb(np.tanh(1.5 * kick) * 0.8 + snap, 1.2, 0.25), t_resume - 0.01)

    M = int(total * SR)
    fx = np.stack([L, R], -1)[:M]
    fx /= max(np.abs(fx).max(), 1e-9) / 0.89
    mix = fx * fx_gain

    # music: low-passed during the freeze, opening into the resume
    if music_path:
        mus = _read_wav(music_path)[:M]
        if len(mus) < M:
            mus = np.pad(mus, ((0, M - len(mus)), (0, 0)))
        nper, hop = 2048, 512
        out_m = np.zeros_like(mus)
        for ch in range(2):
            f, tt, Z = signal.stft(mus[:, ch], SR, nperseg=nper, noverlap=nper - hop)
            fc = np.full(len(tt), 20000.0)
            a1 = t_freeze - 0.02
            dn = (tt >= a1) & (tt < a1 + 0.12)
            fc[dn] = np.exp(np.log(20000) + (np.log(420) - np.log(20000)) * (tt[dn] - a1) / 0.12)
            fc[(tt >= a1 + 0.12) & (tt < t_resume - 0.95)] = 420
            up = (tt >= t_resume - 0.95) & (tt < t_resume)
            fc[up] = np.exp(np.log(420) + (np.log(20000) - np.log(420)) * ((tt[up] - (t_resume - 0.95)) / 0.95) ** 2.2)
            g = np.ones(len(tt)); g[(tt >= a1) & (tt < t_resume - 0.4)] = 0.85
            y = signal.istft(Z * (1.0 / np.sqrt(1 + (f[:, None] / fc[None, :]) ** 8)) * g[None, :], SR, nperseg=nper, noverlap=nper - hop)[1]
            out_m[:len(y[:M]), ch] = y[:M]
        fo = np.clip((M - np.arange(M)) / (1.0 * SR), 0, 1)
        mix = mix + out_m * fo[:, None] * music_gain

    # game audio pieces (normal speed parts of the clip)
    if game_path:
        g = _read_wav(game_path)
        for out_t0, src_t0, dur, fin, fout in game_segments:
            a, b = int(src_t0 * SR), int((src_t0 + dur) * SR)
            seg = g[a:b].copy()
            if not len(seg):
                continue
            n = len(seg); tt = np.arange(n) / SR
            e = np.clip(tt / max(fin, 1e-3), 0, 1) * np.clip((dur - tt) / max(fout, 1e-3), 0, 1)
            o = int(out_t0 * SR)
            seg = seg[: max(0, M - o)]
            mix[o:o + len(seg)] += seg * e[:len(seg), None] * game_gain

    # Imported clips use their own fades; the automatic tail is for built-in audio only.
    end_fade = np.clip((M - np.arange(M)) / (0.7 * SR), 0, 1)
    mix = mix * end_fade[:, None]
    for effect in custom_effects or []:
        path, start, volume = effect[:3]
        fade_in, fade_out = effect[3:5] if len(effect) >= 5 else (0, 0)
        clip = _read_wav(path)
        offset = int(start * SR)
        if offset >= M:
            continue
        clip = clip[:M - offset]
        samples = np.arange(len(clip)) / SR
        envelope = np.ones(len(clip), dtype=np.float32)
        if fade_in:
            envelope *= np.clip(samples / fade_in, 0, 1)
        if fade_out:
            envelope *= np.clip((len(clip) / SR - samples) / fade_out, 0, 1)
        clip *= envelope[:, None]
        mix[offset:offset + len(clip)] += clip * volume

    mix = np.tanh(mix * 1.1) / np.tanh(1.1)
    mix /= max(1.0, np.abs(mix).max() / 0.95)
    wavfile.write(out_path, SR, (mix * 32767).astype(np.int16))
