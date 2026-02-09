from ultralytics import YOLO

class ObjectDetector:
    def __init__(self, model_path="../../yolov8n.pt", conf=0.4):
        self.model = YOLO(model_path)
        self.conf = conf

    def detect(self, frame):
        results = self.model(frame, conf=self.conf)
        return results
