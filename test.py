from alert.telegram_alert import TelegramAlert

alert = TelegramAlert(
    token="BOT_TOKEN",
    chat_id="CHAT_ID"
)

alert.send("🔥 Test AI Home Surveillance")
