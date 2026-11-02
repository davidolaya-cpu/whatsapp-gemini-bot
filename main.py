import os
import re
import json
import hashlib
import requests
import smtplib
import threading
import time
from email.mime.text import MIMEText
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Flask, request, jsonify
from google import genai
from google.genai import types
from google.oauth2 import service_account
from googleapiclient.discovery import build

app = Flask(__name__)

# Zona horaria de Colombia para registros en Sheets y resumen de las 9 PM
TZ_COL = ZoneInfo("America/Bogota")


def ahora_col():
    return datetime.now(TZ_COL)


# ── Telegram ────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_API = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN
ADMIN_PHONE = str(os.environ.get("ADMIN_CHAT_ID", "")).strip()
ADMIN_PHONE_DISPLAY = os.environ.get("ADMIN_CONTACTO", "+57 322 908 2927")
PUBLIC_DOMAIN = os.environ.get("PUBLIC_DOMAIN") or os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
TELEGRAM_SECRET = os.environ.get("TELEGRAM_SECRET") or hashlib.sha256(
    ("gamebot-" + TELEGRAM_BOT_TOKEN).encode("utf-8")).hexdigest()[:40]

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS")
MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN")
MP_NOTIFICATION_URL = os.environ.get("MP_NOTIFICATION_URL") or (
    "https://" + PUBLIC_DOMAIN + "/mercadopago-webhook" if PUBLIC_DOMAIN else None)
SHEET_ID = "1lvIlK1LYbT68HsuDTbMRzWSYh_RGUPHAZeV31_sAmdU"
HORA_SEGUIMIENTO = 3600
HORA_RECORDATORIO_CONSOLA = 86400

EMAIL_ADDRESS = os.environ.get("EMAIL_ADDRESS")
EMAIL_APP_PASSWORD = os.environ.get("EMAIL_APP_PASSWORD")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", EMAIL_ADDRESS)
ALERTA_TOKEN_COOLDOWN = 1800
_ultima_alerta_token = {"ts": 0}

_flood_control = {}
_sheets_errores_consecutivos = 0
bloqueados = {}


def enviar_alerta_email(asunto, cuerpo):
    if not EMAIL_ADDRESS or not EMAIL_APP_PASSWORD or not ADMIN_EMAIL:
        print("⚠️ No se pudo enviar alerta por correo: faltan variables de correo en Railway.")
        return
    try:
        msg = MIMEText(cuerpo)
        msg["Subject"] = asunto
        msg["From"] = EMAIL_ADDRESS
        msg["To"] = ADMIN_EMAIL
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as servidor:
            servidor.login(EMAIL_ADDRESS, EMAIL_APP_PASSWORD)
            servidor.send_message(msg)
        print("📧 Alerta por correo enviada a " + ADMIN_EMAIL)
    except Exception as e:
        print("Error enviando alerta por correo: " + str(e))


def alertar_token_vencido(detalle):
    ahora = time.time()
    if ahora - _ultima_alerta_token["ts"] < ALERTA_TOKEN_COOLDOWN:
        return
    _ultima_alerta_token["ts"] = ahora
    print("🚨 TOKEN DE TELEGRAM INVALIDO: " + str(detalle))
    enviar_alerta_email(
        "🚨 Game Line Col - El token de Telegram dejo de funcionar",
        "El bot detecto que el token de Telegram ya no es valido.\n\n"
        "El bot NO puede enviar ni recibir mensajes hasta que pidas un token nuevo a @BotFather "
        "(/mybots > tu bot > API Token) y lo actualices en la variable TELEGRAM_BOT_TOKEN en Railway.\n\n"
        "Detalle tecnico:\n" + str(detalle)
    )


client = genai.Client(api_key=GEMINI_API_KEY)

catalogo_media_id = None
renovaciones = {}
cuentas = []

MESES_A_DIAS = {
    "1 mes": 30, "2 meses": 60, "3 meses": 90,
    "6 meses": 180, "12 meses": 365
}


def parsear_tiempo_minutos(texto):
    texto = texto.lower().strip()
    match = re.search(r'(\d+)\s*(min|hora|h\b)', texto)
    if match:
        cantidad = int(match.group(1))
        unidad = match.group(2)
        return cantidad * 60 if ("hora" in unidad or unidad == "h") else cantidad
    if "media hora" in texto:
        return 30
    if "una hora" in texto:
        return 60
    return 30


def cargar_cuentas():
    global cuentas
    try:
        service = get_sheets_service()
        result = service.spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range="Cuentas!A:F"
        ).execute()
        filas = result.get("values", [])
        cuentas = []
        for i, fila in enumerate(filas[1:], start=2):
            while len(fila) < 6:
                fila.append("")
            cuentas.append({
                "fila": i,
                "email": fila[0],
                "password": fila[1],
                "principal_ocupado": fila[2].upper() == "SI",
                "cliente_principal": fila[3],
                "secundaria_ocupada": fila[4].upper() == "SI",
                "cliente_secundaria": fila[5]
            })
        print("Cuentas cargadas: " + str(len(cuentas)))
    except Exception as e:
        print("Error cargando cuentas: " + str(e))


def actualizar_fila_cuenta(fila_num, cuenta):
    try:
        service = get_sheets_service()
        valores = [[
            cuenta["email"],
            cuenta["password"],
            "SI" if cuenta["principal_ocupado"] else "NO",
            cuenta["cliente_principal"],
            "SI" if cuenta["secundaria_ocupada"] else "NO",
            cuenta["cliente_secundaria"]
        ]]
        service.spreadsheets().values().update(
            spreadsheetId=SHEET_ID,
            range="Cuentas!A" + str(fila_num) + ":F" + str(fila_num),
            valueInputOption="RAW",
            body={"values": valores}
        ).execute()
    except Exception as e:
        print("Error actualizando cuenta en Sheet: " + str(e))


def asignar_cuenta(phone):
    try:
        service = get_sheets_service()
        result = service.spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range="Cuentas!A:F"
        ).execute()
        filas = result.get("values", [])
        if not filas or len(filas) < 2:
            print("Sheet Cuentas vacio o sin datos")
            return None

        for i, fila in enumerate(filas[1:], start=2):
            while len(fila) < 6:
                fila.append("")
            email = fila[0].strip()
            password = fila[1].strip()
            if not email or not password:
                continue
            principal_ocupado = fila[2].strip().upper() == "SI"
            secundaria_ocupada = fila[4].strip().upper() == "SI"

            if not principal_ocupado:
                service.spreadsheets().values().update(
                    spreadsheetId=SHEET_ID,
                    range="Cuentas!C" + str(i) + ":D" + str(i),
                    valueInputOption="RAW",
                    body={"values": [["SI", phone]]}
                ).execute()
                for c in cuentas:
                    if c["fila"] == i:
                        c["principal_ocupado"] = True
                        c["cliente_principal"] = phone
                verificar_stock_bajo()
                return email, password, "Principal"

            if not secundaria_ocupada:
                service.spreadsheets().values().update(
                    spreadsheetId=SHEET_ID,
                    range="Cuentas!E" + str(i) + ":F" + str(i),
                    valueInputOption="RAW",
                    body={"values": [["SI", phone]]}
                ).execute()
                for c in cuentas:
                    if c["fila"] == i:
                        c["secundaria_ocupada"] = True
                        c["cliente_secundaria"] = phone
                verificar_stock_bajo()
                return email, password, "Secundaria"

        return None
    except Exception as e:
        print("Error asignando cuenta: " + str(e))
        return None


def liberar_cuenta(phone):
    liberada = False
    for cuenta in cuentas:
        cambio = False
        if cuenta["cliente_principal"] == phone:
            cuenta["principal_ocupado"] = False
            cuenta["cliente_principal"] = ""
            cambio = True
        if cuenta["cliente_secundaria"] == phone:
            cuenta["secundaria_ocupada"] = False
            cuenta["cliente_secundaria"] = ""
            cambio = True
        if cambio:
            actualizar_fila_cuenta(cuenta["fila"], cuenta)
            liberada = True
    return liberada


def verificar_stock_bajo():
    try:
        service = get_sheets_service()
        result = service.spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range="Cuentas!A:F"
        ).execute()
        filas = result.get("values", [])
        libres_principal = 0
        libres_secundaria = 0
        for fila in filas[1:]:
            while len(fila) < 6:
                fila.append("")
            if fila[0].strip():
                if fila[2].strip().upper() != "SI":
                    libres_principal += 1
                if fila[4].strip().upper() != "SI":
                    libres_secundaria += 1
        if libres_principal == 1:
            send_message(ADMIN_PHONE, "⚠️ Stock bajo: solo queda *1 cupo Principal* disponible. Considera agregar mas cuentas.")
        if libres_secundaria == 1:
            send_message(ADMIN_PHONE, "⚠️ Stock bajo: solo queda *1 cupo Secundaria* disponible. Considera agregar mas cuentas.")
        if libres_principal == 0 and libres_secundaria == 0:
            send_message(ADMIN_PHONE, "🚨 Sin stock: no hay cupos disponibles. Agrega cuentas urgente!")
    except Exception as e:
        print("Error verificando stock: " + str(e))


_sheets_creds = None


def get_sheets_service():
    global _sheets_creds
    if _sheets_creds is None:
        creds_dict = json.loads(GOOGLE_CREDENTIALS)
        _sheets_creds = service_account.Credentials.from_service_account_info(
            creds_dict,
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
    return build("sheets", "v4", credentials=_sheets_creds, cache_discovery=False)


def asegurar_hoja(titulo):
    try:
        service = get_sheets_service()
        body = {"requests": [{"addSheet": {"properties": {"title": titulo}}}]}
        service.spreadsheets().batchUpdate(spreadsheetId=SHEET_ID, body=body).execute()
        print("Hoja '" + titulo + "' creada")
    except Exception:
        pass


def guardar_config(clave, valor):
    try:
        service = get_sheets_service()
        asegurar_hoja("Config")
        result = service.spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range="Config!A:B"
        ).execute()
        filas = result.get("values", [])
        actualizado = False
        for i, fila in enumerate(filas):
            if len(fila) >= 1 and fila[0] == clave:
                filas[i] = [clave, valor]
                actualizado = True
                break
        if not actualizado:
            filas.append([clave, valor])
        service.spreadsheets().values().clear(
            spreadsheetId=SHEET_ID, range="Config!A:B"
        ).execute()
        service.spreadsheets().values().update(
            spreadsheetId=SHEET_ID,
            range="Config!A1",
            valueInputOption="RAW",
            body={"values": filas}
        ).execute()
    except Exception as e:
        print("Error guardando config: " + str(e))


def cargar_config():
    global catalogo_media_id
    try:
        service = get_sheets_service()
        result = service.spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range="Config!A:B"
        ).execute()
        filas = result.get("values", [])
        for fila in filas:
            if len(fila) >= 2 and fila[0] == "catalogo_media_id":
                catalogo_media_id = fila[1]
                print("Catalogo cargado, media_id: " + catalogo_media_id)
    except Exception as e:
        print("Error cargando config: " + str(e))


def normalizar_id(texto):
    return re.sub(r"\D", "", str(texto or ""))


def normalizar_numero(texto):
    digitos = re.sub(r"\D", "", str(texto or ""))
    if not digitos:
        return ""
    if len(digitos) == 10 and digitos.startswith("3"):
        digitos = "57" + digitos
    return digitos


def cli(ph):
    datos = conversaciones.get(str(ph), {})
    partes = []
    if datos.get("nombre"):
        partes.append(datos["nombre"])
    if datos.get("usuario"):
        partes.append("@" + datos["usuario"])
    if datos.get("telefono"):
        partes.append("tel +" + datos["telefono"])
    base = " ".join(partes) if partes else "Cliente"
    return base + " (ID " + str(ph) + ")"


def primer_nombre(ph):
    datos = conversaciones.get(str(ph), {})
    nombre = str(datos.get("nombre", "")).strip()
    if nombre:
        return nombre.split()[0]
    return ""


def cargar_bloqueados():
    global bloqueados
    try:
        service = get_sheets_service()
        result = service.spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range="Bloqueados!A:C"
        ).execute()
        filas = result.get("values", [])
        nuevos = {}
        for fila in filas[1:]:
            if fila and fila[0].strip():
                telefono = normalizar_id(fila[0])
                if not telefono:
                    continue
                nuevos[telefono] = {
                    "motivo": fila[1] if len(fila) > 1 else "",
                    "fecha": fila[2] if len(fila) > 2 else ""
                }
        bloqueados = nuevos
        print("Numeros bloqueados cargados: " + str(len(bloqueados)))
    except Exception as e:
        print("Error cargando bloqueados: " + str(e))


def guardar_bloqueados():
    try:
        service = get_sheets_service()
        asegurar_hoja("Bloqueados")
        filas = [["Chat ID", "Motivo", "Fecha de bloqueo"]]
        for telefono, datos in bloqueados.items():
            filas.append([telefono, datos.get("motivo", ""), datos.get("fecha", "")])
        service.spreadsheets().values().clear(
            spreadsheetId=SHEET_ID, range="Bloqueados!A:C"
        ).execute()
        service.spreadsheets().values().update(
            spreadsheetId=SHEET_ID,
            range="Bloqueados!A1",
            valueInputOption="RAW",
            body={"values": filas}
        ).execute()
    except Exception as e:
        print("Error guardando bloqueados: " + str(e))


def esta_bloqueado(phone):
    if phone == ADMIN_PHONE:
        return False
    return normalizar_id(phone) in bloqueados


def bloquear_numero(phone, motivo=""):
    telefono = normalizar_id(phone)
    if not telefono:
        return False, "Chat ID invalido."
    if telefono == normalizar_id(ADMIN_PHONE):
        return False, "No puedes bloquear el numero del administrador."
    if telefono in bloqueados:
        return False, "El cliente " + telefono + " ya estaba bloqueado."

    bloqueados[telefono] = {
        "motivo": motivo or "Sin motivo especificado",
        "fecha": ahora_col().strftime("%d/%m/%Y %H:%M")
    }
    guardar_bloqueados()

    detalles = []
    try:
        if liberar_cuenta(telefono):
            detalles.append("cuenta del inventario liberada")
    except Exception as e:
        print("Error liberando cuenta al bloquear: " + str(e))
    if telefono in conversaciones:
        del conversaciones[telefono]
        detalles.append("conversacion eliminada")
    if telefono in renovaciones:
        del renovaciones[telefono]
        guardar_renovaciones()
        detalles.append("renovacion cancelada")

    extra = (" (" + ", ".join(detalles) + ")") if detalles else ""
    return True, "🚫 Cliente " + telefono + " bloqueado" + extra + "."


def desbloquear_numero(phone):
    telefono = normalizar_id(phone)
    if telefono not in bloqueados:
        return False, "El cliente " + str(telefono) + " no estaba bloqueado."
    del bloqueados[telefono]
    guardar_bloqueados()
    return True, "✅ Cliente " + telefono + " desbloqueado. Ya puede escribirle al bot."


