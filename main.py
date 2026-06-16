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
    requests.post(url, headers=headers, json=payload)

def es_codigo_consola(texto):
    texto = texto.strip()
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

MSG_BIENVENIDA = "🎮 Bienvenido a Game Line Col! 🎮\n\nSomos tu tienda de confianza para juegos y suscripciones Xbox.\n\nEn que te podemos ayudar?\n\n1 Game Pass Ultimate (Xbox y PC)\n2 Juegos Xbox\n3 Soporte\n\nResponde con el numero de tu opcion 😊"

MSG_GAMEPASS = "🕹️ GAME PASS ULTIMATE\n\nPRECIOS:\n1 mes: $29.900\n2 meses: $55.000\n3 meses: $80.000\n6 meses: $140.000\n12 meses: $190.000\n\nMODALIDADES:\nPrincipal: juegas desde tu cuenta sin iniciar sesion en otra\nSecundaria: juegas desde tu cuenta iniciando sesion en la del servicio\n\nAmbas funcionan perfecto, solo cambia la configuracion.\n\nGARANTIA: Primero pruebas y luego pagas.\nPAGO: Llave Breve Falabella al 3057059517\n\nTe gustaria contratar? Responde SI 😊"

MSG_MESES = "Por cuantos meses deseas contratar?\n\n1 mes: $29.900\n2 meses: $55.000\n3 meses: $80.000\n6 meses: $140.000\n12 meses: $190.000\n\nResponde con el numero de meses"

MSG_CUENTA = "Que tipo de cuenta prefieres?\n\n1. Cuenta Principal\nJuegas desde tu cuenta sin iniciar sesion en otra\n\n2. Cuenta Secundaria\nJuegas desde tu cuenta iniciando sesion en la del servicio\n\nAmbas funcionan perfecto 😊"

MSG_CONSOLA = "Tienes tu consola o PC disponible ahora?\n\n1. Si, tengo consola disponible\n2. No, quiero apartar y activar despues"

MSG_CONFIG_PRINCIPAL = "CONFIGURACION CUENTA PRINCIPAL:\n\n1. SIGUIENTE\n2. NO GRACIAS\n3. SIN BARRERAS\n4. OMITIR\n5. HACER XBOX PRINCIPAL\n\nSiempre usa el servicio con tu cuenta personal. La cuenta que anadimos nunca la inicies.\n\nUso exclusivo para ti. Si compartes, se cancela el servicio sin devolucion."

MSG_CONFIG_SECUNDARIA = "CONFIGURACION CUENTA SECUNDARIA:\n\n1. SIGUIENTE\n2. NO GRACIAS\n3. SIN BARRERAS\n4. VINCULAR CONTROL\n5. NO CAMBIAR (en pregunta de Xbox principal)\n\nSiempre con sesion iniciada de la cuenta que anadimos, juegas con tu cuenta personal.\n\nUso exclusivo para ti. Si compartes, se cancela el servicio sin devolucion."

MSG_ACTIVACION = "Sigue estos pasos en tu consola:\n\n1. Agregar nuevo (como nueva cuenta)\n2. Usar otro dispositivo\n3. Copia el codigo que aparece y envialo aqui\n\nNuestro asesor lo activara de inmediato! 🚀"

MSG_APARTAR = "Sin problema! Aparta tu servicio pagando ahora.\n\nPago por Llave Breve Falabella al: 3057059517\n\nCuando hayas pagado, envianos el comprobante aqui 📸\nUn asesor confirmara tu reserva.\n\nCuando tengas tu consola lista te indicamos como activarlo 🎮"

MSG_JUEGOS = "JUEGOS XBOX\n\n1. CODIGO (Economico)\nJuego comprado desde Microsoft, para tu cuenta de por vida\n\n2. CUENTA PRINCIPAL (+ Economico)\nAcceso de por vida, sin iniciar sesion en otra cuenta\n\n3. CUENTA SECUNDARIA (++ Economico)\nAcceso de por vida, iniciando sesion en la cuenta del juego\n\n4. SECUNDARIA CON METODO (+++ Economico)\nAcceso de por vida con tutorial que compartimos\n\nQue juego buscas? Dinos el nombre 👇"

