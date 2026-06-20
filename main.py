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
        valores = [[fecha, "+"+phone, mensaje, servicio, estado]]
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
    r = requests.post(url, headers=headers, json=payload)
    return r.json()

PALABRAS_COMUNES = ["gracias", "listo", "vale", "ok", "okay", "perfecto", "genial", "bueno",
                     "claro", "entendido", "excelente", "graciaz", "thanks", "dale", "bien"]

def es_codigo_consola(texto):
    texto = texto.strip()
    if texto.lower() in PALABRAS_COMUNES:
        return False
    return bool(re.match(r'^[A-Za-z0-9]{6,25}$', texto))

def extraer_meses(texto):
    texto = texto.lower().strip()
    meses_map = {
        "1": "1 mes", "un mes": "1 mes", "uno": "1 mes",
        "2": "2 meses", "dos": "2 meses",
        "3": "3 meses", "tres": "3 meses",
        "6": "6 meses", "seis": "6 meses",
        "12": "12 meses", "doce": "12 meses",
        "un ano": "12 meses", "un año": "12 meses"
    }
    for key, value in meses_map.items():
        if texto == key or key in texto:
            return value
    return None

def es_agradecimiento(texto):
    texto = texto.lower().strip()
    palabras = ["gracias", "thanks", "thank you", "muchas gracias", "mil gracias", "graciaz"]
    return any(p in texto for p in palabras)

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
                msg = "Hola! Te escribimos desde Game Line Col 🎮\n\nNotamos que estuviste interesado en nuestros servicios.\n\nEn que te podemos ayudar?\n\n1 Game Pass Ultimate\n2 Juegos Xbox\n3 Soporte"
                send_message(phone, msg)
                conversaciones[phone]["recordatorio_enviado"] = True
                registrar_cliente(phone, "Recordatorio", "Seguimiento", "Recordatorio enviado")

conversaciones = {}
threading.Thread(target=verificar_seguimientos, daemon=True).start()

BIENVENIDA = "🎮 Bienvenido a Game Line Col! 🎮\n\nSomos tu tienda de confianza para juegos y suscripciones Xbox.\n\nEn que te podemos ayudar?\n\n1 Game Pass Ultimate (Xbox y PC)\n2 Juegos Xbox\n3 Soporte\n\nResponde con el numero de tu opcion 😊"

GAMEPASS = "🕹️ GAME PASS ULTIMATE\n\nPRECIOS:\n📅 1 mes: $29.900\n📅 2 meses: $55.000\n📅 3 meses: $80.000\n📅 6 meses: $140.000\n📅 12 meses: $190.000\n\nMODALIDADES:\n🏠 Principal: juegas desde tu cuenta sin iniciar sesion en otra\n👤 Secundaria: juegas desde tu cuenta iniciando sesion en la del servicio\n\nAmbas funcionan perfecto, solo cambia la configuracion.\n\nGARANTIA: Primero pruebas y luego pagas.\nPAGO: Llave Breve Falabella al 3057059517\n\nTe gustaria contratar? Responde SI 😊"

PREGUNTAR_MESES = "Por cuantos meses deseas contratar?\n\n📅 1 mes: $29.900\n📅 2 meses: $55.000\n📅 3 meses: $80.000\n📅 6 meses: $140.000\n📅 12 meses: $190.000\n\nResponde con el numero de meses"

PREGUNTAR_CUENTA = "Que tipo de cuenta prefieres?\n\n1 Cuenta Principal\n🏠 Juegas desde tu cuenta sin iniciar sesion en otra\n\n2 Cuenta Secundaria\n👤 Juegas desde tu cuenta iniciando sesion en la del servicio\n\nAmbas funcionan perfecto 😊"

PREGUNTAR_CONSOLA = "Tienes tu consola o PC disponible ahora?\n\n1 Si, tengo consola disponible\n2 No, quiero apartar y activar despues"

CONFIG_PRINCIPAL = "Una vez habilitemos tu cuenta, sigue estos pasos en tu consola:\n\nCONFIGURACION CUENTA PRINCIPAL\n\nCuando aparezcan las preguntas de asociacion Game Pass Ultimate:\n\n1 SIGUIENTE\n2 NO GRACIAS\n3 SIN BARRERAS\n4 OMITIR\n5 En la pregunta de hacer Xbox principal: HACER XBOX PRINCIPAL ✅\n\nIMPORTANTE:\nSiempre usa el servicio con la sesion de tu cuenta personal. La cuenta que anadimos nunca la inicies.\n\nEl uso es exclusivo para ti. Si compartes, se cancela sin devolucion del dinero."

