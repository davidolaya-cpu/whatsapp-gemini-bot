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

def es_codigo_consola(texto):
    texto = texto.strip()
    return bool(re.match(r'^[A-Za-z0-9]{6,25}$', texto))

def extraer_meses(texto):
    texto = texto.lower().strip()
    meses_map = {
        "1": "1 mes", "un mes": "1 mes", "uno": "1 mes",
        "2": "2 meses", "dos meses": "2 meses", "dos": "2 meses",
        "3": "3 meses", "tres meses": "3 meses", "tres": "3 meses",
        "6": "6 meses", "seis meses": "6 meses", "seis": "6 meses",
        "12": "12 meses", "doce meses": "12 meses", "doce": "12 meses",
        "un año": "12 meses", "un ano": "12 meses"
    }
    for key, value in meses_map.items():
        if texto == key or key in texto:
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

📅 *1 mes* → $29.900
📅 *2 meses* → $55.000
📅 *3 meses* → $80.000
📅 *6 meses* → $140.000
📅 *12 meses* → $190.000

Responde con el número de meses 😊"""

PREGUNTAR_TIPO_CUENTA = """🎯 ¿Qué tipo de cuenta prefieres?

1️⃣ *Cuenta Principal*
🏠 Juegas desde tu cuenta personal sin iniciar sesión en otra cuenta

2️⃣ *Cuenta Secundaria*
👤 Juegas desde tu cuenta personal iniciando sesión en la cuenta del servicio

Ambas funcionan perfecto, solo cambia la configuración 😊"""

PREGUNTAR_CONSOLA = """✅ ¿Tienes tu consola o PC disponible en este momento para generar el código de activación?

1️⃣ *Sí, tengo mi consola/PC disponible*
2️⃣ *No, quiero apartar el servicio y activarlo después*"""

CONFIG_PRINCIPAL = """✅ ¡Perfecto! Una vez habilitemos tu cuenta con el código, sigue estos pasos en tu consola:

━━━━━━━━━━━━━━━━
🏠 *CONFIGURACIÓN CUENTA PRINCIPAL*
━━━━━━━━━━━━━━━━

Cuando aparezcan las preguntas de asociación de cuenta Game Pass Ultimate:

1️⃣ *SIGUIENTE*
2️⃣ *NO GRACIAS*
3️⃣ *SIN BARRERAS*
4️⃣ *OMITIR*
5️⃣ En la pregunta de hacer Xbox principal → *HACER XBOX PRINCIPAL* ✅

━━━━━━━━━━━━━━━━
⚠️ *IMPORTANTE*
━━━━━━━━━━━━━━━━
Siempre vas a usar el servicio con la sesión iniciada de *tu cuenta personal*. La cuenta que añadimos *nunca la inicies*.

🔒 El uso es *exclusivo para ti*, no la puedes compartir. Si llegase a pasar, la penalización es quitarte el servicio *sin devolución del dinero*."""

CONFIG_SECUNDARIA = """✅ ¡Perfecto! Una vez habilitemos tu cuenta con el código, sigue estos pasos en tu consola:

━━━━━━━━━━━━━━━━
👤 *CONFIGURACIÓN CUENTA SECUNDARIA*
━━━━━━━━━━━━━━━━

Cuando aparezcan las preguntas de asociación de cuenta Game Pass Ultimate:

1️⃣ *SIGUIENTE*
2️⃣ *NO GRACIAS*
3️⃣ *SIN BARRERAS*
4️⃣ *VINCULAR CONTROL*
5️⃣ En la pregunta de hacer Xbox principal → *NO CAMBIAR* ⛔

⚠️ *¡Mucho cuidado con esa pregunta! Debes dar en NO CAMBIAR*

━━━━━━━━━━━━━━━━
⚠️ *IMPORTANTE*
━━━━━━━━━━━━━━━━
Siempre con la *sesión iniciada de la cuenta que acabamos de añadir* y juegas directo con tu cuenta personal.

🔒 El uso es *exclusivo para ti*, no la puedes compartir. Si llegase a pasar, la penalización es quitarte el servicio *sin devolución del dinero*."""

ACTIVACION = """📲 Sigue estos pasos en tu consola o PC:

1️⃣ Ve a *"Agregar nuevo"* (como si fueras a agregar una nueva cuenta)
2️⃣ Selecciona *"Usar otro dispositivo"*
3️⃣ Aparecerá un *código*, cópialo y *envíalo aquí mismo* 📩

¡Nuestro asesor lo activará de inmediato! 🚀"""

APARTAR_SERVICIO = """💳 ¡Sin problema! Puedes apartar tu servicio pagando ahora y activarlo cuando tengas tu consola disponible.

