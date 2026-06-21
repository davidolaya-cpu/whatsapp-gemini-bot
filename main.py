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
MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN")
MP_NOTIFICATION_URL = os.environ.get("MP_NOTIFICATION_URL")
SHEET_ID = "1lvIlK1LYbT68HsuDTbMRzWSYh_RGUPHAZeV31_sAmdU"
ADMIN_PHONE = "573229082927"
HORA_SEGUIMIENTO = 3600
HORA_SEGUIMIENTO_24H = 86400

client = genai.Client(api_key=GEMINI_API_KEY)


_sheets_service = None


def get_sheets_service():
    global _sheets_service
    if _sheets_service is None:
        creds_dict = json.loads(GOOGLE_CREDENTIALS)
        creds = service_account.Credentials.from_service_account_info(
            creds_dict,
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        _sheets_service = build("sheets", "v4", credentials=creds)
    return _sheets_service


def registrar_cliente(phone, mensaje, servicio, estado):
    try:
        service = get_sheets_service()
        fecha = datetime.now().strftime("%d/%m/%Y %H:%M")
        valores = [[fecha, "+" + phone, mensaje, servicio, estado]]
        service.spreadsheets().values().append(
            spreadsheetId=SHEET_ID,
            range="A:E",
            valueInputOption="RAW",
            body={"values": valores}
        ).execute()
    except Exception as e:
        print("Error Sheets: " + str(e))


def send_message(phone, message, intentos=3):
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
    ultimo_resultado = None
    for intento in range(intentos):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=10)
            ultimo_resultado = r.json()
            if r.status_code < 400:
                return ultimo_resultado
            print("WhatsApp error (intento " + str(intento + 1) + "): " + str(ultimo_resultado))
        except Exception as e:
            print("Error enviando mensaje (intento " + str(intento + 1) + "): " + str(e))
        time.sleep(2)
    return ultimo_resultado


def reenviar_imagen(phone, media_id, caption="", intentos=2):
    url = "https://graph.facebook.com/v18.0/" + PHONE_NUMBER_ID + "/messages"
    headers = {
        "Authorization": "Bearer " + WHATSAPP_TOKEN,
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "image",
        "image": {"id": media_id}
    }
    if caption:
        payload["image"]["caption"] = caption
    for intento in range(intentos):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=10)
            data = r.json()
            if r.status_code < 400:
                return data
            print("Error reenviando imagen (intento " + str(intento + 1) + "): " + str(data))
        except Exception as e:
            print("Error reenviando imagen (intento " + str(intento + 1) + "): " + str(e))
        time.sleep(2)
    send_message(ADMIN_PHONE, "⚠️ No pude reenviarte la foto del comprobante (puede que el enlace ya haya expirado). Pidele al cliente que la reenvie si la necesitas.")
    return None


def enviar_botones(phone, cuerpo, botones, intentos=3):
    url = "https://graph.facebook.com/v18.0/" + PHONE_NUMBER_ID + "/messages"
    headers = {
        "Authorization": "Bearer " + WHATSAPP_TOKEN,
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": cuerpo},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": b["id"], "title": b["titulo"]}} for b in botones
                ]
            }
        }
    }
    for intento in range(intentos):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=10)
            data = r.json()
            if r.status_code < 400:
                return data
            print("Error enviando botones (intento " + str(intento + 1) + "): " + str(data))
        except Exception as e:
            print("Error enviando botones (intento " + str(intento + 1) + "): " + str(e))
        time.sleep(2)
    return send_message(phone, cuerpo)  # respaldo: si fallan los botones, manda texto plano


def enviar_lista(phone, cuerpo, texto_boton, filas, titulo_seccion="Opciones", intentos=3):
    url = "https://graph.facebook.com/v18.0/" + PHONE_NUMBER_ID + "/messages"
    headers = {
        "Authorization": "Bearer " + WHATSAPP_TOKEN,
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": cuerpo},
            "action": {
                "button": texto_boton,
                "sections": [{
                    "title": titulo_seccion,
                    "rows": [{"id": f["id"], "title": f["titulo"], "description": f.get("descripcion", "")} for f in filas]
                }]
            }
        }
    }
    for intento in range(intentos):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=10)
            data = r.json()
            if r.status_code < 400:
                return data
            print("Error enviando lista (intento " + str(intento + 1) + "): " + str(data))
        except Exception as e:
            print("Error enviando lista (intento " + str(intento + 1) + "): " + str(e))
        time.sleep(2)
    return send_message(phone, cuerpo)  # respaldo: si falla la lista, manda texto plano


