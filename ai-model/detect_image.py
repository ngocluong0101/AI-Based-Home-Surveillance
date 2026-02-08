from ultralytics import YOLO
import cv2
import matplotlib.pyplot as plt

# Load YOLOv8 model
model = YOLO("yolov8n.pt")

# Read image
image_path = "test_images/people.jpg"
image = cv2.imread(image_path)

# Detect
results = model(image)

# Get annotated image
annotated = results[0].plot()

# Convert BGR -> RGB
annotated = cv2.cvtColor(annotated, cv2.COLOR_BGR2RGB)

# Show using matplotlib (NO Qt)
plt.figure(figsize=(10, 6))
plt.imshow(annotated)
plt.axis("off")
plt.show()

# Print detected objects
for box in results[0].boxes:
    cls_id = int(box.cls[0])
    conf = float(box.conf[0])
    print(model.names[cls_id], f"{conf:.2f}")