def guardar_renovaciones():
    try:
        service = get_sheets_service()
        filas = [["cliente", "vencimiento", "tipo_cuenta", "meses", "notificado", "canal"]]
        for ph, datos in renovaciones.items():
            filas.append([
                ph,
                str(datos.get("vencimiento", 0)),
                datos.get("tipo_cuenta", ""),
                datos.get("meses", ""),
                str(datos.get("notificado", False)),
                datos.get("canal", "whatsapp")
            ])
        service.spreadsheets().values().clear(
            spreadsheetId=SHEET_ID, range="Renovaciones!A:F"
        ).execute()
        service.spreadsheets().values().update(
            spreadsheetId=SHEET_ID,
            range="Renovaciones!A1",
            valueInputOption="RAW",
            body={"values": filas}
        ).execute()
    except Exception as e:
        print("Error guardando renovaciones: " + str(e))


def cargar_renovaciones():
    try:
        service = get_sheets_service()
        result = service.spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range="Renovaciones!A:F"
        ).execute()
        filas = result.get("values", [])
        cargadas = 0
        for fila in filas[1:]:
            if len(fila) >= 5:
                renovaciones[fila[0]] = {
                    "vencimiento": float(fila[1]),
                    "tipo_cuenta": fila[2],
                    "meses": fila[3],
                    "notificado": fila[4] == "True",
                    "canal": fila[5] if len(fila) > 5 and fila[5] else "whatsapp"
                }
                cargadas += 1
        print("Renovaciones cargadas: " + str(cargadas))
    except Exception as e:
        print("Error cargando renovaciones: " + str(e))


def registrar_compra(phone, tipo_cuenta, meses, email_cuenta=""):
    global _sheets_errores_consecutivos
    try:
        service = get_sheets_service()
        fecha = ahora_col().strftime("%d/%m/%Y %H:%M")
        valores = [[str(phone), fecha, tipo_cuenta, meses, email_cuenta, cli(phone)]]
        service.spreadsheets().values().append(
            spreadsheetId=SHEET_ID,
            range="Compras!A:F",
            valueInputOption="RAW",
            body={"values": valores}
        ).execute()
        _sheets_errores_consecutivos = 0
        dias = MESES_A_DIAS.get(meses, 30)
        renovaciones[phone] = {
            "vencimiento": time.time() + (dias * 86400),
            "tipo_cuenta": tipo_cuenta,
            "meses": meses,
            "email_cuenta": email_cuenta,
            "notificado": False,
            "canal": "telegram"
        }
        guardar_renovaciones()
    except Exception as e:
        _sheets_errores_consecutivos += 1
        print("Error Sheets: " + str(e))
        if _sheets_errores_consecutivos == 3:
            try:
                send_message(ADMIN_PHONE,
                    "⚠️ Alerta Game Line Col: el registro en Google Sheets ha fallado "
                    "3 veces seguidas. Revisa las credenciales en Railway.")
            except Exception:
                pass


def _tg(metodo, payload, intentos=3):
    ultimo = None
    payload = dict(payload)
    for intento in range(intentos):
        try:
            r = requests.post(TG_API + "/" + metodo, json=payload, timeout=15)
            ultimo = r.json()
            if ultimo.get("ok"):
                return ultimo
            codigo = ultimo.get("error_code")
            descripcion = str(ultimo.get("description", ""))
            if codigo == 400 and "parse" in descripcion.lower() and payload.get("parse_mode"):
                payload.pop("parse_mode", None)
                continue
            print("Telegram error en " + metodo + " (intento " + str(intento + 1) + "): " + descripcion)
            if codigo == 401:
                alertar_token_vencido(ultimo)
                break
            if codigo == 429:
                time.sleep(int(ultimo.get("parameters", {}).get("retry_after", 2)))
                continue
            if codigo in (400, 403):
                break
        except Exception as e:
            print("Error llamando a Telegram " + metodo + " (intento " + str(intento + 1) + "): " + str(e))
        time.sleep(2)
    return None


def _destino_valido(phone):
    if not phone:
        print("⚠️ Mensaje sin destino (¿falta ADMIN_CHAT_ID en Railway?)")
        return False
    return not esta_bloqueado(phone)


def mostrar_escribiendo(phone):
    """Muestra 'escribiendo...' arriba en el chat de Telegram para dar sensacion humana."""
    if _destino_valido(phone):
        try:
            requests.post(TG_API + "/sendChatAction", json={"chat_id": phone, "action": "typing"}, timeout=3)
        except Exception:
            pass


def send_message(phone, message, intentos=3, formato=True, reply_markup=None):
    if not _destino_valido(phone):
        return False
    payload = {"chat_id": phone, "text": message, "disable_web_page_preview": True}
    if formato:
        payload["parse_mode"] = "Markdown"
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return _tg("sendMessage", payload, intentos)


def reenviar_imagen(phone, media_id, caption="", intentos=2):
    if not _destino_valido(phone):
        return False
    r = _tg("sendPhoto", {"chat_id": phone, "photo": media_id, "caption": caption}, intentos)
    if not r:
        send_message(ADMIN_PHONE, "⚠️ No pude reenviarte una foto de comprobante. Pidele al cliente que la reenvie.")
    return r


def enviar_botones(phone, cuerpo, botones, intentos=3):
    if not _destino_valido(phone):
        return False
    teclado = {"inline_keyboard": [[{"text": b["titulo"], "callback_data": b["id"]}] for b in botones]}
    r = send_message(phone, cuerpo, intentos, reply_markup=teclado)
    return r or send_message(phone, cuerpo)


def enviar_lista(phone, cuerpo, texto_boton, filas, titulo_seccion="Opciones", intentos=3):
    botones = []
    for f in filas:
        titulo = f["titulo"] + ((" · " + f["descripcion"]) if f.get("descripcion") else "")
        botones.append({"id": f["id"], "titulo": titulo})
    return enviar_botones(phone, cuerpo, botones, intentos)


def enviar_documento(phone, media_id, caption="", nombre_archivo="Catalogo_Game_Line_Col.pdf", intentos=3):
    if not _destino_valido(phone):
        return False
    r = _tg("sendDocument", {"chat_id": phone, "document": media_id, "caption": caption}, intentos)
    if not r:
        send_message(phone, caption)
    return r


def pedir_telefono(phone):
    send_message(phone,
        "📱 Por cierto, si ya venías siendo cliente nuestro por WhatsApp, puedes tocar el botón de abajo "
        "para compartir tu número y vincular tu plan automáticamente (es opcional).",
        reply_markup={
            "keyboard": [[{"text": "📱 Compartir mi numero", "request_contact": True}]],
            "resize_keyboard": True,
            "one_time_keyboard": True
        }
    )


def responder_callback(callback_id):
    if callback_id:
        _tg("answerCallbackQuery", {"callback_query_id": callback_id}, intentos=1)


PRECIOS_GAMEPASS = {
    "1 mes": 29900,
    "2 meses": 55000,
    "3 meses": 80000,
    "6 meses": 140000,
    "12 meses": 190000
}


def crear_link_pago(phone, concepto, monto):
    try:
        referencia = str(phone) + "-" + str(int(time.time()))
        if phone in conversaciones:
            conversaciones[phone]["pago_mp_confirmado"] = False
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
    texto = "Puedes realizar el pago por cualquiera de estos medios:\n\n"
    if link:
        texto += "💳 *Tarjeta, PSE o Nequi por Mercado Pago* (se confirma al instante):\n" + link + "\n\n"
    texto += ("📲 *Nequi:* 3057059517\n📲 *Daviplata:* 3057059517\n🏦 *Llave:* 3057059517 (David Olaya)\n\n"
              "📸 *Nota:* Si transfieres por Nequi, Daviplata o Llave, envíame por aquí mismo "
              "la foto o captura del comprobante apenas lo hagas 👍\n\n"
              "Si pagas desde el link de Mercado Pago, el sistema me avisa automáticamente.")
    return texto


RECORDAR_COMPROBANTE_ADMIN = (
    "📸 Quedo súper atento: apenas realices el pago, envíame *por aquí mismo la foto del comprobante* para continuar 👍\n\n"
    "Si tienes alguna duda dime con confianza, o escribe *menu* si prefieres volver al inicio."
)

CONFIRMACIONES_PAGO = (
    "listo", "listo!", "ya", "ya envie", "ya envié", "ya lo envie", "ya lo envié",
    "ya te envie", "ya te envié", "ya lo mande", "ya lo mandé", "ya mande", "ya mandé",
    "enviado", "ya pague", "ya pagué", "ya lo pague", "ya lo pagué", "pagado",
    "ya esta", "ya está", "hecho", "ok listo"
)


def avanzar_tras_pago(phone, estado):
    if estado in ("esperando_comprobante", "comprobante_reserva_enviado"):
        conversaciones[phone]["estado"] = "esperando_consola"
        conversaciones[phone]["reserva_pagada"] = True
        conversaciones[phone]["compro"] = True
        conversaciones[phone]["recordatorio_consola_at"] = time.time() + HORA_RECORDATORIO_CONSOLA
        registrar_evento_diario("reservas")
        enviar_boton_consola_lista(phone,
            "✅ ¡Súper, muchas gracias! Ya recibimos tu comprobante y tu cupo quedó apartado.\n\n"
            "Apenas estés frente a tu consola o PC, toca el botón de abajo o avísame por aquí para entregarte tu cuenta al instante 🎮"
        )
        send_message(ADMIN_PHONE,
            "💰 RESERVA - El cliente " + cli(phone) + " envio foto del comprobante.\n\n"
            "Plan: " + str(conversaciones[phone].get("meses")) + "\n\n"
            "⚠️ Verifica la imagen que te acabo de reenviar. El bot ya lo dejo esperando consola.\n"
            "Si el pago NO llego: *anular " + phone[-4:] + "*"
        )
        return True

    if estado in ("esperando_pago_final", "pago_final_enviado",
                  "renovacion_espera_pago", "renovacion_comprobante_enviado"):
        tipo_cuenta_c = conversaciones[phone].get("tipo_cuenta", "No especificado")
        meses_c = conversaciones[phone].get("meses", "No especificado")
        email_c = conversaciones[phone].get("email_cuenta", "")
        es_renovacion = conversaciones[phone].get("es_renovacion", False)

        conversaciones[phone]["estado"] = "pago_confirmado"
        conversaciones[phone]["compro"] = True
        send_message(phone, CIERRE)
        registrar_compra(phone, tipo_cuenta_c, meses_c, email_c)
        registrar_evento_diario("cierres")
        if es_renovacion and phone in renovaciones:
            renovaciones[phone]["notificado"] = False

        etiqueta = "RENOVACION" if es_renovacion else "PAGO FINAL"
        send_message(ADMIN_PHONE,
            "💰 " + etiqueta + " - El cliente " + cli(phone) + " envio foto del comprobante.\n\n"
            "Plan: " + str(meses_c) + " - " + str(tipo_cuenta_c) + "\n\n"
            "⚠️ Verifica la imagen que te acabo de reenviar. El bot ya registro la compra en la hoja Compras y cerro la venta.\n"
            "Si el pago NO llego: *anular " + phone[-4:] + "*"
        )
        return True

    return False


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
    if texto in meses_map:
        return meses_map[texto]
    for key, value in meses_map.items():
        if re.search(r"\b" + re.escape(key) + r"\b", texto):
            return value
    return None


def es_agradecimiento(texto):
    texto = texto.lower().strip()
    palabras = ["gracias", "thanks", "thank you", "muchas gracias", "mil gracias", "graciaz"]
    return any(p in texto for p in palabras)


ESTADOS_RANGE = "Estados_TG!A:B"


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
                        continue
                    conversaciones[fila[0]] = datos
                    cargados += 1
                except Exception:
                    continue
        print("Estados cargados desde Sheets: " + str(cargados))
    except Exception as e:
        print("Error cargando estados: " + str(e))


ESTADOS_PENDIENTES = ["activacion", "esperando_comprobante", "comprobante_reserva_enviado",
                      "esperando_consola", "esperando_pago_final", "pago_final_enviado"]

ESTADOS_SIN_PROCESO_ACTIVO = ["menu", "pago_confirmado"]

estadisticas_diarias = {"fecha": None, "nuevos": 0, "cierres": 0, "reservas": 0}


def registrar_evento_diario(tipo):
    hoy = ahora_col().strftime("%d/%m/%Y")
    if estadisticas_diarias["fecha"] != hoy:
        estadisticas_diarias["fecha"] = hoy
        estadisticas_diarias["nuevos"] = 0
        estadisticas_diarias["cierres"] = 0
        estadisticas_diarias["reservas"] = 0
    estadisticas_diarias[tipo] = estadisticas_diarias.get(tipo, 0) + 1


