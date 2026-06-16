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
                mensaje = "Hola! Te escribimos desde Game Line Col. Notamos que estuviste interesado en nuestros servicios. Podemos ayudarte?\n\n1 Game Pass Ultimate\n2 Juegos Xbox\n3 Soporte"
                send_message(phone, mensaje)
                conversaciones[phone]["recordatorio_enviado"] = True
                registrar_cliente(phone, "Recordatorio", "Seguimiento", "Recordatorio enviado")

BIENVENIDA = "Bienvenido a Game Line Col!\n\nSomos tu tienda de confianza para juegos y suscripciones Xbox.\n\nEn que te podemos ayudar?\n\n1 Game Pass Ultimate (Xbox y PC)\n2 Juegos Xbox\n3 Soporte\n\nResponde con el numero de tu opcion"

GAMEPASS = "GAME PASS ULTIMATE - Xbox y PC\n\nPRECIOS:\n1 mes: $29.900\n2 meses: $55.000\n3 meses: $80.000\n6 meses: $140.000\n12 meses: $190.000\n\nMODALIDADES:\nCuenta Principal: Juegas desde tu cuenta personal sin iniciar sesion en otra cuenta\nCuenta Secundaria: Juegas desde tu cuenta personal iniciando sesion en la cuenta del servicio\n\nAmbas funcionan perfecto, solo cambia la configuracion.\n\nGARANTIA: Primero pruebas el servicio y solo pagas cuando estes satisfecho.\n\nPAGO: Llave Breve Falabella al 3057059517\n\nTe gustaria contratar el servicio? Responde SI para continuar"

PREGUNTAR_MESES = "Por cuantos meses deseas contratar?\n\n1 mes: $29.900\n2 meses: $55.000\n3 meses: $80.000\n6 meses: $140.000\n12 meses: $190.000\n\nResponde con el numero de meses"

PREGUNTAR_CUENTA = "Que tipo de cuenta prefieres?\n\n1. Cuenta Principal\nJuegas desde tu cuenta personal sin iniciar sesion en otra cuenta\n\n2. Cuenta Secundaria\nJuegas desde tu cuenta personal iniciando sesion en la cuenta del servicio\n\nAmbas funcionan perfecto, solo cambia la configuracion"

PREGUNTAR_CONSOLA = "Tienes tu consola o PC disponible ahora para generar el codigo de activacion?\n\n1. Si, tengo mi consola disponible\n2. No, quiero apartar el servicio y activarlo despues"

CONFIG_PRINCIPAL = "Configuracion CUENTA PRINCIPAL:\n\nCuando aparezcan las preguntas de asociacion sigue estos pasos:\n1. SIGUIENTE\n2. NO GRACIAS\n3. SIN BARRERAS\n4. OMITIR\n5. En la pregunta de hacer Xbox principal: HACER XBOX PRINCIPAL\n\nIMPORTANTE: Siempre usa el servicio con la sesion de tu cuenta personal. La cuenta que anadimos nunca la inicies.\n\nEl uso es exclusivo para ti, no la puedes compartir. Si llegase a pasar, la penalizacion es quitarte el servicio sin devolucion del dinero."

CONFIG_SECUNDARIA = "Configuracion CUENTA SECUNDARIA:\n\nCuando aparezcan las preguntas de asociacion sigue estos pasos:\n1. SIGUIENTE\n2. NO GRACIAS\n3. SIN BARRERAS\n4. VINCULAR CONTROL\n5. En la pregunta de hacer Xbox principal: NO CAMBIAR\n\nIMPORTANTE: Siempre con la sesion iniciada de la cuenta que anadimos y juegas directo con tu cuenta personal.\n\nEl uso es exclusivo para ti, no la puedes compartir. Si llegase a pasar, la penalizacion es quitarte el servicio sin devolucion del dinero."

ACTIVACION = "Sigue estos pasos en tu consola o PC:\n\n1. Ve a Agregar nuevo (como si fueras a agregar una nueva cuenta)\n2. Selecciona Usar otro dispositivo\n3. Aparecera un codigo, copialos y envialo aqui mismo\n\nNuestro asesor lo activara de inmediato!"

APARTAR = "Puedes apartar tu servicio pagando ahora y activarlo cuando tengas tu consola.\n\nRealiza el pago por Llave Breve Falabella al: 3057059517\n\nUna vez realizado el pago, envianos el comprobante aqui y un asesor confirmara tu reserva.\n\nCuando tengas tu consola lista, te indicamos como activarlo"

JUEGOS_MENU = "JUEGOS XBOX\n\nTenemos 4 modalidades:\n\n1. CODIGO (Economico)\nJuego comprado desde Microsoft, se agrega a tu cuenta de por vida\n\n2. CUENTA PRINCIPAL (+ Economico)\nAcceso de por vida, juegas desde tu cuenta sin iniciar sesion en otra\n\n3. CUENTA SECUNDARIA (++ Economico)\nAcceso de por vida, juegas iniciando sesion en la cuenta del juego\n\n4. SECUNDARIA CON METODO (+++ Economico)\nAcceso de por vida siguiendo un tutorial que compartimos\n\nQue juego estas buscando? Dinos el nombre y te conseguimos el precio"

SOPORTE = "SOPORTE\n\nUn asesor te atendera personalmente.\n\nEscribenos al: +57 322 908 2927"