def descargar_media(media_id):
    url = "https://graph.facebook.com/v18.0/" + media_id
    headers = {"Authorization": "Bearer " + WHATSAPP_TOKEN}
    info = requests.get(url, headers=headers).json()
    media_url = info.get("url")
    mime_type = info.get("mime_type", "image/jpeg")
    media_resp = requests.get(media_url, headers=headers)
    return media_resp.content, mime_type


PRECIOS_GAMEPASS = {
    "1 mes": 29900,
    "2 meses": 55000,
    "3 meses": 80000,
    "6 meses": 140000,
    "12 meses": 190000
}


def crear_link_pago(phone, concepto, monto):
    try:
        referencia = phone + "-" + str(int(time.time()))
        url = "https://api.mercadopago.com/checkout/preferences"
        headers = {
            "Authorization": "Bearer " + MP_ACCESS_TOKEN,
            "Content-Type": "application/json"
        }
        body = {
            "items": [{
                "title": concepto,
                "quantity": 1,
                "unit_price": float(monto),
                "currency_id": "COP"
            }],
            "external_reference": referencia,
            "notification_url": MP_NOTIFICATION_URL,
            "back_urls": {
                "success": "https://www.mercadopago.com.co",
                "failure": "https://www.mercadopago.com.co",
                "pending": "https://www.mercadopago.com.co"
            }
        }
        r = requests.post(url, headers=headers, json=body, timeout=10)
        data = r.json()
        link = data.get("init_point")
        if not link:
            print("Error creando preferencia MP: " + str(data))
        return link, referencia
    except Exception as e:
        print("Error creando link de pago: " + str(e))
        return None, None


def mensaje_opciones_pago(link):
    texto = "Puedes pagar de cualquiera de estas formas:\n\n"
    if link:
        texto += "💳 Tarjeta, PSE o Nequi por Mercado Pago (confirmacion automatica):\n" + link + "\n\n"
    texto += ("📲 Nequi: 3057059517\n📲 Daviplata: 3057059517\n🏦 Llave: 3057059517 (David Olaya)\n\n"
              "Si pagas directo por Nequi/Daviplata/Llave, envianos la foto del comprobante aqui 📸. "
              "Si usas el link de Mercado Pago, lo confirmamos automaticamente.")
    return texto


def leer_comprobante(media_bytes, mime_type):
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=[
                types.Part.from_bytes(data=media_bytes, mime_type=mime_type),
                "Esta imagen es un comprobante de pago colombiano (Nequi, Daviplata o transferencia/llave "
                "bancaria). Extrae y resume en espanol, en pocas lineas: monto pagado, fecha y hora si aparecen, "
                "y el numero, cuenta o llave destino si aparece. Si la imagen no parece un comprobante de pago "
                "o no logras leer algun dato, dilo claramente."
            ]
        )
        return response.text.strip()
    except Exception as e:
        print("Error leyendo comprobante: " + str(e))
        return "No pude leer el comprobante automaticamente, revisa la imagen manualmente."


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


ESTADOS_RANGE = "Estados!A:B"


def asegurar_hoja_estados():
    try:
        service = get_sheets_service()
        body = {"requests": [{"addSheet": {"properties": {"title": "Estados"}}}]}
        service.spreadsheets().batchUpdate(spreadsheetId=SHEET_ID, body=body).execute()
        print("Hoja 'Estados' creada")
    except Exception:
        pass  # ya existe, no pasa nada


def cargar_estados():
    try:
        service = get_sheets_service()
        result = service.spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range=ESTADOS_RANGE
        ).execute()
        filas = result.get("values", [])
        cargados = 0
        for fila in filas[1:]:
            if len(fila) >= 2:
                try:
                    datos = json.loads(fila[1])
                    if datos.get("estado") == "pago_confirmado":
                        continue  # ya cerrado, no hace falta tenerlo en memoria
                    conversaciones[fila[0]] = datos
                    cargados += 1
                except Exception:
                    continue
        print("Estados cargados desde Sheets: " + str(cargados))
    except Exception as e:
        print("Error cargando estados: " + str(e))