CONFIG_SECUNDARIA = "Una vez habilitemos tu cuenta, sigue estos pasos en tu consola:\n\nCONFIGURACION CUENTA SECUNDARIA\n\nCuando aparezcan las preguntas de asociacion Game Pass Ultimate:\n\n1 SIGUIENTE\n2 NO GRACIAS\n3 SIN BARRERAS\n4 VINCULAR CONTROL\n5 En la pregunta de hacer Xbox principal: NO CAMBIAR ⛔\n\nMucho cuidado con esa pregunta, debes dar NO CAMBIAR.\n\nSiempre con sesion iniciada de la cuenta que anadimos y juegas con tu cuenta personal.\n\nEl uso es exclusivo para ti. Si compartes, se cancela sin devolucion del dinero."

ACTIVACION = "Sigue estos pasos en tu consola o PC:\n\n1 Ve a Agregar nuevo (como nueva cuenta)\n2 Selecciona Usar otro dispositivo\n3 Copia el codigo que aparece y envialo aqui\n\nNuestro asesor lo activara de inmediato! 🚀"

APARTAR = "Sin problema! Aparta tu servicio pagando ahora.\n\nPago por Llave Breve Falabella al: 3057059517\n\nCuando hayas pagado, envianos el comprobante aqui 📸\nUn asesor confirmara tu reserva.\n\nCuando tengas tu consola lista te indicamos como activarlo 🎮"

JUEGOS = "JUEGOS XBOX\n\n1 CODIGO (Economico)\nJuego desde Microsoft, para tu cuenta de por vida\n\n2 CUENTA PRINCIPAL (+ Economico)\nAcceso de por vida, sin iniciar sesion en otra cuenta\n\n3 CUENTA SECUNDARIA (++ Economico)\nAcceso de por vida, iniciando sesion en la cuenta del juego\n\n4 SECUNDARIA CON METODO (+++ Economico)\nAcceso de por vida con tutorial que compartimos\n\nQue juego buscas? Dinos el nombre 👇"

SOPORTE = "SOPORTE\n\nUn asesor te atendera personalmente.\n\nEscribenos al: +57 322 908 2927 😊"

CIERRE = "🎮 Con mucho gusto! Gracias a ti por confiar en Game Line Col 🙌\n\nCualquier cosa que necesites aqui estamos. Que disfrutes tu juego! 🚀"

