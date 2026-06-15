import os
import re
import json
import requests
import threading
import time
from datetime import datetime
from flask import Flask, request, jsonify
from google import genai
from google.genai import types
from google.oauth2 import service_account
from googleapiclient.discovery import build

app = Flask(__name__)

VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN")
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS")
SHEET_ID = "1lvIlK1LYbT68HsuDTbMRzWSYh_RGUPHAZeV31_sAmdU"
ADMIN_PHONE = "573229082927"
HORA_SEGUIMIENTO = 3600

client = genai.Client(api_key=GEMINI_API_KEY)

def get_sheets_service():
    creds_dict = json.loads(GOOGLE_CREDENTIALS)
    creds = service_account.Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return build("sheets", "v4", credentials=creds)

def registrar_cliente(phone, mensaje, servicio, estado):
    try:
        service = get_sheets_service()
        fecha = datetime.now().strftime("%d/%m/%Y %H:%M")
        valores = [[fecha, f"+{phone}", mensaje, servicio, estado]]
        service.spreadsheets().values().append(
            spreadsheetId=SHEET_ID,
            range="A:E",
            valueInputOption="RAW",
            body={"values": valores}
        ).execute()
    except Exception as e:
        print(f"Error registrando en Sheets: {e}")

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

def es_codigo_activacion(texto):
    texto = texto.strip()
    return bool(re.match(r'^[A-Za-z0-9]{6,12}$', texto))

def extraer_meses(texto):
    texto = texto.lower()
    meses_map = {
        "1": "1 mes", "un mes": "1 mes", "uno": "1 mes",
        "2": "2 meses", "dos": "2 meses",
        "3": "3 meses", "tres": "3 meses",
        "6": "6 meses", "seis": "6 meses",
        "12": "12 meses", "doce": "12 meses", "un año": "12 meses", "un ano": "12 meses"
    }
    for key, value in meses_map.items():
        if key in texto:
            return value
    return None

def verificar_seguimientos():
    while True:
        time.sleep(60)
        ahora = time.time()
        for phone, datos in list(conversaciones.items()):
            if datos.get("compro"):
                continue
            ultima = datos.get("ultima_interaccion", 0)
            recordatorio_enviado = datos.get("recordatorio_enviado", False)
            if not recordatorio_enviado and (ahora - ultima) >= HORA_SEGUIMIENTO:
                mensaje = """👋 ¡Hola! Te escribimos desde *Game Line Col* 🎮

Notamos que estuviste interesado en nuestros servicios pero no completaste tu compra.

¿Podemos ayudarte con algo? 😊

1️⃣ *Game Pass Ultimate* (Xbox y PC)
2️⃣ *Juegos Xbox*
3️⃣ *Soporte*

¡Estamos aquí para ayudarte! 🚀"""
                send_message(phone, mensaje)
                conversaciones[phone]["recordatorio_enviado"] = True
                registrar_cliente(phone, "Recordatorio automático", "Seguimiento", "Recordatorio enviado 🔔")

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

PREGUNTAR_MESES = """⏳ ¡Perfecto! ¿Por cuántos meses deseas contratar el servicio?

📅 1 mes → $29.900
📅 2 meses → $55.000
📅 3 meses → $80.000
📅 6 meses → $140.000
📅 12 meses → $190.000

Responde con el número de meses 😊"""

ACTIVACION = """✅ ¡Perfecto! Sigue estos pasos en tu consola para activar el servicio:

1️⃣ Ve a *"Agregar nuevo"* (como si fueras a agregar una nueva cuenta)
2️⃣ Selecciona *"Usar otro dispositivo"*
3️⃣ Aparecerá un *código alfanumérico*, cópialo y *envíalo aquí mismo* 📩

¡Nuestro asesor lo activará de inmediato! 🚀"""

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
- Para activar Game Pass el cliente debe enviar el código alfanumérico en este mismo chat
- No tenemos catálogo de juegos, cotizamos según lo que pida el cliente