def scheduler():
    ultimo_resumen = None
    ultimo_guardado = 0
    ultima_limpieza = 0

    while True:
        time.sleep(60)
        ahora = time.time()
        hora_actual = ahora_col()

        for phone, datos in list(conversaciones.items()):
            estado = datos.get("estado")
            ultima = datos.get("ultima_interaccion", 0)

            # 1. Recordatorio de seguimiento (1h sin respuesta, no ha comprado)
            if not datos.get("compro") and not datos.get("recordatorio_enviado"):
                if (ahora - ultima) >= HORA_SEGUIMIENTO:
                    nom = primer_nombre(phone)
                    saludo_seg = ("¡Hola " + nom + "! 👋") if nom else "¡Hola! 👋"
                    msg = (saludo_seg + " Te escribimos de *Game Line Col* 🎮\n\n"
                           "Vimos que ahorita estabas mirando nuestros servicios y quedamos atentos por si te quedó alguna duda.\n\n"
                           "Recuerda que en Game Pass Ultimate *primero pruebas tu cuenta en la consola y luego pagas*. ¿Te gustaría que te ayude a activarlo o buscabas algún juego en especial?")
                    send_message(phone, msg)
                    enviar_menu_principal(phone)
                    conversaciones[phone]["recordatorio_enviado"] = True

            # 2. Alerta pago final inactivo (24h)
            if estado == "esperando_pago_final" and not datos.get("alerta_inactividad_enviada"):
                if (ahora - ultima) >= 86400:
                    send_message(ADMIN_PHONE,
                        "⚠️ Cliente " + cli(phone) + " lleva mas de 24h sin enviar "
                        "el comprobante del pago final. Quizas valga la pena escribirle."
                    )
                    conversaciones[phone]["alerta_inactividad_enviada"] = True

        # 3. Resumen diario a las 9pm (Hora de Colombia)
        hoy = hora_actual.strftime("%d/%m/%Y")
        if hora_actual.hour == 21 and ultimo_resumen != hoy:
            pendientes = sum(1 for d in conversaciones.values() if d.get("estado") in ESTADOS_PENDIENTES)
            send_message(ADMIN_PHONE,
                "📊 Resumen del dia " + hoy + "\n\n"
                "Nuevos clientes: " + str(estadisticas_diarias.get("nuevos", 0)) + "\n"
                "Cierres confirmados: " + str(estadisticas_diarias.get("cierres", 0)) + "\n"
                "Reservas pagadas: " + str(estadisticas_diarias.get("reservas", 0)) + "\n"
                "Pendientes actuales: " + str(pendientes)
            )
            ultimo_resumen = hoy

        # 4. Avisos de vencimiento de servicio
        for phone_rv, datos_rv in list(renovaciones.items()):
            if datos_rv.get("notificado"):
                continue
            if datos_rv.get("canal") != "telegram":
                continue
            if ahora >= datos_rv.get("vencimiento", 0):
                tipo_rv = datos_rv.get("tipo_cuenta", "")
                meses_rv = datos_rv.get("meses", "")
                send_message(phone_rv,
                    "¡Hola! 🎮 Te escribimos de *Game Line Col* para avisarte que hoy vence tu plan de *Game Pass Ultimate* "
                    "(" + tipo_rv + " - " + meses_rv + ").\n\n"
                    "¿Te gustaría renovarlo para seguir jugando sin interrupciones?"
                )
                enviar_botones(phone_rv, "¿Deseas renovar tu Game Pass?", [
                    {"id": "renovar_si", "titulo": "✅ Sí, quiero renovar"},
                    {"id": "renovar_no", "titulo": "No por ahora"}
                ])
                if phone_rv not in conversaciones:
                    conversaciones[phone_rv] = {"ultima_interaccion": ahora}
                conversaciones[phone_rv]["estado"] = "renovacion_pendiente"
                conversaciones[phone_rv]["ultima_interaccion"] = ahora
                renovaciones[phone_rv]["notificado"] = True
                guardar_renovaciones()

        # 4b. Recordatorio a clientes con reserva pagada
        for phone, datos in list(conversaciones.items()):
            if datos.get("estado") != "esperando_consola":
                continue
            recordatorio_consola_at = datos.get("recordatorio_consola_at", 0)
            if recordatorio_consola_at and ahora >= recordatorio_consola_at:
                enviar_boton_consola_lista(phone,
                    "¡Hola! 🎮 Paso por aquí para recordarte que tienes tu reserva de *Game Pass Ultimate* lista y pagada.\n\n"
                    "Apenas tengas tu consola o PC a la mano, avísame con el botón de abajo y te entrego tu cuenta de una vez."
                )
                conversaciones[phone]["recordatorio_consola_at"] = ahora + HORA_RECORDATORIO_CONSOLA

        # 5. Recordatorio único de pago de renovación
        for phone, datos in list(conversaciones.items()):
            if datos.get("estado") != "renovacion_espera_pago":
                continue
            if datos.get("renovacion_recordatorio_enviado"):
                continue
            recordatorio_at = datos.get("renovacion_recordatorio_at", 0)
            if recordatorio_at and ahora >= recordatorio_at:
                meses_rv = renovaciones.get(phone, {}).get("meses", "")
                send_message(phone,
                    "⏰ ¡Hola! Te recuerdo tu renovación pendiente de Game Pass" + (" (" + meses_rv + ")" if meses_rv else "") + " en Game Line Col.\n\n"
                    "Apenas puedas hacer el pago, envíame por aquí la foto del comprobante 📸"
                )
                conversaciones[phone]["renovacion_recordatorio_enviado"] = True

        # 6. Guardar estados cada 5 minutos
        if (ahora - ultimo_guardado) >= 300:
            try:
                service = get_sheets_service()
                filas = [["telefono", "json"]]
                for phone, datos in list(conversaciones.items()):
                    if datos.get("estado") == "pago_confirmado":
                        continue
                    datos_reducidos = {k: v for k, v in datos.items() if k != "historial"}
                    filas.append([phone, json.dumps(datos_reducidos)])
                service.spreadsheets().values().clear(
                    spreadsheetId=SHEET_ID, range=ESTADOS_RANGE
                ).execute()
                service.spreadsheets().values().update(
                    spreadsheetId=SHEET_ID,
                    range="Estados_TG!A1",
                    valueInputOption="RAW",
                    body={"values": filas}
                ).execute()
                ultimo_guardado = ahora
            except Exception as e:
                print("Error guardando estados: " + str(e))

        # 7. Limpieza de memoria cada hora
        if (ahora - ultima_limpieza) >= 3600:
            eliminadas = 0
            for phone in list(conversaciones.keys()):
                datos = conversaciones[phone]
                ult = datos.get("ultima_interaccion", 0)
                est = datos.get("estado")
                if est == "pago_confirmado" and (ahora - ult) >= 86400:
                    del conversaciones[phone]
                    eliminadas += 1
                elif est == "menu" and (ahora - ult) >= (30 * 86400):
                    del conversaciones[phone]
                    eliminadas += 1
            if eliminadas:
                print("Limpieza: " + str(eliminadas) + " conversaciones eliminadas. Activas: " + str(len(conversaciones)))
            ultima_limpieza = ahora


conversaciones = {}
asegurar_hoja("Estados_TG")
asegurar_hoja("Config")
asegurar_hoja("Compras")
asegurar_hoja("Renovaciones")
asegurar_hoja("Cuentas")
asegurar_hoja("Bloqueados")


def inicializar_hojas():
    try:
        service = get_sheets_service()
        r = service.spreadsheets().values().get(spreadsheetId=SHEET_ID, range="Compras!A1:F1").execute()
        if not r.get("values"):
            service.spreadsheets().values().update(
                spreadsheetId=SHEET_ID, range="Compras!A1",
                valueInputOption="RAW",
                body={"values": [["Cliente (ID)", "Fecha de compra", "Tipo de cuenta", "Tiempo adquirido", "Email cuenta", "Nombre / usuario"]]}
            ).execute()
        r2 = service.spreadsheets().values().get(spreadsheetId=SHEET_ID, range="Cuentas!A1:F1").execute()
        if not r2.get("values"):
            service.spreadsheets().values().update(
                spreadsheetId=SHEET_ID, range="Cuentas!A1",
                valueInputOption="RAW",
                body={"values": [["Email", "Contraseña", "Principal Ocupado", "Cliente Principal", "Secundaria Ocupada", "Cliente Secundaria"]]}
            ).execute()
        print("Hojas inicializadas")
    except Exception as e:
        print("Error inicializando hojas: " + str(e))


def verificar_token_telegram():
    try:
        r = requests.get(TG_API + "/getMe", timeout=10).json()
        if r.get("ok"):
            print("✅ Token de Telegram correcto. Bot: @" + str(r["result"].get("username")))
        else:
            alertar_token_vencido(r)
    except Exception as e:
        print("No se pudo verificar el token de Telegram al iniciar: " + str(e))


def configurar_webhook():
    if not PUBLIC_DOMAIN:
        print("⚠️ No hay dominio publico: genera uno en Railway (Settings > Networking > Generate Domain).")
        return
    url = "https://" + PUBLIC_DOMAIN + "/telegram-webhook"
    try:
        r = requests.post(TG_API + "/setWebhook", json={
            "url": url,
            "secret_token": TELEGRAM_SECRET,
            "allowed_updates": ["message", "callback_query"]
        }, timeout=10).json()
        if r.get("ok"):
            print("🔗 Webhook de Telegram configurado en " + url)
        else:
            print("Error configurando webhook: " + str(r))
    except Exception as e:
        print("Error configurando webhook: " + str(e))


cargar_bloqueados()
cargar_estados()
cargar_config()
cargar_renovaciones()
cargar_cuentas()
inicializar_hojas()
verificar_token_telegram()
configurar_webhook()
if not ADMIN_PHONE:
    print("⚠️ Falta ADMIN_CHAT_ID: escribele /miid al bot y pon ese numero en Railway.")

threading.Thread(target=scheduler, daemon=True).start()


def texto_bienvenida(phone):
    nom = primer_nombre(phone)
    saludo = ("¡Hola *" + nom + "*! Qué gusto saludarte 👋\n") if nom else "¡Hola! Qué gusto saludarte 👋\n"
    return (saludo +
            "Bienvenido a *Game Line Col* 🎮, tu tienda de confianza para suscripciones y juegos de Xbox.\n\n"
            "Cuéntame, ¿en qué te puedo colaborar hoy? Puedes tocar una opción abajo o escribirme directamente lo que buscas 👇")


GAMEPASS = (
    "🕹️ *GAME PASS ULTIMATE*\n\n"
    "Con Ultimate tienes acceso a cientos de juegos (incluyendo EA Play, estrenos Día 1 como Call of Duty, FIFA/FC, Halo, Forza) y modo multijugador online.\n\n"
    "*NUESTROS PLANES (COP):*\n"
    "📅 1 mes: *$29.900*\n"
    "📅 2 meses: *$55.000*\n"
    "📅 3 meses: *$80.000*\n"
    "📅 6 meses: *$140.000*\n"
    "📅 12 meses: *$190.000*\n\n"
    "*MODALIDADES:*\n"
    "🏠 *Principal:* Juegas desde tu perfil personal de toda la vida sin tener que iniciar sesión en otra cuenta.\n"
    "👤 *Secundaria:* Juegas también desde tu perfil personal, manteniendo sesión abierta en segundo plano con la cuenta del servicio.\n\n"
    "🛡️ *GARANTÍA TOTAL:* Primero te entregamos la cuenta, la pruebas en tu consola y *solo pagas cuando verifiques que todo funciona perfecto*."
)

PREGUNTAR_CUENTA = "¿Qué tipo de cuenta prefieres?\n\n🏠 Principal: Juegas desde tu cuenta sin iniciar sesion en otra\n👤 Secundaria: Juegas desde tu cuenta iniciando sesion en la del servicio\n\nAmbas funcionan perfecto 😊"

PREGUNTAR_CONSOLA = "Para entregarte la cuenta de una vez y que la pruebes: ¿tienes tu consola o PC a la mano en este momento?"

CONFIG_PRINCIPAL = "Una vez habilitemos tu cuenta, sigue estos pasos en tu consola:\n\nCONFIGURACION CUENTA PRINCIPAL\n\nCuando aparezcan las preguntas de asociacion Game Pass Ultimate:\n\n1 SIGUIENTE\n2 NO GRACIAS\n3 SIN BARRERAS\n4 OMITIR\n5 En la pregunta de hacer Xbox principal: HACER XBOX PRINCIPAL ✅\n\nIMPORTANTE:\nSiempre usa el servicio con la sesion de tu cuenta personal. La cuenta que anadimos nunca la inicies.\n\nEl uso es exclusivo para ti. Si compartes, se cancela sin devolucion del dinero."

CONFIG_SECUNDARIA = "Una vez habilitemos tu cuenta, sigue estos pasos en tu consola:\n\nCONFIGURACION CUENTA SECUNDARIA\n\nCuando aparezcan las preguntas de asociacion Game Pass Ultimate:\n\n1 SIGUIENTE\n2 NO GRACIAS\n3 SIN BARRERAS\n4 VINCULAR CONTROL\n5 En la pregunta de hacer Xbox principal: NO CAMBIAR ⛔\n\nMucho cuidado con esa pregunta, debes dar NO CAMBIAR.\n\nSiempre con sesion iniciada de la cuenta que anadimos y juegas con tu cuenta personal.\n\nEl uso es exclusivo para ti. Si compartes, se cancela sin devolucion del dinero."

CIERRE = "🎮 ¡Con todo el gusto! Mil gracias a ti por confiar en *Game Line Col* 🙌\n\nCualquier duda o juego que necesites más adelante, por aquí estaré atento. ¡A disfrutar esos juegos! 🚀"

JUEGOS = (
    "🎮 *JUEGOS INDIVIDUALES XBOX*\n\n"
    "Manejamos 4 modalidades según tu presupuesto (todas con acceso de por vida):\n\n"
    "1️⃣ *Código Digital:* Se canjea directo en tu cuenta personal de Microsoft.\n"
    "2️⃣ *Cuenta Principal:* Juegas desde tu perfil personal sin iniciar sesión en otra cuenta.\n"
    "3️⃣ *Cuenta Secundaria:* Más económico; inicias sesión en la cuenta del juego y juegas en tu perfil.\n"
    "4️⃣ *Secundaria con Método:* La opción más económica con un paso rápido que te enseñamos.\n\n"
    "¿Qué juego estás buscando? Escríbeme el nombre y te averiguo de una 👇"
)

PROMPT = """Eres el asesor principal de ventas y atención al cliente de 'Game Line Col', una tienda colombiana especializada en Xbox Game Pass Ultimate y videojuegos digitales para Xbox (Series X|S, Xbox One y PC).

TU PERSONALIDAD Y ESTILO:
- Hablas como un asesor humano real en Colombia: cercano, carismático, claro, servicial y confiable.
- No suenes como un robot ni uses lenguaje corporativo acartonado. Usa un tono cálido y natural (puedes usar expresiones suaves como "¡Claro que sí!", "Con todo gusto", "Te cuento:", "Mira, funciona así:", pero sin abusar de jerga exagerada).
- Respuestas ágiles y fáciles de leer en el celular (usa viñetas cortas y algún emoji ocasional 🎮🙌).

INFORMACIÓN OFICIAL DE GAME LINE COL (NUNCA INVENTES PRECIOS FUERA DE ESTOS):
1. Precios de Xbox Game Pass Ultimate (en pesos colombianos - COP):
   - 1 mes: $29.900
   - 2 meses: $55.000
   - 3 meses: $80.000
   - 6 meses: $140.000
   - 12 meses (1 año): $190.000
2. ¿Qué incluye Game Pass Ultimate?:
   - Catálogo de más de 400 juegos para Xbox One, Xbox Series X/S y PC.
   - Incluye EA Play (FC/FIFA, Battlefield, F1, Need for Speed, etc.), juegos de Activision/Bethesda/Xbox Studios (Call of Duty Black Ops 6, Warzone, Halo, Forza, GTA, Minecraft, etc.) y permite jugar online multijugador.
3. ¿Cómo se entrega y cuál es la garantía?:
   - Nuestro mayor diferencial es la confianza: PRIMERO entregamos la cuenta para que el cliente la agregue y la pruebe en su consola, y LUEGO realiza el pago.
   - Si el cliente no tiene la consola a la mano en este momento, puede pagar para apartar su cupo y le entregamos apenas llegue a casa.
   - El cliente siempre juega desde su propio perfil personal (conserva sus amigos, logros y partidas guardadas).
   - Medios de pago: Mercado Pago (Tarjeta, PSE, Nequi automático), o transferencia a Nequi / Daviplata / Llave al 3057059517 (David Olaya).
   - Garantía durante todo el tiempo contratado (uso exclusivo para 1 sola consola; no se debe compartir ni cambiar datos).
4. Juegos individuales:
   - Vendemos cualquier juego de Xbox en formato Código, Cuenta Principal o Cuenta Secundaria. No tienes los precios exactos de cada juego suelto en memoria, así que si el cliente pide el precio o disponibilidad de un juego específico (que NO sea preguntar si está en Game Pass), dile amablemente que ya mismo le consultas el valor exacto en las modalidades disponibles y agrega al final de tu respuesta la etiqueta exacta: ALERTA_JUEGO:[nombre del juego]

REGLAS CRÍTICAS DE ETIQUETAS:
- Si el cliente cotiza o pide comprar un juego específico individual, termina tu mensaje con: ALERTA_JUEGO:[nombre del juego]
- Si el cliente tiene un problema técnico complejo de su cuenta, un reclamo de dinero, o algo que requiera intervención del dueño, termina tu mensaje con: ALERTA_ASESOR
- Si el cliente quiere contratar Game Pass y está en conversación libre, anímalo a elegir cuántos meses quiere (1, 2, 3, 6 o 12 meses)."""