PROMPT = "Eres GameBot de Game Line Col. Responde en espanol, amable y profesional. Si el cliente pregunta por un juego especifico termina con ALERTA_JUEGO:[nombre]. Si no puedes resolver algo termina con ALERTA_ASESOR. No inventes precios."

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
        msg_id = message.get("id", "")
        text = message["text"]["body"].strip()
        text_lower = text.lower()

        # ── COMANDO DEL ADMIN: "activo XXXX" ──
        if phone == ADMIN_PHONE and text_lower.startswith("activo"):
            ultimos_4 = text_lower.replace("activo", "").strip()
            cliente_encontrado = None
            for ph, datos in conversaciones.items():
                if ph.endswith(ultimos_4) and datos.get("compro"):
                    cliente_encontrado = ph
                    break

            if cliente_encontrado:
                tipo_cuenta = conversaciones[cliente_encontrado].get("tipo_cuenta", "Principal")
                config = CONFIG_PRINCIPAL if tipo_cuenta == "Principal" else CONFIG_SECUNDARIA
                mensaje_activo = "✅ Tu cuenta ha sido activada en la consola! 🎮\n\nYa puedes empezar a jugar. Sigue estas instrucciones:\n\n" + config
                send_message(cliente_encontrado, mensaje_activo)
                send_message(ADMIN_PHONE, "✅ Confirmacion enviada al cliente +" + cliente_encontrado)
                registrar_cliente(cliente_encontrado, "Activacion confirmada", "Game Pass " + tipo_cuenta, "✅ CUENTA ACTIVADA - Confirmado")
            else:
                send_message(ADMIN_PHONE, "⚠️ No encontre un cliente pendiente con esos ultimos 4 digitos: " + ultimos_4)
            return jsonify({"status": "ok"}), 200

        saludos = ["hola", "buenas", "buenos dias", "buenas tardes", "buenas noches", "hi", "hello", "inicio"]
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
                "bienvenida_enviada": False,
                "ultimo_msg_id": ""
            }

        if msg_id and msg_id == conversaciones[phone].get("ultimo_msg_id", ""):
            return jsonify({"status": "ok"}), 200
        conversaciones[phone]["ultimo_msg_id"] = msg_id

        if not conversaciones[phone].get("bienvenida_enviada") or es_saludo:
            conversaciones[phone]["bienvenida_enviada"] = True
            conversaciones[phone]["estado"] = "menu"
            conversaciones[phone]["ultima_interaccion"] = time.time()
            send_message(phone, BIENVENIDA)
            registrar_cliente(phone, text, "Inicio", "Bienvenida enviada")
            return jsonify({"status": "ok"}), 200

        # ── AGRADECIMIENTO EN CUALQUIER MOMENTO (cierre de venta) ──
        if es_agradecimiento(text) and conversaciones[phone].get("compro"):
            conversaciones[phone]["ultima_interaccion"] = time.time()
            send_message(phone, CIERRE)
            return jsonify({"status": "ok"}), 200

        conversaciones[phone]["ultima_interaccion"] = time.time()
        conversaciones[phone]["recordatorio_enviado"] = False
        estado = conversaciones[phone].get("estado", "menu")

        historial = conversaciones[phone].get("historial", [])
        meses = conversaciones[phone].get("meses", "No especificado")
        tipo_cuenta = conversaciones[phone].get("tipo_cuenta", "No especificado")

        if estado == "seleccion_meses":
            m = extraer_meses(text)
            if m:
                conversaciones[phone]["meses"] = m
                conversaciones[phone]["estado"] = "seleccion_cuenta"
                send_message(phone, "Seleccionaste " + m + ".\n\n" + PREGUNTAR_CUENTA)
            else:
                send_message(phone, "No entendi. Responde con: 1, 2, 3, 6 o 12")
            return jsonify({"status": "ok"}), 200

        if estado == "seleccion_cuenta":
            if "1" in text or "principal" in text_lower:
                conversaciones[phone]["tipo_cuenta"] = "Principal"
                conversaciones[phone]["estado"] = "preguntar_consola"
                send_message(phone, "Elegiste Cuenta Principal!\n\n" + PREGUNTAR_CONSOLA)
            elif "2" in text or "secundaria" in text_lower:
                conversaciones[phone]["tipo_cuenta"] = "Secundaria"
                conversaciones[phone]["estado"] = "preguntar_consola"
                send_message(phone, "Elegiste Cuenta Secundaria!\n\n" + PREGUNTAR_CONSOLA)
            else:
                send_message(phone, PREGUNTAR_CUENTA)
            return jsonify({"status": "ok"}), 200

        if estado == "preguntar_consola":
            meses = conversaciones[phone].get("meses", "No especificado")
            tipo_cuenta = conversaciones[phone].get("tipo_cuenta", "No especificado")
            if "1" in text or "si" in text_lower or "tengo" in text_lower:
                conversaciones[phone]["estado"] = "activacion"
                send_message(phone, ACTIVACION)
                alerta = "NUEVO CLIENTE Game Line Col\nCliente: +" + phone + "\nMeses: " + meses + "\nCuenta: " + tipo_cuenta + "\nEspera codigo de activacion!"
                send_message(ADMIN_PHONE, alerta)
            elif "2" in text or "no" in text_lower or "apartar" in text_lower:
                conversaciones[phone]["estado"] = "esperando_comprobante"
                send_message(phone, APARTAR)
                alerta = "CLIENTE QUIERE APARTAR Game Line Col\nCliente: +" + phone + "\nMeses: " + meses + "\nCuenta: " + tipo_cuenta + "\nEspera comprobante!"
                send_message(ADMIN_PHONE, alerta)
                registrar_cliente(phone, text, "Game Pass " + tipo_cuenta + " - " + meses, "Quiere apartar")
            else:
                send_message(phone, PREGUNTAR_CONSOLA)
            return jsonify({"status": "ok"}), 200

        if estado == "activacion" and es_codigo_consola(text):
            meses = conversaciones[phone].get("meses", "No especificado")
            tipo_cuenta = conversaciones[phone].get("tipo_cuenta", "No especificado")
            conversaciones[phone]["compro"] = True
            send_message(phone, "Codigo recibido! Nuestro asesor lo activara en breve. 🎮\n\nTe avisaremos cuando este lista la activacion.")
            alerta = "CODIGO DE ACTIVACION Game Line Col\nCliente: +" + phone + "\nMeses: " + meses + "\nCuenta: " + tipo_cuenta + "\nCodigo: " + text + "\nActiva el servicio!\n\nPara confirmar al cliente responde: activo " + phone[-4:]
            send_message(ADMIN_PHONE, alerta)
            registrar_cliente(phone, "Codigo: " + text, "Game Pass " + tipo_cuenta + " - " + meses, "COMPRA CONFIRMADA - Pendiente activar")
            return jsonify({"status": "ok"}), 200

        if estado == "esperando_comprobante":
            meses = conversaciones[phone].get("meses", "No especificado")
            tipo_cuenta = conversaciones[phone].get("tipo_cuenta", "No especificado")
            conversaciones[phone]["estado"] = "esperando_codigo_apartado"
            send_message(phone, "Comprobante recibido! Un asesor confirmara tu reserva.\n\nCuando tengas tu consola disponible envianos el codigo de activacion aqui 🎮")
            alerta = "COMPROBANTE DE PAGO Game Line Col\nCliente: +" + phone + "\nMeses: " + meses + "\nCuenta: " + tipo_cuenta + "\nConfirma el pago!"
            send_message(ADMIN_PHONE, alerta)
            registrar_cliente(phone, "Comprobante enviado", "Game Pass " + tipo_cuenta + " - " + meses, "PAGO RECIBIDO")
            return jsonify({"status": "ok"}), 200

        if estado == "esperando_codigo_apartado" and es_codigo_consola(text):
            meses = conversaciones[phone].get("meses", "No especificado")
            tipo_cuenta = conversaciones[phone].get("tipo_cuenta", "No especificado")
            conversaciones[phone]["compro"] = True
            send_message(phone, "Codigo recibido! Nuestro asesor lo activara en breve. 🎮\n\nTe avisaremos cuando este lista la activacion.")
            alerta = "CODIGO ACTIVACION APARTADO Game Line Col\nCliente: +" + phone + "\nMeses: " + meses + "\nCuenta: " + tipo_cuenta + "\nCodigo: " + text + "\nActiva el servicio!\n\nPara confirmar al cliente responde: activo " + phone[-4:]
            send_message(ADMIN_PHONE, alerta)
            registrar_cliente(phone, "Codigo: " + text, "Game Pass " + tipo_cuenta + " - " + meses, "CODIGO RECIBIDO - Pendiente activar")
            return jsonify({"status": "ok"}), 200

        if text == "1" or ("game pass" in text_lower and estado == "menu"):
            conversaciones[phone]["estado"] = "gamepass"
            send_message(phone, GAMEPASS)
            registrar_cliente(phone, text, "Game Pass Ultimate", "Consulto precios")
            return jsonify({"status": "ok"}), 200

        if text == "2" or text_lower in ["juegos", "juego"]:
            conversaciones[phone]["estado"] = "juegos"
            send_message(phone, JUEGOS)
            registrar_cliente(phone, text, "Juegos Xbox", "Consulto juegos")
            return jsonify({"status": "ok"}), 200

        if text == "3" or "soporte" in text_lower:
            conversaciones[phone]["estado"] = "soporte"
            send_message(phone, SOPORTE)
            registrar_cliente(phone, text, "Soporte", "Solicito soporte")
            alerta = "SOPORTE Game Line Col\nCliente: +" + phone + " solicito soporte."
            send_message(ADMIN_PHONE, alerta)
            return jsonify({"status": "ok"}), 200

        if estado == "gamepass" and text_lower in ["si", "sí", "yes", "quiero", "dale", "listo"]:
            conversaciones[phone]["estado"] = "seleccion_meses"
            send_message(phone, PREGUNTAR_MESES)
            return jsonify({"status": "ok"}), 200

        # ── AGRADECIMIENTO SIN COMPRA AUN (despedida general) ──
        if es_agradecimiento(text):
            send_message(phone, "🎮 Con mucho gusto! Cualquier cosa que necesites aqui estamos 😊")
            return jsonify({"status": "ok"}), 200

        historial.append({"role": "user", "content": text})
        historial_texto = ""
        for h in historial[-10:]:
            rol = "Cliente" if h["role"] == "user" else "GameBot"
            historial_texto += rol + ": " + h["content"] + "\n"

        response = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=PROMPT + "\n\nHistorial:\n" + historial_texto + "\nResponde al ultimo mensaje.",
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
            registrar_cliente(phone, text, "Juego: " + nombre_juego, "Cotizacion solicitada")
            alerta = "COTIZACION Game Line Col\nCliente: +" + phone + "\nJuego: " + nombre_juego
            send_message(ADMIN_PHONE, alerta)
        elif "ALERTA_ASESOR" in reply:
            reply = reply.replace("ALERTA_ASESOR", "").strip()
            registrar_cliente(phone, text, "Consulta", "Necesita asesor")
            alerta = "ALERTA ASESOR Game Line Col\nCliente: +" + phone + "\nPregunta: " + text
            send_message(ADMIN_PHONE, alerta)
        else:
            registrar_cliente(phone, text, "Consulta", "Respondido por bot")

        historial.append({"role": "assistant", "content": reply})
        conversaciones[phone]["historial"] = historial[-20:]
       
