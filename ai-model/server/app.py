from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import json
import os

app = FastAPI(title="AI Home Surveillance")

EVENT_FILE = "logs/events.json"


def get_last_event():
    if not os.path.exists(EVENT_FILE):
        return "No events yet"

    try:
        with open(EVENT_FILE, "r") as f:
            events = json.load(f)
            if len(events) == 0:
                return "No events yet"
            return events[-1]["message"]
    except:
        return "Error reading events"


@app.get("/", response_class=HTMLResponse)
def dashboard():
    last_event = get_last_event()

    return f"""
    <html>
        <head>
            <title>AI Home Surveillance</title>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    background-color: #f5f5f5;
                    padding: 40px;
                }}
                .card {{
                    background: white;
                    padding: 20px;
                    border-radius: 10px;
                    width: 400px;
                    box-shadow: 0 4px 10px rgba(0,0,0,0.1);
                }}
                h1 {{
                    color: #222;
                }}
                .status {{
                    color: green;
                    font-weight: bold;
                }}
            </style>
        </head>
        <body>
            <div class="card">
                <h1>🏠 AI Home Surveillance</h1>
                <p>📷 Camera: <span class="status">Online</span></p>
                <p>⚙️ System: <span class="status">Running</span></p>
                <p>🚨 Last Event: <b>{last_event}</b></p>
            </div>
        </body>
    </html>
    """
