import os
import re
import hmac
import hashlib
import requests

from datetime import datetime, timezone
from flask import Flask, request, jsonify
from urllib.parse import quote

app = Flask(__name__)

# CONFIGURATION
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY", "")

ACCESS_PRICE_NAIRA = int(os.getenv("ACCESS_PRICE_NAIRA", "300"))

DESTINATION_BOT_LINK = os.getenv("DESTINATION_BOT_LINK", "")
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "")

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
PAYSTACK_API = "https://api.paystack.co"


# TELEGRAM MESSAGING

def send_message(chat_id, text):
    if not BOT_TOKEN:
        return False

    try:
        response = requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True
            },
            timeout=20
        )

        return response.ok and response.json().get("ok", False)

    except Exception as e:
        print("Telegram error:", e)
        return False


# SUPABASE CONNECTION

def supabase_request(method, endpoint, **kwargs):
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise Exception("Supabase configuration is missing.")

    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    }

    extra_headers = kwargs.pop("headers", {})
    headers.update(extra_headers)

    response = requests.request(
        method,
        f"{SUPABASE_URL}/rest/v1/{endpoint}",
        headers=headers,
        timeout=20,
        **kwargs
    )

    response.raise_for_status()

    if response.text:
        return response.json()

    return None


def get_purchase(reference):
    rows = supabase_request(
        "GET",
        "purchases",
        params={
            "paystack_reference": f"eq.{reference}",
            "select": "*",
            "limit": "1"
        }
    )

    return rows[0] if rows else None


def update_purchase(reference, data, extra_params=None):
    params = {
        "paystack_reference": f"eq.{reference}"
    }

    if extra_params:
        params.update(extra_params)

    return supabase_request(
        "PATCH",
        "purchases",
        params=params,
        json=data,
        headers={
            "Prefer": "return=representation"
        }
    )


# PAYMENT DELIVERY

def deliver_access(reference):
    purchase = get_purchase(reference)

    if not purchase:
        return False

    if purchase.get("payment_status") != "paid":
        return False

    if purchase.get("access_delivered"):
        return True

    telegram_id = purchase.get("telegram_user_id")

    if not DESTINATION_BOT_LINK:
        print("Destination link is missing.")
        return False

    message = (
        "Payment confirmed successfully! 🎉\n\n"
        "Your lifetime access link is ready:\n\n"
        f"{DESTINATION_BOT_LINK}\n\n"
        "Thank you for using Veltrix Hub."
    )

    if send_message(telegram_id, message):
        update_purchase(
            reference,
            {"access_delivered": True}
        )
        return True

    return False


# VERIFY PAYMENT WITH PAYSTACK

def verify_payment(reference, telegram_id):
    if not PAYSTACK_SECRET_KEY:
        return False, "Payment configuration is missing."

    purchase = get_purchase(reference)

    if not purchase:
        return False, "Payment reference was not found."

    if str(purchase["telegram_user_id"]) != str(telegram_id):
        return False, "This payment does not belong to your account."

    headers = {
        "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}"
    }

    response = requests.get(
        f"{PAYSTACK_API}/transaction/verify/{quote(reference, safe='')}",
        headers=headers,
        timeout=30
    )

    result = response.json()

    if not result.get("status"):
        return False, "Paystack could not verify this payment."

    data = result.get("data", {})

    if data.get("status") != "success":
        return False, "Your payment has not been completed."

    if data.get("amount") != ACCESS_PRICE_NAIRA * 100:
        return False, "Payment amount does not match."

    if data.get("currency") != "NGN":
        return False, "Payment currency is incorrect."

    metadata = data.get("metadata", {})

    if str(metadata.get("telegram_user_id")) != str(telegram_id):
        return False, "Payment account verification failed."

    update_purchase(
        reference,
        {
            "payment_status": "paid",
            "paid_at": datetime.now(timezone.utc).isoformat()
        }
    )

    deliver_access(reference)

    return True, "Payment confirmed."


# CREATE PAYMENT

def create_payment(chat_id, email):
    if not PAYSTACK_SECRET_KEY:
        send_message(chat_id, "Payment service is not configured.")
        return

    if ACCESS_PRICE_NAIRA <= 0:
        send_message(chat_id, "Payment price is not configured.")
        return

    headers = {
        "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "email": email,
        "amount": ACCESS_PRICE_NAIRA * 100,
        "currency": "NGN",
        "metadata": {
            "telegram_user_id": str(chat_id)
        }
    }

    try:
        response = requests.post(
            f"{PAYSTACK_API}/transaction/initialize",
            json=payload,
            headers=headers,
            timeout=30
        )

        result = response.json()


        if not result.get("status"):
            print(
                "Paystack initialization failed:",
                response.status_code,
                result.get("message"),
                result.get("errors")
            )
            send_message(
                chat_id,
                "Payment could not be created. Please contact support."
            )
            return

        data = result["data"]
        reference = data["reference"]
        payment_url = data["authorization_url"]

        # SAVE PENDING PAYMENT IN SUPABASE

        supabase_request(
            "POST",
            "purchases",
            json={
                "telegram_user_id": int(chat_id),
                "paystack_reference": reference,
                "amount_kobo": ACCESS_PRICE_NAIRA * 100,
                "payment_status": "pending",
                "currency": "NGN"
            },
            headers={
                "Prefer": "return=representation"
            }
        )

        send_message(
            chat_id,
            "Your payment has been created.\n\n"
            f"Amount: ₦{ACCESS_PRICE_NAIRA}\n\n"
            f"Pay here:\n{payment_url}\n\n"
            f"Payment reference:\n{reference}\n\n"
            "After paying, return here and type /verify."
        )

    except Exception as e:
        print("Payment creation error:", e)
        send_message(
            chat_id,
            "Something went wrong while creating your payment. Please try again."
        )


