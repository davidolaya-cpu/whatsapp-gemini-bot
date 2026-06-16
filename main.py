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
        print("Error Sheets: " + str(e))

def send_message(phone, message):
    url = "https://graph.facebook.com/v18.0/" + PHONE_NUMBER_ID + "/messages"
    headers = {
        "Authorization": "Bearer " + WHATSAPP_TOKEN,
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
        "un ano": "12 meses", "un año": "12 meses"
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
                mensaje = (
                    "👋 Hola! Te escribimos desde *Game Line Col* 🎮\n\n"
                    "Notamos que estuviste interesado en nuestros servicios pero no completaste tu compra.\n\n"
                    "En que te podemos ayudar? 😊\n\n"
                    "1️⃣ *Game Pass Ultimate* (Xbox y PC)\n"
                    "2️⃣ *Juegos Xbox*\n"
                    "3️⃣ *Soporte*"
                )
                send_message(phone, mensaje)
                conversaciones[phone]["recordatorio_enviado"] = True
                registrar_cliente(phone, "Recordatorio automatico", "Seguimiento", "Recordatorio enviado 🔔")

BIENVENIDA = (
    "🎮 Bienvenido a *Game Line Col*! 🎮\n\n"
    "Somos tu tienda de confianza para juegos y suscripciones Xbox 🕹️\n\n"
    "En que te podemos ayudar hoy?\n\n"
    "1️⃣ *Game Pass Ultimate* (Xbox y PC)\n"
    "2️⃣ *Juegos Xbox*\n"
    "3️⃣ *Soporte*\n\n"
    "Responde con el numero de tu opcion 😊"
)

GAMEPASS = (
    "🕹️ *GAME PASS ULTIMATE - Xbox y PC* 🕹️\n\n"
    "Con Game Pass Ultimate tienes acceso a cientos de juegos en Xbox y PC.\n\n"
    "━━━━━━━━━━━━━━━━\n"
    "💰 *PRECIOS*\n"
    "━━━━━━━━━━━━━━━━\n"
    "📅 1 mes → $29.900\n"
    "📅 2 meses → $55.000\n"
    "📅 3 meses → $80.000\n"
    "📅 6 meses → $140.000\n"
    "📅 12 meses → $190.000\n\n"
    "━━━━━━━━━━━━━━━━\n"
    "🎯 *MODALIDADES*\n"
    "━━━━━━━━━━━━━━━━\n"
    "🏠 *Cuenta Principal:* Juegas desde tu cuenta personal sin iniciar sesion en otra cuenta\n"
    "👤 *Cuenta Secundaria:* Juegas desde tu cuenta personal iniciando sesion en la cuenta del servicio\n\n"
    "Ambas funcionan perfecto, solo cambia la configuracion.\n\n"
    "━━━━━━━━━━━━━━━━\n"
    "🛡️ *GARANTIA*\n"
    "━━━━━━━━━━━━━━━━\n"
    "✅ Primero pruebas el servicio\n"
    "✅ Solo pagas cuando estes satisfecho\n\n"
    "━━━━━━━━━━━━━━━━\n"
    "💳 *PAGO*\n"
    "━━━━━━━━━━━━━━━━\n"
    "Llave Breve Falabella al *3057059517*\n\n"
    "Te gustaria contratar el servicio? Responde *SI* para continuar 😊"
)

PREGUNTAR_MESES = (
    "⏳ Por cuantos meses deseas contratar el servicio?\n\n"
    "📅 *1 mes* → $29.900\n"
    "📅 *2 meses* → $55.000\n"
    "📅 *3 meses* → $80.000\n"
    "📅 *6 meses* → $140.000\n"
    "📅 *12 meses* → $190.000\n\n"
    "Responde con el numero de meses 😊"
)

PREGUNTAR_CUENTA = (
    "🎯 Que tipo de cuenta prefieres?\n\n"
    "1️⃣ *Cuenta Principal*\n"
    "🏠 Juegas desde tu cuenta personal sin iniciar sesion en otra cuenta\n\n"
    "2️⃣ *Cuenta Secundaria*\n"
    "👤 Juegas desde tu cuenta personal iniciando sesion en la cuenta del servicio\n\n"
    "Ambas funcionan perfecto, solo cambia la configuracion 😊"
)

PREGUNTAR_CONSOLA = (
    "✅ Tienes tu consola o PC disponible ahora para generar el codigo de activacion?\n\n"
    "1️⃣ *Si, tengo mi consola/PC disponible*\n"
    "2️⃣ *No, quiero apartar el servicio y activarlo despues*"
)

CONFIG_PRINCIPAL = (
    "━━━━━━━━━━━━━━━━\n"
    "🏠 *CONFIGURACION CUENTA PRINCIPAL*\n"
    "━━━━━━━━━━━━━━━━\n\n"
    "Cuando aparezcan las preguntas de asociacion sigue estos pasos:\n\n"
    "1️⃣ SIGUIENTE\n"
    "2️⃣ NO GRACIAS\n"
    "3️⃣ SIN BARRERAS\n"
    "4️⃣ OMITIR\n"
    "5️⃣ En la pregunta de hacer Xbox principal → *HACER XBOX PRINCIPAL* ✅\n\n"
    "⚠️ *IMPORTANTE:*\n"
    "Siempre usa el servicio con la sesion de tu cuenta personal. "
    "La cuenta que anadimos *nunca la inicies*.\n\n"
    "🔒 El uso es *exclusivo para ti*, no la puedes compartir. "
    "Si llegase a pasar, la penalizacion es quitarte el servicio *sin devolucion del dinero*."
)

CONFIG_SECUNDARIA = (
    "━━━━━━━━━━━━━━━━\n"
    "👤 *CONFIGURACION CUENTA SECUNDARIA*\n"
    "━━━━━━━━━━━━━━━━\n\n"
    "Cuando aparezcan las preguntas de asociacion sigue estos pasos:\n\n"
    "1️⃣ SIGUIENTE\n"
    "2️⃣ NO GRACIAS\n"
    "3️⃣ SIN BARRERAS\n"
    "4️⃣ VINCULAR CONTROL\n"
    "5️⃣ En la pregunta de hacer Xbox principal → *NO CAMBIAR* ⛔\n\n"
    "⚠️ *IMPORTANTE:*\n"
    "Siempre con la *sesion iniciada de la cuenta que anadimos* y juegas directo con tu cuenta personal.\n\n"
    "🔒 El uso es *exclusivo para ti*, no la puedes compartir. "
    "Si llegase a pasar, la penalizacion es quitarte el servicio *sin devolucion del dinero*."
)

ACTIVACION = (
    "📲 Sigue estos pasos en tu consola o PC:\n\n"
    "1️⃣ Ve a *Agregar nuevo* (como si fueras a agregar una nueva cuenta)\n"
    "2️⃣ Selecciona *Usar otro dispositivo*\n"
    "3️⃣ Aparecera un *codigo*, copialos y *envialo aqui mismo* 📩\n\n"
    "Nuestro asesor lo activara de inmediato! 🚀"
)

APARTAR = (
    "💳 Sin problema! Puedes apartar tu servicio pagando ahora y activarlo cuando tengas tu consola.\n\n"
    "━━━━━━━━━━━━━━━━\n"
    "💰 *PAGO PARA APARTAR*\n"
    "━━━━━━━━━━━━━━━━\n"
    "Realiza el pago por Llave Breve Falabella al:\n"
    "👉 *3057059517*\n\n"
    "Una vez realizado el pago, *envianos el comprobante aqui* 📸\n"
    "Un asesor confirmara tu reserva en breve 😊\n\n"
    "Cuando tengas tu consola lista, te indicamos como activarlo 🎮"
)

JUEGOS_MENU = (
    "🎮 *JUEGOS XBOX* 🎮\n\n"
    "Tenemos 4 modalidades de compra:\n\n"
    "1️⃣ *CODIGO* (Economico)\n"
    "🔑 Juego comprado desde Microsoft, se agrega directamente a tu cuenta de por vida\n\n"
    "2️⃣ *CUENTA PRINCIPAL* (+ Economico)\n"
    "🏠 Acceso de por vida, juegas desde tu cuenta sin iniciar sesion en otra\n\n"
    "3️⃣ *CUENTA SECUNDARIA* (++ Economico)\n"
    "👤 Acceso de por vida, juegas iniciando sesion en la cuenta del juego\n\n"
    "4️⃣ *SECUNDARIA CON METODO* (+++ Economico)\n"
    "🔧 Acceso de por vida siguiendo un tutorial que compartimos\n\n"
    "━━━━━━━━━━━━━━━━\n"
    "Que juego estas buscando? 🎮 *Dinos el nombre* y te conseguimos el precio 👇"
)

SOPORTE = (
    "🛠️ *SOPORTE* 🛠️\n\n"
    "Un asesor te atendera personalmente para resolver tu solicitud.\n\n"
    "📲 Escribenos directamente al:\n"
    "👉 *+57 322 908 2927*\n\n"
    "Estamos para ayudarte! 😊"
)

SYSTEM_PROMPT = (
    "Eres GameBot, asistente virtual de Game Line Col, tienda colombiana de Game Pass Ultimate y juegos Xbox. "
    "Responde en espanol, amable y profesional. Usa emojis ocasionalmente.\n\n"
    "NEGOCIO:\n"
    "- Game Pass Ultimate para Xbox y PC\n"
    "- Juegos Xbox en 4 modalidades: Codigo, Principal, Secundaria, Secundaria con metodo\n"
    "- Pago por Llave Breve Falabella al 3057059517\n"
    "- Cliente primero prueba y luego paga\n"
    "- No tenemos catalogo de juegos, cotizamos segun pedido\n"
    "- Cuenta Principal y Secundaria funcionan igual, solo cambia la configuracion\n\n"
    "INSTRUCCIONES:\n"
    "- Responde solo lo que el cliente pregunta\n"
    "- Si pregunta por un juego especifico termina con ALERTA_JUEGO:[nombre]\n"
    "- Si no puedes resolver algo termina con ALERTA_ASESOR\n"
    "- No inventes precios de juegos\n"
    "- Se conciso"
)

conversaciones = {}

threading.Thread(target=verificar_seguimientos, daemon=True).start()

@app.route("/webhook", methods=["GET"])
def verify():
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if token == VERIFY_TOKEN:
        return challenge, 200
    return "Token invalido", 403

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

        saludos = ["hola", "buenas", "buenos dias", "buenas tardes", "buenas noches", "hi", "hello", "buen dia", "inicio"]
        es_saludo = any(s in text_lower for s in saludos)

        if phone not in conversaciones:
            conversaciones[phone] = {
                "estado": "menu",
                "historial": [],
                "ultima_interaccion": time.time(),
                "recordatorio_enviado": False,
                "compro": False,
                "meses": None,
                "tipo_cuenta": None,
                "bienvenida_enviada": False
            }

        if not conversaciones[phone].get("bienvenida_enviada") or es_saludo:
            conversaciones[phone]["bienvenida_enviada"] = True
            conversaciones[phone]["estado"] = "menu"
            conversaciones[phone]["ultima_interaccion"] = time.time()
            send_message(phone, BIENVENIDA)
            registrar_cliente(phone, text, "Inicio", "Bienvenida enviada")
            return jsonify({"status": "ok"}), 200

        conversaciones[phone]["ultima_interaccion"] = time.time()
        conversaciones[phone]["recordatorio_enviado"] = False
        estado = conversaciones[phone].get("estado", "menu")
        historial = conversaciones[phone].get("historial", [])

        if estado == "seleccion_meses":
            meses = extraer_meses(text)
            if meses:
                conversaciones[phone]["meses"] = meses
                conversaciones[phone]["estado"] = "seleccion_cuenta"
                send_message(phone, "✅ Seleccionaste *" + meses + "*.\n\n" + PREGUNTAR_CUENTA)
            else:
                send_message(phone, "No entendi 😊 Responde con el numero de meses: 1, 2, 3, 6 o 12")
            return jsonify({"status": "ok"}), 200

        if estado == "seleccion_cuenta":
            if "1" in text or "principal" in text_lower:
                conversaciones[phone]["tipo_cuenta"] = "Principal"
                conversaciones[phone]["estado"] = "preguntar_consola"
                send_message(phone, "🏠 Elegiste *Cuenta Principal*!\n\n" + PREGUNTAR_CONSOLA)
            elif "2" in text or "secundaria" in text_lower:
                conversaciones[phone]["tipo_cuenta"] = "Secundaria"
                conversaciones[phone]["estado"] = "preguntar_consola"
                send_message(phone, "👤 Elegiste *Cuenta Secundaria*!\n\n" + PREGUNTAR_CONSOLA)
            elif "diferencia" in text_lower or "recomienda" in text_lower or "cual" in text_lower:
                send_message(phone, "Ambas funcionan perfecto! 😊\n\n🏠 *Principal:* Juegas sin iniciar sesion en otra cuenta.\n👤 *Secundaria:* Juegas iniciando sesion en la cuenta del servicio.\n\nCual prefieres?\n1️⃣ Principal\n2️⃣ Secundaria")
            else:
                send_message(phone, PREGUNTAR_CUENTA)
            return jsonify({"status": "ok"}), 200

        if estado == "preguntar_consola":
            meses = conversaciones[phone].get("meses", "No especificado")
            tipo_cuenta = conversaciones[phone].get("tipo_cuenta", "No especificado")
            if "1" in text or "si" in text_lower or "tengo" in text_lower:
                conversaciones[phone]["estado"] = "activacion"
                config = CONFIG_PRINCIPAL if tipo_cuenta == "Principal" else CONFIG_SECUNDARIA
                send_message(phone, config)
                send_message(phone, ACTIVACION)
                alerta = (
                    "🎮 *NUEVO CLIENTE - Game Line Col* 🎮\n\n"
                    "Cliente: *+" + phone + "*\n"
                    "Meses: *" + meses + "*\n"
                    "Cuenta: *" + tipo_cuenta + "*\n\n"
                    "Espera su codigo de activacion! 🚀"
                )
                send_message(ADMIN_PHONE, alerta)
            elif "2" in text or "no" in text_lower or "apartar" in text_lower:
                conversaciones[phone]["estado"] = "esperando_comprobante"
                send_message(phone, APARTAR)
                alerta = (
                    "💳 *CLIENTE QUIERE APARTAR - Game Line Col* 💳\n\n"
                    "Cliente: *+" + phone + "*\n"
                    "Meses: *" + meses + "*\n"
                    "Cuenta: *" + tipo_cuenta + "*\n\n"
                    "Espera el comprobante de pago! 🚀"
                )
                send_message(ADMIN_PHONE, alerta)
                registrar_cliente(phone, text, "Game Pass " + tipo_cuenta + " - " + meses, "💳 Quiere apartar servicio")
            else:
                send_message(phone, PREGUNTAR_CONSOLA)
            return jsonify({"status": "ok"}), 200

        if estado == "activacion" and es_codigo_consola(text):
            meses = conversaciones[phone].get("meses", "No especificado")
            tipo_cuenta = conversaciones[phone].get("tipo_cuenta", "No especificado")
            conversaciones[phone]["compro"] = True
            send_message(phone, "✅ Codigo recibido! Nuestro asesor lo activara en breve. Gracias por tu compra! 🎮")
            alerta = (
                "🎮 *CODIGO DE ACTIVACION - Game Line Col* 🎮\n\n"
                "Cliente: *+" + phone + "*\n"
                "Meses: *" + meses + "*\n"
                "Cuenta: *" + tipo_cuenta + "*\n"
                "Codigo: *" + text + "*\n\n"
                "Activa el servicio! 🚀"
            )
            send_message(ADMIN_PHONE, alerta)
            registrar_cliente(phone, "Codigo: " + text, "Game Pass " + tipo_cuenta + " - " + meses, "💰 COMPRA CONFIRMADA")
            return jsonify({"status": "ok"}), 200

        if estado == "esperando_comprobante":
            meses = conversaciones[phone].get("meses", "No especificado")
            tipo_cuenta = conversaciones[phone].get("tipo_cuenta", "No especificado")
            conversaciones[phone]["compro"] = True
            conversaciones[phone]["estado"] = "esperando_codigo_apartado"
            send_message(phone, "✅ Comprobante recibido! Un asesor confirmara tu reserva en breve.\n\nCuando tengas tu consola lista envianos el codigo de activacion aqui y lo activamos de inmediato! 🎮")
            alerta = (
                "💳 *COMPROBANTE DE PAGO - Game Line Col* 💳\n\n"
                "Cliente: *+" + phone + "*\n"
                "Meses: *" + meses + "*\n"
                "Cuenta: *" + tipo_cuenta + "*\n\n"
                "El cliente envio comprobante. Confirma el pago! 🚀"
            )
            send_message(ADMIN_PHONE, alerta)
            registrar_cliente(phone, "Comprobante enviado", "Game Pass " + tipo_cuenta + " - " + meses, "💰 PAGO RECIBIDO - Servicio apartado")
            return jsonify({"status": "ok"}), 200

        if estado == "esperando_codigo_apartado" and es_codigo_consola(text):
            meses = conversaciones[phone].get("meses", "No especificado")
            tipo_cuenta = conversaciones[phone].get("tipo_cuenta", "No especificado")
            config = CONFIG_PRINCIPAL if tipo_cuenta == "Principal" else CONFIG_SECUNDARIA
            send_message(phone, config)
            send_message(phone, "✅ Codigo recibido! Nuestro asesor lo activara en breve. 🎮")
            alerta = (
                "🎮 *CODIGO DE ACTIVACION (APARTADO) - Game Line Col* 🎮\n\n"
                "Cliente: *+" + phone + "*\n"
                "Meses: *" + meses + "*\n"
                "Cuenta: *" + tipo_cuenta + "*\n"
                "Codigo: *" + text + "*\n\n"
                "Activa el servicio! 🚀"
            )
            send_message(ADMIN_PHONE, alerta)
            registrar_cliente(phone, "Codigo apartado: " + text, "Game Pass " + tipo_cuenta + " - " + meses, "🎮 CODIGO RECIBIDO - Activar servicio")
            return jsonify({"status": "ok"}), 200

        if text == "1" or ("game pass" in text_lower and estado == "menu"):
            conversaciones[phone]["estado"] = "gamepass"
            send_message(phone, GAMEPASS)
            registrar_cliente(phone, text, "Game Pass Ultimate", "Consulto precios")
            return jsonify({"status": "ok"}), 200

        if text == "2" or text_lower in ["juegos", "juego", "juegos xbox"]:
            conversaciones[phone]["estado"] = "juegos"
            send_message(phone, JUEGOS_MENU)
            registrar_cliente(phone, text, "Juegos Xbox", "Consulto juegos")
            return jsonify({"status": "ok"}), 200

        if text == "3" or "soporte" in text_lower:
            conversaciones[phone]["estado"] = "soporte"
            send_message(phone, SOPORTE)
            registrar_cliente(phone, text, "Soporte", "Solicito soporte")
            alerta = "🚨 *SOPORTE Game Line Col* 🚨\n\nEl cliente *+" + phone + "* solicito soporte."
            send_message(ADMIN_PHONE, alerta)
            return jsonify({"status": "ok"}), 200

        if estado == "gamepass" and text_lower in ["si", "si", "yes", "quiero", "dale", "listo"]:
            conversaciones[phone]["estado"] = "seleccion_meses"
            send_message(phone, PREGUNTAR_MESES)
            return jsonify({"status": "ok"}), 200

        historial.append({"role": "user", "content": text})
        historial_texto = "\n".join([
            ("Cliente: " if h["role"] == "user" else "GameBot: ") + h["content"]
            for h in historial[-10:]
        ])

        response = client.models.generate_content(
            model="gemini-2.5-flash-lite",
