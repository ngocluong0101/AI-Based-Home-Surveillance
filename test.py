from ultralytics import YOLO
import cv2

model = YOLO("./ai-model/test_images/yolov8n.pt")
cap = cv2.VideoCapture(0)

while True:
    ret, frame = cap.read()
    if not ret:
        break

    results = model(frame, conf=0.4)

    annotated_frame = results[0].plot()
    cv2.imshow("AI Home Surveillance", annotated_frame)

    if cv2.waitKey(1) & 0xFF == 27:
        break

cap.release()
cv2.destroyAllWindows()
