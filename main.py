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
🎯 *MODALIDADES*
━━━━━━━━━━━━━━━━
🏠 *Cuenta Principal:* Juegas desde tu cuenta personal sin iniciar sesión en otra cuenta
👤 *Cuenta Secundaria:* Juegas desde tu cuenta personal iniciando sesión en la cuenta del servicio

El precio es el mismo en ambas modalidades.

━━━━━━━━━━━━━━━━
🛡️ *GARANTÍA*
━━━━━━━━━━━━━━━━
✅ Primero pruebas el servicio
✅ Solo pagas cuando estés satisfecho

━━━━━━━━━━━━━━━━
💳 *PAGO*
━━━━━━━━━━━━━━━━
Llave Breve Falabella al *3057059517*

¿Te gustaría contratar el servicio? Responde *SÍ* para continuar 😊"""

ACTIVACION = """✅ ¡Perfecto! Sigue estos pasos en tu consola para activar el servicio:

1️⃣ Ve a *"Agregar nuevo"* (como si fueras a agregar una nueva cuenta)
2️⃣ Selecciona *"Usar otro dispositivo"*
3️⃣ Aparecerá un *código alfanumérico*, cópialo

📲 Luego envía ese código directamente a nuestro asesor al:
👉 *+57 322 908 2927*

¡El asesor activará tu servicio de inmediato! 🚀"""

JUEGOS_MENU = """🎮 *JUEGOS XBOX* 🎮

Tenemos 4 modalidades de compra:

1️⃣ *CÓDIGO* (Económico)
🔑 Juego comprado desde Microsoft, se agrega directamente a tu cuenta de por vida

2️⃣ *CUENTA PRINCIPAL* (+ Económico)
🏠 Acceso de por vida, juegas desde tu cuenta sin iniciar sesión en otra

3️⃣ *CUENTA SECUNDARIA* (++ Económico)
👤 Acceso de por vida, juegas desde tu cuenta iniciando sesión en la cuenta del juego

4️⃣ *SECUNDARIA CON MÉTODO* (+++ Económico)
🔧 Acceso de por vida siguiendo un tutorial que te compartimos

━━━━━━━━━━━━━━━━
¿Qué juego estás buscando? 🎮 *Dinos el nombre* y te conseguimos el precio 👇"""

SOPORTE = """🛠️ *SOPORTE* 🛠️

Un asesor te atenderá personalmente para resolver tu solicitud.

📲 Escríbenos directamente al:
👉 *+57 322 908 2927*

¡Estamos para ayudarte! 😊"""

SYSTEM_PROMPT = """Eres un asistente virtual de Game Line Col, una tienda colombiana especializada en Game Pass Ultimate y juegos para Xbox.

Tu nombre es GameBot. Responde siempre en español, de forma amable y profesional. Usa emojis ocasionalmente 🎮

CONTEXTO DEL NEGOCIO:
- Vendemos Game Pass Ultimate para Xbox y PC
- Vendemos juegos Xbox en 4 modalidades: Código, Principal, Secundaria, Secundaria con método
- El pago es por Llave Breve Falabella al 3057059517
- El cliente primero prueba y luego paga
- Para activar Game Pass el cliente debe enviar el código alfanumérico al +57 322 908 2927
- No tenemos catálogo de juegos, cotizamos según lo que pida el cliente

INSTRUCCIONES:
- Responde SOLO lo que el cliente está preguntando, sin repetir menús ni opciones ya mostradas
- Si el cliente pregunta por un juego específico, dile que lo vas a cotizar y termina con ALERTA_JUEGO:[nombre del juego]
- Si el cliente tiene una duda técnica sobre Xbox o Game Pass, resuélvela directamente
- Si no puedes resolver algo, termina con ALERTA_ASESOR
- No inventes precios de juegos, siempre deriva al asesor para cotizar
- Sé conciso y directo"""

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

        saludos = ["hola", "buenas", "buenos días", "buenas tardes", "buenas noches", "hi", "hello", "buen día", "inicio", "empezar"]
        es_saludo = any(saludo in text_lower for saludo in saludos)

        if phone not in conversaciones or es_saludo:
            conversaciones[phone] = {"activo": True, "estado": "menu"}
            send_message(phone, BIENVENIDA)
            return jsonify({"status": "ok"}), 200

        estado = conversaciones[phone].get("estado", "menu")

        # Opciones del menú principal
        if text == "1" or "game pass" in text_lower:
            conversaciones[phone]["estado"] = "gamepass"
            send_message(phone, GAMEPASS)
            return jsonify({"status": "ok"}), 200

        if text == "2" or text_lower in ["juegos", "juego", "juegos xbox"]:
            conversaciones[phone]["estado"] = "juegos"
            send_message(phone, JUEGOS_MENU)
            return jsonify({"status": "ok"}), 200

        if text == "3" or "soporte" in text_lower:
            conversaciones[phone]["estado"] = "soporte"
            send_message(phone, SOPORTE)
            alerta = f"🚨 *SOPORTE Game Line Col* 🚨\n\nEl cliente *+{phone}* solicitó soporte."
            send_message(ADMIN_PHONE, alerta)
            return jsonify({"status": "ok"}), 200

        # Cliente dice SÍ para contratar Game Pass
        if estado == "gamepass" and text_lower in ["si", "sí", "yes", "quiero", "dale", "listo"]:
            conversaciones[phone]["estado"] = "activacion"
            send_message(phone, ACTIVACION)
            alerta = f"🎮 *NUEVO CLIENTE - Game Line Col* 🎮\n\nEl cliente *+{phone}* quiere contratar Game Pass Ultimate.\n\n¡Espera su código de activación! 🚀"
            send_message(ADMIN_PHONE, alerta)
            return jsonify({"status": "ok"}), 200

        # Respuesta con IA para todo lo demás
        response = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=f"{SYSTEM_PROMPT}\n\nEstado actual del cliente: {estado}\n\nCliente dice: {text}",
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())]
            )
        )
        reply = response.text

        if "ALERTA_JUEGO:" in reply:
            import re
            match = re.search(r'ALERTA_JUEGO:([^\n]+)', reply)
            nombre_juego = match.group(1).strip() if match else text
            reply = re.sub(r'ALERTA_JUEGO:[^\n]+', '', reply).strip()
            alerta = f"🎮 *COTIZACIÓN DE JUEGO - Game Line Col* 🎮\n\nEl cliente *+{phone}* busca:\n👉 *{nombre_juego}*\n\nPor favor cotiza y respóndele."
            send_message(ADMIN_PHONE, alerta)

        elif "ALERTA_ASESOR" in reply:
            reply = reply.replace("ALERTA_ASESOR", "").strip()
            alerta = f"🚨 *ALERTA Game Line Col* 🚨\n\nEl cliente *+{phone}* necesita un asesor.\n\n💬 Su pregunta:\n_{text}_"
            send_message(ADMIN_PHONE, alerta)

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
