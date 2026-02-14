import json
from datetime import datetime

class EventLogger:
    def __init__(self, file="logs/events.json"):
        self.file = file

    def log(self, message):
        event = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "event": message
        }

        try:
            with open(self.file, "r") as f:
                data = json.load(f)
        except:
            data = []

        data.append(event)

        with open(self.file, "w") as f:
            json.dump(data, f, indent=4)