def guardar_estados_periodico():
    while True:
        time.sleep(60)
        try:
            service = get_sheets_service()
            filas = [["telefono", "json"]]
            for phone, datos in list(conversaciones.items()):
                if datos.get("estado") == "pago_confirmado":
                    continue  # ya cerrado, no hace falta seguir persistiendolo
                datos_reducidos = {k: v for k, v in datos.items() if k != "historial"}
                filas.append([phone, json.dumps(datos_reducidos)])
            service.spreadsheets().values().clear(
                spreadsheetId=SHEET_ID, range=ESTADOS_RANGE
            ).execute()
            service.spreadsheets().values().update(
                spreadsheetId=SHEET_ID,
                range="Estados!A1",
                valueInputOption="RAW",
                body={"values": filas}
            ).execute()
        except Exception as e:
            print("Error guardando estados: " + str(e))


def limpiar_conversaciones_antiguas():
    while True:
        time.sleep(3600)
        ahora = time.time()
        eliminadas = 0
        for phone in list(conversaciones.keys()):
            datos = conversaciones[phone]
            ultima = datos.get("ultima_interaccion", 0)
            estado = datos.get("estado")
            if estado == "pago_confirmado" and (ahora - ultima) >= 86400:
                del conversaciones[phone]
                eliminadas += 1
            elif estado == "menu" and (ahora - ultima) >= (30 * 86400):
                del conversaciones[phone]
                eliminadas += 1
        if eliminadas:
            print("Limpieza de memoria: " + str(eliminadas) + " conversaciones antiguas eliminadas. Activas: " + str(len(conversaciones)))


ESTADOS_PENDIENTES = ["activacion", "esperando_comprobante", "esperando_codigo_apartado",
                      "esperando_pago_final", "pago_final_enviado"]

estadisticas_diarias = {"fecha": None, "nuevos": 0, "cierres": 0}


def registrar_evento_diario(tipo):
    hoy = datetime.now().strftime("%d/%m/%Y")
    if estadisticas_diarias["fecha"] != hoy:
        estadisticas_diarias["fecha"] = hoy
        estadisticas_diarias["nuevos"] = 0
        estadisticas_diarias["cierres"] = 0
    estadisticas_diarias[tipo] = estadisticas_diarias.get(tipo, 0) + 1


def verificar_seguimientos():
    while True:
        time.sleep(60)
        ahora = time.time()
        for phone, datos in list(conversaciones.items()):
            if datos.get("compro"):
                continue
            ultima = datos.get("ultima_interaccion", 0)
            recordatorio_enviado = datos.get("recordatorio_enviado", False)
            recordatorio_24h_enviado = datos.get("recordatorio_24h_enviado", False)
            if not recordatorio_enviado and (ahora - ultima) >= HORA_SEGUIMIENTO:
                msg = "Hola! Te escribimos desde Game Line Col 🎮\n\nNotamos que estuviste interesado en nuestros servicios.\n\nEn que te podemos ayudar?\n\n1 Game Pass Ultimate\n2 Juegos Xbox\n3 Soporte"
                send_message(phone, msg)
                conversaciones[phone]["recordatorio_enviado"] = True
                registrar_cliente(phone, "Recordatorio", "Seguimiento", "Recordatorio enviado")
            elif recordatorio_enviado and not recordatorio_24h_enviado and (ahora - ultima) >= HORA_SEGUIMIENTO_24H:
                msg = "Hola de nuevo! 🎮 Tu oferta sigue disponible.\n\n🔥 Si confirmas hoy tienes 10% de descuento en cualquiera de nuestros servicios.\n\nEscribenos y te ayudamos enseguida!"
                send_message(phone, msg)
                conversaciones[phone]["recordatorio_24h_enviado"] = True
                registrar_cliente(phone, "Recordatorio 24h", "Seguimiento", "Recordatorio 24h con descuento enviado")


def verificar_inactivos():
    while True:
        time.sleep(300)
        ahora = time.time()
        for phone, datos in list(conversaciones.items()):
            if datos.get("estado") == "esperando_pago_final" and not datos.get("alerta_inactividad_enviada"):
                ultima = datos.get("ultima_interaccion", 0)
                if (ahora - ultima) >= 86400:
                    msg = "⚠️ Cliente +" + phone + " lleva mas de 24h sin enviar el comprobante del pago final. Quizas valga la pena escribirle."
                    send_message(ADMIN_PHONE, msg)
                    conversaciones[phone]["alerta_inactividad_enviada"] = True


