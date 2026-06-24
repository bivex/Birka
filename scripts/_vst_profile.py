import sys, time, os, numpy as np
sys.path.insert(0, "/Volumes/External/Code/Birka/src")
import dawdreamer as daw
from birka.infrastructure import midi_renderer as mr

SR = mr._VST_SAMPLE_RATE      # 96000
BS = mr._VST_BUFFER_SIZE      # 512
P = mr._VST_PLUGIN_PATHS

# 20s stereo test signal at 96k (pink-ish noise + tone bursts -> realistic load)
dur = 20.0
n = int(dur*SR)
rng = np.random.default_rng(0)
x = (rng.standard_normal(n)*0.1).astype(np.float32)
t = np.arange(n)/SR
x += (0.2*np.sin(2*np.pi*110*t)).astype(np.float32)
audio = np.stack([x, x])  # (2, n)

# silence stderr from plugins
devnull=open(os.devnull,'w'); old=os.dup(2); os.dup2(devnull.fileno(),2)

def make(name, key):
    return eng.make_plugin_processor(name, P[key])

eng = daw.RenderEngine(SR, BS)

# plugin key -> (graph name, configured?)
chain = [
    ("chow","tape"), ("spiff","spiff"), ("soothe","soothe"), ("pro_q","pro_q"),
    ("pro_mb","pro_mb"), ("kot","kot"), ("sdrr","sdrr"), ("reverb","reverb"),
    ("fresh","fresh"), ("limiter","limiter"),
]

results=[]
for key, gname in chain:
    proc = make(gname, key)
    # apply known configs for the two that have explicit configurators
    if key=="kot": mr._configure_kotelnikov_ge(proc)
    if key=="limiter": mr._configure_limiter(proc)
    pb = eng.make_playback_processor("pb_"+gname, audio)
    eng.load_graph([(pb,[]),(proc,["pb_"+gname])])
    # warmup
    eng.render(dur)
    best=1e9
    for _ in range(3):
        t0=time.perf_counter(); eng.render(dur); dt=time.perf_counter()-t0
        best=min(best,dt)
    rtf = dur/best
    results.append((gname, best, rtf))

os.dup2(old,2); os.close(old); devnull.close()

results.sort(key=lambda r:-r[1])
print(f"{'plugin':10s} {'render_s':>9} {'xRealtime':>10} {'%ofchain':>9}")
total=sum(r[1] for r in results)
for gname,dt,rtf in results:
    print(f"{gname:10s} {dt:9.3f} {rtf:10.1f} {100*dt/total:8.1f}%")
print(f"\nsum isolated render of {dur:.0f}s audio @96k: {total:.2f}s  (chain xRT ~{dur/total:.1f})")


def profile_full_chain():
    """End-to-end timing of the real VST chain (_render_sfizz_vst_chain), which
    includes the two-pass loudness calibration. Verifies the optimization's
    effect on the production path and that the master still hits its targets."""
    import tempfile, soundfile as sf
    out = os.path.join(tempfile.mkdtemp(), "chain.wav")
    devnull = open(os.devnull, "w"); old = os.dup(2); os.dup2(devnull.fileno(), 2)
    try:
        # reset cached engine so plugin reconfig (e.g. oversampling) takes effect
        mr._VST_ENGINE = None
        mr._VST_GRAPH = None
        flat = audio.T.flatten().astype(np.float32)  # interleaved stereo @96k
        mr._render_sfizz_vst_chain(flat, SR, out)  # warmup + build engine
        best = 1e9
        for _ in range(3):
            t0 = time.perf_counter()
            mr._render_sfizz_vst_chain(flat, SR, out)
            best = min(best, time.perf_counter() - t0)
    finally:
        os.dup2(old, 2); os.close(old); devnull.close()
    a, sr = sf.read(out)
    try:
        import pyloudnorm as pyln
        lufs = pyln.Meter(sr).integrated_loudness(a)
    except Exception:
        lufs = float("nan")
    from scipy.signal import resample_poly
    tp = max(float(np.max(np.abs(resample_poly(a[:, c], 4, 1)))) for c in range(a.shape[1]))
    print(f"\nFULL CHAIN (two-pass) {dur:.0f}s @96k: {best:.2f}s  ({dur/best:.1f}x realtime)")
    print(f"  master LUFS={lufs:.1f} (target {mr.TARGET_LOUDNESS_LUFS})  "
          f"true-peak={20*np.log10(tp+1e-9):.2f} dBTP (ceiling -1.0)")


if __name__ == "__main__" or True:
    profile_full_chain()

