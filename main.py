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

ADMIN_PHONE = "573229082927"

client = genai.Client(api_key=GEMINI_API_KEY)

BIENVENIDA = """🎮 ¡Bienvenido a *Game Line Col*! 🎮

Somos tu tienda de confianza para juegos y suscripciones Xbox 🕹️

¿En qué te podemos ayudar hoy?

1️⃣ *Game Pass Ultimate* (Xbox y PC)
2️⃣ *Juegos Xbox*
3️⃣ *Soporte*

Responde con el número de tu opción 😊"""

MENU = """¿En qué más te puedo ayudar? 😊

1️⃣ *Game Pass Ultimate* (Xbox y PC)
2️⃣ *Juegos Xbox*
3️⃣ *Soporte*"""

GAMEPASS = """🕹️ *GAME PASS ULTIMATE - Xbox y PC* 🕹️

Con Game Pass Ultimate tienes acceso a cientos de juegos en Xbox y PC.

━━━━━━━━━━━━━━━━
💰 *PRECIOS*
━━━━━━━━━━━━━━━━
📅 1 mes → $29.900
📅 2 meses → $55.000
📅 3 meses → $80.000
📅 6 meses → $140.000
📅 12 meses → $190.000

━━━━━━━━━━━━━━━━
🛡️ *GARANTÍA*
━━━━━━━━━━━━━━━━
✅ Primero pruebas el servicio
✅ Solo pagas cuando estés satisfecho

━━━━━━━━━━━━━━━━
💳 *PAGO*
━━━━━━━━━━━━━━━━
Llave Breve Falabella al *3057059517*

━━━━━━━━━━━━━━━━
📲 *¿CÓMO ACTIVARLO?*
━━━━━━━━━━━━━━━━
Sigue estos pasos en tu consola:

1️⃣ Ve a *"Agregar nuevo"* (como si fueras a agregar una nueva cuenta)
2️⃣ Selecciona *"Usar otro dispositivo"*
3️⃣ Copia el código alfanumérico que aparece y *envíalo aquí* 📩

¿Te gustaría contratar el servicio? Responde *SÍ* y envíanos el código 🎮"""

JUEGOS = """🎮 *JUEGOS XBOX* 🎮

Tenemos 4 modalidades de compra. Te explicamos cada una:

━━━━━━━━━━━━━━━━
1️⃣ *CÓDIGO* (Económico)
━━━━━━━━━━━━━━━━
🔑 Juego comprado desde Microsoft Xbox
✅ Se agrega directamente a tu cuenta
✅ Tuyo de por vida

━━━━━━━━━━━━━━━━
2️⃣ *CUENTA PRINCIPAL* (+ Económico)
━━━━━━━━━━━━━━━━
🏠 Acceso al juego de por vida
✅ Juegas desde tu cuenta personal
✅ Sin necesidad de iniciar sesión en otra cuenta

━━━━━━━━━━━━━━━━
3️⃣ *CUENTA SECUNDARIA* (++ Económico)
━━━━━━━━━━━━━━━━
👤 Acceso al juego de por vida
✅ Juegas desde tu cuenta personal
⚠️ Debes iniciar sesión en la cuenta del juego para jugar

━━━━━━━━━━━━━━━━
4️⃣ *SECUNDARIA CON MÉTODO* (+++ Económico)
━━━━━━━━━━━━━━━━
🔧 Acceso al juego de por vida
✅ Juegas desde tu cuenta personal
📋 Siguiendo un tutorial que te compartimos

━━━━━━━━━━━━━━━━

¿Qué juego te interesa? Dinos el nombre y te damos el precio 🎮"""

SOPORTE = """🛠️ *SOPORTE* 🛠️

Para resolver tu solicitud, un asesor te atenderá personalmente.

📲 Escríbenos directamente al:
👉 *+57 322 908 2927*

¡Estamos para ayudarte! 😊"""

SYSTEM_PROMPT = """Eres un asistente virtual de Game Line Col, una tienda colombiana especializada en Game Pass y juegos para Xbox.

Tu nombre es GameBot. Responde siempre en español, de forma amable y profesional.

INSTRUCCIONES IMPORTANTES:
- NUNCA repitas el mensaje de bienvenida, ese solo se envía una vez al inicio
- Siempre que termines una respuesta muestra el menú de opciones
- Si el cliente escribe 1 o "game pass" muestra la información de Game Pass
- Si el cliente escribe 2 o "juegos" muestra las opciones de juegos
- Si el cliente escribe 3 o "soporte" dile que escriba al +57 322 908 2927
- Si el cliente pregunta por un juego específico, búscalo en G2A.com, toma el precio en pesos colombianos, súmale $25.000 COP y ese es el precio final
- Si el cliente dice SÍ después de ver Game Pass, dile que siga los pasos en su consola y envíe el código
- No inventes precios ni información
- Si no puedes resolver algo termina tu respuesta con: ALERTA_ASESOR"""

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
        text = message["text"]["body"].strip()
        text_lower = text.lower()

        # Saludo o primer mensaje
        saludos = ["hola", "buenas", "buenos días", "buenas tardes", "buenas noches", "hi", "hello", "buen día", "inicio"]
        es_saludo = any(saludo in text_lower for saludo in saludos)

        if phone not in conversaciones or es_saludo:
            conversaciones[phone] = {"activo": True}
            send_message(phone, BIENVENIDA)
            return jsonify({"status": "ok"}), 200

        # Opciones del menú
        if text == "1" or "game pass" in text_lower:
            send_message(phone, GAMEPASS)
            send_message(phone, MENU)
            return jsonify({"status": "ok"}), 200

        if text == "2" or ("juego" in text_lower and len(text_lower) < 10):
            send_message(phone, JUEGOS)
            send_message(phone, MENU)
            return jsonify({"status": "ok"}), 200

        if text == "3" or "soporte" in text_lower:
            send_message(phone, SOPORTE)
            alerta = f"🚨 *ALERTA Game Line Col* 🚨\n\nEl cliente *+{phone}* solicitó soporte."
            send_message(ADMIN_PHONE, alerta)
            return jsonify({"status": "ok"}), 200

        # Respuesta con IA para preguntas específicas
        response = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=f"{SYSTEM_PROMPT}\n\nCliente dice: {text}",
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())]
            )
        )
        reply = response.text

        if "ALERTA_ASESOR" in reply:
            reply = reply.replace("ALERTA_ASESOR", "").strip()
            alerta = f"🚨 *ALERTA Game Line Col* 🚨\n\nEl cliente *+{phone}* necesita un asesor.\n\n💬 Su pregunta:\n_{text}_"
            send_message(ADMIN_PHONE, alerta)

        send_message(phone, reply)
        send_message(phone, MENU)

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
