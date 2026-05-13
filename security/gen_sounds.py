import wave, struct, math, array, os

SAMPLE_RATE = 44100
SOUNDS_DIR = os.path.dirname(os.path.abspath(__file__)) + '/sounds'
os.makedirs(SOUNDS_DIR, exist_ok=True)

def make_wav(filename, freq_pairs, duration, amplitude=0.4):
    frames = []
    total_samples = int(SAMPLE_RATE * duration)
    for i in range(total_samples):
        t = i / SAMPLE_RATE
        sample = 0
        for freq, vol in freq_pairs:
            sample += int(32767 * amplitude * vol * math.sin(2 * math.pi * freq * t))
        sample = max(-32768, min(32767, int(sample)))
        frames.append(sample)
    path = os.path.join(SOUNDS_DIR, filename)
    with wave.open(path, 'w') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(array.array('h', frames).tobytes())
    print(f"Created {path}")

make_wav('activate.wav', [(880, 0.7), (1320, 0.3)], 0.8)
make_wav('deactivate.wav', [(1320, 0.7), (880, 0.3)], 0.8)
make_wav('alarm.wav', [(440, 0.5), (880, 0.5)], 1.0, amplitude=0.6)
