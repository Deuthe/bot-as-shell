import subprocess, os, sys

SND_DIR = os.path.dirname(os.path.abspath(__file__)) + '/sounds'

def play(name):
    path = os.path.join(SND_DIR, name)
    if not os.path.exists(path):
        path += '.wav'
    if not os.path.exists(path):
        return False
    try:
        subprocess.run(['aplay', '-q', path], timeout=3)
        return True
    except Exception:
        try:
            subprocess.run(['ffplay', '-nodisp', '-autoexit', '-loglevel', 'quiet', path], timeout=3)
            return True
        except Exception:
            return False

if __name__ == '__main__':
    name = sys.argv[1] if len(sys.argv) > 1 else 'activate'
    play(name)