def resumen_diario():
    ultimo_envio = None
    while True:
        time.sleep(60)
        ahora = datetime.now()
        hoy = ahora.strftime("%d/%m/%Y")
        if ahora.hour == 21 and ultimo_envio != hoy:
            pendientes = sum(1 for d in conversaciones.values() if d.get("estado") in ESTADOS_PENDIENTES)
            msg = ("📊 Resumen del dia " + hoy + "\n\n"
                   "Nuevos clientes: " + str(estadisticas_diarias.get("nuevos", 0)) + "\n"
                   "Cierres confirmados: " + str(estadisticas_diarias.get("cierres", 0)) + "\n"
                   "Reservas pagadas (MP): " + str(estadisticas_diarias.get("reservas", 0)) + "\n"
                   "Pendientes actuales: " + str(pendientes))
            send_message(ADMIN_PHONE, msg)
            ultimo_envio = hoy


conversaciones = {}
asegurar_hoja_estados()
cargar_estados()
threading.Thread(target=verificar_seguimientos, daemon=True).start()
threading.Thread(target=verificar_inactivos, daemon=True).start()
threading.Thread(target=resumen_diario, daemon=True).start()
threading.Thread(target=guardar_estados_periodico, daemon=True).start()
threading.Thread(target=limpiar_conversaciones_antiguas, daemon=True).start()

BIENVENIDA = "🎮 Bienvenido a Game Line Col! 🎮\n\nSomos tu tienda de confianza para juegos y suscripciones Xbox.\n\nEn que te podemos ayudar? 👇"

GAMEPASS = "🕹️ GAME PASS ULTIMATE\n\nPRECIOS:\n📅 1 mes: $29.900\n📅 2 meses: $55.000\n📅 3 meses: $80.000\n📅 6 meses: $140.000\n📅 12 meses: $190.000\n\nMODALIDADES:\n🏠 Principal: juegas desde tu cuenta sin iniciar sesion en otra\n👤 Secundaria: juegas desde tu cuenta iniciando sesion en la del servicio\n\nAmbas funcionan perfecto, solo cambia la configuracion.\n\nGARANTIA: Primero pruebas y luego pagas."

PREGUNTAR_CUENTA = "Que tipo de cuenta prefieres?\n\n🏠 Principal: Juegas desde tu cuenta sin iniciar sesion en otra\n👤 Secundaria: Juegas desde tu cuenta iniciando sesion en la del servicio\n\nAmbas funcionan perfecto 😊"

PREGUNTAR_CONSOLA = "Tienes tu consola o PC disponible ahora?"

CONFIG_PRINCIPAL = "Una vez habilitemos tu cuenta, sigue estos pasos en tu consola:\n\nCONFIGURACION CUENTA PRINCIPAL\n\nCuando aparezcan las preguntas de asociacion Game Pass Ultimate:\n\n1 SIGUIENTE\n2 NO GRACIAS\n3 SIN BARRERAS\n4 OMITIR\n5 En la pregunta de hacer Xbox principal: HACER XBOX PRINCIPAL ✅\n\nIMPORTANTE:\nSiempre usa el servicio con la sesion de tu cuenta personal. La cuenta que anadimos nunca la inicies.\n\nEl uso es exclusivo para ti. Si compartes, se cancela sin devolucion del dinero."

CONFIG_SECUNDARIA = "Una vez habilitemos tu cuenta, sigue estos pasos en tu consola:\n\nCONFIGURACION CUENTA SECUNDARIA\n\nCuando aparezcan las preguntas de asociacion Game Pass Ultimate:\n\n1 SIGUIENTE\n2 NO GRACIAS\n3 SIN BARRERAS\n4 VINCULAR CONTROL\n5 En la pregunta de hacer Xbox principal: NO CAMBIAR ⛔\n\nMucho cuidado con esa pregunta, debes dar NO CAMBIAR.\n\nSiempre con sesion iniciada de la cuenta que anadimos y juegas con tu cuenta personal.\n\nEl uso es exclusivo para ti. Si compartes, se cancela sin devolucion del dinero."

ACTIVACION = "Sigue estos pasos en tu consola o PC:\n\n1 Ve a Agregar nuevo (como nueva cuenta)\n2 Selecciona Usar otro dispositivo\n3 Copia el codigo que aparece y envialo aqui\n\nNuestro asesor lo activara de inmediato! 🚀"

SOPORTE = "SOPORTE\n\nUn asesor te atendera personalmente.\n\nEscribenos al: +57 322 908 2927 😊"

CIERRE = "🎮 Con mucho gusto! Gracias a ti por confiar en Game Line Col 🙌\n\nCualquier cosa que necesites aqui estamos. Que disfrutes tu juego! 🚀"