SYSTEM_PROMPT = """Eres GameBot, asistente virtual de Game Line Col, tienda colombiana de Game Pass Ultimate y juegos Xbox. Responde en español, amable y profesional.

NEGOCIO:
- Game Pass Ultimate para Xbox y PC
- Juegos Xbox en 4 modalidades: Codigo, Principal, Secundaria, Secundaria con metodo
- Pago por Llave Breve Falabella al 3057059517
- Cliente primero prueba y luego paga
- No tenemos catalogo de juegos, cotizamos segun pedido

INSTRUCCIONES:
- Responde solo lo que el cliente pregunta
- Si pregunta por un juego especifico termina con ALERTA_JUEGO:[nombre]
- Si no puedes resolver algo termina con ALERTA_ASESOR
- No inventes precios de juegos
- Se conciso"""

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

        if phone not in conversaciones or es_saludo:
            conversaciones[phone] = {
                "estado": "menu",
                "historial": [],
                "ultima_interaccion": time.time(),
                "recordatorio_enviado": False,
                "compro": False,
                "meses": None,
                "tipo_cuenta": None
            }
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
                send_message(phone, "Seleccionaste " + meses + ".\n\n" + PREGUNTAR_CUENTA)
            else:
                send_message(phone, "No entendi. Responde con el numero de meses: 1, 2, 3, 6 o 12")
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
            elif "diferencia" in text_lower or "recomienda" in text_lower or "cual" in text_lower:
                send_message(phone, "Ambas funcionan perfecto! La diferencia es solo la configuracion.\nPrincipal: juegas sin iniciar sesion en otra cuenta.\nSecundaria: juegas iniciando sesion en la cuenta del servicio.\n\nCual prefieres?\n1. Principal\n2. Secundaria")
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
                alerta = "NUEVO CLIENTE Game Line Col\nCliente: +" + phone + "\nMeses: " + meses + "\nCuenta: " + tipo_cuenta + "\nEspera su codigo de activacion!"
                send_message(ADMIN_PHONE, alerta)
            elif "2" in text or "no" in text_lower or "apartar" in text_lower:
                conversaciones[phone]["estado"] = "apartar"
                send_message(phone, APARTAR)
                alerta = "CLIENTE QUIERE APARTAR Game Line Col\nCliente: +" + phone + "\nMeses: " + meses + "\nCuenta: " + tipo_cuenta + "\nEspera el comprobante de pago!"
                send_message(ADMIN_PHONE, alerta)
                registrar_cliente(phone, text, "Game Pass " + tipo_cuenta + " - " + meses, "Quiere apartar servicio")
            else:
                send_message(phone, PREGUNTAR_CONSOLA)
            return jsonify({"status": "ok"}), 200

        if estado == "activacion" and es_codigo_consola(text):
            meses = conversaciones[phone].get("meses", "No especificado")
            tipo_cuenta = conversaciones[phone].get("tipo_cuenta", "No especificado")
            conversaciones[phone]["compro"] = True
            send_message(phone, "Codigo recibido! Nuestro asesor lo activara en breve. Gracias por tu compra!")
            alerta = "CODIGO DE ACTIVACION Game Line Col\nCliente: +" + phone + "\nMeses: " + meses + "\nCuenta: " + tipo_cuenta + "\nCodigo: " + text + "\nActiva el servicio!"
            send_message(ADMIN_PHONE, alerta)
            registrar_cliente(phone, "Codigo: " + text, "Game Pass " + tipo_cuenta + " - " + meses, "COMPRA CONFIRMADA")
            return jsonify({"status": "ok"}), 200

        if estado == "apartar":
            meses = conversaciones[phone].get("meses", "No especificado")
            tipo_cuenta = conversaciones[phone].get("tipo_cuenta", "No especificado")
            conversaciones[phone]["compro"] = True
            send_message(phone, "Comprobante recibido! Un asesor confirmara tu reserva en breve. Cuando tengas tu consola lista te contactamos!")
            alerta = "COMPROBANTE DE PAGO Game Line Col\nCliente: +" + phone + "\nMeses: " + meses + "\nCuenta: " + tipo_cuenta + "\nConfirma el pago y reserva el servicio!"
            send_message(ADMIN_PHONE, alerta)
            registrar_cliente(phone, "Comprobante enviado", "Game Pass " + tipo_cuenta + " - " + meses, "PAGO RECIBIDO - Servicio apartado")
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
            alerta = "SOPORTE Game Line Col\nCliente: +" + phone + " solicito soporte."
            send_message(ADMIN_PHONE, alerta)
            return jsonify({"status": "ok"}), 200

        if estado == "gamepass" and text_lower in ["si", "sí", "yes", "quiero", "dale", "listo"]:
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
            contents=SYSTEM_PROMPT + "\n\nHistorial:\n" + historial_texto + "\n\nResponde al ultimo mensaje del cliente.",
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
            alerta = "COTIZACION DE JUEGO Game Line Col\nCliente: +" + phone + "\nJuego: " + nombre_juego + "\nPor favor cotiza y respondele."
            send_message(ADMIN_PHONE, alerta)
        elif "ALERTA_ASESOR" in reply:
            reply = reply.replace("ALERTA_ASESOR", "").strip()
            registrar_cliente(phone, text, "Consulta general", "Necesita asesor")
            alerta = "ALERTA Game Line Col\nCliente: +" + phone + " necesita asesor.\nPregunta: " + text
            send_message(ADMIN_PHONE, alerta)
        else:
            registrar_cliente(phone, text, "Consulta general", "Respondido por bot")

        historial.append({"role": "assistant", "content": reply})
        conversaciones[phone]["historial"] = historial[-20:]
        send_message(phone, reply)

    except Exception as e:
        print("Error: " + str(e))
    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
