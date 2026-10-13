"""Sound design: riser, freeze boom, scan, orbit pad, whip; music low-passed during the freeze; game audio under it."""
import numpy as np
import os
import tempfile
from fractions import Fraction
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
                    music_gain=0.55, fx_gain=0.75, game_gain=0.6, custom_effects=None, legacy_treatment=True):
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
        out_m = np.zeros_like(mus) if legacy_treatment else mus
        for ch in range(2) if legacy_treatment else ():
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
        fo = np.clip((M - np.arange(M)) / (1.0 * SR), 0, 1) if legacy_treatment else np.ones(M)
        mix = mix + out_m * fo[:, None] * music_gain

    # game audio pieces (normal speed parts of the clip)
    if game_path:
        g = _read_wav(game_path)
        for piece in game_segments:
            out_t0, src_t0, dur, fin, fout = piece[:5]
            speed = piece[5] if len(piece)>5 else 1.0
            a, b = round(src_t0 * SR), round((src_t0 + dur*speed) * SR)
            seg = g[a:b].copy()
            if not len(seg):
                continue
            target=round(dur*SR)
            if abs(speed-1)>1e-6:
                ratio=Fraction(1/float(speed)).limit_denominator(1000)
                seg=signal.resample_poly(seg,ratio.numerator,ratio.denominator,axis=0)
            seg=seg[:target]
            if len(seg)<target: seg=np.pad(seg,((0,target-len(seg)),(0,0)))
            n = len(seg); tt = np.arange(n) / SR
            e = np.ones(n)
            if fin>0: e *= np.clip(tt/fin,0,1)
            if fout>0: e *= np.clip((dur-tt)/fout,0,1)
            o = int(out_t0 * SR)
            seg = seg[: max(0, M - o)]
            mix[o:o + len(seg)] += seg * e[:len(seg), None] * game_gain

    # Imported clips use their own fades; the automatic tail is for built-in audio only.
    end_fade = np.clip((M - np.arange(M)) / (0.7 * SR), 0, 1)
    if legacy_treatment: mix = mix * end_fade[:, None]
    background = np.zeros_like(mix)
    for effect in custom_effects or []:
        path, start, volume = effect[:3]
        fade_in, fade_out = effect[3:5] if len(effect) >= 5 else (0, 0)
        clip = _read_wav(path)
        rate = effect[5] if len(effect) > 5 else 1.0
        if abs(rate - 1) > 1e-6:
            ratio = Fraction(1 / float(rate)).limit_denominator(1000)
            target = round(len(clip) / rate)
            clip = signal.resample_poly(clip, ratio.numerator, ratio.denominator, axis=0)[:target]
            if len(clip) < target:
                clip = np.pad(clip, ((0, target - len(clip)), (0, 0)))
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
        target = background if len(effect) > 6 and effect[6] else mix
        target[offset:offset + len(clip)] += clip * volume
    if background.any():
        # Background tracks drop to 30% wherever any other sound plays, with ~0.3 s glides.
        from scipy.ndimage import uniform_filter1d
        smooth = lambda x: uniform_filter1d(x, int(.3 * SR))
        mix += background * smooth(np.where(smooth(np.abs(mix).max(axis=1)) > .02, .3, 1.))[:, None]

    mix = np.tanh(mix * 1.1) / np.tanh(1.1)
    mix /= max(1.0, np.abs(mix).max() / 0.95)
    wavfile.write(out_path, SR, (mix * 32767).astype(np.int16))


BUILTIN_SOUNDS = [
    ('freeze_mix','تجميد كامل',6.0),('impact','ضربة التجميد',2.2),
    ('whoosh','مرور سريع',.65),('riser','تصاعد',1.3),
    ('rewind','رجعة',.85),('orbit_pad','جو الدوران',3.0),
    ('scan','مسح إلكتروني',1.1),('glitch','تقطيع إلكتروني',.55),
]


def builtin_sound(key):
    """Cached ordinary WAV assets; adding one never changes video effects."""
    from .util import CACHE_DIR
    item=next((item for item in BUILTIN_SOUNDS if item[0]==key),None)
    if item is None: raise ValueError('Unknown built-in sound.')
    _,title,duration=item
    directory=os.path.join(CACHE_DIR,'sounds'); os.makedirs(directory,exist_ok=True)
    path=os.path.join(directory,'studio4_'+key+'.wav')
    if os.path.isfile(path): return path,title,duration
    fd,temp=tempfile.mkstemp(suffix='.wav',dir=directory); os.close(fd)
    try:
        if key=='freeze_mix':
            make_soundtrack(duration,0,1,4,2.7,None,None,[],temp,fx_gain=.8)
        else:
            t=np.arange(round(duration*SR))/SR; rng=np.random.default_rng(41)
            noise=rng.normal(0,.25,len(t))
            if key=='impact':
                y=np.sin(2*np.pi*np.cumsum(35+80*np.exp(-t*8))/SR)*np.exp(-t/ .45)+noise*np.exp(-t/.025)
            elif key in ('whoosh','riser','rewind'):
                low=signal.sosfilt(signal.butter(2,3800,'low',fs=SR,output='sos'),noise)
                env=np.sin(np.pi*t/duration)**1.5
                if key=='riser': env=(t/duration)**2
                f=150+2200*(t/duration if key!='rewind' else 1-t/duration)
                y=(low+np.sin(2*np.pi*np.cumsum(f)/SR)*.09)*env
            elif key=='orbit_pad':
                y=sum(np.sin(2*np.pi*f*t)*a for f,a in ((55,.25),(82.41,.2),(110,.13),(164.8,.06)))*np.sin(np.pi*t/duration)**.7
            elif key=='scan':
                y=(np.sin(2*np.pi*np.cumsum(400+1500*t/duration)/SR)*.4+noise*.2)*( .6+.4*np.sin(2*np.pi*28*t))
            else:
                y=(np.sign(np.sin(2*np.pi*700*t))*.25+noise)*((np.floor(t*45)%3)!=1)
            y*=np.clip(t/.012,0,1)*np.clip((duration-t)/.08,0,1)
            y=y/max(np.max(np.abs(y)),1e-6)*.72
            stereo=np.stack([y,y],axis=1)
            wavfile.write(temp,SR,(stereo*32767).astype(np.int16))
        os.replace(temp,path)
    finally:
        if os.path.exists(temp): os.unlink(temp)
    return path,title,duration