ESTADO_CLIENTE_MENSAJE = {
    "menu": "Aún no tienes un pedido abierto, pero cuéntame qué te gustaría adquirir o toca una opción del menú 🎮",
    "gamepass": "Estábamos viendo los planes de Game Pass Ultimate. Dime si te animas a probarlo o si tienes alguna duda 😊",
    "seleccion_meses": "Estamos en la elección de tu plan. Cuéntame cuántos meses prefieres (1, 2, 3, 6 o 12 meses) o hazme cualquier pregunta.",
    "confirmacion_compra": "Estamos por confirmar tu plan de Game Pass Ultimate. Toca el botón de confirmar o dime si tienes alguna duda antes.",
    "seleccion_cuenta": "Estamos esperando que elijas el tipo de cuenta (Principal o Secundaria).",
    "preguntar_consola": "Te preguntaba si tienes tu consola o PC a la mano ahora mismo para entregarte la cuenta de una vez 🎮",
    "activacion": "Estamos esperando el código de activación de tu consola. Envíalo aquí cuando lo tengas 🎮",
    "esperando_comprobante": "Estamos pendientes de la foto del comprobante de tu reserva para dejarte el cupo apartado 📸",
    "comprobante_reserva_enviado": "Envíanos por aquí la foto del comprobante de tu reserva ⏳",
    "esperando_consola": "Tu reserva ya está confirmada y guardada ✅. Apenas estés en tu consola o PC avísame por aquí para entregarte tu cuenta 🎮",
    "esperando_pago_final": "Ya te entregamos tu cuenta para que la pruebes 🎮. Quedo atento por aquí a la foto del comprobante de pago 📸",
    "pago_final_enviado": "Envíanos aquí la foto del comprobante de tu pago final ⏳",
    "pago_confirmado": "Tu pedido ya quedó 100% confirmado y activo. ¡Gracias por tu compra! 🎮🙌",
    "juegos": "Cuéntame el nombre del juego que estás buscando para averiguarte disponibilidad y precio 🎮",
    "soporte": "Tu solicitud de soporte fue enviada, un asesor te contactará pronto 😊",
    "soporte_menu": "Estamos en el menú de soporte técnico. Elige cuál inconveniente presentas para guiarte paso a paso.",
    "sop_online": "Estamos revisando el modo online. Indícame qué tipo de cuenta tienes.",
    "sop_online_p1": "Estamos verificando la casilla de Xbox Principal en tu consola.",
    "sop_online_p2": "Probando el paso 1 de conexión online. Cuéntame cómo te fue.",
    "sop_online_p3": "Probando el ajuste de facturación periódica. Cuéntame si ya te dejó entrar.",
    "sop_online_p4": "Aplicando el reinicio completo de dirección MAC y Xbox Principal. Avísame cómo te va con esos 6 pasos.",
    "sop_online_s1": "Aplicando ajuste de facturación para cuenta Secundaria. Cuéntame cómo te fue.",
    "sop_jugando": "Revisando el aviso de otro usuario jugando. Dime qué tipo de cuenta tienes.",
    "sop_jugando_p1": "Probando cierre de sesión de la cuenta secundaria para dejar solo tu perfil.",
    "sop_jugando_s1": "Ajustando configuración de cuenta Secundaria. Cuéntame cómo te fue.",
    "soporte_asesor": "Ya le pasé tu caso a nuestro asesor humano para que te atienda personalmente en breve 🙏",
    "renovacion_pendiente": "Te consultaba si deseas renovar tu suscripción de Game Pass que vence hoy 🎮",
    "renovacion_espera_admin": "Ya le avisé al asesor de tu renovación, en un momento te damos razón 🙏",
    "renovacion_espera_tiempo": "Cuéntame en cuánto tiempo aproximado podrías realizar el pago (ej: 30 minutos, 1 hora).",
    "renovacion_espera_pago": "Quedo atento por aquí a la foto del comprobante de tu renovación 📸",
    "renovacion_comprobante_enviado": "Envíanos aquí la foto del comprobante de tu renovación ⏳"
}


def responder_con_ia(phone, text, contexto_extra=""):
    """Genera una respuesta natural con Gemini manteniendo el historial y el contexto del estado actual."""
    mostrar_escribiendo(phone)
    historial = conversaciones[phone].get("historial", [])
    historial.append({"role": "user", "content": text})

    historial_texto = ""
    for h in historial[-12:]:
        rol = "Cliente" if h["role"] == "user" else "Asesor Game Line Col"
        historial_texto += rol + ": " + h["content"] + "\n"

    nombre_cli = primer_nombre(phone)
    estado_actual = conversaciones[phone].get("estado", "menu")
    meses_sel = conversaciones[phone].get("meses")

    info_contexto = "\n\nCONTEXTO ACTUAL DEL CLIENTE:"
    if nombre_cli:
        info_contexto += "\n- Nombre del cliente: " + nombre_cli
    info_contexto += "\n- Etapa en la que está: " + estado_actual
    if meses_sel:
        info_contexto += "\n- Plan que estaba mirando: " + str(meses_sel) + " ($" + str(PRECIOS_GAMEPASS.get(meses_sel, "")) + ")"
    if contexto_extra:
        info_contexto += "\n- Instrucción adicional para esta respuesta: " + contexto_extra

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=PROMPT + info_contexto + "\n\nHistorial de conversación:\n" + historial_texto + "\nResponde al último mensaje del cliente como un asesor humano amable y elocuente:",
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())]
            )
        )
        reply = response.text or "Cuéntame, ¿en qué te puedo colaborar hoy? 😊"
    except Exception as e:
        print("Error llamando a Gemini: " + str(e))
        reply = "Disculpa, tuve un pequeño parpadeo en la señal 😅 ¿Me repites por favor o te ayudo con alguno de los botones?"

    if "ALERTA_JUEGO:" in reply:
        match = re.search(r'ALERTA_JUEGO:([^\n]+)', reply)
        nombre_juego = match.group(1).strip() if match else text
        reply = re.sub(r'ALERTA_JUEGO:[^\n]+', '', reply).strip()
        conversaciones[phone]["compro"] = True
        alerta = "🎯 COTIZACION JUEGO - Game Line Col\nCliente: " + cli(phone) + "\nJuego: " + nombre_juego + "\nMensaje cliente: " + text
        send_message(ADMIN_PHONE, alerta)
    elif "ALERTA_ASESOR" in reply:
        reply = reply.replace("ALERTA_ASESOR", "").strip()
        alerta = "🙋 ALERTA ASESOR - Game Line Col\nCliente: " + cli(phone) + "\nMensaje: " + text
        send_message(ADMIN_PHONE, alerta)

    historial.append({"role": "assistant", "content": reply})
    conversaciones[phone]["historial"] = historial[-20:]
    send_message(phone, reply, formato=False)


def enviar_menu_principal(phone):
    enviar_botones(phone, "¿Qué te gustaría ver hoy?", [
        {"id": "1", "titulo": "🕹️ Game Pass Ultimate"},
        {"id": "2", "titulo": "🎮 Juegos Xbox"},
        {"id": "3", "titulo": "🛠️ Soporte Técnico"}
    ])


def enviar_pregunta_contratar(phone):
    enviar_botones(phone, "¿Te gustaría elegir un plan para probarlo en tu consola o tienes alguna duda?", [
        {"id": "si", "titulo": "🙌 Sí, quiero ver planes"}
    ])


def enviar_pregunta_meses(phone, cuerpo="¿Por cuántos meses te gustaría activar tu Game Pass Ultimate?"):
    enviar_lista(phone, cuerpo, "Ver planes", [
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
        {"id": "consola_si", "titulo": "✅ Sí, la tengo aquí"},
        {"id": "consola_no", "titulo": "⏳ No la tengo ahora"}
    ])


