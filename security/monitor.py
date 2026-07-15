import cv2, os, time, datetime, signal, sys, json
import numpy as np
from config import CONFIG
from alert import send_alert

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = BASE_DIR + '/.state'
PID_FILE = BASE_DIR + '/.monitor_pid'
MOTION_COUNTER = BASE_DIR + '/.motion_counter'

def _inc_motion_counter():
    c = 0
    try:
        if os.path.exists(MOTION_COUNTER):
            with open(MOTION_COUNTER) as f:
                c = int(f.read().strip() or '0')
        c += 1
        with open(MOTION_COUNTER, 'w') as f:
            f.write(str(c))
    except Exception:
        pass

def acquire_pid_lock():
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f:
                old_pid = int(f.read().strip())
            if old_pid != os.getpid():
                try:
                    os.kill(old_pid, 0)
                    print(f"Another monitor is already running (PID {old_pid}). Exiting.")
                    sys.exit(1)
                except OSError:
                    pass
        except (ValueError, OSError):
            pass
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))

class SecurityMonitor:
    def __init__(self):
        acquire_pid_lock()
        self.active = False
        self.last_alert_time = 0
        self.cap = None
        self.bg_subtractor = None
        self.running = True
        self.last_cleanup_time = 0
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

    def cleanup_old_captures(self):
        now = time.time()
        max_age = 12 * 3600
        capture_dir = CONFIG['capture_dir']
        if not os.path.exists(capture_dir):
            return
        for filename in os.listdir(capture_dir):
            filepath = os.path.join(capture_dir, filename)
            if os.path.isfile(filepath):
                try:
                    if now - os.path.getmtime(filepath) > max_age:
                        os.remove(filepath)
                        print(f"Cleaned up old capture: {filename}")
                except Exception as e:
                    print(f"Error cleaning up {filename}: {e}")

    def _handle_signal(self, signum, frame):
        self.running = False

    def load_state(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE) as f:
                    data = json.load(f)
                self.active = data.get('active', False)
            except (json.JSONDecodeError, OSError) as e:
                print(f"Warning: corrupted .state file — resetting to inactive ({e})")
                self.active = False
                try:
                    self.save_state()
                except OSError:
                    pass

    def save_state(self):
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, 'w') as f:
            json.dump({'active': self.active}, f)

    def init_camera(self):
        for attempt in range(5):
            try:
                self.cap = cv2.VideoCapture(CONFIG['camera_id'], cv2.CAP_V4L2)
                if self.cap.isOpened():
                    self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                    self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                    ret, _ = self.cap.read()
                    if ret:
                        print("Camera initialized")
                        return True
                self.cap.release()
                time.sleep(2)
            except Exception as e:
                print(f"Camera init attempt {attempt + 1}: {e}")
                time.sleep(2)
        return False

    def _normalize_brightness(self, frame):
        if len(frame.shape) == 3:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        else:
            gray = frame
        mean = np.mean(gray)
        if mean < 5:
            return frame
        scale = 128.0 / mean
        return cv2.convertScaleAbs(frame, alpha=min(scale, 3.0), beta=0)

    def detect_motion(self, frame):
        frame = self._normalize_brightness(frame)
        if self.bg_subtractor is None:
            self.bg_subtractor = cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=50)
            return 0

        fg_mask = self.bg_subtractor.apply(frame)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        fg_mask = cv2.erode(fg_mask, kernel, iterations=1)
        fg_mask = cv2.dilate(fg_mask, kernel, iterations=2)
        contours, _ = cv2.findContours(fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        total_motion = sum(cv2.contourArea(c) for c in contours if cv2.contourArea(c) > CONFIG['min_area'])
        return total_motion

    def capture_image(self):
        os.makedirs(CONFIG['capture_dir'], exist_ok=True)
        ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        path = os.path.join(CONFIG['capture_dir'], f'motion_{ts}.jpg')
        if self.cap:
            ret, frame = self.cap.read()
            if ret:
                cv2.imwrite(path, frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                return path, ts
        return None, ts

    def record_clip(self, ts, duration=5):
        path = os.path.join(CONFIG['capture_dir'], f'motion_{ts}.mp4')
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        fps = 15
        w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 640
        h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 480
        out = cv2.VideoWriter(path, fourcc, fps, (w, h))
        if not out.isOpened():
            return None
        for _ in range(fps * duration):
            ret, frame = self.cap.read()
            if not ret:
                break
            out.write(frame)
        out.release()
        return path if os.path.getsize(path) > 0 else None

    def run(self):
        print("Security monitor starting...")
        self.load_state()

        if self.active:
            if not self.init_camera():
                print("FATAL: Could not initialize camera")
                sys.exit(1)

        self.cleanup_old_captures()
        self.last_cleanup_time = time.time()

        warmup_frames = 0
        heartbeat_interval = 60
        last_heartbeat = time.time()
        last_state_check = 0
        was_active = self.active
        while self.running:
            now = time.time()

            if now - last_state_check >= 1:
                self.load_state()
                last_state_check = now

            if self.active and not was_active:
                print("Activating: initializing camera...")
                if self.cap is None and not self.init_camera():
                    print("FATAL: Could not reinitialize camera on activate")
                    self.active = False
                    self.save_state()
                    was_active = False
                    time.sleep(5)
                    continue
                self.bg_subtractor = None
                warmup_frames = 0
                print("Activated: resetting background model")

            if was_active and not self.active:
                print("Deactivated: releasing camera")
                if self.cap is not None:
                    self.cap.release()
                    self.cap = None
                self.bg_subtractor = None

            was_active = self.active

            if not self.active:
                warmup_frames = 0
                if now - last_heartbeat >= heartbeat_interval:
                    print(f"[IDLE] Security inactive, watching for state changes")
                    last_heartbeat = now
                if now - self.last_cleanup_time >= 3600:
                    self.cleanup_old_captures()
                    self.last_cleanup_time = now
                time.sleep(1)
                continue

            if self.cap is None:
                if not self.init_camera():
                    print("Camera lost, retrying...")
                    time.sleep(2)
                    continue

            ret, frame = self.cap.read()

            if now - last_heartbeat >= heartbeat_interval:
                print(f"[ALIVE] Monitoring active, threshold={CONFIG['motion_threshold']}")
                last_heartbeat = now

            if now - self.last_cleanup_time >= 3600:
                self.cleanup_old_captures()
                self.last_cleanup_time = now

            if not ret:
                print("Frame read failed, reinitializing...")
                self.cap.release()
                time.sleep(2)
                self.init_camera()
                continue

            if warmup_frames < 150:
                warmup_frames += 1
                normalized = self._normalize_brightness(frame)
                if self.bg_subtractor is not None:
                    self.bg_subtractor.apply(normalized)
                else:
                    self.bg_subtractor = cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=50)
                    self.bg_subtractor.apply(normalized)
                time.sleep(0.1)
                continue

            motion = self.detect_motion(frame)
            now = time.time()

            if motion > CONFIG['motion_threshold'] and (now - self.last_alert_time) > CONFIG['cooldown_seconds']:
                self.last_alert_time = now
                ts_str = datetime.datetime.now().strftime('%Y-%m-%d_%H%M%S')
                ts_display = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                img_path, ts_raw = self.capture_image()
                video_path = self.record_clip(ts_raw)
                msg = f"Motion detected at {ts_display} (intensity: {int(motion)})"
                print(f">>> {msg}")
                _inc_motion_counter()
                send_alert(msg, image_path=img_path, video_path=video_path)

        if self.cap is not None:
            self.cap.release()
        print("Monitor stopped")

if __name__ == '__main__':
    monitor = SecurityMonitor()
    monitor.run()
