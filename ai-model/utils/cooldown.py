import time

class AlertCooldown:
    def __init__(self, seconds=30):
        self.last_time = 0
        self.cooldown = seconds

    def can_send(self):
        now = time.time()
        if now - self.last_time > self.cooldown:
            self.last_time = now
            return True
        return False