def nueva_conversacion():
    return {
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


def vincular_telefono(chat_id, contacto, usuario_tg):
    quitar = {"remove_keyboard": True}
    if contacto.get("user_id") and str(contacto.get("user_id")) != str(usuario_tg.get("id")):
        send_message(chat_id, "Por seguridad, comparte *tu propio* número tocando el botón de abajo 📱", reply_markup=quitar)
        return
    telefono = normalizar_numero(contacto.get("phone_number", ""))
    if not telefono:
        return
    datos = conversaciones.setdefault(chat_id, nueva_conversacion())
    datos["telefono"] = telefono
    datos["nombre"] = (str(usuario_tg.get("first_name", "")) + " " + str(usuario_tg.get("last_name", ""))).strip()
    datos["usuario"] = usuario_tg.get("username", "")

    encontrado = []
    if telefono in renovaciones and chat_id not in renovaciones:
        renovaciones[chat_id] = renovaciones.pop(telefono)
        renovaciones[chat_id]["canal"] = "telegram"
        guardar_renovaciones()
        rv = renovaciones[chat_id]
        encontrado.append("tu plan de Game Pass (" + str(rv.get("tipo_cuenta", "")) + " - " + str(rv.get("meses", "")) + ")")
    for c in cuentas:
        cambio = False
        if str(c.get("cliente_principal", "")).strip() == telefono:
            c["cliente_principal"] = chat_id
            cambio = True
        if str(c.get("cliente_secundaria", "")).strip() == telefono:
            c["cliente_secundaria"] = chat_id
            cambio = True
        if cambio:
            actualizar_fila_cuenta(c["fila"], c)
            if not encontrado:
                encontrado.append("tu cuenta asignada")

    if encontrado:
        send_message(chat_id,
            "✅ ¡Qué alegría tenerte por aquí! Ya te reconocimos y vinculamos " + " y ".join(encontrado) + ".\n\n"
            "Desde ahora te avisaremos por este chat cuando tu servicio esté próximo a vencer 🎮",
            reply_markup=quitar)
        send_message(ADMIN_PHONE, "🔗 Cliente de WhatsApp vinculado a Telegram: " + cli(chat_id))
    else:
        send_message(chat_id, "✅ ¡Listo, muchas gracias! Ya dejamos tu número registrado en tu ficha.", reply_markup=quitar)
    if not datos.get("bienvenida_enviada"):
        datos["bienvenida_enviada"] = True
        send_message(chat_id, texto_bienvenida(chat_id))
        enviar_menu_principal(chat_id)


def enviar_boton_consola_lista(phone, mensaje):
    enviar_botones(phone, mensaje, [
        {"id": "consola_lista", "titulo": "🎮 Ya tengo mi consola"}
    ])


@app.route("/", methods=["GET"])
def salud():
    return "GameBot Telegram activo", 200


@app.route("/telegram-webhook", methods=["POST"])
def webhook():
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token", "") != TELEGRAM_SECRET:
        print("🛑 Webhook RECHAZADO: clave secreta invalida. IP: " +
              str(request.headers.get("X-Forwarded-For", request.remote_addr)))
        return jsonify({"status": "forbidden"}), 403

    update = request.get_json(silent=True) or {}
    try:
        msg_id = str(update.get("update_id", ""))
        text = ""
        foto_id = None
        if "callback_query" in update:
            cq = update["callback_query"]
            responder_callback(cq.get("id"))
            message = cq.get("message") or {}
            usuario_tg = cq.get("from", {})
            text = str(cq.get("data", ""))
            msg_type = "interactive"
        elif "message" in update:
            message = update["message"]
            usuario_tg = message.get("from", {})
            if "text" in message:
                msg_type = "text"
                text = message["text"].strip()
            elif "photo" in message:
                msg_type = "image"
                foto_id = message["photo"][-1]["file_id"]
            elif "document" in message:
                msg_type = "document"
            elif "contact" in message:
                msg_type = "contact"
            else:
                return jsonify({"status": "ok"}), 200
        else:
            return jsonify({"status": "ok"}), 200

        chat = message.get("chat", {})
        if chat.get("type") != "private":
            return jsonify({"status": "ok"}), 200
        phone = str(chat.get("id"))
        print("📩 Mensaje de " + phone + " (tipo: " + msg_type + ")")

        if msg_type == "text" and text.startswith("/"):
            partes_cmd = text[1:].split(" ", 1)
            text = partes_cmd[0].split("@")[0] + ((" " + partes_cmd[1]) if len(partes_cmd) > 1 else "")
            if text.lower().startswith("start"):
                text = "hola"

        if text.lower().strip() == "miid":
            send_message(phone, "Tu chat ID es: " + phone, formato=False)
            return jsonify({"status": "ok"}), 200

        if esta_bloqueado(phone):
            print("🚫 Mensaje ignorado de cliente bloqueado: " + phone)
            return jsonify({"status": "ok"}), 200

        if phone in conversaciones:
            conversaciones[phone]["nombre"] = (str(usuario_tg.get("first_name", "")) + " " +
                                               str(usuario_tg.get("last_name", ""))).strip()
            conversaciones[phone]["usuario"] = usuario_tg.get("username", "")

        if phone != ADMIN_PHONE:
            ahora_flood = time.time()
            ultimo_flood = _flood_control.get(phone, 0)
            if (ahora_flood - ultimo_flood) < 1:
                return jsonify({"status": "ok"}), 200
            _flood_control[phone] = ahora_flood
            if len(_flood_control) > 100:
                hace_5min = ahora_flood - 300
                for p in [k for k, v in _flood_control.items() if v < hace_5min]:
                    del _flood_control[p]

        # ── Comando setcatalogo: admin envía el PDF con caption "setcatalogo" ─
        if msg_type == "document" and phone == ADMIN_PHONE:
            caption_doc = str(message.get("caption", "")).lower().strip()
            if "setcatalogo" in caption_doc:
                global catalogo_media_id
                catalogo_media_id = message["document"]["file_id"]
                guardar_config("catalogo_media_id", catalogo_media_id)
                send_message(ADMIN_PHONE, "✅ Catalogo actualizado correctamente! Ahora se enviara automaticamente a los clientes que pregunten por juegos 🎮")
            else:
                send_message(ADMIN_PHONE, "Documento recibido. Si quieres usarlo como catalogo, envialo de nuevo con el caption: setcatalogo")
            return jsonify({"status": "ok"}), 200

        if msg_type == "document":
            return jsonify({"status": "ok"}), 200

        if msg_type == "contact":
            vincular_telefono(phone, message.get("contact", {}), usuario_tg)
            return jsonify({"status": "ok"}), 200

        text_lower = text.lower()

        # ── COMANDOS EXCLUSIVOS DEL ADMIN ────────────────────────────────────
        if phone == ADMIN_PHONE and text_lower.startswith("activo"):
            partes = text_lower.replace("activo", "").strip().split()
            ultimos_4 = partes[0] if partes else ""
            tipo_suffix = partes[1] if len(partes) > 1 else ""

            if tipo_suffix == "p":
                tipo_cuenta_elegida = "Principal"
            elif tipo_suffix == "s":
                tipo_cuenta_elegida = "Secundaria"
            else:
                send_message(ADMIN_PHONE,
                    "Indica el tipo de cuenta:\n"
                    "✅ activo " + ultimos_4 + " p → Principal\n"
                    "✅ activo " + ultimos_4 + " s → Secundaria")
                return jsonify({"status": "ok"}), 200

            cliente_encontrado = None
            for ph, datos in conversaciones.items():
                if ph.endswith(ultimos_4) and datos.get("compro"):
                    cliente_encontrado = ph
                    break

            if cliente_encontrado:
                conversaciones[cliente_encontrado]["tipo_cuenta"] = tipo_cuenta_elegida
                meses_c = conversaciones[cliente_encontrado].get("meses", "1 mes")
                config_c = CONFIG_PRINCIPAL if tipo_cuenta_elegida == "Principal" else CONFIG_SECUNDARIA
                mensaje_activo = "✅ ¡Tu cuenta ya quedó activada en la consola! 🎮\n\nYa puedes empezar a jugar. Sigue estas instrucciones finales:\n\n" + config_c
                send_message(cliente_encontrado, mensaje_activo)

                monto_c = PRECIOS_GAMEPASS.get(meses_c)
                link_c, referencia_c = (None, None)
                if monto_c:
                    link_c, referencia_c = crear_link_pago(cliente_encontrado, "Game Pass Ultimate " + tipo_cuenta_elegida + " - " + meses_c, monto_c)
                if referencia_c:
                    conversaciones[cliente_encontrado]["referencia_pago"] = referencia_c
                    conversaciones[cliente_encontrado]["tipo_pago_pendiente"] = "final"
                send_message(cliente_encontrado, "Para terminar de confirmar tu activación:\n\n" + mensaje_opciones_pago(link_c))

                conversaciones[cliente_encontrado]["estado"] = "esperando_pago_final"
                send_message(ADMIN_PHONE, "✅ Configuracion " + tipo_cuenta_elegida + " y opciones de pago enviadas al cliente " + cli(cliente_encontrado))
            else:
                send_message(ADMIN_PHONE, "No encontre un cliente pendiente con esos ultimos 4 digitos: " + ultimos_4)
            return jsonify({"status": "ok"}), 200

        if phone == ADMIN_PHONE and text_lower.startswith("pagook"):
            ultimos_4 = text_lower.replace("pagook", "").strip()
            cliente_encontrado = None
            for ph, datos in conversaciones.items():
                if ph.endswith(ultimos_4) and datos.get("estado") in (
                    "esperando_pago_final", "pago_final_enviado",
                    "renovacion_espera_pago", "renovacion_comprobante_enviado"
                ):
                    cliente_encontrado = ph
                    break

            if cliente_encontrado:
                tipo_cuenta_c = conversaciones[cliente_encontrado].get("tipo_cuenta", "No especificado")
                meses_c = conversaciones[cliente_encontrado].get("meses", "No especificado")
                email_c = conversaciones[cliente_encontrado].get("email_cuenta", "")
                es_renovacion = conversaciones[cliente_encontrado].get("es_renovacion", False)
                conversaciones[cliente_encontrado]["estado"] = "pago_confirmado"
                conversaciones[cliente_encontrado]["compro"] = True
                send_message(cliente_encontrado, CIERRE)
                send_message(ADMIN_PHONE, "✅ Pago confirmado, cierre enviado al cliente " + cli(cliente_encontrado))
                registrar_compra(cliente_encontrado, tipo_cuenta_c, meses_c, email_c)
                registrar_evento_diario("cierres")
                if es_renovacion and cliente_encontrado in renovaciones:
                    renovaciones[cliente_encontrado]["notificado"] = False
            else:
                send_message(ADMIN_PHONE, "No encontre un cliente esperando confirmacion de pago con esos ultimos 4 digitos: " + ultimos_4)
            return jsonify({"status": "ok"}), 200

        if phone == ADMIN_PHONE and text_lower.startswith("reservaok"):
            ultimos_4 = text_lower.replace("reservaok", "").strip()
            cliente_encontrado = None
            for ph, datos in conversaciones.items():
                if ph.endswith(ultimos_4) and datos.get("estado") in ("esperando_comprobante", "comprobante_reserva_enviado"):
                    cliente_encontrado = ph
                    break

            if cliente_encontrado:
                conversaciones[cliente_encontrado]["estado"] = "esperando_consola"
                conversaciones[cliente_encontrado]["reserva_pagada"] = True
                conversaciones[cliente_encontrado]["compro"] = True
                conversaciones[cliente_encontrado]["recordatorio_consola_at"] = time.time() + HORA_RECORDATORIO_CONSOLA
                registrar_evento_diario("reservas")
                enviar_boton_consola_lista(cliente_encontrado,
                    "✅ ¡Tu reserva quedó confirmada!\n\n"
                    "Cuando tengas tu consola o PC disponible, avísame por aquí para entregarte tu cuenta al instante 🎮"
                )
                send_message(ADMIN_PHONE, "✅ Reserva confirmada para " + cli(cliente_encontrado) + ". Quedara esperando a que avise cuando tenga consola.")
            else:
                send_message(ADMIN_PHONE, "No encontre un cliente con reserva pendiente con esos ultimos 4 digitos: " + ultimos_4)
            return jsonify({"status": "ok"}), 200

        if phone == ADMIN_PHONE and text_lower.startswith("misma"):
            ultimos_4 = text_lower.replace("misma", "").strip()
            cliente_encontrado = None
            for ph, datos in conversaciones.items():
                if ph.endswith(ultimos_4) and datos.get("estado") == "renovacion_espera_admin":
                    cliente_encontrado = ph
                    break
            if cliente_encontrado:
                meses_rv = renovaciones.get(cliente_encontrado, {}).get("meses", "1 mes")
                monto_rv = PRECIOS_GAMEPASS.get(meses_rv)
                link_rv, ref_rv = (None, None)
                if monto_rv:
                    link_rv, ref_rv = crear_link_pago(cliente_encontrado, "Renovacion Game Pass " + meses_rv, monto_rv)
                if ref_rv:
                    conversaciones[cliente_encontrado]["referencia_pago"] = ref_rv
                    conversaciones[cliente_encontrado]["tipo_pago_pendiente"] = "renovacion"
                send_message(cliente_encontrado,
                    "¡Excelente noticia! Puedes continuar con la misma cuenta que ya tienes configurada en tu consola 🎮\n\n" + mensaje_opciones_pago(link_rv)
                )
                send_message(cliente_encontrado,
                    "Cuéntame, ¿en cuánto tiempo aproximado podrías realizar el pago para agendarte el recordatorio?\n\n"
                    "(Por ejemplo: 30 minutos, 1 hora, 2 horas...)"
                )
                conversaciones[cliente_encontrado]["estado"] = "renovacion_espera_tiempo"
                conversaciones[cliente_encontrado]["es_renovacion"] = True
                send_message(ADMIN_PHONE, "✅ Opciones de pago de renovacion enviadas al cliente " + cli(cliente_encontrado))
            else:
                send_message(ADMIN_PHONE, "No encontre un cliente esperando decision de renovacion con esos ultimos 4 digitos: " + ultimos_4)
            return jsonify({"status": "ok"}), 200

        if phone == ADMIN_PHONE and text_lower.startswith("cambia"):
            ultimos_4 = text_lower.replace("cambia", "").strip()
            cliente_encontrado = None
            for ph, datos in conversaciones.items():
                if ph.endswith(ultimos_4) and datos.get("estado") == "renovacion_espera_admin":
                    cliente_encontrado = ph
                    break
            if cliente_encontrado:
                meses_rv = renovaciones.get(cliente_encontrado, {}).get("meses", "1 mes")
                liberar_cuenta(cliente_encontrado)
                asignacion_rv = asignar_cuenta(cliente_encontrado)
                if not asignacion_rv:
                    send_message(ADMIN_PHONE, "❌ No hay cuentas disponibles para asignar al cliente " + cli(cliente_encontrado) + ". Agrega stock primero.")
                    return jsonify({"status": "ok"}), 200
                email_rv, password_rv, tipo_rv = asignacion_rv
                conversaciones[cliente_encontrado]["tipo_cuenta"] = tipo_rv
                conversaciones[cliente_encontrado]["email_cuenta"] = email_rv
                conversaciones[cliente_encontrado]["estado"] = "esperando_pago_final"
                conversaciones[cliente_encontrado]["es_renovacion"] = True
                config_rv = CONFIG_PRINCIPAL if tipo_rv == "Principal" else CONFIG_SECUNDARIA
                monto_rv = PRECIOS_GAMEPASS.get(meses_rv)
                link_rv, ref_rv = (None, None)
                if monto_rv:
                    link_rv, ref_rv = crear_link_pago(cliente_encontrado, "Renovacion Game Pass " + tipo_rv + " - " + meses_rv, monto_rv)
                if ref_rv:
                    conversaciones[cliente_encontrado]["referencia_pago"] = ref_rv
                    conversaciones[cliente_encontrado]["tipo_pago_pendiente"] = "renovacion"
                send_message(cliente_encontrado,
                    "Para tu renovación te acabamos de asignar una cuenta nueva recién renovada 🔄\n\n"
                    "Primero *elimina la cuenta anterior* de tu consola y luego agrega esta:\n\n"
                    "📧 *Email:* " + email_rv + "\n"
                    "🔒 *Contraseña:* " + password_rv
                )
                send_message(cliente_encontrado,
                    "PASOS EN TU CONSOLA:\n\n"
                    "1️⃣ Ve a *Agregar nueva cuenta*\n"
                    "2️⃣ Ingresa el email y contraseña de arriba\n"
                    "3️⃣ Sigue esta configuración:\n\n" + config_rv
                )
                send_message(cliente_encontrado, "OPCIONES PARA TU PAGO:\n\n" + mensaje_opciones_pago(link_rv))
                send_message(ADMIN_PHONE,
                    "✅ Nueva cuenta asignada al cliente " + cli(cliente_encontrado) +
                    "\nEmail: " + email_rv + "\nTipo: " + tipo_rv
                )
            else:
                send_message(ADMIN_PHONE, "No encontre un cliente esperando decision de renovacion con esos ultimos 4 digitos: " + ultimos_4)
            return jsonify({"status": "ok"}), 200

        if phone == ADMIN_PHONE and text_lower == "pendientes":
            pendientes = []
            for ph, datos in conversaciones.items():
                if datos.get("estado") in ESTADOS_PENDIENTES:
                    pendientes.append(
                        "" + cli(ph) + " - " + str(datos.get("estado")) +
                        " - " + str(datos.get("meses")) + " - " + str(datos.get("tipo_cuenta"))
                    )
            if pendientes:
                msg = "📋 Clientes pendientes (" + str(len(pendientes)) + "):\n\n" + "\n".join(pendientes)
            else:
                msg = "No hay clientes pendientes en este momento 🎉"
            send_message(ADMIN_PHONE, msg)
            return jsonify({"status": "ok"}), 200

        if phone == ADMIN_PHONE and text_lower.startswith("anular"):
            ultimos_4 = re.sub(r"\D", "", text_lower.replace("anular", ""))
            cliente_encontrado = None
            for ph, datos in conversaciones.items():
                if ph.endswith(ultimos_4) and datos.get("estado") in (
                    "esperando_consola", "pago_confirmado", "esperando_pago_final"
                ):
                    cliente_encontrado = ph
                    break

            if not cliente_encontrado:
                send_message(ADMIN_PHONE,
                    "No encontre un cliente con avance reciente con esos ultimos 4 digitos: " + ultimos_4)
                return jsonify({"status": "ok"}), 200

            datos_c = conversaciones[cliente_encontrado]
            if datos_c.get("estado") == "esperando_consola":
                datos_c["estado"] = "esperando_comprobante"
                datos_c["reserva_pagada"] = False
                datos_c["recordatorio_consola_at"] = None
                aviso_admin = "Volvio a esperar el comprobante de la reserva."
            else:
                if datos_c.get("es_renovacion"):
                    datos_c["estado"] = "renovacion_espera_pago"
                    aviso_admin = "Volvio a esperar el pago de la renovacion."
                else:
                    datos_c["estado"] = "esperando_pago_final"
                    aviso_admin = ("Volvio a esperar el pago final.\n"
                                   "⚠️ Si el cliente no va a pagar y deseas quitarle el cupo y bloquearlo, usa: *bloquear " + ultimos_4 + " no pago*")

            send_message(cliente_encontrado,
                "¡Hola! 👋 Estuvimos revisando pero aún no nos aparece reflejado el pago con la imagen enviada.\n\n"
                "¿Podrías por favor verificar el comprobante y enviarme una captura clara por aquí para poder continuar? 🙏"
            )
            send_message(ADMIN_PHONE,
                "↩️ Avance anulado para " + cli(cliente_encontrado) + ".\n" + aviso_admin)
            return jsonify({"status": "ok"}), 200

        if phone == ADMIN_PHONE and text_lower.startswith("bloquear"):
            resto = text[len("bloquear"):].strip()
            if not resto:
                send_message(ADMIN_PHONE,
                    "Uso del comando:\n\n"
                    "🚫 *bloquear 123456789 motivo*\n"
                    "🚫 *bloquear 2927 motivo* (ultimos 4 digitos de un cliente activo)\n\n"
                    "El motivo es opcional.")
                return jsonify({"status": "ok"}), 200

            partes = resto.split(None, 1)
            objetivo = partes[0]
            motivo = partes[1].strip() if len(partes) > 1 else ""

            solo_digitos = re.sub(r"\D", "", objetivo)
            if len(solo_digitos) <= 5:
                candidatos = [ph for ph in conversaciones if ph.endswith(solo_digitos)]
                if len(candidatos) == 1:
                    objetivo = candidatos[0]
                elif len(candidatos) > 1:
                    send_message(ADMIN_PHONE,
                        "Hay varios clientes que terminan en " + solo_digitos + ":\n\n" +
                        "\n".join(cli(c) for c in candidatos) +
                        "\n\nEnvia el chat ID completo para bloquear el correcto.")
                    return jsonify({"status": "ok"}), 200
                else:
                    send_message(ADMIN_PHONE,
                        "No encontre ningun cliente que termine en " + solo_digitos +
                        ". Envia el chat ID completo.")
                    return jsonify({"status": "ok"}), 200

            ok, respuesta = bloquear_numero(objetivo, motivo)
            if ok and motivo:
                respuesta += "\nMotivo: " + motivo
            send_message(ADMIN_PHONE, respuesta)
            return jsonify({"status": "ok"}), 200

        if phone == ADMIN_PHONE and text_lower.startswith("desbloquear"):
            objetivo = text[len("desbloquear"):].strip()
            if not objetivo:
                send_message(ADMIN_PHONE, "Uso: *desbloquear 123456789*")
                return jsonify({"status": "ok"}), 200
            ok, respuesta = desbloquear_numero(objetivo)
            send_message(ADMIN_PHONE, respuesta)
            return jsonify({"status": "ok"}), 200

        if phone == ADMIN_PHONE and text_lower == "bloqueados":
            if not bloqueados:
                send_message(ADMIN_PHONE, "No hay numeros bloqueados en este momento ✅")
            else:
                lineas = []
                for telefono, datos in bloqueados.items():
                    lineas.append(
                        telefono + " - " + str(datos.get("motivo", "")) +
                        " (" + str(datos.get("fecha", "")) + ")"
                    )
                send_message(ADMIN_PHONE,
                    "🚫 Numeros bloqueados (" + str(len(bloqueados)) + "):\n\n" + "\n".join(lineas) +
                    "\n\nPara quitar uno: *desbloquear <numero>*")
            return jsonify({"status": "ok"}), 200

        # ── INICIO DE LÓGICA DE CLIENTE ──────────────────────────────────────
        saludos_exactos = {"hola", "buenas", "buenos dias", "buenos días", "buenas tardes",
                           "buenas noches", "hi", "hello", "inicio", "ola", "buen dia", "buen día"}
        es_saludo_simple = text_lower.strip(" !¡?¿.,") in saludos_exactos

        if phone not in conversaciones:
            conversaciones[phone] = nueva_conversacion()
            registrar_evento_diario("nuevos")
        conversaciones[phone]["nombre"] = (str(usuario_tg.get("first_name", "")) + " " +
                                           str(usuario_tg.get("last_name", ""))).strip()
        conversaciones[phone]["usuario"] = usuario_tg.get("username", "")

        if msg_id and msg_id == conversaciones[phone].get("ultimo_msg_id", ""):
            return jsonify({"status": "ok"}), 200
        conversaciones[phone]["ultimo_msg_id"] = msg_id

        # Cliente totalmente nuevo: si escribe un saludo simple le damos la bienvenida;
        # si de entrada hace una pregunta específica ("hola tienes fifa 25?"), le damos la bienvenida
        # y además le respondemos su pregunta con IA para que sienta atención humana.
        if not conversaciones[phone].get("bienvenida_enviada"):
            conversaciones[phone]["bienvenida_enviada"] = True
            conversaciones[phone]["estado"] = "menu"
            conversaciones[phone]["ultima_interaccion"] = time.time()
            send_message(phone, texto_bienvenida(phone))
            if not es_saludo_simple and msg_type == "text" and text not in ("1", "2", "3"):
                responder_con_ia(phone, text)
            enviar_menu_principal(phone)
            if not conversaciones[phone].get("telefono"):
                pedir_telefono(phone)
            return jsonify({"status": "ok"}), 200

        # ── Palabras de escape: funcionan desde CUALQUIER estado ──────────────
        PALABRAS_REINICIO = ("menu", "menú", "inicio", "reiniciar", "reinicio", "salir",
                             "cancelar", "empezar de nuevo", "volver", "atras", "atrás",
                             "regresar", "empezar", "otra cosa", "menu principal")
        PALABRAS_ASESOR = ("asesor", "humano", "persona real", "hablar con alguien",
                           "atencion personal", "atención personal", "agente",
                           "hablar con una persona", "necesito ayuda de una persona")

        if text_lower.strip() in PALABRAS_REINICIO:
            conversaciones[phone]["estado"] = "menu"
            conversaciones[phone]["ultima_interaccion"] = time.time()
            conversaciones[phone]["msgs_mismo_estado"] = 0
            send_message(phone, "¡Claro que sí! Volvamos al menú principal 🎮")
            enviar_menu_principal(phone)
            return jsonify({"status": "ok"}), 200

        if not text_lower.startswith("sop_") and any(p in text_lower for p in PALABRAS_ASESOR):
            estado_previo = conversaciones[phone].get("estado", "desconocido")
            conversaciones[phone]["estado"] = "soporte_asesor"
            conversaciones[phone]["ultima_interaccion"] = time.time()
            send_message(phone,
                "¡Claro que sí! 🙌 Ya mismo le paso tu chat a nuestro asesor principal para que te escriba personalmente.\n\n"
                "Si prefieres contactarlo directo por WhatsApp, su número es: *" + ADMIN_PHONE_DISPLAY + "*\n\n"
                "Cuando quieras volver al menú automático, solo escribe *menu*."
            )
            send_message(ADMIN_PHONE,
                "🙋 El cliente " + cli(phone) + " pidio hablar con un asesor.\n"
                "Estado en el que estaba: " + str(estado_previo)
            )
            return jsonify({"status": "ok"}), 200

        # Saludo simple cuando ya había hablado antes
        if es_saludo_simple:
            estado_saludo = conversaciones[phone].get("estado", "menu")
            conversaciones[phone]["ultima_interaccion"] = time.time()
            nom = primer_nombre(phone)
            saludo_hola = ("¡Hola de nuevo, *" + nom + "*! 👋") if nom else "¡Hola de nuevo! 👋"
            if estado_saludo in ESTADOS_SIN_PROCESO_ACTIVO:
                conversaciones[phone]["estado"] = "menu"
                send_message(phone, texto_bienvenida(phone))
                enviar_menu_principal(phone)
            else:
                descripcion_saludo = ESTADO_CLIENTE_MENSAJE.get(
                    estado_saludo, "Tienes un proceso abierto con nosotros."
                )
                send_message(phone,
                    saludo_hola + "\n\n" + descripcion_saludo +
                    "\n\n(Si prefieres empezar de cero o ver otra cosa, escribe *menu*)."
                )
            return jsonify({"status": "ok"}), 200

        if es_agradecimiento(text) and conversaciones[phone].get("compro") and len(text.split()) <= 4:
            conversaciones[phone]["ultima_interaccion"] = time.time()
            send_message(phone, CIERRE)
            return jsonify({"status": "ok"}), 200

        if text_lower == "estado":
            estado_actual = conversaciones[phone].get("estado", "menu")
            meses_e = conversaciones[phone].get("meses")
            tipo_cuenta_e = conversaciones[phone].get("tipo_cuenta")
            descripcion = ESTADO_CLIENTE_MENSAJE.get(estado_actual, "No tenemos un pedido activo en este momento. Escribe 'hola' para ver el menú 🎮")
            msg = "📦 *Estado de tu pedido:*\n\n" + descripcion
            detalle = []
            if meses_e:
                detalle.append("• Plan: " + meses_e)
            if tipo_cuenta_e:
                detalle.append("• Cuenta: " + tipo_cuenta_e)
            if detalle:
                msg += "\n\n" + "\n".join(detalle)
            send_message(phone, msg)
            return jsonify({"status": "ok"}), 200

        conversaciones[phone]["ultima_interaccion"] = time.time()
        conversaciones[phone]["recordatorio_enviado"] = False
        estado = conversaciones[phone].get("estado", "menu")

        # ── Detector de bucle (subido a 5 turnos porque ahora conversa con IA) ─
        if estado == conversaciones[phone].get("estado_anterior_registrado"):
            conversaciones[phone]["msgs_mismo_estado"] = conversaciones[phone].get("msgs_mismo_estado", 0) + 1
        else:
            conversaciones[phone]["estado_anterior_registrado"] = estado
            conversaciones[phone]["msgs_mismo_estado"] = 1

        if estado not in ("menu", "juegos", "gamepass") and conversaciones[phone].get("msgs_mismo_estado", 0) >= 5:
            conversaciones[phone]["msgs_mismo_estado"] = 0
            send_message(phone,
                "Quiero asegurarme de ayudarte bien con lo que necesitas 😊\n\n"
                "¿Prefieres que volvamos al menú principal o te paso directamente con un asesor humano?"
            )
            enviar_botones(phone, "Elige cómo prefieres seguir:", [
                {"id": "reiniciar_menu", "titulo": "🔄 Volver al menú"},
                {"id": "sop_asesor", "titulo": "🙋 Hablar con asesor"}
            ])
            return jsonify({"status": "ok"}), 200

        if text == "reiniciar_menu":
            conversaciones[phone]["estado"] = "menu"
            conversaciones[phone]["msgs_mismo_estado"] = 0
            send_message(phone, "¡Listo! Empecemos de nuevo 🎮")
            enviar_menu_principal(phone)
            return jsonify({"status": "ok"}), 200

        meses = conversaciones[phone].get("meses", "No especificado")

        # ── RECEPCIÓN DE IMÁGENES (COMPROBANTES) ─────────────────────────────
        if msg_type == "image":
            if estado in ("esperando_pago_final", "pago_final_enviado", "renovacion_espera_pago",
                          "renovacion_comprobante_enviado", "esperando_comprobante",
                          "comprobante_reserva_enviado"):
                reenviar_imagen(ADMIN_PHONE, foto_id, "🧾 Comprobante de " + cli(phone) + "\nEstado: " + estado)
                send_message(phone, "¡Súper, muchas gracias! 🙌 Ya recibimos la imagen de tu comprobante.")
                avanzar_tras_pago(phone, estado)
            else:
                send_message(phone, "Recibí tu imagen 😊 Si es un comprobante o tienes alguna duda sobre lo que me muestras, cuéntame por texto o escribe *asesor* para que la revisemos.")
            return jsonify({"status": "ok"}), 200

        # ── PASO: SELECCIÓN DE MESES (con soporte conversacional) ────────────
        if estado == "seleccion_meses":
            m = extraer_meses(text)
            if m:
                conversaciones[phone]["meses"] = m
                conversaciones[phone]["estado"] = "confirmacion_compra"
                conversaciones[phone]["msgs_mismo_estado"] = 0
                enviar_botones(phone,
                    "¡Excelente elección! Vas a activar *Game Pass Ultimate por " + m + "* 🎮\n\n"
                    "📅 *Duración:* " + m + "\n"
                    "💰 *Valor:* $" + str(PRECIOS_GAMEPASS.get(m, "")) + " COP\n"
                    "🛡️ *Garantía:* Primero pruebas tu cuenta y luego pagas.\n\n"
                    "¿Confirmas para que pasemos a la entrega?",
                    [
                        {"id": "confirmar_compra", "titulo": "✅ Sí, confirmo"},
                        {"id": "cancelar_compra", "titulo": "❌ Cambiar / Cancelar"}
                    ]
                )
            else:
                # Si el cliente preguntó algo en vez de dar los meses, la IA le responde y le vuelve a mostrar los planes
                responder_con_ia(phone, text,
                    "El cliente está en el paso de elegir cuántos meses de Game Pass quiere. "
                    "Responde su duda con amabilidad en un párrafo corto y anímalo a tocar uno de los botones de abajo para elegir su plan.")
                enviar_pregunta_meses(phone, "Aquí te dejo los planes para cuando quieras elegir 👇")
            return jsonify({"status": "ok"}), 200

        # ── PASO: CONFIRMACIÓN DE COMPRA (con soporte conversacional) ────────
        if estado == "confirmacion_compra":
            m_nuevo = extraer_meses(text)
            if m_nuevo and m_nuevo != conversaciones[phone].get("meses"):
                conversaciones[phone]["meses"] = m_nuevo
                enviar_botones(phone,
                    "¡Listo, actualizado a *" + m_nuevo + "*! ($" + str(PRECIOS_GAMEPASS.get(m_nuevo, "")) + " COP) 🎮\n\n"
                    "¿Confirmas este plan?",
                    [
                        {"id": "confirmar_compra", "titulo": "✅ Sí, confirmo"},
                        {"id": "cancelar_compra", "titulo": "❌ Cancelar"}
                    ]
                )
                return jsonify({"status": "ok"}), 200

            if text_lower in ("confirmar_compra", "si", "sí", "yes", "confirmo", "dale", "de una", "claro", "ok"):
                conversaciones[phone]["estado"] = "preguntar_consola"
                conversaciones[phone]["msgs_mismo_estado"] = 0
                enviar_pregunta_consola(phone)
            elif text_lower in ("cancelar_compra", "no", "cancelar"):
                conversaciones[phone]["estado"] = "menu"
                send_message(phone, "¡Tranquilo, no hay problema! Si quieres mirar otro plan o tienes alguna pregunta, aquí estoy 😊")
                enviar_menu_principal(phone)
            else:
                responder_con_ia(phone, text,
                    "El cliente está a punto de confirmar su plan de Game Pass Ultimate (" + str(conversaciones[phone].get("meses", "")) + "). "
                    "Resuelve su inquietud de forma breve y cálida, y dile que puede tocar 'Sí, confirmo' abajo para continuar.")
                enviar_botones(phone,
                    "¿Continuamos con tu plan de *Game Pass Ultimate - " + str(conversaciones[phone].get("meses", "")) + "*?",
                    [
                        {"id": "confirmar_compra", "titulo": "✅ Sí, confirmo"},
                        {"id": "cancelar_compra", "titulo": "❌ Cancelar"}
                    ]
                )
            return jsonify({"status": "ok"}), 200

        # ── PASO: ¿TIENE CONSOLA/PC DISPONIBLE? ──────────────────────────────
        if estado == "preguntar_consola":
            meses = conversaciones[phone].get("meses", "1 mes")

            es_si_consola = text == "consola_si" or text_lower in (
                "si", "sí", "ya", "si la tengo", "sí la tengo", "aqui estoy", "aquí estoy", "lista", "de una"
            )
            es_no_consola = text == "consola_no" or text_lower in (
                "no", "no la tengo", "mas tarde", "más tarde", "ahorita no", "luego", "estoy trabajando", "no estoy en casa"
            )

            if es_si_consola:
                asignacion = asignar_cuenta(phone)
                if not asignacion:
                    send_message(phone,
                        "¡Uy, qué pena contigo! Justo en este instante se nos agotaron las cuentas preparadas para entrega inmediata 😔\n\n"
                        "Ya mismo le avisé a nuestro asesor principal para que te aliste una y te escriba en unos minutos."
                    )
                    send_message(ADMIN_PHONE,
                        "🚨 Sin stock - Game Line Col\nCliente " + cli(phone) +
                        " quiso contratar " + meses + " pero no hay cuentas disponibles."
                    )
                    conversaciones[phone]["estado"] = "menu"
                    return jsonify({"status": "ok"}), 200

                email_asig, password_asig, tipo_asig = asignacion
                conversaciones[phone]["tipo_cuenta"] = tipo_asig
                conversaciones[phone]["email_cuenta"] = email_asig
                conversaciones[phone]["estado"] = "esperando_pago_final"
                conversaciones[phone]["msgs_mismo_estado"] = 0

                config_asig = CONFIG_PRINCIPAL if tipo_asig == "Principal" else CONFIG_SECUNDARIA

                try:
                    monto_asig = PRECIOS_GAMEPASS.get(meses)
                    link_asig, ref_asig = (None, None)
                    if monto_asig:
                        link_asig, ref_asig = crear_link_pago(phone, "Game Pass Ultimate " + tipo_asig + " - " + meses, monto_asig)
                    if ref_asig:
                        conversaciones[phone]["referencia_pago"] = ref_asig
                        conversaciones[phone]["tipo_pago_pendiente"] = "final"
                except Exception as e:
                    print("Error creando link de pago: " + str(e))
                    link_asig = None

                send_message(phone,
                    "🎉 ¡Listo! Aquí tienes tu cuenta de *Game Pass Ultimate* para que la ingreses y la pruebes:\n\n"
                    "📅 *Plan:* " + meses + " (Modalidad " + tipo_asig + ")\n"
                    "📧 *Correo:* " + email_asig + "\n"
                    "🔒 *Contraseña:* " + password_asig
                )
                send_message(phone,
                    "1️⃣ *CÓMO AGREGARLA EN TU CONSOLA:*\n\n"
                    "• Ve a *Agregar nuevo* (agregar cuenta)\n"
                    "• Escribe el *correo y contraseña* que te acabo de enviar\n"
                    "• Sigue exactamente los pasos de abajo 👇"
                )
                send_message(phone, config_asig)
                send_message(phone,
                    "2️⃣ *CUANDO VERIFIQUES QUE TODO ESTÁ LISTO, REALIZA EL PAGO:*\n\n" + mensaje_opciones_pago(link_asig)
                )

                send_message(ADMIN_PHONE,
                    "🎮 NUEVA ASIGNACION (En prueba, pendiente de pago)\n"
                    "Cliente: " + cli(phone) + "\nPlan: " + meses + " - Cuenta " + tipo_asig +
                    "\n\n📧 Email: " + email_asig +
                    "\n🔒 Contraseña: " + password_asig +
                    "\n\nVerifica que esta cuenta ya este canjeada (con Game Pass activo) antes de que el cliente la use."
                )

            elif es_no_consola:
                monto_rsv = PRECIOS_GAMEPASS.get(meses)
                link_rsv, ref_rsv = (None, None)
                try:
                    if monto_rsv:
                        link_rsv, ref_rsv = crear_link_pago(phone, "Reserva Game Pass Ultimate - " + meses, monto_rsv)
                except Exception as e:
                    print("Error creando link de reserva: " + str(e))
                if ref_rsv:
                    conversaciones[phone]["referencia_pago"] = ref_rsv
                    conversaciones[phone]["tipo_pago_pendiente"] = "reserva"
                conversaciones[phone]["estado"] = "esperando_comprobante"
                conversaciones[phone]["msgs_mismo_estado"] = 0

                send_message(phone,
                    "¡Dale, no te preocupes! 🎮 Si quieres, puedes dejar apartado tu cupo de *" + meses + "* de una vez.\n\n"
                    "Así apenas llegues a tu consola o PC me avisas por aquí y te entrego tu cuenta al instante:\n\n"
                    + mensaje_opciones_pago(link_rsv)
                )
                send_message(ADMIN_PHONE,
                    "📌 RESERVA Game Line Col\nCliente: " + cli(phone) + "\nPlan: " + meses +
                    "\n\nEl cliente aun no tiene consola/PC disponible. Pago pendiente para apartar el cupo."
                )
            else:
                responder_con_ia(phone, text,
                    "Estamos preguntándole al cliente si tiene su consola o PC disponible en este momento para entregarle la cuenta de una vez. "
                    "Responde su pregunta con calidez y pídele que nos indique con los botones de abajo si ya tiene la consola a la mano o no.")
                enviar_pregunta_consola(phone)
            return jsonify({"status": "ok"}), 200

        # ── RENOVACIÓN ───────────────────────────────────────────────────────
        if estado == "renovacion_pendiente":
            if text in ("renovar_si", "si", "sí", "yes", "quiero", "dale", "claro"):
                tipo_rv = renovaciones.get(phone, {}).get("tipo_cuenta", "No especificado")
                meses_rv = renovaciones.get(phone, {}).get("meses", "No especificado")
                conversaciones[phone]["estado"] = "renovacion_espera_admin"
                send_message(phone, "¡Excelente! Dame un minutico mientras verifico tu cuenta para darte los datos de renovación 🎮")
                send_message(ADMIN_PHONE,
                    "🔄 RENOVACION Game Line Col\nCliente: " + cli(phone) +
                    "\nServicio actual: " + tipo_rv + " - " + meses_rv +
                    "\n\nResponde:\n✅ misma " + phone[-4:] + " → Misma cuenta\n🔄 cambia " + phone[-4:] + " → Cambiar cuenta"
                )
            elif text in ("renovar_no", "no"):
                conversaciones[phone]["estado"] = "menu"
                send_message(phone, "¡Listo, entendido! Si más adelante te animas a renovar, por aquí estaremos a la orden 🎮")
            else:
                responder_con_ia(phone, text, "El cliente tiene su suscripción vencida hoy y le preguntamos si desea renovar.")
                enviar_botones(phone, "¿Deseas renovar tu Game Pass?", [
                    {"id": "renovar_si", "titulo": "✅ Sí, quiero renovar"},
                    {"id": "renovar_no", "titulo": "No por ahora"}
                ])
            return jsonify({"status": "ok"}), 200

        if estado == "renovacion_espera_admin":
            send_message(phone, "Ya le pasé tu solicitud de renovación al asesor para verificar tu cuenta, en un momento te respondemos 🙏")
            return jsonify({"status": "ok"}), 200

        if estado == "renovacion_espera_tiempo":
            minutos = parsear_tiempo_minutos(text)
            conversaciones[phone]["renovacion_recordatorio_at"] = time.time() + (minutos * 60)
            conversaciones[phone]["renovacion_recordatorio_enviado"] = False
            conversaciones[phone]["estado"] = "renovacion_espera_pago"
            send_message(phone,
                "¡Perfecto! Te agendo un recordatorio en " + str(minutos) + " minutos ⏰\n\n"
                "Apenas realices el pago, envíame la foto del comprobante por aquí mismo 📸"
            )
            return jsonify({"status": "ok"}), 200

        # ── ESPERA DE COMPROBANTE (Conversacional + Exige foto) ──────────────
        if estado in ("renovacion_espera_pago", "renovacion_comprobante_enviado",
                      "esperando_pago_final", "pago_final_enviado",
                      "esperando_comprobante", "comprobante_reserva_enviado"):
            if text_lower.strip() in CONFIRMACIONES_PAGO:
                send_message(phone,
                    "🙌 ¡Genial! Para dejar tu pago registrado en el sistema, por favor *adjunta y envía aquí mismo la foto o captura del comprobante* 📸\n\n"
                    "(Si pagaste directamente desde el botón de Mercado Pago, se confirma solo en unos segundos)."
                )
            elif len(text.split()) >= 3 or "?" in text:
                responder_con_ia(phone, text,
                    "El cliente tiene pendiente enviarnos la foto del comprobante de pago (o tiene una duda mientras configura la cuenta o realiza el pago). "
                    "Ayúdalo de forma muy amable con su duda y recuérdale al final que cuando haga el pago nos envíe la foto del comprobante por aquí.")
            else:
                send_message(phone, RECORDAR_COMPROBANTE_ADMIN)
            return jsonify({"status": "ok"}), 200

        # ── CLIENTE CON RESERVA PAGADA CONFIRMA QUE YA TIENE CONSOLA/PC ─────
        if estado == "esperando_consola":
            confirmaciones_consola = (
                "consola_lista", "si", "sí", "ya", "yes", "listo", "lista",
                "ya tengo", "ya la tengo", "tengo consola", "ya llegue", "ya llegué", "ya estoy"
            )
            if text_lower in confirmaciones_consola or "ya tengo" in text_lower or "ya estoy" in text_lower:
                meses = conversaciones[phone].get("meses", "1 mes")
                asignacion = asignar_cuenta(phone)
                if not asignacion:
                    send_message(phone,
                        "¡Uy! Justo ahorita estamos terminando de alistar las cuentas nuevas 😔\n\n"
                        "Ya mismo le avisé con prioridad a nuestro asesor para entregarte tu reserva en unos minutos."
                    )
                    send_message(ADMIN_PHONE,
                        "🚨 URGENTE - Sin stock para reserva ya pagada\nCliente " + cli(phone) +
                        " confirmo que ya tiene consola (" + meses + ") pero no hay cuentas disponibles. Resolver manualmente."
                    )
                    return jsonify({"status": "ok"}), 200

                email_asig, password_asig, tipo_asig = asignacion
                conversaciones[phone]["tipo_cuenta"] = tipo_asig
                conversaciones[phone]["email_cuenta"] = email_asig
                conversaciones[phone]["estado"] = "pago_confirmado"
                conversaciones[phone]["compro"] = True

                config_asig = CONFIG_PRINCIPAL if tipo_asig == "Principal" else CONFIG_SECUNDARIA

                send_message(phone,
                    "🎉 ¡Excelente! Aquí tienes tu cuenta de *Game Pass Ultimate* lista para usar:\n\n"
                    "📅 *Plan:* " + meses + " - Cuenta " + tipo_asig + "\n\n"
                    "📧 *Email:* " + email_asig + "\n"
                    "🔒 *Contraseña:* " + password_asig
                )
                send_message(phone,
                    "PASO 1 - AGREGAR LA CUENTA EN TU CONSOLA:\n\n"
                    "1️⃣ Ve a *Agregar nueva cuenta*\n"
                    "2️⃣ Ingresa el *email y contraseña* de arriba\n"
                    "3️⃣ Sigue la configuración a continuación 👇"
                )
                send_message(phone, "CONFIGURACION DE TU CUENTA:\n\n" + config_asig)
                send_message(phone, CIERRE)

                send_message(ADMIN_PHONE,
                    "🎮 RESERVA ENTREGADA Game Line Col\n"
                    "Cliente: " + cli(phone) + "\nPlan: " + meses + " - Cuenta " + tipo_asig +
                    "\n\n📧 Email: " + email_asig +
                    "\n🔒 Contraseña: " + password_asig +
                    "\n\nYa estaba pagada. Verifica que la cuenta este canjeada antes de que el cliente la use."
                )

                registrar_compra(phone, tipo_asig, meses, email_asig)
                registrar_evento_diario("cierres")
            else:
                responder_con_ia(phone, text,
                    "El cliente ya tiene una reserva pagada y estamos esperando a que esté frente a su consola para entregarle la cuenta. "
                    "Responde su mensaje amablemente y recuérdale tocar el botón '🎮 Ya tengo mi consola' cuando esté listo.")
                enviar_boton_consola_lista(phone, "Toca aquí apenas estés en tu consola o PC 👇")
            return jsonify({"status": "ok"}), 200

        # ── OPCIONES DEL MENÚ PRINCIPAL ──────────────────────────────────────
        if text == "1" or (text_lower in ("game pass", "gamepass", "game pass ultimate") and estado == "menu"):
            conversaciones[phone]["estado"] = "gamepass"
            send_message(phone, GAMEPASS)
            enviar_pregunta_contratar(phone)
            return jsonify({"status": "ok"}), 200

        if text == "2" or text_lower in ["juegos", "juego", "catalogo", "catálogo"]:
            conversaciones[phone]["estado"] = "juegos"
            if catalogo_media_id:
                enviar_documento(phone, catalogo_media_id, "🎮 Catálogo Oficial Game Line Col - Juegos Xbox")
                send_message(phone,
                    "¡Aquí tienes nuestro catálogo actualizado con los juegos y sus precios! 👆\n\n"
                    "Si buscas alguno en especial o quieres que te cotice tu juego favorito en Principal, Secundaria o Código, dime cómo se llama 😊"
                )
            else:
                send_message(phone, JUEGOS)
            return jsonify({"status": "ok"}), 200

        # ── SOPORTE: botones sop_* ───────────────────────────────────────────
        SOP_IDS = {"sop_online", "sop_online_p", "sop_online_s",
                   "sop_online_p_marcada", "sop_online_p_no_marcada",
                   "sop_online_p3", "sop_online_p4", "sop_jugando", "sop_jugando_p",
                   "sop_jugando_s", "sop_password", "sop_resuelto", "sop_asesor"}

        if estado in ("soporte_menu", "sop_online", "sop_online_p1", "sop_online_p2",
                      "sop_online_p3", "sop_online_p4", "sop_online_s1",
                      "sop_jugando", "sop_jugando_p1", "sop_jugando_s1") or text in SOP_IDS:
            if text not in SOP_IDS:
                tl = text_lower
                if any(p in tl for p in ["online", "internet", "multijugador", "jugar"]):
                    text = "sop_online"
                elif any(p in tl for p in ["alguien", "jugando", "otro", "ocupada"]):
                    text = "sop_jugando"
                elif any(p in tl for p in ["contrasena", "contraseña", "password", "clave"]):
                    text = "sop_password"
                elif any(p in tl for p in ["principal"]):
                    if estado == "sop_online":
                        text = "sop_online_p"
                    elif estado == "sop_jugando":
                        text = "sop_jugando_p"
                elif any(p in tl for p in ["secundaria"]):
                    if estado == "sop_online":
                        text = "sop_online_s"
                    elif estado == "sop_jugando":
                        text = "sop_jugando_s"
                elif any(p in tl for p in ["marcada", "si", "sí", "yes"]):
                    if estado == "sop_online_p1":
                        text = "sop_online_p_marcada"
                elif any(p in tl for p in ["no", "desmarcada", "sin marcar"]):
                    if estado == "sop_online_p1":
                        text = "sop_online_p_no_marcada"
                elif any(p in tl for p in ["sigue", "error", "persiste", "todavia", "todavía",
                                           "tampoco", "nada", "no funciona", "no sirve",
                                           "no me funciona", "igual", "sigue igual"]):
                    if estado == "sop_online_p2":
                        text = "sop_online_p3"
                    elif estado == "sop_online_p3":
                        text = "sop_online_p4"
                    else:
                        text = "sop_asesor"
                elif any(p in tl for p in ["funciona", "listo", "ya", "bien", "ok", "resuelto"]):
                    text = "sop_resuelto"

            if text == "sop_online":
                conversaciones[phone]["estado"] = "sop_online"
                enviar_botones(phone, "¡Claro! Vamos a dejar funcionando tu online. Primero dime: ¿qué modalidad de cuenta tienes?", [
                    {"id": "sop_online_p", "titulo": "🏠 Cuenta Principal"},
                    {"id": "sop_online_s", "titulo": "👤 Cuenta Secundaria"}
                ])
                return jsonify({"status": "ok"}), 200

            if text == "sop_online_p":
                conversaciones[phone]["estado"] = "sop_online_p1"
                send_message(phone,
                    "Perfecto, revisemos la configuración de tu *Xbox Principal* 🎮\n\n"
                    "Entra por favor *desde la cuenta de Game Pass* (no desde tu perfil personal) y sigue esta ruta:\n\n"
                    "1️⃣ *Configuración*\n"
                    "2️⃣ *General > Personalización*\n"
                    "3️⃣ *Mi Xbox principal*\n\n"
                    "Cuéntame: ¿la casilla de *Hacer de esta mi Xbox principal* aparece marcada o desmarcada?"
                )
                enviar_botones(phone, "Estado de la casilla Xbox Principal:", [
                    {"id": "sop_online_p_marcada", "titulo": "✅ Sí, está marcada"},
                    {"id": "sop_online_p_no_marcada", "titulo": "⬜ No está marcada"}
                ])
                return jsonify({"status": "ok"}), 200

            if text in ("sop_online_p_marcada", "sop_online_p_no_marcada"):
                conversaciones[phone]["estado"] = "sop_online_p2"
                instruccion = ("Como ya está marcada, vamos a refrescar el permiso:\n\n✅ *Desmarca* la casilla\n✅ *Vuelve a marcarla* enseguida"
                               if text == "sop_online_p_marcada"
                               else "Ahí estaba el detalle. Haz lo siguiente:\n\n✅ *Marca* la casilla de Mi Xbox Principal")
                send_message(phone,
                    instruccion + "\n\n"
                    "Y luego:\n"
                    "🔄 *Reinicia tu consola*\n"
                    "⚠️ Al encender, entra *únicamente* a tu perfil personal (cierra la de Game Pass).\n\n"
                    "Haz la prueba en el juego y dime cómo te fue:"
                )
                enviar_botones(phone, "¿Ya te dejó jugar online?", [
                    {"id": "sop_resuelto", "titulo": "🎉 ¡Ya funciona!"},
                    {"id": "sop_online_p3", "titulo": "Sigue el error"}
                ])
                return jsonify({"status": "ok"}), 200

            if text == "sop_online_p3":
                conversaciones[phone]["estado"] = "sop_online_p3"
                send_message(phone,
                    "Dale, hagamos el segundo ajuste que suele destrabar el permiso en los servidores de Xbox. *Desde la cuenta de Game Pass*:\n\n"
                    "1️⃣ Ve a *Configuración > Cuenta*\n"
                    "2️⃣ Entra a *Pago y facturación > Suscripciones*\n"
                    "3️⃣ Entra a la suscripción y busca *Facturación periódica*\n"
                    "4️⃣ Si aparece *Activa*, desactívala (o si está inactiva, actívala)\n"
                    "5️⃣ Repite ese cambio *2 veces seguidas*\n\n"
                    "Después:\n"
                    "🔄 *Reinicia la consola*\n"
                    "⚠️ Al encender, inicia sesión *solo* con tu cuenta personal.\n\n"
                    "Cuéntame cómo te va con este paso:"
                )
                enviar_botones(phone, "Resultado:", [
                    {"id": "sop_resuelto", "titulo": "🎉 ¡Ya funciona!"},
                    {"id": "sop_online_p4", "titulo": "Sigue el error"}
                ])
                return jsonify({"status": "ok"}), 200

            if text == "sop_online_p4":
                conversaciones[phone]["estado"] = "sop_online_p4"
                send_message(phone,
                    "Tranquilo, ya sabemos exactamente qué pasa 🙌\n\n"
                    "Tu suscripción está activa de nuestro lado, pero la caché de red de tu consola se quedó pegada. "
                    "Vamos a limpiarla con estos 6 pasos en orden y verás que queda al pelo 👇"
                )
                send_message(phone,
                    "🎮 *REINICIO DE CACHÉ ONLINE EN XBOX*\n\n"
                    "1️⃣ Inicia sesión con la *cuenta de Game Pass Ultimate*.\n\n"
                    "2️⃣ Ve a *Configuración > General > Personalización > Mi Xbox principal*.\n\n"
                    "3️⃣ *Desmarca* \"Mi Xbox principal\", confirma y *reinicia la consola*.\n\n"
                    "4️⃣ Al encender, entra otra vez a la *cuenta de Game Pass Ultimate*, vuelve a "
                    "*Mi Xbox principal* y *márcala* nuevamente ✅.\n\n"
                    "5️⃣ Ahora ve a *Configuración > General > Configuración de red > Configuración avanzada > Dirección MAC alternativa*. "
                    "Elige *Borrar* y deja que la consola se reinicie.\n\n"
                    "6️⃣ Cuando prenda, entra *únicamente a tu cuenta personal* (sin abrir la de Game Pass) y prueba el online 🚀"
                )
                enviar_botones(phone, "¿Cómo te fue con este paso?", [
                    {"id": "sop_resuelto", "titulo": "🎉 ¡Ya funciona!"},
                    {"id": "sop_asesor", "titulo": "Sigue el error"}
                ])
                return jsonify({"status": "ok"}), 200

            if text == "sop_online_s":
                conversaciones[phone]["estado"] = "sop_online_s1"
                send_message(phone,
                    "Para cuenta *Secundaria* vamos a refrescar la sincronización. "
                    "Entra *desde la cuenta de Game Pass*:\n\n"
                    "1️⃣ Ve a *Configuración > Cuenta*\n"
                    "2️⃣ Entra a *Pago y facturación > Suscripciones*\n"
                    "3️⃣ Busca *Facturación periódica*\n"
                    "4️⃣ Si está *Activa*, desactívala (y si está inactiva, actívala)\n"
                    "5️⃣ Repite ese cambio *2 veces*\n\n"
                    "Luego:\n"
                    "🔄 *Reinicia la consola*\n"
                    "👤 Recuerda iniciar sesión en la cuenta del servicio y luego cambiar a tu perfil personal para jugar.\n\n"
                    "Cuéntame cómo te fue:"
                )
                enviar_botones(phone, "Resultado:", [
                    {"id": "sop_resuelto", "titulo": "🎉 ¡Ya funciona!"},
                    {"id": "sop_asesor", "titulo": "Sigue el error"}
                ])
                return jsonify({"status": "ok"}), 200

            if text == "sop_jugando":
                conversaciones[phone]["estado"] = "sop_jugando"
                enviar_botones(phone, "¡Claro! Eso tiene solución rápida. ¿Qué tipo de cuenta tienes asignada?", [
                    {"id": "sop_jugando_p", "titulo": "🏠 Cuenta Principal"},
                    {"id": "sop_jugando_s", "titulo": "👤 Cuenta Secundaria"}
                ])
                return jsonify({"status": "ok"}), 200

            if text == "sop_jugando_p":
                conversaciones[phone]["estado"] = "sop_jugando_p1"
                send_message(phone,
                    "¡Ah, súper fácil! En *Cuenta Principal* te sale ese aviso porque tienes abierta la sesión de la cuenta de Game Pass en segundo plano 🎮\n\n"
                    "Haz solo esto:\n"
                    "1️⃣ Presiona el botón Xbox del control y selecciona la cuenta de Game Pass\n"
                    "2️⃣ Dale en *Cerrar sesión* a esa cuenta\n"
                    "3️⃣ Quédate *únicamente* con tu cuenta personal abierta\n\n"
                    "Como ya está configurada como Principal, no necesitas volver a abrir la otra cuenta para nada. ¡Pruébalo y me cuentas!"
                )
                enviar_botones(phone, "Resultado:", [
                    {"id": "sop_resuelto", "titulo": "🎉 ¡Ya funciona!"},
                    {"id": "sop_asesor", "titulo": "Sigue el error"}
                ])
                return jsonify({"status": "ok"}), 200

            if text == "sop_jugando_s":
                conversaciones[phone]["estado"] = "sop_jugando_s1"
                send_message(phone,
                    "Para solucionarlo en tu cuenta, entra *desde el perfil de Game Pass* y haz este ajuste:\n\n"
                    "1️⃣ Ve a *Configuración*\n"
                    "2️⃣ Luego a *Personalización*\n"
                    "3️⃣ Entra a *Mi Xbox principal*\n"
                    "4️⃣ *Marca* la casilla de hacer Xbox Principal ✅\n\n"
                    "Luego:\n"
                    "🔄 *Reinicia la consola*\n"
                    "⚠️ Al encender, abre *únicamente* tu perfil personal.\n\n"
                    "Pruébalo y dime cómo te fue:"
                )
                enviar_botones(phone, "Resultado:", [
                    {"id": "sop_resuelto", "titulo": "🎉 ¡Ya funciona!"},
                    {"id": "sop_asesor", "titulo": "Sigue el error"}
                ])
                return jsonify({"status": "ok"}), 200

            if text in ("sop_password", "sop_asesor"):
                estado_previo_sop = conversaciones[phone].get("estado", "desconocido")
                conversaciones[phone]["estado"] = "soporte_asesor"
                send_message(phone, "¡Listo! Ya mismo le acabo de pasar el reporte a nuestro asesor técnico para que revise tu cuenta personalmente 🙏\n\nEn breve te escribimos.")
                alerta = ("🛠️ SOPORTE - ESCALADO AL ASESOR\nCliente: " + cli(phone) +
                          "\nPaso de soporte previo: " + estado_previo_sop +
                          "\nMotivo/Boton: " + text +
                          "\nContactalo para ayudarlo manualmente.")
                send_message(ADMIN_PHONE, alerta)
                return jsonify({"status": "ok"}), 200

            if text == "sop_resuelto":
                conversaciones[phone]["estado"] = "menu"
                send_message(phone, "🎉 ¡Qué buena noticia! Me alegra mucho que ya haya quedado al 100% 🎮🙌\n\nA disfrutar el juego. Si necesitas algo más, escribe *hola* cuando quieras.")
                return jsonify({"status": "ok"}), 200

            enviar_botones(phone, "Cuéntame cuál de estos inconvenientes estás presentando para guiarte:", [
                {"id": "sop_online", "titulo": "🌐 Online no funciona"},
                {"id": "sop_jugando", "titulo": "👥 Dice 'Alguien más jugando'"},
                {"id": "sop_password", "titulo": "🔑 Pide contraseña / Otro"}
            ])
            return jsonify({"status": "ok"}), 200

        # ── Menú principal soporte (opcion 3 o texto "soporte") ─────────────
        if text == "3" or text_lower in ("soporte", "soporte tecnico", "soporte técnico"):
            conversaciones[phone]["estado"] = "soporte_menu"
            enviar_botones(phone, "🛠️ *Soporte Técnico Game Line Col*\n\nCuéntame, ¿con cuál de estos temas necesitas ayuda hoy?", [
                {"id": "sop_online", "titulo": "🌐 Online no funciona"},
                {"id": "sop_jugando", "titulo": "👥 Dice 'Alguien más jugando'"},
                {"id": "sop_password", "titulo": "🔑 Pide contraseña / Otro"}
            ])
            return jsonify({"status": "ok"}), 200

        # Si está viendo Game Pass y dice que sí o menciona directamente los meses
        if estado == "gamepass":
            m_directo = extraer_meses(text)
            if m_directo:
                conversaciones[phone]["meses"] = m_directo
                conversaciones[phone]["estado"] = "confirmacion_compra"
                enviar_botones(phone,
                    "¡De una! Vas a activar *Game Pass Ultimate por " + m_directo + "* 🎮\n\n"
                    "📅 *Duración:* " + m_directo + "\n"
                    "💰 *Valor:* $" + str(PRECIOS_GAMEPASS.get(m_directo, "")) + " COP\n"
                    "🛡️ *Garantía:* Primero pruebas tu cuenta y luego pagas.\n\n"
                    "¿Confirmas tu pedido?",
                    [
                        {"id": "confirmar_compra", "titulo": "✅ Sí, confirmo"},
                        {"id": "cancelar_compra", "titulo": "❌ Cancelar"}
                    ]
                )
                return jsonify({"status": "ok"}), 200
            if text_lower in ["si", "sí", "yes", "quiero", "dale", "listo", "de una", "claro"]:
                conversaciones[phone]["estado"] = "seleccion_meses"
                enviar_pregunta_meses(phone)
                return jsonify({"status": "ok"}), 200

        if es_agradecimiento(text) and len(text.split()) <= 4:
            send_message(phone, "🎮 ¡Con todo el gusto! Cualquier cosa que necesites, por aquí estoy atento 😊")
            return jsonify({"status": "ok"}), 200

        # ── RESPUESTA ABIERTA Y ELOCUENTE CON GEMINI ─────────────────────────
        responder_con_ia(phone, text)
        if estado == "gamepass":
            enviar_pregunta_contratar(phone)

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
                        conversaciones[phone_pagador]["estado"] = "esperando_consola"
                        conversaciones[phone_pagador]["reserva_pagada"] = True
                        conversaciones[phone_pagador]["compro"] = True
                        conversaciones[phone_pagador]["recordatorio_consola_at"] = time.time() + HORA_RECORDATORIO_CONSOLA
                        enviar_boton_consola_lista(phone_pagador,
                            "✅ ¡Pago de tu reserva confirmado automáticamente!\n\n"
                            "Apenas tengas tu consola o PC disponible, avísame por aquí para entregarte tu cuenta al instante 🎮"
                        )
                        etiqueta_mp = "RESERVA"
                        registrar_evento_diario("reservas")
                    else:
                        conversaciones[phone_pagador]["compro"] = True
                        conversaciones[phone_pagador]["estado"] = "pago_confirmado"
                        send_message(phone_pagador, CIERRE)
                        etiqueta_mp = "ACTIVACION FINAL"
                        tipo_cuenta_mp = datos_cliente.get("tipo_cuenta", "No especificado")
                        meses_mp = datos_cliente.get("meses", "No especificado")
                        registrar_compra(phone_pagador, tipo_cuenta_mp, meses_mp, datos_cliente.get("email_cuenta", ""))
                        registrar_evento_diario("cierres")

                    send_message(ADMIN_PHONE, "✅ Pago confirmado automaticamente por Mercado Pago (" + etiqueta_mp + ")\nCliente: " + cli(phone_pagador) + "\nMonto: $" + str(monto_pagado))
    except Exception as e:
        print("Error webhook Mercado Pago: " + str(e))
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
