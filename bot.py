import os
import re
import hmac
import hashlib
import requests

from flask import Flask, request, jsonify

app = Flask(__name__)

# Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY", "")
ACCESS_PRICE_NAIRA = int(os.getenv("ACCESS_PRICE_NAIRA", "0"))
DESTINATION_BOT_LINK = os.getenv("DESTINATION_BOT_LINK", "")
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
PAYSTACK_API = "https://api.paystack.co"

# Temporary conversation states
user_states = {}


def send_message(chat_id, text):
    if not BOT_TOKEN:
        return

    requests.post(
        f"{TELEGRAM_API}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        },
        timeout=20
    )


def main_menu(chat_id):
    send_message(
        chat_id,
        "🏠 <b>VELTRIX HUB</b>\n\n"
        "Welcome! Choose a command below:\n\n"
        "/access - Purchase lifetime access\n"
        "/plans - View available plans\n"
        "/payment - Make a payment\n"
        "/verify - Verify your payment\n"
        "/status - Check your access status\n"
        "/help - Get assistance\n"
        "/support - Contact customer support"
    )


def verify_payment(reference, telegram_id):
    headers = {
        "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}"
    }

    response = requests.get(
        f"{PAYSTACK_API}/transaction/verify/{reference}",
        headers=headers,
        timeout=30
    )

    result = response.json()

    if not result.get("status"):
        return False, "Payment could not be verified."

    data = result.get("data", {})

    if data.get("status") != "success":
        return False, "Your payment has not been completed successfully."

    if data.get("currency") != "NGN":
        return False, "The payment currency is incorrect."

    if data.get("amount") != ACCESS_PRICE_NAIRA * 100:
        return False, "The payment amount does not match the access price."

    metadata = data.get("metadata") or {}

    if str(metadata.get("telegram_user_id")) != str(telegram_id):
        return False, "This payment reference does not belong to your account."

    return True, "Payment successfully verified!"


def handle_command(chat_id, user_id, text):
    command = text.split()[0].split("@")[0].lower()
    arguments = text.split(maxsplit=1)

    if command == "/start":
        user_states.pop(user_id, None)
        main_menu(chat_id)

    elif command == "/access":
        send_message(
            chat_id,
            "🔐 <b>Lifetime Access</b>\n\n"
            "Purchase authorised access to our available Telegram service.\n\n"
            "Use /plans to view the price.\n"
            "Use /payment to continue."
        )

    elif command == "/plans":
        if ACCESS_PRICE_NAIRA <= 0:
            send_message(chat_id, "Access pricing is being configured. Please check again later.")
        else:
            send_message(
                chat_id,
                "📦 <b>AVAILABLE PLAN</b>\n\n"
                f"Lifetime Access: ₦{ACCESS_PRICE_NAIRA:,}\n\n"
                "Use /payment to purchase."
            )

    elif command == "/payment":
        if ACCESS_PRICE_NAIRA <= 0:
            send_message(chat_id, "Payments are not available yet. Please check again later.")
        elif not PAYSTACK_SECRET_KEY:
            send_message(chat_id, "Payment service is not configured yet.")
        else:
            user_states[user_id] = "email"
            send_message(
                chat_id,
                "💳 <b>Make Payment</b>\n\n"
                "Please enter your email address to continue with Paystack."
            )

    elif command in ["/verify", "/status"]:
        if len(arguments) > 1:
            reference = arguments[1].strip()
            check_reference(chat_id, user_id, reference)
        else:
            user_states[user_id] = "reference"
            send_message(
                chat_id,
                "Please enter your Paystack payment reference."
            )

    elif command == "/help":
        send_message(
            chat_id,
            "ℹ️ <b>VELTRIX HUB HELP</b>\n\n"
            "Use /access to learn about lifetime access.\n"
            "Use /payment to make a payment.\n"
            "Use /verify to verify a payment.\n"
            "Use /status to check your access."
        )

    elif command == "/support":
        if SUPPORT_USERNAME:
            send_message(chat_id, f"Contact customer support: {SUPPORT_USERNAME}")
        else:
            send_message(chat_id, "Customer support details are being updated.")

    else:
        send_message(chat_id, "Unknown command. Use /help to see available commands.")


def check_reference(chat_id, user_id, reference):
    if not PAYSTACK_SECRET_KEY:
        send_message(chat_id, "Payment verification is not configured yet.")
        return

    try:
        success, message = verify_payment(reference, user_id)

        if success:
            send_message(
                chat_id,
                "✅ <b>PAYMENT VERIFIED</b>\n\n"
                f"{message}\n\n"
                "Your payment has been confirmed."
            )

            if DESTINATION_BOT_LINK:
                send_message(
                    chat_id,
                    "🎉 Your authorised access link:\n\n"
                    f"{DESTINATION_BOT_LINK}"
                )
            else:
                send_message(
                    chat_id,
                    "Your payment is confirmed. Access delivery is being configured."
                )
        else:
            send_message(chat_id, f"❌ {message}")

    except Exception:
        send_message(
            chat_id,
            "We couldn't verify your payment right now. Please try again later."
        )


def create_payment(chat_id, user_id, email):
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        send_message(chat_id, "That email address doesn't look valid. Please try again.")
        return

    amount = ACCESS_PRICE_NAIRA * 100

    payload = {
        "email": email,
        "amount": amount,
        "currency": "NGN",
        "metadata": {
            "telegram_user_id": str(user_id)
        }
    }

    headers = {
        "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(
            f"{PAYSTACK_API}/transaction/initialize",
            json=payload,
            headers=headers,
            timeout=30
        )

        result = response.json()

        if result.get("status"):
            payment_url = result["data"]["authorization_url"]
            reference = result["data"]["reference"]

            send_message(
                chat_id,
                "✅ <b>Payment link created!</b>\n\n"
                f"Amount: ₦{ACCESS_PRICE_NAIRA:,}\n"
                f"Reference: <code>{reference}</code>\n\n"
                "Tap the link below to pay:\n"
                f"{payment_url}\n\n"
                "After payment, return here and use /verify."
            )
        else:
            send_message(chat_id, "Unable to create your payment link. Please try again.")

    except Exception:
        send_message(chat_id, "Payment service is temporarily unavailable.")


@app.route("/")
def home():
    return "Veltrix Hub is running!"


@app.route("/health")
def health():
    return jsonify({"status": "online"})


@app.route("/webhook", methods=["POST"])
def webhook():
    update = request.get_json(silent=True) or {}

    message = update.get("message")

    if not message:
        return jsonify({"ok": True})

    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]
    text = message.get("text", "").strip()

    if not text:
        return jsonify({"ok": True})

    if text.startswith("/"):
        user_states.pop(user_id, None)
        handle_command(chat_id, user_id, text)

    elif user_states.get(user_id) == "email":
        user_states.pop(user_id, None)
        create_payment(chat_id, user_id, text)

    elif user_states.get(user_id) == "reference":
        user_states.pop(user_id, None)
        check_reference(chat_id, user_id, text)

    else:
        send_message(chat_id, "Use /start to open Veltrix Hub.")

    return jsonify({"ok": True})