━━━━━━━━━━━━━━━━
💰 *PAGO PARA APARTAR*
━━━━━━━━━━━━━━━━
Realiza el pago por Llave Breve Falabella al:
👉 *3057059517*

Una vez realizado el pago, envíanos el *comprobante aquí* 📸 y un asesor confirmará tu reserva.

Cuando tengas tu consola lista, te indicamos cómo activarlo 🎮"""

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
- Para activar Game Pass el cliente debe enviar el código de su consola en este mismo chat
- No tenemos catálogo de juegos, cotizamos según lo que pida el cliente
- Cuenta Principal y Secundaria funcionan igual de bien, solo cambia la configuración

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
                "meses_seleccionados": None,
                "tipo_cuenta": None
            }
            send_message(phone, BIENVENIDA)
            registrar_cliente(phone, text, "Inicio", "Bienvenida enviada")
            return jsonify({"status": "ok"}), 200

        conversaciones[phone]["ultima_interaccion"] = time.time()
        conversaciones[phone]["recordatorio_enviado"] = False

        estado = conversaciones[phone].get("estado", "menu")
        historial = conversaciones[phone].get("historial", [])
        meses = conversaciones[phone].get("meses_seleccionados", "No especificado")
        tipo_cuenta = conversaciones[phone].get("tipo_cuenta", "No especificado")

        # ── ESTADO: selección de meses ──
        if estado == "seleccion_meses":
            m = extraer_meses(text)
            if m:
                conversaciones[phone]["meses_seleccionados"] = m
                conversaciones[phone]["estado"] = "seleccion_cuenta"
                send_message(phone, f"✅ Seleccionaste *{m}*.\n\n" + PREGUNTAR_TIPO_CUENTA)
            else:
                send_message(phone, "No entendí 😊 Responde con el número de meses:\n\n1️⃣ 1 mes\n2️⃣ 2 meses\n3️⃣ 3 meses\n4️⃣ 6 meses\n5️⃣ 12 meses")
            return jsonify({"status": "ok"}), 200

        # ── ESTADO: selección tipo de cuenta ──
        if estado == "seleccion_cuenta":
            if "1" in text or "principal" in text_lower:
                conversaciones[phone]["tipo_cuenta"] = "Principal"
                conversaciones[phone]["estado"] = "preguntar_consola"
                send_message(phone, "🏠 ¡Elegiste *Cuenta Principal*!\n\n" + PREGUNTAR_CONSOLA)
            elif "2" in text or "secundaria" in text_lower:
                conversaciones[phone]["tipo_cuenta"] = "Secundaria"
                conversaciones[phone]["estado"] = "preguntar_consola"
                send_message(phone, "👤 ¡Elegiste *Cuenta Secundaria*!\n\n" + PREGUNTAR_CONSOLA)
            elif "diferencia" in text_lower or "recomienda" in text_lower or "cual" in text_lower:
                send_message(phone, "Ambas funcionan perfecto 😊\n\n🏠 *Principal:* Juegas sin iniciar sesión en otra cuenta.\n👤 *Secundaria:* Juegas iniciando sesión en la cuenta del servicio.\n\n¿Cuál prefieres?\n1️⃣ Principal\n2️⃣ Secundaria")
            else:
                send_message(phone, PREGUNTAR_TIPO_CUENTA)
            return jsonify({"status": "ok"}), 200

        # ── ESTADO: preguntar si tiene consola ──
        if estado == "preguntar_consola":
            tipo_cuenta = conversaciones[phone].get("tipo_cuenta", "No especificado")
            meses = conversaciones[phone].get("meses_seleccionados", "No especificado")
            if "1" in text or "si" in text_lower or "sí" in text_lower or "tengo" in text_lower:
                conversaciones[phone]["estado"] = "activacion"
                send_message(phone, ACTIVACION)
                alerta = f"🎮 *NUEVO CLIENTE - Game Line Col* 🎮\n\nCliente: *+{phone}*\n⏳ Meses: *{meses}*\n🎯 Cuenta: *{tipo_cuenta}*\n\n¡Espera su código de activación! 🚀"
                send_message(ADMIN_PHONE, alerta)
            elif "2" in text or "no" in text_lower or "apartar" in text_lower:
                conversaciones[phone]["estado"] = "esperando_comprobante"
                send_message(phone, APARTAR_SERVICIO)
                alerta = f"💳 *CLIENTE QUIERE APARTAR - Game Line Col* 💳\n\nCliente: *+{phone}*\n⏳ Meses: *{meses}*\n🎯 Cuenta: *{tipo_cuenta}*\n\n¡Espera el comprobante de pago! 🚀"
                send_message(ADMIN_PHONE, alerta)
                registrar_cliente(phone, text, f"Game Pass {tipo_cuenta} - {meses}", "💳 Quiere apartar servicio")
            else:
                send_message(phone, PREGUNTAR_CONSOLA)
            return jsonify({"status": "ok"}), 200

        # ── ESTADO: esperando código de consola ──
        if estado == "activacion" and es_codigo_consola(text):
            tipo_cuenta = conversaciones[phone].get("tipo_cuenta", "No especificado")
            meses = conversaciones[phone].get("meses_seleccionados", "No especificado")
            config = CONFIG_PRINCIPAL if tipo_cuenta == "Principal" else CONFIG_SECUNDARIA
            conversaciones[phone]["compro"] = True
            send_message(phone, "✅ ¡Código recibido! Nuestro asesor lo activará en breve. 🎮\n\nMientras tanto, guarda estos pasos para cuando se habilite tu cuenta:")
            send_message(phone, config)
            alerta = f"🎮 *CÓDIGO DE ACTIVACIÓN - Game Line Col* 🎮\n\nCliente: *+{phone}*\n⏳ Meses: *{meses}*\n🎯 Cuenta: *{tipo_cuenta}*\n🔑 Código: *{text}*\n\n¡Activa el servicio! 🚀"
            send_message(ADMIN_PHONE, alerta)
            registrar_cliente(phone, f"Código: {text}", f"Game Pass {tipo_cuenta} - {meses}", "💰 COMPRA CONFIRMADA")
            return jsonify({"status": "ok"}), 200

        # ── ESTADO: esperando comprobante de pago ──
        if estado == "esperando_comprobante":
            tipo_cuenta = conversaciones[phone].get("tipo_cuenta", "No especificado")
            meses = conversaciones[phone].get("meses_seleccionados", "No especificado")
            conversaciones[phone]["estado"] = "esperando_codigo_apartado"
            send_message(phone, "✅ ¡Comprobante recibido! Un asesor confirmará tu reserva en breve.\n\nCuando tengas tu consola lista, envíanos el código de activación aquí y lo activamos de inmediato 🎮")
            alerta = f"💳 *COMPROBANTE DE PAGO - Game Line Col* 💳\n\nCliente: *+{phone}*\n⏳ Meses: *{meses}*\n🎯 Cuenta: *{tipo_cuenta}*\n\n¡Confirma el pago y reserva el servicio! 🚀"
            send_message(ADMIN_PHONE, alerta)
            registrar_cliente(phone, "Comprobante enviado", f"Game Pass {tipo_cuenta} - {meses}", "💰 PAGO RECIBIDO - Servicio apartado")
            return jsonify({"status": "ok"}), 200

        # ── ESTADO: esperando código de cliente que apartó ──
        if estado == "esperando_codigo_apartado" and es_codigo_consola(text):
            tipo_cuenta = conversaciones[phone].get("tipo_cuenta", "No especificado")
            meses = conversaciones[phone].get("meses_seleccionados", "No especificado")
            config = CONFIG_PRINCIPAL if tipo_cuenta == "Principal" else CONFIG_SECUNDARIA
            conversaciones[phone]["compro"] = True
            send_message(phone, "✅ ¡Código recibido! Nuestro asesor lo activará en breve. 🎮\n\nMientras tanto, guarda estos pasos para cuando se habilite tu cuenta:")
            send_message(phone, config)
            alerta = f"🎮 *CÓDIGO ACTIVACIÓN (APARTADO) - Game Line Col* 🎮\n\nCliente: *+{phone}*\n⏳ Meses: *{meses}*\n🎯 Cuenta: *{tipo_cuenta}*\n🔑 Código: *{text}*\n\n¡Activa el servicio! 🚀"
            send_message(ADMIN_PHONE, alerta)
            registrar_cliente(phone, f"Código: {text}", f"Game Pass {tipo_cuenta} - {meses}", "🎮 CÓDIGO RECIBIDO - Activar servicio")
            return jsonify({"status": "ok"}), 200

        # ── MENÚ PRINCIPAL ──
        if text == "1" or ("game pass" in text_lower and estado == "menu"):
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

        if estado == "gamepass" and text_lower in ["si", "sí", "yes", "quiero", "dale", "listo"]:
            conversaciones[phone]["estado"] = "seleccion_meses"
            send_message(phone, PREGUNTAR_MESES)
            return jsonify({"status": "ok"}), 200

        # ── RESPUESTA CON IA ──
        historial.append({"role": "user", "content": text})
        historial_texto = "\n".join(
    ("Cliente: " if h["role"] == "user" else "GameBot: ") + h["content"]
    for h in historial[-10:]
        )
