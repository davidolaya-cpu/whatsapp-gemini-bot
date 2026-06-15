import os
import requests
from flask import Flask, request, jsonify
from google import genai

app = Flask(__name__)

VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN")
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

client = genai.Client(api_key=GEMINI_API_KEY)

@app.route("/webhook", methods=["GET"])
def verify():
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if token == VERIFY_TOKEN:
        return challenge, 200
    return "Token inválido", 403

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    print(f"Mensaje recibido: {data}")
    try:
        message = data["entry"][0]["changes"][0]["value"]["messages"][0]
        phone = message["from"]
        text = message["text"]["body"]
        print(f"De: {phone}, Texto: {text}")
        response = client.models.generate_content(model="gemini-2.5-flash-lite", contents=text)
        reply = response.text
        print(f"Respuesta Gemini: {reply}")
        result = send_message(phone, reply)
        print(f"Resultado envío: {result}")
    except Exception as e:
        print(f"Error: {e}")
    return jsonify({"status": "ok"}), 200

def send_message(phone, message):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": message}
    }
    response = requests.post(url, headers=headers, json=payload)
    return response.json()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