MSG_SOPORTE = "SOPORTE\n\nUn asesor te atendera personalmente.\n\nEscribenos al: +57 322 908 2927 😊"

SYSTEM_PROMPT = "Eres GameBot de Game Line Col. Responde en espanol, amable y profesional. Si el cliente pregunta por un juego especifico termina con ALERTA_JUEGO:[nombre]. Si no puedes resolver algo termina con ALERTA_ASESOR. No inventes precios."

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
                "bienvenida_enviada": False
            }

        if not conversaciones[phone].get("bienvenida_enviada") or es_saludo:
            conversaciones[phone]["bienvenida_enviada"] = True
            conversaciones[phone]["estado"] = "menu"
            conversaciones[phone]["ultima_interaccion"] = time.time()
            send_message(phone, MSG_BIENVENIDA)
            registrar_cliente(phone, text, "Inicio", "Bienvenida enviada")
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
                send_message(phone, "Seleccionaste " + m + ".\n\n" + MSG_CUENTA)
            else:
                send_message(phone, "No entendi. Responde con: 1, 2, 3, 6 o 12")
            return jsonify({"status": "ok"}), 200

        if estado == "seleccion_cuenta":
            if "1" in text or "principal" in text_lower:
                conversaciones[phone]["tipo_cuenta"] = "Principal"
                conversaciones[phone]["estado"] = "preguntar_consola"
                send_message(phone, "Elegiste Cuenta Principal!\n\n" + MSG_CONSOLA)
            elif "2" in text or "secundaria" in text_lower:
                conversaciones[phone]["tipo_cuenta"] = "Secundaria"
                conversaciones[phone]["estado"] = "preguntar_consola"
                send_message(phone, "Elegiste Cuenta Secundaria!\n\n" + MSG_CONSOLA)
            else:
                send_message(phone, MSG_CUENTA)
            return jsonify({"status": "ok"}), 200

        if estado == "preguntar_consola":
            if "1" in text or "si" in text_lower or "tengo" in text_lower:
                conversaciones[phone]["estado"] = "activacion"
                config = MSG_CONFIG_PRINCIPAL if tipo_cuenta == "Principal" else MSG_CONFIG_SECUNDARIA
                send_message(phone, config)
                send_message(phone, MSG_ACTIVACION)
                alerta = "NUEVO CLIENTE\nCliente: +" + phone + "\nMeses: " + meses + "\nCuenta: " + tipo_cuenta + "\nEspera codigo de activacion!"
                send_message(ADMIN_PHONE, alerta)
            elif "2" in text or "no" in text_lower or "apartar" in text_lower:
                conversaciones[phone]["estado"] = "esperando_comprobante"
                send_message(phone, MSG_APARTAR)
                alerta = "CLIENTE QUIERE APARTAR\nCliente: +" + phone + "\nMeses: " + meses + "\nCuenta: " + tipo_cuenta + "\nEspera comprobante de pago!"
                send_message(ADMIN_PHONE, alerta)
                registrar_cliente(phone, text, "Game Pass " + tipo_cuenta + " - " + meses, "Quiere apartar")
            else:
                send_message(phone, MSG_CONSOLA)
            return jsonify({"status": "ok"}), 200

        if estado == "activacion" and es_codigo_consola(text):
            conversaciones[phone]["compro"] = True
            send_message(phone, "Codigo recibido! Nuestro asesor lo activara en breve. Gracias! 🎮")
            alerta = "CODIGO DE ACTIVACION\nCliente: +" + phone + "\nMeses: " + meses + "\nCuenta: " + tipo_cuenta + "\nCodigo: " + text + "\nActiva el servicio!"
            send_message(ADMIN_PHONE, alerta)
            registrar_cliente(phone, "Codigo: " + text, "Game Pass " + tipo_cuenta + " - " + meses, "COMPRA CONFIRMADA")
            return jsonify({"status": "ok"}), 200

        if estado == "esperando_comprobante":
            conversaciones[phone]["estado"] = "esperando_codigo_apartado"
            send_message(phone, "Comprobante recibido! Un asesor confirmara tu reserva.\n\nCuando tengas tu consola disponible envianos el codigo de activacion aqui 🎮")
            alerta = "COMPROBANTE DE PAGO\nCliente: +" + phone + "\nMeses: " + meses + "\nCuenta: " + tipo_cuenta + "\nConfirma el pago!"
            send_message(ADMIN_PHONE, alerta)
            registrar_cliente(phone, "Comprobante enviado", "Game Pass " + tipo_cuenta + " - " + meses, "PAGO RECIBIDO")
            return jsonify({"status": "ok"}), 200

        if estado == "esperando_codigo_apartado" and es_codigo_consola(text):
            conversaciones[phone]["compro"] = True
            config = MSG_CONFIG_PRINCIPAL if tipo_cuenta == "Principal" else MSG_CONFIG_SECUNDARIA
            send_message(phone, config)
            send_message(phone, "Codigo recibido! Nuestro asesor lo activara en breve. 🎮")
            alerta = "CODIGO ACTIVACION (APARTADO)\nCliente: +" + phone + "\nMeses: " + meses + "\nCuenta: " + tipo_cuenta + "\nCodigo: " + text + "\nActiva el servicio!"
            send_message(ADMIN_PHONE, alerta)
            registrar_cliente(phone, "Codigo: " + text, "Game Pass " + tipo_cuenta + " - " + meses, "CODIGO RECIBIDO - Activar")
            return jsonify({"status": "ok"}), 200

        if text == "1" or ("game pass" in text_lower and estado == "menu"):
            conversaciones[phone]["estado"] = "gamepass"
            send_message(phone, MSG_GAMEPASS)
            registrar_cliente(phone, text, "Game Pass Ultimate", "Consulto precios")
            return jsonify({"status": "ok"}), 200

        if text == "2" or text_lower in ["juegos", "juego"]:
            conversaciones[phone]["estado"] = "juegos"
            send_message(phone, MSG_JUEGOS)
            registrar_cliente(phone, text, "Juegos Xbox", "Consulto juegos")
            return jsonify({"status": "ok"}), 200

        if text == "3" or "soporte" in text_lower:
            conversaciones[phone]["estado"] = "soporte"
            send_message(phone, MSG_SOPORTE)
            registrar_cliente(phone, text, "Soporte", "Solicito soporte")
            alerta = "SOPORTE\nCliente: +" + phone + " solicito soporte."
            send_message(ADMIN_PHONE, alerta)
            return jsonify({"status": "ok"}), 200

        if estado == "gamepass" and text_lower in ["si", "sí", "yes", "quiero", "dale", "listo"]:
            conversaciones[phone]["estado"] = "seleccion_meses"
            send_message(phone, MSG_MESES)
            return jsonify({"status": "ok"}), 200

        historial.append({"role": "user", "content": text})
        historial_texto = "\n".join([
            ("Cliente: " if h["role"] == "user" else "GameBot: ") + h["content"]
            for h in historial[-10:]
        ])

        response = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=SYSTEM_PROMPT + "\n\nHistorial:\n" + historial_texto + "\n\nResponde al ultimo mensaje.",
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
            alerta = "COTIZACION DE JUEGO\nCliente: +" + phone + "\nJuego: " + nombre_juego
            send_message(ADMIN_PHONE, alerta)
        elif "ALERTA_ASESOR" in reply:
            reply = reply.replace("ALERTA_ASESOR", "").strip()
            registrar_cliente(phone, text, "Consulta", "Necesita asesor")
            alerta = "ALERTA ASESOR\nCliente: +" + phone + "\nPregunta: " + text
            send_message(ADMIN_PHONE, alerta)
        else:
            registrar_cliente(phone, text, "Consulta", "Respondido por bot")

        historial.append({"role": "assistant", "content": reply})
        conversaciones[phone]["historial"] = historial[-20:]
        send_message(phone, reply)

    except Exception as e:
        print("Error: " + str(e))
    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