JUEGOS = "JUEGOS XBOX\n\n1 CODIGO (Economico)\nJuego desde Microsoft, para tu cuenta de por vida\n\n2 CUENTA PRINCIPAL (+ Economico)\nAcceso de por vida, sin iniciar sesion en otra cuenta\n\n3 CUENTA SECUNDARIA (++ Economico)\nAcceso de por vida, iniciando sesion en la cuenta del juego\n\n4 SECUNDARIA CON METODO (+++ Economico)\nAcceso de por vida con tutorial que compartimos\n\nQue juego buscas? Dinos el nombre 👇"

PROMPT = "Eres GameBot de Game Line Col. Responde en espanol, amable y profesional. Si el cliente pregunta por un juego especifico termina con ALERTA_JUEGO:[nombre]. Si no puedes resolver algo termina con ALERTA_ASESOR. No inventes precios."

ESTADO_CLIENTE_MENSAJE = {
    "menu": "Aun no has iniciado un pedido. Escribe 'hola' para ver el menu 🎮",
    "gamepass": "Estas viendo la info de Game Pass Ultimate. Responde si quieres contratar 😊",
    "seleccion_meses": "Estamos esperando que elijas el plan de meses.",
    "seleccion_cuenta": "Estamos esperando que elijas el tipo de cuenta (Principal o Secundaria).",
    "preguntar_consola": "Estamos esperando que nos digas si tienes tu consola disponible.",
    "activacion": "Estamos esperando el codigo de activacion de tu consola. Envialo aqui cuando lo tengas 🎮",
    "esperando_comprobante": "Estamos esperando el comprobante de pago de tu reserva 📸",
    "esperando_codigo_apartado": "Tu reserva esta pagada ✅. Estamos esperando que nos envies el codigo de activacion cuando tengas tu consola disponible.",
    "esperando_pago_final": "Tu cuenta ya esta activada 🎮. Estamos esperando el comprobante del pago final 📸",
    "pago_final_enviado": "Recibimos tu comprobante de pago final, un asesor lo esta confirmando ⏳",
    "pago_confirmado": "Tu pedido esta cerrado y confirmado. Gracias por tu compra! 🎮🙌",
    "juegos": "Estamos esperando que nos digas el nombre del juego que buscas.",
    "soporte": "Tu solicitud de soporte fue enviada, un asesor te contactara pronto 😊"
}


def enviar_menu_principal(phone):
    enviar_botones(phone, "Elige una opcion:", [
        {"id": "1", "titulo": "Game Pass Ultimate"},
        {"id": "2", "titulo": "Juegos Xbox"},
        {"id": "3", "titulo": "Soporte"}
    ])


def enviar_pregunta_contratar(phone):
    enviar_botones(phone, "Te gustaria contratar?", [
        {"id": "si", "titulo": "Sí, quiero 😊"}
    ])


def enviar_pregunta_meses(phone):
    enviar_lista(phone, "Por cuantos meses deseas contratar?", "Ver planes", [
        {"id": "1", "titulo": "1 mes", "descripcion": "$29.900"},
        {"id": "2", "titulo": "2 meses", "descripcion": "$55.000"},
        {"id": "3", "titulo": "3 meses", "descripcion": "$80.000"},
        {"id": "6", "titulo": "6 meses", "descripcion": "$140.000"},
        {"id": "12", "titulo": "12 meses", "descripcion": "$190.000"}
    ], titulo_seccion="Planes Game Pass")


def enviar_pregunta_cuenta(phone, prefijo=""):
    enviar_botones(phone, prefijo + PREGUNTAR_CUENTA, [
        {"id": "1", "titulo": "Cuenta Principal"},
        {"id": "2", "titulo": "Cuenta Secundaria"}
    ])


