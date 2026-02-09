import datetime

class SecurityRules:
    def __init__(self):
        self.suspicious_hours = (0, 23)  # 22h → 5h

    def is_night_time(self):
        hour = datetime.datetime.now().hour
        return hour >= 0 or hour <= 23

    def check_person(self, label):
        if label == "person" and self.is_night_time():
            return True
        return False
