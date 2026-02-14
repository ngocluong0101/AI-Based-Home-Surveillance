import json

EVENT_FILE = "logs/events.json"

def get_events():
    try:
        with open(EVENT_FILE, "r") as f:
            return json.load(f)
    except:
        return []
