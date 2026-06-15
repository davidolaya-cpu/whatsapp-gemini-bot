import os
import requests
from flask import Flask, request, jsonify
from google import genai
from google.genai import types

app = Flask(__name__)

VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN")
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

client = genai.Client(api_key=GEMINI_API_KEY)

BIENVENIDA = """🎮 ¡Bienvenido a *Game Line Col*! 🎮

Somos tu tienda de confianza para:
✅ Cuentas de *Game Pass* para Xbox y PC
✅ *Juegos para Xbox* a los mejores precios

━━━━━━━━━━━━━━━━
🕹️ *PLANES GAME PASS*
━━━━━━━━━━━━━━━━
📅 1 mes → $29.900
📅 2 meses → $55.000
📅 3 meses → $80.000
📅 6 meses → $140.000
📅 12 meses → $190.000

━━━━━━━━━━━━━━━━
🛡️ *NUESTRA GARANTÍA*
━━━━━━━━━━━━━━━━
Primero pruebas el servicio y luego pagas. ¡Tu satisfacción es nuestra prioridad!

━━━━━━━━━━━━━━━━
💳 *PAGO*
━━━━━━━━━━━━━━━━
Llave Breve Falabella al 3057059517

¿En qué te puedo ayudar hoy? 😊"""

SYSTEM_PROMPT = """Eres un asistente virtual de Game Line Col, una tienda colombiana especializada en la venta de cuentas de Game Pass para Xbox y PC, así como juegos para Xbox.

Tu nombre es GameBot y debes responder siempre en español, de forma amable, clara y profesional. Usa emojis ocasionalmente para hacer la conversación más amena 🎮

PRECIOS DE GAME PASS:
- 1 mes: $29.900
- 2 meses: $55.000
- 3 meses: $80.000
- 6 meses: $140.000
- 12 meses: $190.000

MÉTODO DE ENTREGA:
- El servicio se entrega de forma remota por WhatsApp
- El cliente debe generar un código desde su consola Xbox o PC y enviarlo al asesor
- Con ese código se hace el logueo de la cuenta de forma remota
- Es un proceso sencillo y rápido

MÉTODO DE PAGO:
- Únicamente por Llave Breve (Banco Falabella) al número 3057059517
- El pago se realiza DESPUÉS de que el cliente prueba el servicio

GARANTÍA:
- Primero se entrega el servicio para que el cliente lo revise y compruebe que funciona
- Solo después de que el cliente está satisfecho se realiza el pago
- Esto garantiza total seguridad y confianza

JUEGOS PARA XBOX:
- Si un cliente pregunta por un juego específico, búscalo en G2A.com
- Al precio que encuentres en G2A en pesos colombianos, súmale $25.000 COP y ese es el precio final
- Dale al cliente el precio final, información del juego y dile que puede adquirirlo contigo
- Si no encuentras el juego en G2A, dile al cliente que un asesor lo contactará pronto

INSTRUCCIONES GENERALES:
- Si el cliente pregunta por precios de Game Pass, muéstralos todos de forma clara
- Si el cliente quiere comprar, explícale el proceso paso a paso
- Si el cliente tiene dudas técnicas sobre Game Pass, Xbox o juegos, ayúdalo
- No inventes precios ni información que no puedas verificar
- Sé siempre amable y profesional
- Si el cliente dice Hola, Buenos días, Buenas o cualquier saludo, responde ÚNICAMENTE con el mensaje de bienvenida predefinido sin agregar nada más"""

conversaciones = {}

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
    try:
        message = data["entry"][0]["changes"][0]["value"]["messages"][0]
        phone = message["from"]
        text = message["text"]["body"].strip().lower()

        saludos = ["hola", "buenas", "buenos días", "buenas tardes", "buenas noches", "hi", "hello", "buen día"]
        es_saludo = any(saludo in text for saludo in saludos)

        if phone not in conversaciones or es_saludo:
            conversaciones[phone] = True
            send_message(phone, BIENVENIDA)
        else:
            response = client.models.generate_content(
                model="gemini-2.5-flash-lite",
                contents=f"{SYSTEM_PROMPT}\n\nCliente dice: {text}",
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())]
                )
            )
            reply = response.text
            send_message(phone, reply)

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
    requests.post(url, headers=headers, json=payload)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