def enviar_pregunta_consola(phone, prefijo=""):
    enviar_botones(phone, prefijo + PREGUNTAR_CONSOLA, [
        {"id": "1", "titulo": "Sí, tengo consola"},
        {"id": "2", "titulo": "Quiero apartar"}
    ])


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
        msg_type = message.get("type")
        if msg_type not in ("text", "image", "interactive"):
            return jsonify({"status": "ok"}), 200

        phone = message["from"]
        msg_id = message.get("id", "")

        if msg_type == "text":
            text = message["text"]["body"].strip()
        elif msg_type == "interactive":
            interactive_data = message.get("interactive", {})
            if interactive_data.get("type") == "button_reply":
                text = interactive_data["button_reply"]["id"]
            elif interactive_data.get("type") == "list_reply":
                text = interactive_data["list_reply"]["id"]
            else:
                text = ""
        else:
            text = ""
        text_lower = text.lower()

        if phone == ADMIN_PHONE and text_lower.startswith("activo"):
            ultimos_4 = text_lower.replace("activo", "").strip()
            cliente_encontrado = None
            for ph, datos in conversaciones.items():
                if ph.endswith(ultimos_4) and datos.get("compro"):
                    cliente_encontrado = ph
                    break

            if cliente_encontrado:
                tipo_cuenta_c = conversaciones[cliente_encontrado].get("tipo_cuenta", "Principal")
                meses_c = conversaciones[cliente_encontrado].get("meses", "1 mes")
                config_c = CONFIG_PRINCIPAL if tipo_cuenta_c == "Principal" else CONFIG_SECUNDARIA
                mensaje_activo = "✅ Tu cuenta ha sido activada en la consola! 🎮\n\nYa puedes empezar a jugar. Sigue estas instrucciones:\n\n" + config_c
                send_message(cliente_encontrado, mensaje_activo)

                monto_c = PRECIOS_GAMEPASS.get(meses_c)
                link_c, referencia_c = (None, None)
                if monto_c:
                    link_c, referencia_c = crear_link_pago(cliente_encontrado, "Game Pass Ultimate " + tipo_cuenta_c + " - " + meses_c, monto_c)
                if referencia_c:
                    conversaciones[cliente_encontrado]["referencia_pago"] = referencia_c
                    conversaciones[cliente_encontrado]["tipo_pago_pendiente"] = "final"
                send_message(cliente_encontrado, "Para terminar de confirmar tu activacion:\n\n" + mensaje_opciones_pago(link_c))

                conversaciones[cliente_encontrado]["estado"] = "esperando_pago_final"
                send_message(ADMIN_PHONE, "✅ Configuracion y opciones de pago enviadas al cliente +" + cliente_encontrado)
                registrar_cliente(cliente_encontrado, "Activacion confirmada", "Game Pass " + tipo_cuenta_c, "CUENTA ACTIVADA - Esperando pago")
            else:
                send_message(ADMIN_PHONE, "No encontre un cliente pendiente con esos ultimos 4 digitos: " + ultimos_4)
            return jsonify({"status": "ok"}), 200

        if phone == ADMIN_PHONE and text_lower.startswith("pagook"):
            ultimos_4 = text_lower.replace("pagook", "").strip()
            cliente_encontrado = None
            for ph, datos in conversaciones.items():
                if ph.endswith(ultimos_4) and datos.get("estado") in ("esperando_pago_final", "pago_final_enviado"):
                    cliente_encontrado = ph
                    break

            if cliente_encontrado:
                tipo_cuenta_c = conversaciones[cliente_encontrado].get("tipo_cuenta", "No especificado")
                meses_c = conversaciones[cliente_encontrado].get("meses", "No especificado")
                conversaciones[cliente_encontrado]["estado"] = "pago_confirmado"
                send_message(cliente_encontrado, CIERRE)
                send_message(ADMIN_PHONE, "✅ Pago confirmado, mensaje de cierre enviado al cliente +" + cliente_encontrado)
                registrar_cliente(cliente_encontrado, "Pago confirmado por admin", "Game Pass " + tipo_cuenta_c + " - " + meses_c, "PAGO CONFIRMADO - Cerrado")
                registrar_evento_diario("cierres")
            else:
                send_message(ADMIN_PHONE, "No encontre un cliente esperando confirmacion de pago con esos ultimos 4 digitos: " + ultimos_4)
            return jsonify({"status": "ok"}), 200

        if phone == ADMIN_PHONE and text_lower == "pendientes":
            pendientes = []
            for ph, datos in conversaciones.items():
                if datos.get("estado") in ESTADOS_PENDIENTES:
                    pendientes.append(
                        "+" + ph + " (..." + ph[-4:] + ") - " + str(datos.get("estado")) +
                        " - " + str(datos.get("meses")) + " - " + str(datos.get("tipo_cuenta"))
                    )
            if pendientes:
                msg = "📋 Clientes pendientes (" + str(len(pendientes)) + "):\n\n" + "\n".join(pendientes)
            else:
                msg = "No hay clientes pendientes en este momento 🎉"
            send_message(ADMIN_PHONE, msg)
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
            registrar_evento_diario("nuevos")

        if msg_id and msg_id == conversaciones[phone].get("ultimo_msg_id", ""):
            return jsonify({"status": "ok"}), 200
        conversaciones[phone]["ultimo_msg_id"] = msg_id

        if not conversaciones[phone].get("bienvenida_enviada") or es_saludo:
            conversaciones[phone]["bienvenida_enviada"] = True
            conversaciones[phone]["estado"] = "menu"
            conversaciones[phone]["ultima_interaccion"] = time.time()
            send_message(phone, BIENVENIDA)
            enviar_menu_principal(phone)
            registrar_cliente(phone, text, "Inicio", "Bienvenida enviada")
            return jsonify({"status": "ok"}), 200

        if es_agradecimiento(text) and conversaciones[phone].get("compro"):
            conversaciones[phone]["ultima_interaccion"] = time.time()
            send_message(phone, CIERRE)
            return jsonify({"status": "ok"}), 200

        if text_lower == "estado":
            estado_actual = conversaciones[phone].get("estado", "menu")
            meses_e = conversaciones[phone].get("meses")
            tipo_cuenta_e = conversaciones[phone].get("tipo_cuenta")
            descripcion = ESTADO_CLIENTE_MENSAJE.get(estado_actual, "No tenemos un pedido activo en este momento. Escribe 'hola' para ver el menu 🎮")
            msg = "📦 Estado de tu pedido:\n\n" + descripcion
            detalle = []
            if meses_e:
                detalle.append("Plan: " + meses_e)
            if tipo_cuenta_e:
                detalle.append("Cuenta: " + tipo_cuenta_e)
            if detalle:
                msg += "\n\n" + "\n".join(detalle)
            send_message(phone, msg)
            return jsonify({"status": "ok"}), 200

        conversaciones[phone]["ultima_interaccion"] = time.time()
        conversaciones[phone]["recordatorio_enviado"] = False
        estado = conversaciones[phone].get("estado", "menu")

        historial = conversaciones[phone].get("historial", [])
        meses = conversaciones[phone].get("meses", "No especificado")
        tipo_cuenta = conversaciones[phone].get("tipo_cuenta", "No especificado")

        if msg_type == "image":
            if estado in ("esperando_comprobante", "esperando_pago_final"):
                media_id = message["image"]["id"]
                try:
                    media_bytes, mime_type = descargar_media(media_id)
                    analisis = leer_comprobante(media_bytes, mime_type)
                except Exception as e:
                    print("Error procesando imagen: " + str(e))
                    analisis = "No pude leer el comprobante automaticamente, revisa la imagen manualmente."

                if estado == "esperando_comprobante":
                    conversaciones[phone]["estado"] = "esperando_codigo_apartado"
                    send_message(phone, "Comprobante recibido! Un asesor confirmara tu reserva.\n\nCuando tengas tu consola disponible envianos el codigo de activacion aqui 🎮")
                    etiqueta = "COMPROBANTE DE PAGO (Reserva)"
                else:
                    conversaciones[phone]["estado"] = "pago_final_enviado"
                    send_message(phone, "Comprobante recibido! Un asesor confirmara tu pago en breve. Gracias por tu compra 🎮🙌")
                    etiqueta = "COMPROBANTE DE PAGO (Activacion final)"

                reenviar_imagen(ADMIN_PHONE, media_id)
                alerta = (etiqueta + " Game Line Col\nCliente: +" + phone + "\nMeses: " + meses +
                          "\nCuenta: " + tipo_cuenta + "\n\nLectura automatica:\n" + analisis +
                          "\n\nConfirma el pago manualmente.")
                send_message(ADMIN_PHONE, alerta)
                registrar_cliente(phone, "Comprobante imagen", "Game Pass " + tipo_cuenta + " - " + meses, etiqueta)
            else:
                send_message(phone, "Recibimos tu imagen, pero en este momento no la necesitamos. Si tienes alguna duda escribenos 😊")
            return jsonify({"status": "ok"}), 200

        if estado == "seleccion_meses":
            m = extraer_meses(text)
            if m:
                conversaciones[phone]["meses"] = m
                conversaciones[phone]["estado"] = "seleccion_cuenta"
                send_message(phone, "Seleccionaste " + m + ".")
                enviar_pregunta_cuenta(phone)
            else:
                send_message(phone, "No entendi cual plan elegiste 🙏")
                enviar_pregunta_meses(phone)
            return jsonify({"status": "ok"}), 200

        if estado == "seleccion_cuenta":
            if "1" in text or "principal" in text_lower:
                conversaciones[phone]["tipo_cuenta"] = "Principal"
                conversaciones[phone]["estado"] = "preguntar_consola"
                send_message(phone, "Elegiste Cuenta Principal! 🏠")
                enviar_pregunta_consola(phone)
            elif "2" in text or "secundaria" in text_lower:
                conversaciones[phone]["tipo_cuenta"] = "Secundaria"
                conversaciones[phone]["estado"] = "preguntar_consola"
                send_message(phone, "Elegiste Cuenta Secundaria! 👤")
                enviar_pregunta_consola(phone)
            else:
                enviar_pregunta_cuenta(phone)
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
                monto_r = PRECIOS_GAMEPASS.get(meses)
                link_r, referencia_r = (None, None)
                if monto_r:
                    link_r, referencia_r = crear_link_pago(phone, "Reserva Game Pass " + tipo_cuenta + " - " + meses, monto_r)
                if referencia_r:
                    conversaciones[phone]["referencia_pago"] = referencia_r
                    conversaciones[phone]["tipo_pago_pendiente"] = "reserva"
                mensaje_apartar = "Sin problema! Aparta tu servicio pagando ahora.\n\n" + mensaje_opciones_pago(link_r) + "\n\nCuando tengas tu consola lista te indicamos como activarlo 🎮"
                send_message(phone, mensaje_apartar)
                alerta = "CLIENTE QUIERE APARTAR Game Line Col\nCliente: +" + phone + "\nMeses: " + meses + "\nCuenta: " + tipo_cuenta + "\nEspera comprobante!"
                send_message(ADMIN_PHONE, alerta)
                registrar_cliente(phone, text, "Game Pass " + tipo_cuenta + " - " + meses, "Quiere apartar")
            else:
                enviar_pregunta_consola(phone)
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
            send_message(phone, "Para confirmar tu reserva necesitamos la foto de tu comprobante de pago 📸")
            return jsonify({"status": "ok"}), 200

        if estado == "esperando_pago_final":
            send_message(phone, "Para confirmar tu activacion necesitamos la foto de tu comprobante de pago 📸")
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
            enviar_pregunta_contratar(phone)
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
            enviar_pregunta_meses(phone)
            return jsonify({"status": "ok"}), 200

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
        send_message(phone, reply)

    except Exception as e:
        print("Error: " + str(e))
    return jsonify({"status": "ok"}), 200


