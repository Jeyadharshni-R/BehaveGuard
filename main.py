import json, time, os, ctypes, sys, threading
import cv2
import smtplib
import tkinter as tk
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
import requests

# ========= PATH FIX =========
def get_path(file):
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, file)
    return os.path.join(os.getcwd(), file)

def get_runtime_path(path):
    base = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.getcwd()
    return os.path.join(base, path)

# ========= CONFIG =========
with open(get_path('config.json')) as f:
    config = json.load(f)

WATCH_FOLDERS = config['watch_folders']
EMAIL = config['email']
EMAIL_PASS = config['email_password']
BOT_TOKEN = config['telegram_bot_token']
CHAT_ID = config['telegram_chat_id']

# ========= GLOBAL =========
APPROVED = False
lock_window = None

# ========= ANOMALY DETECTION =========
EVENT_COUNT = 0
LAST_EVENT_TIME = time.time()

def is_anomaly():
    global EVENT_COUNT, LAST_EVENT_TIME

    current_time = time.time()
    hour = time.localtime().tm_hour

    # Count rapid events
    if current_time - LAST_EVENT_TIME < 5:
        EVENT_COUNT += 1
    else:
        EVENT_COUNT = 1

    LAST_EVENT_TIME = current_time

    # Rule 1: Unusual time
    if hour < 6 or hour > 22:
        print("⚠️ Anomaly: unusual time access")
        return True

    # Rule 2: Too many changes quickly
    if EVENT_COUNT > 5:
        print("⚠️ Anomaly: too many file changes")
        return True

    return False

# ========= TELEGRAM =========
def send_telegram(msg, photo):
    try:
        if photo:
            with open(photo, 'rb') as p:
                requests.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                    data={"chat_id": CHAT_ID, "caption": msg},
                    files={"photo": p}
                )

        keyboard = {
            "inline_keyboard": [[
                {"text": "✅ Approve", "callback_data": "approve"},
                {"text": "❌ Reject", "callback_data": "reject"}
            ]]
        }

        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={
                "chat_id": CHAT_ID,
                "text": "🔒 System locked. Allow access?",
                "reply_markup": json.dumps(keyboard)
            }
        )

        print("📱 Telegram sent with buttons")

    except Exception as e:
        print("Telegram error:", e)

# ========= TELEGRAM LISTENER =========
def listen_for_approval():
    global APPROVED
    last_update = None

    print("📡 Waiting for phone approval...")

    while True:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
            res = requests.get(url).json()

            for update in res.get("result", []):
                update_id = update["update_id"]

                if last_update is not None and update_id <= last_update:
                    continue

                last_update = update_id

                if "callback_query" in update:
                    data = update["callback_query"]["data"]

                    if data == "approve":
                        APPROVED = True
                        print("✅ APPROVED from phone")
                        return

                    elif data == "reject":
                        print("❌ REJECTED from phone")

        except Exception as e:
            print("Listener error:", e)

        time.sleep(2)

# ========= EMAIL =========
def send_email(msg, photo):
    try:
        m = MIMEMultipart()
        m['From'] = EMAIL
        m['To'] = EMAIL
        m['Subject'] = "BehaveGuard Alert"

        m.attach(MIMEText(msg))

        if photo:
            with open(photo, 'rb') as f:
                img = MIMEImage(f.read())
                m.attach(img)

        s = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        s.login(EMAIL, EMAIL_PASS)
        s.send_message(m)
        s.quit()

        print("📧 Email sent")

    except Exception as e:
        print("Email error:", e)

# ========= CAMERA =========
def capture_photo():
    cap = cv2.VideoCapture(0)

    if not cap.isOpened():
        print("❌ Camera not working")
        return None

    ret, frame = cap.read()
    cap.release()

    folder = get_runtime_path('logs')
    os.makedirs(folder, exist_ok=True)

    path = os.path.join(folder, f"intruder_{int(time.time())}.jpg")

    if ret:
        cv2.imwrite(path, frame)
        print("📸 Photo saved:", path)
        return path
    else:
        print("❌ Capture failed")
        return None

# ========= WINDOWS LOCK =========
def lock_system():
    ctypes.windll.user32.LockWorkStation()
    print("🔒 Windows Locked")

# ========= FULL SCREEN LOCK =========
def show_lock_screen():
    global lock_window

    lock_window = tk.Tk()
    lock_window.title("Locked")
    lock_window.attributes("-fullscreen", True)
    lock_window.attributes("-topmost", True)

    label = tk.Label(
        lock_window,
        text="🔒 SYSTEM LOCKED\nWaiting for mobile approval...",
        font=("Arial", 30),
        fg="white",
        bg="black"
    )
    label.pack(expand=True, fill="both")

    lock_window.configure(bg="black")

    lock_window.protocol("WM_DELETE_WINDOW", lambda: None)

    lock_window.mainloop()

def close_lock_screen():
    global lock_window
    if lock_window:
        lock_window.destroy()

# ========= HANDLER =========
class Guard(FileSystemEventHandler):

    def on_any_event(self, event):
        if event.is_directory:
            return

        print("📂 Change detected:", event.src_path)

        # 🔥 ONLY ADDITION
        if is_anomaly():
            self.trigger_alert(event.src_path)

    def trigger_alert(self, file):
        global APPROVED

        APPROVED = False

        print("🚨 ALERT TRIGGERED")

        photo = capture_photo()

        lock_system()

        threading.Thread(target=show_lock_screen, daemon=True).start()

        msg = f"🚨 BehaveGuard Alert!\nFile changed: {file}"

        send_email(msg, photo)
        send_telegram(msg, photo)

        listen_for_approval()

        if APPROVED:
            print("✅ Approved → Unlocking system")
            close_lock_screen()
        else:
            print("❌ Not approved → System remains locked")

# ========= MAIN =========
if __name__ == "__main__":
    os.makedirs(get_runtime_path('logs'), exist_ok=True)

    observer = Observer()
    handler = Guard()

    for folder in WATCH_FOLDERS:
        observer.schedule(handler, folder, recursive=True)

    observer.start()

    print("✅ BehaveGuard RUNNING...")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()

    observer.join()