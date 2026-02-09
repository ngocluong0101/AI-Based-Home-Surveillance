import cv2
import time
import os

class Logger:
    def __init__(self, base_dir="logs/intrusion"):
        os.makedirs(base_dir, exist_ok=True)
        self.base_dir = base_dir

    def save_image(self, frame):
        filename = f"{int(time.time())}.jpg"
        path = os.path.join(self.base_dir, filename)
        cv2.imwrite(path, frame)
        print(f"[LOG] Saved: {path}")