@app.route("/mercadopago-webhook", methods=["POST", "GET"])
def mercadopago_webhook():
    try:
        payment_id = request.args.get("id") or request.args.get("data.id")
        topic = request.args.get("topic") or request.args.get("type")

        if not payment_id:
            body = request.get_json(silent=True) or {}
            if body.get("type") == "payment":
                payment_id = body.get("data", {}).get("id")
                topic = "payment"

        if topic == "payment" and payment_id:
            headers = {"Authorization": "Bearer " + MP_ACCESS_TOKEN}
            r = requests.get("https://api.mercadopago.com/v1/payments/" + str(payment_id), headers=headers, timeout=10)
            pago = r.json()
            estado_pago = pago.get("status")
            referencia = pago.get("external_reference", "")
            monto_pagado = pago.get("transaction_amount")

            if estado_pago == "approved" and referencia:
                phone_pagador = referencia.split("-")[0]
                datos_cliente = conversaciones.get(phone_pagador)
                if datos_cliente and datos_cliente.get("referencia_pago") == referencia and not datos_cliente.get("pago_mp_confirmado"):
                    conversaciones[phone_pagador]["pago_mp_confirmado"] = True
                    tipo_pago = datos_cliente.get("tipo_pago_pendiente")

                    if tipo_pago == "reserva":
                        conversaciones[phone_pagador]["estado"] = "esperando_codigo_apartado"
                        send_message(phone_pagador, "✅ Pago de tu reserva confirmado automaticamente!\n\nCuando tengas tu consola disponible envianos el codigo de activacion aqui 🎮")
                        etiqueta_mp = "RESERVA"
                        registrar_evento_diario("reservas")
                    else:
                        conversaciones[phone_pagador]["compro"] = True
                        conversaciones[phone_pagador]["estado"] = "pago_confirmado"
                        send_message(phone_pagador, CIERRE)
                        etiqueta_mp = "ACTIVACION FINAL"
                        registrar_evento_diario("cierres")

                    send_message(ADMIN_PHONE, "✅ Pago confirmado automaticamente por Mercado Pago (" + etiqueta_mp + ")\nCliente: +" + phone_pagador + "\nMonto: $" + str(monto_pagado))
                    registrar_cliente(phone_pagador, "Pago Mercado Pago aprobado", "Pago automatico - " + etiqueta_mp, "PAGO CONFIRMADO MP")
    except Exception as e:
        print("Error webhook Mercado Pago: " + str(e))
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
