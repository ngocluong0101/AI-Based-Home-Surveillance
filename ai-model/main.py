import cv2
import time
from camera.webcam import Webcam
from detection.detector import ObjectDetector
from logic.security_rules import SecurityRules
from utils.logger import Logger
from utils.event_logger import EventLogger
from alert.telegram_alert import TelegramAlert

# Init
cam = Webcam()
detector = ObjectDetector()
rules = SecurityRules()
logger = Logger()
event_logger = EventLogger()

alert = TelegramAlert(
    token="8501512619:AAH2o1K-ncYxKkdYPey15phuUqeXhKRG46U",
    chat_id="5349568679"
)

#  COOLDOWN CONFIG 
COOLDOWN_SECONDS = 60   # 1 phút
last_alert_time = 0


while True:
    frame = cam.get_frame()
    if frame is None:
        break

    results = detector.detect(frame)

    suspicious_detected = False

    for box in results[0].boxes:
        cls_id = int(box.cls[0])
        label = detector.model.names[cls_id]

        if rules.check_person(label):
            suspicious_detected = True
            break   # chỉ cần 1 người là đủ

    current_time = time.time()

    if suspicious_detected and (current_time - last_alert_time >= COOLDOWN_SECONDS):
        logger.save_image(frame)
        event_logger.log("Person detected at night")
        alert.send("🚨 Phát hiện người khả nghi vào ban đêm!")

        last_alert_time = current_time   # cập nhật thời điểm gửi

    annotated = results[0].plot()
    cv2.imshow("AI Home Surveillance", annotated)

    if cv2.waitKey(1) & 0xFF == 27:
        break

cam.release()
cv2.destroyAllWindows()