INSTRUCCIONES:
- Usa el historial de conversación para dar respuestas coherentes y contextuales
- Responde SOLO lo que el cliente está preguntando, sin repetir menús ni opciones ya mostradas
- Si el cliente pregunta por un juego específico, dile que lo vas a cotizar y termina con ALERTA_JUEGO:[nombre del juego]
- Si el cliente tiene una duda técnica sobre Xbox o Game Pass, resuélvela directamente
- Si no puedes resolver algo, termina con ALERTA_ASESOR
- No inventes precios de juegos, siempre deriva al asesor para cotizar
- Sé conciso y directo"""

conversaciones = {}

threading.Thread(target=verificar_seguimientos, daemon=True).start()

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
        value = data["entry"][0]["changes"][0]["value"]

        if "messages" not in value:
            return jsonify({"status": "ok"}), 200

        message = value["messages"][0]

        if message.get("type") != "text":
            return jsonify({"status": "ok"}), 200

        phone = message["from"]
        text = message["text"]["body"].strip()
        text_lower = text.lower()

        saludos = ["hola", "buenas", "buenos días", "buenas tardes", "buenas noches", "hi", "hello", "buen día", "inicio", "empezar"]
        es_saludo = any(saludo in text_lower for saludo in saludos)

        if phone not in conversaciones or es_saludo:
            conversaciones[phone] = {
                "activo": True,
                "estado": "menu",
                "historial": [],
                "ultima_interaccion": time.time(),
                "recordatorio_enviado": False,
                "compro": False,
                "meses_seleccionados": None
            }
            send_message(phone, BIENVENIDA)
            registrar_cliente(phone, text, "Inicio", "Bienvenida enviada")
            return jsonify({"status": "ok"}), 200

        conversaciones[phone]["ultima_interaccion"] = time.time()
        conversaciones[phone]["recordatorio_enviado"] = False

        estado = conversaciones[phone].get("estado", "menu")
        historial = conversaciones[phone].get("historial", [])

        # Detectar código de activación
        if es_codigo_activacion(text) and not conversaciones[phone].get("compro") and estado == "activacion":
            meses = conversaciones[phone].get("meses_seleccionados", "No especificado")
            conversaciones[phone]["compro"] = True
            send_message(phone, "✅ ¡Código recibido! Nuestro asesor lo activará en breve. ¡Gracias por tu compra! 🎮")
            alerta = f"🎮 *CÓDIGO DE ACTIVACIÓN - Game Line Col* 🎮\n\nCliente: *+{phone}*\n⏳ Meses contratados: *{meses}*\n🔑 Código: *{text}*\n\n¡Activa el servicio! 🚀"
            send_message(ADMIN_PHONE, alerta)
            registrar_cliente(phone, f"Código: {text}", f"Game Pass - {meses}", "💰 COMPRA CONFIRMADA")
            return jsonify({"status": "ok"}), 200

        if text == "1" or "game pass" in text_lower:
            conversaciones[phone]["estado"] = "gamepass"
            historial.append({"role": "user", "content": text})
            historial.append({"role": "assistant", "content": GAMEPASS})
            conversaciones[phone]["historial"] = historial
            send_message(phone, GAMEPASS)
            registrar_cliente(phone, text, "Game Pass Ultimate", "Consultó precios")
            return jsonify({"status": "ok"}), 200

        if text == "2" or text_lower in ["juegos", "juego", "juegos xbox"]:
            conversaciones[phone]["estado"] = "juegos"
            historial.append({"role": "user", "content": text})
            historial.append({"role": "assistant", "content": JUEGOS_MENU})
            conversaciones[phone]["historial"] = historial
            send_message(phone, JUEGOS_MENU)
            registrar_cliente(phone, text, "Juegos Xbox", "Consultó juegos")
            return jsonify({"status": "ok"}), 200

        if text == "3" or "soporte" in text_lower:
            conversaciones[phone]["estado"] = "soporte"
            send_message(phone, SOPORTE)
            registrar_cliente(phone, text, "Soporte", "Solicitó soporte")
            alerta = f"🚨 *SOPORTE Game Line Col* 🚨\n\nEl cliente *+{phone}* solicitó soporte."
            send_message(ADMIN_PHONE, alerta)
            return jsonify({"status": "ok"}), 200

        # Cliente dice SÍ para contratar
        if estado == "gamepass" and text_lower in ["si", "sí", "yes", "quiero", "dale", "listo"]:
            conversaciones[phone]["estado"] = "seleccion_meses"
            send_message(phone, PREGUNTAR_MESES)
            registrar_cliente(phone, text, "Game Pass Ultimate", "Quiere contratar ✅")
            return jsonify({"status": "ok"}), 200

        # Cliente elige meses
        if estado == "seleccion_meses":
            meses = extraer_meses(text)
            if meses:
                conversaciones[phone]["meses_seleccionados"] = meses
                conversaciones[phone]["estado"] = "activacion"
                historial.append({"role": "user", "content": text})
                historial.append({"role": "assistant", "content": ACTIVACION})
                conversaciones[phone]["historial"] = historial
                send_message(phone, f"✅ ¡Perfecto! Seleccionaste *{meses}*.\n\n" + ACTIVACION)
                alerta = f"🎮 *NUEVO CLIENTE - Game Line Col* 🎮\n\nEl cliente *+{phone}* quiere contratar:\n⏳ *{meses}* de Game Pass Ultimate\n\n¡Espera su código de activación! 🚀"
                send_message(ADMIN_PHONE, alerta)
                return jsonify({"status": "ok"}), 200
            else:
                send_message(phone, "No entendí la cantidad de meses 😊 Por favor responde con un número:\n\n1️⃣ 1 mes\n2️⃣ 2 meses\n3️⃣ 3 meses\n4️⃣ 6 meses\n5️⃣ 12 meses")
                return jsonify({"status": "ok"}), 200

        historial.append({"role": "user", "content": text})

        historial_texto = "\n".join([
            f"{'Cliente' if h['role'] == 'user' else 'GameBot'}: {h['content']}"
            for h in historial[-10:]
        ])

        response = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=f"{SYSTEM_PROMPT}\n\nHistorial de conversación:\n{historial_texto}\n\nResponde al último mensaje del cliente.",
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())]
            )
        )
        reply = response.text

        if "ALERTA_JUEGO:" in reply:
            match = re.search(r'ALERTA_JUEGO:([^\n]+)', reply)
            nombre_juego = match.group(1).strip() if match else text
            reply = re.sub(r'ALERTA_JUEGO:[^\n]+', '', reply).strip()
            conversaciones[phone]["compro"] = True
            registrar_cliente(phone, text, f"Juego: {nombre_juego}", "🎮 Cotización solicitada")
            alerta = f"🎮 *COTIZACIÓN DE JUEGO - Game Line Col* 🎮\n\nEl cliente *+{phone}* busca:\n👉 *{nombre_juego}*\n\nPor favor cotiza y respóndele."
            send_message(ADMIN_PHONE, alerta)

        elif "ALERTA_ASESOR" in reply:
            reply = reply.replace("ALERTA_ASESOR", "").strip()
            registrar_cliente(phone, text, "Consulta general", "🚨 Necesita asesor")
            alerta = f"🚨 *ALERTA Game Line Col* 🚨\n\nEl cliente *+{phone}* necesita un asesor.\n\n💬 Su pregunta:\n_{text}_"
            send_message(ADMIN_PHONE, alerta)

        else:
            registrar_cliente(phone, text, "Consulta general", "Respondido por bot ✅")

        historial.append({"role": "assistant", "content": reply})
        conversaciones[phone]["historial"] = historial[-20:]

        send_message(phone, reply)

    except Exception as e:
        print(f"Error: {e}")
    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