# BOT COMMANDS

user_states = {}


def handle_command(chat_id, text):
    text = text.strip()

    if text.startswith("/start"):
        send_message(
            chat_id,
            "👋 Welcome to VELTRIX HUB!\n\n"
            "Choose a command below:\n\n"
            "/access - Purchase lifetime access\n"
            "/plans - View available plans\n"
            "/payment - Make a payment\n"
            "/verify - Verify your payment\n"
            "/status - Check your access status\n"
            "/help - Get assistance\n"
            "/support - Contact customer support"
        )

    elif text.startswith("/access"):
        send_message(
            chat_id,
            f"Lifetime access costs ₦{ACCESS_PRICE_NAIRA}.\n\n"
            "Use /payment to continue."
        )

    elif text.startswith("/plans"):
        send_message(
            chat_id,
            f"AVAILABLE PLAN\n\n"
            f"Lifetime Access: ₦{ACCESS_PRICE_NAIRA}\n"
            "One-time payment."
        )

    elif text.startswith("/payment"):
        user_states[chat_id] = "email"

        send_message(
            chat_id,
            "Please enter your email address to create your payment."
        )

    elif text.startswith("/verify"):
        try:
            rows = supabase_request(
                "GET",
                "purchases",
                params={
                    "telegram_user_id": f"eq.{chat_id}",
                    "payment_status": "eq.pending",
                    "select": "paystack_reference",
                    "order": "created_at.desc",
                    "limit": "1"
                }
            )

            if not rows:
                send_message(
                    chat_id,
                    "No pending payment was found. Use /payment to start."
                )
                return

            reference = rows[0]["paystack_reference"]

            success, message = verify_payment(reference, chat_id)

            send_message(chat_id, message)

        except Exception as e:
            print("Verification error:", e)
            send_message(
                chat_id,
                "Unable to verify your payment right now. Please try again."
            )

    elif text.startswith("/status"):
        try:
            rows = supabase_request(
                "GET",
                "purchases",
                params={
                    "telegram_user_id": f"eq.{chat_id}",
                    "payment_status": "eq.paid",
                    "select": "*",
                    "order": "created_at.desc",
                    "limit": "1"
                }
            )

            if rows:
                purchase = rows[0]

                if not purchase.get("access_delivered"):
                    deliver_access(purchase["paystack_reference"])

                send_message(
                    chat_id,
                    "Your payment is confirmed. Check your messages for your access link."
                )
            else:
                send_message(
                    chat_id,
                    "No successful payment was found. Use /payment to purchase access."
                )

        except Exception as e:
            print("Status error:", e)
            send_message(chat_id, "Could not check your status. Try again later.")

    elif text.startswith("/help"):
        send_message(
            chat_id,
            "Welcome to Veltrix Hub support.\n\n"
            "Use /payment to purchase access.\n"
            "Use /verify after making payment.\n"
            "Use /status to check your access."
        )

    elif text.startswith("/support"):
        if SUPPORT_USERNAME:
            send_message(
                chat_id,
                f"Contact customer support: @{SUPPORT_USERNAME.lstrip('@')}"
            )
        else:
            send_message(chat_id, "Please contact the bot administrator.")

    else:
        if user_states.get(chat_id) == "email":
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", text):
                send_message(chat_id, "Please enter a valid email address.")
                return

            user_states.pop(chat_id, None)
            create_payment(chat_id, text)

        else:
            send_message(
                chat_id,
                "I don't understand that message. Use /start to see the commands."
            )


# TELEGRAM WEBHOOK

@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    update = request.get_json(silent=True) or {}

    message = update.get("message", {})

    if message:
        chat_id = message.get("chat", {}).get("id")
        text = message.get("text", "")

        if chat_id and text:
            handle_command(chat_id, text)

    return jsonify({"ok": True})


# PAYSTACK WEBHOOK

@app.route("/paystack-webhook", methods=["POST"])
def paystack_webhook():
    if not PAYSTACK_SECRET_KEY:
        return jsonify({"error": "Configuration missing"}), 500

    raw_body = request.get_data()

    signature = request.headers.get("x-paystack-signature", "")

    expected_signature = hmac.new(
        PAYSTACK_SECRET_KEY.encode("utf-8"),
        raw_body,
        hashlib.sha512
    ).hexdigest()

    if not hmac.compare_digest(signature, expected_signature):
        return jsonify({"error": "Invalid signature"}), 401

    event = request.get_json(silent=True) or {}

    if event.get("event") == "charge.success":
        data = event.get("data", {})
        reference = data.get("reference")

        if not reference:
            return jsonify({"error": "Missing reference"}), 400

        try:
            purchase = get_purchase(reference)

            if not purchase:
                return jsonify({"error": "Purchase not found"}), 404

            if data.get("status") != "success":
                return jsonify({"ok": True})

            if data.get("amount") != ACCESS_PRICE_NAIRA * 100:
                return jsonify({"error": "Incorrect amount"}), 400

            if data.get("currency") != "NGN":
                return jsonify({"error": "Incorrect currency"}), 400

            metadata = data.get("metadata", {})

            if str(metadata.get("telegram_user_id")) != str(
                purchase["telegram_user_id"]
            ):
                return jsonify({"error": "User mismatch"}), 400

            update_purchase(
                reference,
                {
                    "payment_status": "paid",
                    "paid_at": datetime.now(timezone.utc).isoformat()
                }
            )

            deliver_access(reference)

        except Exception as e:
            print("Paystack webhook error:", e)
            return jsonify({"error": "Processing failed"}), 500

    return jsonify({"ok": True})


# HEALTH CHECK

@app.route("/", methods=["GET"])
def home():
    return "Veltrix Hub is running!"


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "online"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
