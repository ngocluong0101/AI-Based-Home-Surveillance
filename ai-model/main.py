import cv2
from camera.webcam import Webcam
from detection.detector import ObjectDetector
from logic.security_rules import SecurityRules
from utils.logger import Logger

cam = Webcam()
detector = ObjectDetector()
rules = SecurityRules()
logger = Logger()

while True:
    frame = cam.get_frame()
    if frame is None:
        break

    results = detector.detect(frame)

    for box in results[0].boxes:
        cls_id = int(box.cls[0])
        label = detector.model.names[cls_id]

        if rules.check_person(label):
            logger.save_image(frame)

    annotated = results[0].plot()
    cv2.imshow("AI Home Surveillance", annotated)

    if cv2.waitKey(1) & 0xFF == 27:
        break

cam.release()
