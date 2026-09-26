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
from flask import Flask, request, jsonify
from google import genai
from google.genai import types
from google.oauth2 import service_account
from googleapiclient.discovery import build

app = Flask(__name__)

# ── Telegram ────────────────────────────────────────────────────────────────
# Token que da @BotFather al crear el bot.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_API = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN
# Chat ID de Telegram del administrador. Para saberlo, escribele /miid al bot.
ADMIN_PHONE = str(os.environ.get("ADMIN_CHAT_ID", "")).strip()
# Contacto del asesor que se les muestra a los clientes (WhatsApp personal, @usuario...).
ADMIN_PHONE_DISPLAY = os.environ.get("ADMIN_CONTACTO", "+57 322 908 2927")
# Dominio publico del bot. Railway lo pone solo en RAILWAY_PUBLIC_DOMAIN.
PUBLIC_DOMAIN = os.environ.get("PUBLIC_DOMAIN") or os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
# Clave que Telegram manda en cada webhook para demostrar que viene de Telegram.
# Si no se configura, se deriva del token (no hace falta crear otra variable).
TELEGRAM_SECRET = os.environ.get("TELEGRAM_SECRET") or hashlib.sha256(
    ("gamebot-" + TELEGRAM_BOT_TOKEN).encode("utf-8")).hexdigest()[:40]

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS")
MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN")
MP_NOTIFICATION_URL = os.environ.get("MP_NOTIFICATION_URL") or (
    "https://" + PUBLIC_DOMAIN + "/mercadopago-webhook" if PUBLIC_DOMAIN else None)
SHEET_ID = "1lvIlK1LYbT68HsuDTbMRzWSYh_RGUPHAZeV31_sAmdU"
HORA_SEGUIMIENTO = 3600
HORA_RECORDATORIO_CONSOLA = 86400  # 24h para recordarle al cliente que avise cuando tenga consola

# Alerta por correo cuando el token de Telegram deja de funcionar (no se puede
# avisar por Telegram porque justo ese canal es el que fallo).
EMAIL_ADDRESS = os.environ.get("EMAIL_ADDRESS")
EMAIL_APP_PASSWORD = os.environ.get("EMAIL_APP_PASSWORD")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", EMAIL_ADDRESS)
ALERTA_TOKEN_COOLDOWN = 1800  # 30 min entre alertas para no saturar el correo
_ultima_alerta_token = {"ts": 0}

_flood_control = {}  # {chat_id: ultimo_timestamp_procesado}
_sheets_errores_consecutivos = 0

# Clientes bloqueados: el bot ignora todo lo que llegue de ellos y nunca les
# envia mensajes. Se persiste en la hoja "Bloqueados" del Sheet.
bloqueados = {}  # {chat_id: {"motivo": str, "fecha": str}}


def enviar_alerta_email(asunto, cuerpo):
    if not EMAIL_ADDRESS or not EMAIL_APP_PASSWORD or not ADMIN_EMAIL:
        print("⚠️ No se pudo enviar alerta por correo: faltan EMAIL_ADDRESS / EMAIL_APP_PASSWORD / ADMIN_EMAIL en las variables de entorno de Railway.")
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
renovaciones = {}  # {phone: {vencimiento, tipo_cuenta, meses, notificado}}
cuentas = []  # lista de dicts con el inventario de cuentas

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
        for i, fila in enumerate(filas[1:], start=2):  # fila 1 es encabezado
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
    """Lee el Sheet en tiempo real y asigna el primer cupo libre de arriba hacia abajo.
    Orden por cuenta: Principal → Secundaria, luego siguiente cuenta."""
    try:
        service = get_sheets_service()
        result = service.spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range="Cuentas!A:F"
        ).execute()
        filas = result.get("values", [])
        if not filas or len(filas) < 2:
            print("Sheet Cuentas vacio o sin datos")
            return None

        for i, fila in enumerate(filas[1:], start=2):  # fila 1 es encabezado
            while len(fila) < 6:
                fila.append("")
            email = fila[0].strip()
            password = fila[1].strip()
            if not email or not password:
                continue
            principal_ocupado = fila[2].strip().upper() == "SI"
            secundaria_ocupada = fila[4].strip().upper() == "SI"

            if not principal_ocupado:
                # Actualizar en Sheet
                service.spreadsheets().values().update(
                    spreadsheetId=SHEET_ID,
                    range="Cuentas!C" + str(i) + ":D" + str(i),
                    valueInputOption="RAW",
                    body={"values": [["SI", phone]]}
                ).execute()
                # Actualizar en memoria
                for c in cuentas:
                    if c["fila"] == i:
                        c["principal_ocupado"] = True
                        c["cliente_principal"] = phone
                verificar_stock_bajo()
                return email, password, "Principal"

            if not secundaria_ocupada:
                # Actualizar en Sheet
                service.spreadsheets().values().update(
                    spreadsheetId=SHEET_ID,
                    range="Cuentas!E" + str(i) + ":F" + str(i),
                    valueInputOption="RAW",
                    body={"values": [["SI", phone]]}
                ).execute()
                # Actualizar en memoria
                for c in cuentas:
                    if c["fila"] == i:
                        c["secundaria_ocupada"] = True
                        c["cliente_secundaria"] = phone
                verificar_stock_bajo()
                return email, password, "Secundaria"

        return None  # No hay cupos disponibles
    except Exception as e:
        print("Error asignando cuenta: " + str(e))
        return None


def liberar_cuenta(phone):
    """Libera todos los cupos asignados a un cliente (para renovación con cambio de cuenta)."""
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


def verificar_stock_bajo():
    """Avisa al admin si queda solo 1 cupo libre de cualquier tipo."""
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
        return 60
    return 30  # default


_sheets_creds = None


def get_sheets_service():
    # Se crea un servicio NUEVO en cada llamada. Antes se reutilizaba uno solo,
    # y su conexion se "moria" tras horas sin uso (Errno 32 Broken pipe).
    # Ademas, compartirlo entre el scheduler y el webhook no es seguro entre hilos.
    # Las credenciales si se reutilizan, asi que esto es rapido.
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
        pass  # ya existe


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
    # Deja solo los digitos del chat ID de Telegram.
    return re.sub(r"\D", "", str(texto or ""))


def normalizar_numero(texto):
    # Convierte "+57 322 908 2927", "322 908 2927" o "573229082927" a 573229082927.
    # Se usa para reconocer a los clientes que venian de WhatsApp.
    digitos = re.sub(r"\D", "", str(texto or ""))
    if not digitos:
        return ""
    if len(digitos) == 10 and digitos.startswith("3"):
        digitos = "57" + digitos  # celular colombiano sin indicativo
    return digitos


def cli(ph):
    """Etiqueta legible de un cliente para los mensajes al admin."""
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
        return False  # jamas bloqueamos al admin, seria quedarse sin control
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
        "fecha": datetime.now().strftime("%d/%m/%Y %H:%M")
    }
    guardar_bloqueados()

    # Liberar recursos que tuviera ocupados el cliente bloqueado.
    detalles = []
    try:
        tenia_cuenta = any(
            c.get("cliente_principal") == telefono or c.get("cliente_secundaria") == telefono
            for c in cuentas
        )
        if tenia_cuenta:
            liberar_cuenta(telefono)
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
        fecha = datetime.now().strftime("%d/%m/%Y %H:%M")
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
    """Llama a la API de Telegram con reintentos. Devuelve la respuesta o None."""
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
            # Texto con * o _ sueltos (ej. una contraseña): se reenvia sin formato.
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
                break  # chat inexistente o el usuario bloqueo al bot: no sirve reintentar
        except Exception as e:
            print("Error llamando a Telegram " + metodo + " (intento " + str(intento + 1) + "): " + str(e))
        time.sleep(2)
    return None


def _destino_valido(phone):
    if not phone:
        print("⚠️ Mensaje sin destino (¿falta ADMIN_CHAT_ID en Railway?)")
        return False
    return not esta_bloqueado(phone)


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
    return r or send_message(phone, cuerpo)  # respaldo: texto plano


def enviar_lista(phone, cuerpo, texto_boton, filas, titulo_seccion="Opciones", intentos=3):
    # En Telegram la "lista" se muestra como botones, uno por renglon.
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
    """Boton de Telegram para que el cliente comparta su numero (opcional).
    Sirve para reconocer a quienes ya eran clientes por WhatsApp."""
    send_message(phone,
        "📱 Si ya eras cliente nuestro por WhatsApp, toca el boton de abajo para compartir tu numero "
        "y reconocer tu plan. Es opcional.",
        reply_markup={
            "keyboard": [[{"text": "📱 Compartir mi numero", "request_contact": True}]],
            "resize_keyboard": True,
            "one_time_keyboard": True
        }
    )


def responder_callback(callback_id):
    # Quita el "cargando..." del boton que toco el cliente.
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
            conversaciones[phone]["pago_mp_confirmado"] = False  # link nuevo = pago nuevo
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
              "📸 IMPORTANTE: si pagas por Nequi, Daviplata o Llave, envia *aqui mismo* "
              "la foto del comprobante para continuar 👍\n\n"
              "Si usas el link de Mercado Pago, lo confirmamos automaticamente y no necesitas enviar nada.")
    return texto


# Mensaje corto reutilizable para recordarle al cliente a donde va el comprobante.
RECORDAR_COMPROBANTE_ADMIN = (
    "📸 Cuando hayas pagado, envia *aqui mismo* la foto del comprobante para continuar 👍\n\n"
    "Si necesitas otra cosa, escribe *menu*. Si quieres hablar con una persona, escribe *asesor*."
)

# Frases con las que el cliente nos avisa que ya pago y ya mando el comprobante.
CONFIRMACIONES_PAGO = (
    "listo", "listo!", "ya", "ya envie", "ya envié", "ya lo envie", "ya lo envié",
    "ya te envie", "ya te envié", "ya lo mande", "ya lo mandé", "ya mande", "ya mandé",
    "enviado", "ya pague", "ya pagué", "ya lo pague", "ya lo pagué", "pagado",
    "ya esta", "ya está", "hecho", "ok listo"
)


def avanzar_tras_pago(phone, estado):
    """El cliente avisa que ya pago y que ya envio el comprobante al asesor.
    El bot avanza el flujo solo (ya no se espera el comando pagook/reservaok).
    Devuelve True si efectivamente avanzo el estado."""

    # ── Reserva pagada: pasa a esperar que tenga la consola disponible ────────
    if estado in ("esperando_comprobante", "comprobante_reserva_enviado"):
        conversaciones[phone]["estado"] = "esperando_consola"
        conversaciones[phone]["reserva_pagada"] = True
        conversaciones[phone]["compro"] = True
        conversaciones[phone]["recordatorio_consola_at"] = time.time() + HORA_RECORDATORIO_CONSOLA
        registrar_evento_diario("reservas")
        enviar_boton_consola_lista(phone,
            "✅ Perfecto, gracias!\n\n"
            "Cuando tengas tu consola o PC disponible, avisanos aqui para entregarte tu cuenta al instante 🎮"
        )
        send_message(ADMIN_PHONE,
            "💰 RESERVA - El cliente " + cli(phone) + " avisa que ya pago.\n\n"
            "Plan: " + str(conversaciones[phone].get("meses")) + "\n\n"
            "⚠️ Verifica el pago (te reenvie la foto si la mando). El bot ya lo dejo esperando consola.\n"
            "Si el pago NO llego: *anular " + phone[-4:] + "*"
        )
        return True

    # ── Pago final o renovacion: se cierra la venta ───────────────────────────
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
            "💰 " + etiqueta + " - El cliente " + cli(phone) + " avisa que ya pago.\n\n"
            "Plan: " + str(meses_c) + " - " + str(tipo_cuenta_c) + "\n\n"
            "⚠️ Verifica el pago (te reenvie la foto si la mando). El bot ya registro la compra y cerro la venta.\n"
            "Si el pago NO llego: *anular " + phone[-4:] + "*"
        )
        return True

    return False


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


ESTADOS_RANGE = "Estados_TG!A:B"  # hoja aparte: no se mezcla con los chats de WhatsApp



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


ESTADOS_PENDIENTES = ["activacion", "esperando_comprobante", "comprobante_reserva_enviado",
                      "esperando_consola", "esperando_pago_final", "pago_final_enviado"]

# Estados en los que el cliente NO tiene ningun proceso de compra activo.
# Solo en estos casos un saludo puede reiniciar la conversacion al menu principal.
ESTADOS_SIN_PROCESO_ACTIVO = ["menu", "pago_confirmado"]

estadisticas_diarias = {"fecha": None, "nuevos": 0, "cierres": 0}


def registrar_evento_diario(tipo):
    hoy = datetime.now().strftime("%d/%m/%Y")
    if estadisticas_diarias["fecha"] != hoy:
        estadisticas_diarias["fecha"] = hoy
        estadisticas_diarias["nuevos"] = 0
        estadisticas_diarias["cierres"] = 0
    estadisticas_diarias[tipo] = estadisticas_diarias.get(tipo, 0) + 1


def scheduler():
    ultimo_resumen = None
    ultimo_guardado = 0
    ultima_limpieza = 0

    while True:
        time.sleep(60)
        ahora = time.time()
        hora_actual = datetime.now()

        for phone, datos in list(conversaciones.items()):
            estado = datos.get("estado")
            ultima = datos.get("ultima_interaccion", 0)

            # 1. Recordatorio de seguimiento (1h sin respuesta, no ha comprado)
            if not datos.get("compro") and not datos.get("recordatorio_enviado"):
                if (ahora - ultima) >= HORA_SEGUIMIENTO:
                    msg = ("Hola! Te escribimos desde Game Line Col 🎮\n\n"
                           "Notamos que estuviste interesado en nuestros servicios.\n\n"
                           "En que te podemos ayudar?\n\n1 Game Pass Ultimate\n2 Juegos Xbox\n3 Soporte")
                    send_message(phone, msg)
                    conversaciones[phone]["recordatorio_enviado"] = True

            # 2. Codigo pendiente sin activar (5 min)
            codigo = datos.get("codigo_pendiente")
            codigo_at = datos.get("codigo_pendiente_at")
            if codigo and codigo_at and not datos.get("codigo_recordatorio_enviado"):
                if (ahora - codigo_at) >= 120:  # 2 minutos
                    meses = datos.get("meses", "No especificado")
                    tipo_cuenta = datos.get("tipo_cuenta", "No especificado")
                    send_message(phone,
                        "Hola! Los codigos de activacion de Xbox expiran en pocos minutos 🎮\n\n"
                        "Por favor genera un nuevo codigo en tu consola:\n\n"
                        "1️⃣ Ve a Agregar nuevo (como nueva cuenta)\n"
                        "2️⃣ Selecciona Usar otro dispositivo\n"
                        "3️⃣ Copia el nuevo codigo que aparece y envialo aqui 📲"
                    )
                    send_message(ADMIN_PHONE,
                        "⏰ CODIGO VENCIDO - Game Line Col\nCliente: " + cli(phone) +
                        "\nMeses: " + meses + "\nCuenta: " + tipo_cuenta +
                        "\nCodigo anterior: " + codigo +
                        "\nEl cliente va a generar un nuevo codigo, espera el nuevo."
                    )
                    conversaciones[phone]["codigo_recordatorio_enviado"] = True
                    conversaciones[phone]["codigo_pendiente"] = None
                    conversaciones[phone]["codigo_pendiente_at"] = None

            # 3. Alerta pago final inactivo (24h)
            if estado == "esperando_pago_final" and not datos.get("alerta_inactividad_enviada"):
                if (ahora - ultima) >= 86400:
                    send_message(ADMIN_PHONE,
                        "⚠️ Cliente " + cli(phone) + " lleva mas de 24h sin enviar "
                        "el comprobante del pago final. Quizas valga la pena escribirle."
                    )
                    conversaciones[phone]["alerta_inactividad_enviada"] = True

        # 4. Resumen diario a las 9pm
        hoy = hora_actual.strftime("%d/%m/%Y")
        if hora_actual.hour == 21 and ultimo_resumen != hoy:
            pendientes = sum(1 for d in conversaciones.values() if d.get("estado") in ESTADOS_PENDIENTES)
            send_message(ADMIN_PHONE,
                "📊 Resumen del dia " + hoy + "\n\n"
                "Nuevos clientes: " + str(estadisticas_diarias.get("nuevos", 0)) + "\n"
                "Cierres confirmados: " + str(estadisticas_diarias.get("cierres", 0)) + "\n"
                "Reservas pagadas (MP): " + str(estadisticas_diarias.get("reservas", 0)) + "\n"
                "Pendientes actuales: " + str(pendientes)
            )
            ultimo_resumen = hoy

        # 5. Avisos de vencimiento de servicio (mismo día)
        for phone_rv, datos_rv in list(renovaciones.items()):
            if datos_rv.get("notificado"):
                continue
            if datos_rv.get("canal") != "telegram":
                continue  # cliente de WhatsApp que aun no vincula su numero
            if ahora >= datos_rv.get("vencimiento", 0):
                tipo_rv = datos_rv.get("tipo_cuenta", "")
                meses_rv = datos_rv.get("meses", "")
                send_message(phone_rv,
                    "Hola! 🎮 Hoy vence tu servicio de *Game Pass Ultimate* "
                    "(" + tipo_rv + " - " + meses_rv + ").\n\n"
                    "¿Deseas renovarlo?"
                )
                enviar_botones(phone_rv, "¿Quieres renovar tu Game Pass?", [
                    {"id": "renovar_si", "titulo": "Sí, quiero renovar"},
                    {"id": "renovar_no", "titulo": "No por ahora"}
                ])
                if phone_rv not in conversaciones:
                    conversaciones[phone_rv] = {"ultima_interaccion": ahora}
                conversaciones[phone_rv]["estado"] = "renovacion_pendiente"
                conversaciones[phone_rv]["ultima_interaccion"] = ahora
                renovaciones[phone_rv]["notificado"] = True
                guardar_renovaciones()

        # 5b. Recordatorio a clientes con reserva pagada que aun no avisan que tienen consola
        for phone, datos in list(conversaciones.items()):
            if datos.get("estado") != "esperando_consola":
                continue
            recordatorio_consola_at = datos.get("recordatorio_consola_at", 0)
            if recordatorio_consola_at and ahora >= recordatorio_consola_at:
                enviar_boton_consola_lista(phone,
                    "Hola! 🎮 Solo para recordarte que tu reserva de Game Pass Ultimate ya esta pagada.\n\n"
                    "Avisanos aqui cuando tengas tu consola o PC disponible y te entregamos tu cuenta al instante."
                )
                conversaciones[phone]["recordatorio_consola_at"] = ahora + HORA_RECORDATORIO_CONSOLA

        # 6. Recordatorio único de pago de renovación (al cumplirse el tiempo que dijo el cliente)
        for phone, datos in list(conversaciones.items()):
            if datos.get("estado") != "renovacion_espera_pago":
                continue
            if datos.get("renovacion_recordatorio_enviado"):
                continue
            recordatorio_at = datos.get("renovacion_recordatorio_at", 0)
            if recordatorio_at and ahora >= recordatorio_at:
                meses_rv = renovaciones.get(phone, {}).get("meses", "")
                send_message(phone,
                    "⏰ Recordatorio Game Line Col\n\n"
                    "Tu renovacion de Game Pass" + (" " + meses_rv if meses_rv else "") + " sigue pendiente de pago.\n\n"
                    "Cuando hayas pagado envia aqui mismo la foto del comprobante 📸"
                )
                conversaciones[phone]["renovacion_recordatorio_enviado"] = True

        # 5. Guardar estados cada 5 minutos
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

        # 6. Limpieza de memoria cada hora
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
        # Encabezados Compras
        r = service.spreadsheets().values().get(spreadsheetId=SHEET_ID, range="Compras!A1:F1").execute()
        if not r.get("values"):
            service.spreadsheets().values().update(
                spreadsheetId=SHEET_ID, range="Compras!A1",
                valueInputOption="RAW",
                body={"values": [["Cliente (ID)", "Fecha de compra", "Tipo de cuenta", "Tiempo adquirido", "Email cuenta", "Nombre / usuario"]]}
            ).execute()
        # Encabezados Cuentas
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
    # Le dice a Telegram a que URL mandar los mensajes. Se hace solo en cada arranque.
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

BIENVENIDA = ("🎮 Bienvenido a Game Line Col! 🎮\n\n"
              "Somos tu tienda de confianza para juegos y suscripciones Xbox.\n\n"
              "💡 En cualquier momento puedes escribir *menu* para volver al inicio, "
              "o *asesor* si necesitas hablar con una persona.\n\n"
              "En que te podemos ayudar? 👇")

GAMEPASS = "🕹️ GAME PASS ULTIMATE\n\nPRECIOS:\n📅 1 mes: $29.900\n📅 2 meses: $55.000\n📅 3 meses: $80.000\n📅 6 meses: $140.000\n📅 12 meses: $190.000\n\nMODALIDADES:\n🏠 Principal: juegas desde tu cuenta sin iniciar sesion en otra\n👤 Secundaria: juegas desde tu cuenta iniciando sesion en la del servicio\n\nAmbas funcionan perfecto, solo cambia la configuracion.\n\nGARANTIA: Primero pruebas y luego pagas."

PREGUNTAR_CUENTA = "Que tipo de cuenta prefieres?\n\n🏠 Principal: Juegas desde tu cuenta sin iniciar sesion en otra\n👤 Secundaria: Juegas desde tu cuenta iniciando sesion en la del servicio\n\nAmbas funcionan perfecto 😊"

PREGUNTAR_CONSOLA = "Tienes tu consola o PC disponible ahora?"

CONFIG_PRINCIPAL = "Una vez habilitemos tu cuenta, sigue estos pasos en tu consola:\n\nCONFIGURACION CUENTA PRINCIPAL\n\nCuando aparezcan las preguntas de asociacion Game Pass Ultimate:\n\n1 SIGUIENTE\n2 NO GRACIAS\n3 SIN BARRERAS\n4 OMITIR\n5 En la pregunta de hacer Xbox principal: HACER XBOX PRINCIPAL ✅\n\nIMPORTANTE:\nSiempre usa el servicio con la sesion de tu cuenta personal. La cuenta que anadimos nunca la inicies.\n\nEl uso es exclusivo para ti. Si compartes, se cancela sin devolucion del dinero."

CONFIG_SECUNDARIA = "Una vez habilitemos tu cuenta, sigue estos pasos en tu consola:\n\nCONFIGURACION CUENTA SECUNDARIA\n\nCuando aparezcan las preguntas de asociacion Game Pass Ultimate:\n\n1 SIGUIENTE\n2 NO GRACIAS\n3 SIN BARRERAS\n4 VINCULAR CONTROL\n5 En la pregunta de hacer Xbox principal: NO CAMBIAR ⛔\n\nMucho cuidado con esa pregunta, debes dar NO CAMBIAR.\n\nSiempre con sesion iniciada de la cuenta que anadimos y juegas con tu cuenta personal.\n\nEl uso es exclusivo para ti. Si compartes, se cancela sin devolucion del dinero."

ACTIVACION = "Sigue estos pasos en tu consola o PC:\n\n1 Ve a Agregar nuevo (como nueva cuenta)\n2 Selecciona Usar otro dispositivo\n3 Copia el codigo que aparece y envialo aqui\n\nNuestro asesor lo activara de inmediato! 🚀"

SOPORTE = "SOPORTE\n\nUn asesor te atendera personalmente.\n\nEscribenos a: " + ADMIN_PHONE_DISPLAY + " 😊"

CIERRE = "🎮 Con mucho gusto! Gracias a ti por confiar en Game Line Col 🙌\n\nCualquier cosa que necesites aqui estamos. Que disfrutes tu juego! 🚀"

JUEGOS = "JUEGOS XBOX\n\n1 CODIGO (Economico)\nJuego desde Microsoft, para tu cuenta de por vida\n\n2 CUENTA PRINCIPAL (+ Economico)\nAcceso de por vida, sin iniciar sesion en otra cuenta\n\n3 CUENTA SECUNDARIA (++ Economico)\nAcceso de por vida, iniciando sesion en la cuenta del juego\n\n4 SECUNDARIA CON METODO (+++ Economico)\nAcceso de por vida con tutorial que compartimos\n\nQue juego buscas? Dinos el nombre 👇"

PROMPT = "Eres GameBot de Game Line Col. Responde en espanol, amable y profesional. Si el cliente pregunta por un juego especifico termina con ALERTA_JUEGO:[nombre]. Si no puedes resolver algo termina con ALERTA_ASESOR. No inventes precios."

ESTADO_CLIENTE_MENSAJE = {
    "menu": "Aun no has iniciado un pedido. Escribe 'hola' para ver el menu 🎮",
    "gamepass": "Estas viendo la info de Game Pass Ultimate. Responde si quieres contratar 😊",
    "seleccion_meses": "Estamos esperando que elijas el plan de meses.",
    "seleccion_cuenta": "Estamos esperando que elijas el tipo de cuenta (Principal o Secundaria).",
    "preguntar_consola": "Estamos esperando que nos digas si tienes tu consola o PC disponible ahora.",
    "activacion": "Estamos esperando el codigo de activacion de tu consola. Envialo aqui cuando lo tengas 🎮",
    "esperando_comprobante": "Estamos esperando que pagues tu reserva y nos envies aqui la foto del comprobante 📸",
    "comprobante_reserva_enviado": "Envianos aqui la foto del comprobante de tu reserva ⏳",
    "esperando_consola": "Tu reserva esta confirmada ✅. Avisanos aqui cuando tengas tu consola o PC disponible para entregarte tu cuenta 🎮",
    "esperando_pago_final": "Tu cuenta ya esta activada 🎮. Envianos aqui la foto del comprobante del pago final 📸",
    "pago_final_enviado": "Envianos aqui la foto del comprobante de tu pago final ⏳",
    "pago_confirmado": "Tu pedido esta cerrado y confirmado. Gracias por tu compra! 🎮🙌",
    "juegos": "Estamos esperando que nos digas el nombre del juego que buscas.",
    "soporte": "Tu solicitud de soporte fue enviada, un asesor te contactara pronto 😊",
    "soporte_menu": "Estas en el menu de soporte. Elige tu problema para continuar.",
    "sop_online": "Estamos diagnosticando el problema de online. Elige el tipo de cuenta.",
    "sop_online_p1": "Revisando Xbox Principal. Dinos el estado de la casilla.",
    "sop_online_p2": "Aplicando solucion paso 1. Prueba el online y cuentanos como te fue.",
    "sop_online_p3": "Aplicando solucion de facturacion. Prueba el online y cuentanos como te fue.",
    "sop_online_p4": "Aplicando el reinicio completo de configuracion de tu consola. Sigue los 6 pasos y cuentanos como te fue.",
    "sop_online_s1": "Aplicando solucion de facturacion para cuenta Secundaria. Prueba y cuentanos.",
    "sop_jugando": "Diagnosticando problema de otro usuario jugando. Elige el tipo de cuenta.",
    "sop_jugando_p1": "Aplicando solucion para cuenta Principal. Prueba y cuentanos como te fue.",
    "sop_jugando_s1": "Aplicando solucion para cuenta Secundaria. Prueba y cuentanos como te fue.",
    "soporte_asesor": "Tu caso fue escalado a un asesor, te contactara en breve 🙏",
    "renovacion_pendiente": "Te preguntamos si quieres renovar tu servicio. Usa los botones para responder.",
    "renovacion_espera_admin": "Tu solicitud de renovacion fue enviada al asesor, en breve te respondemos 🙏",
    "renovacion_espera_tiempo": "Dinos en cuanto tiempo puedes hacer el pago (ej: 30 minutos, 1 hora).",
    "renovacion_espera_pago": "Envianos aqui la foto del comprobante de tu renovacion 📸",
    "renovacion_comprobante_enviado": "Envianos aqui la foto del comprobante de tu renovacion ⏳"
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
        {"id": "consola_si", "titulo": "Sí, la tengo"},
        {"id": "consola_no", "titulo": "No la tengo ahora"}
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
    """El cliente compartio su numero. Si ya era cliente por WhatsApp, su
    renovacion y su cuenta asignada pasan a este chat de Telegram."""
    quitar = {"remove_keyboard": True}
    if contacto.get("user_id") and str(contacto.get("user_id")) != str(usuario_tg.get("id")):
        send_message(chat_id, "Por seguridad, comparte *tu propio* numero con el boton 📱", reply_markup=quitar)
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
            "✅ Te reconocimos! Encontramos " + " y ".join(encontrado) + ".\n\n"
            "Desde ahora te avisaremos por aqui cuando tu servicio vaya a vencer 🎮",
            reply_markup=quitar)
        send_message(ADMIN_PHONE, "🔗 Cliente de WhatsApp vinculado a Telegram: " + cli(chat_id))
    else:
        send_message(chat_id, "✅ Gracias! Guardamos tu numero.", reply_markup=quitar)
    if not datos.get("bienvenida_enviada"):
        datos["bienvenida_enviada"] = True
        send_message(chat_id, BIENVENIDA)
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
    # Telegram manda en cada peticion la clave secreta que registramos al arrancar.
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token", "") != TELEGRAM_SECRET:
        print("🛑 Webhook RECHAZADO: clave secreta invalida. IP: " +
              str(request.headers.get("X-Forwarded-For", request.remote_addr)))
        return jsonify({"status": "forbidden"}), 403

    update = request.get_json(silent=True) or {}
    try:
        msg_id = str(update.get("update_id", ""))
        text = ""
        foto_id = None
        if "callback_query" in update:  # el cliente toco un boton
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
                foto_id = message["photo"][-1]["file_id"]  # la de mayor resolucion
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
            return jsonify({"status": "ok"}), 200  # solo se atienden chats privados
        phone = str(chat.get("id"))
        print("📩 Mensaje de " + phone + " (tipo: " + msg_type + ")")

        # Comandos de Telegram: "/start", "/menu@MiBot", "/bloquear 1234 motivo"...
        if msg_type == "text" and text.startswith("/"):
            partes_cmd = text[1:].split(" ", 1)
            text = partes_cmd[0].split("@")[0] + ((" " + partes_cmd[1]) if len(partes_cmd) > 1 else "")
            if text.lower().startswith("start"):
                text = "hola"

        if text.lower().strip() == "miid":
            send_message(phone, "Tu chat ID es: " + phone, formato=False)
            return jsonify({"status": "ok"}), 200

        # ── Clientes bloqueados: se descarta el mensaje sin responder nada ────
        if esta_bloqueado(phone):
            print("🚫 Mensaje ignorado de cliente bloqueado: " + phone)
            return jsonify({"status": "ok"}), 200

        # Nombre y usuario de Telegram, para que el admin sepa quien es quien.
        if phone in conversaciones:
            conversaciones[phone]["nombre"] = (str(usuario_tg.get("first_name", "")) + " " +
                                               str(usuario_tg.get("last_name", ""))).strip()
            conversaciones[phone]["usuario"] = usuario_tg.get("username", "")

        # ── Anti-flood: máximo 1 mensaje procesado por segundo por cliente ────
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
                mensaje_activo = "✅ Tu cuenta ha sido activada en la consola! 🎮\n\nYa puedes empezar a jugar. Sigue estas instrucciones:\n\n" + config_c
                send_message(cliente_encontrado, mensaje_activo)

                monto_c = PRECIOS_GAMEPASS.get(meses_c)
                link_c, referencia_c = (None, None)
                if monto_c:
                    link_c, referencia_c = crear_link_pago(cliente_encontrado, "Game Pass Ultimate " + tipo_cuenta_elegida + " - " + meses_c, monto_c)
                if referencia_c:
                    conversaciones[cliente_encontrado]["referencia_pago"] = referencia_c
                    conversaciones[cliente_encontrado]["tipo_pago_pendiente"] = "final"
                send_message(cliente_encontrado, "Para terminar de confirmar tu activacion:\n\n" + mensaje_opciones_pago(link_c))

                conversaciones[cliente_encontrado]["estado"] = "esperando_pago_final"
                conversaciones[cliente_encontrado]["codigo_pendiente"] = None
                conversaciones[cliente_encontrado]["codigo_pendiente_at"] = None
                conversaciones[cliente_encontrado]["codigo_recordatorio_enviado"] = True
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
                if es_renovacion:
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
                    "✅ Tu reserva quedo confirmada!\n\n"
                    "Cuando tengas tu consola o PC disponible, avisanos aqui para entregarte tu cuenta al instante 🎮"
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
                    "Perfecto! Puedes renovar con las mismas opciones de pago 🎮\n\n" + mensaje_opciones_pago(link_rv)
                )
                send_message(cliente_encontrado,
                    "¿En cuanto tiempo aproximado puedes hacer el pago?\n\n"
                    "Ej: 30 minutos, 1 hora, 2 horas..."
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
                    "Para tu renovacion vamos a asignarte una nueva cuenta 🔄\n\n"
                    "Primero *elimina la cuenta anterior* de tu consola, luego agrega esta:\n\n"
                    "📧 *Email:* " + email_rv + "\n"
                    "🔒 *Contraseña:* " + password_rv
                )
                send_message(cliente_encontrado,
                    "PASOS EN TU CONSOLA:\n\n"
                    "1️⃣ Ve a *Agregar nueva cuenta*\n"
                    "2️⃣ Ingresa el email y contraseña que te compartimos arriba\n"
                    "3️⃣ Sigue la configuracion:\n\n" + config_rv
                )
                send_message(cliente_encontrado, "REALIZA EL PAGO:\n\n" + mensaje_opciones_pago(link_rv))
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

        # ── Revertir un avance automatico cuando el pago NO llego ────────────
        if phone == ADMIN_PHONE and text_lower.startswith("anular"):
            ultimos_4 = re.sub(r"\D", "", text_lower.replace("anular", ""))
            cliente_encontrado = None
            for ph, datos in conversaciones.items():
                if ph.endswith(ultimos_4) and datos.get("estado") in (
                    "esperando_consola", "pago_confirmado"
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
                else:
                    datos_c["estado"] = "esperando_pago_final"
                aviso_admin = ("Volvio a esperar el pago final.\n"
                               "⚠️ Recuerda borrar la fila en la hoja *Compras* si ya se registro.")

            send_message(cliente_encontrado,
                "Hola! 👋 No logramos encontrar tu pago registrado.\n\n"
                "Por favor verifica el comprobante y envialo de nuevo aqui para poder continuar 🙏"
            )
            send_message(ADMIN_PHONE,
                "↩️ Avance anulado para " + cli(cliente_encontrado) + ".\n" + aviso_admin)
            return jsonify({"status": "ok"}), 200

        # ── Comandos de bloqueo (solo admin) ─────────────────────────────────
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

            # Si solo dio 4 digitos, buscamos el cliente en las conversaciones.
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

        saludos = ["hola", "buenas", "buenos dias", "buenas tardes", "buenas noches", "hi", "hello", "inicio"]
        es_saludo = any(re.search(r"\b" + re.escape(s) + r"\b", text_lower) for s in saludos)

        if phone not in conversaciones:
            conversaciones[phone] = nueva_conversacion()
            registrar_evento_diario("nuevos")
        conversaciones[phone]["nombre"] = (str(usuario_tg.get("first_name", "")) + " " +
                                           str(usuario_tg.get("last_name", ""))).strip()
        conversaciones[phone]["usuario"] = usuario_tg.get("username", "")

        if msg_id and msg_id == conversaciones[phone].get("ultimo_msg_id", ""):
            return jsonify({"status": "ok"}), 200
        conversaciones[phone]["ultimo_msg_id"] = msg_id

        # Cliente totalmente nuevo: siempre se le da la bienvenida completa.
        if not conversaciones[phone].get("bienvenida_enviada"):
            conversaciones[phone]["bienvenida_enviada"] = True
            conversaciones[phone]["estado"] = "menu"
            conversaciones[phone]["ultima_interaccion"] = time.time()
            send_message(phone, BIENVENIDA)
            enviar_menu_principal(phone)
            if not conversaciones[phone].get("telefono"):
                pedir_telefono(phone)
            return jsonify({"status": "ok"}), 200

        # ── Palabras de escape: funcionan desde CUALQUIER estado ──────────────
        # Es la salida de emergencia cuando el cliente siente que el bot no lo
        # entiende. Debe ir antes de cualquier logica de estado.
        PALABRAS_REINICIO = ("menu", "menú", "inicio", "reiniciar", "reinicio", "salir",
                             "cancelar", "empezar de nuevo", "volver", "atras", "atrás",
                             "regresar", "empezar", "otra cosa", "menu principal")
        PALABRAS_ASESOR = ("asesor", "humano", "persona real", "hablar con alguien",
                           "atencion personal", "atención personal", "agente",
                           "hablar con una persona", "necesito ayuda de una persona")

        if text_lower.strip() in PALABRAS_REINICIO or text_lower.strip() in ("menu", "menú"):
            conversaciones[phone]["estado"] = "menu"
            conversaciones[phone]["ultima_interaccion"] = time.time()
            conversaciones[phone]["msgs_mismo_estado"] = 0
            send_message(phone, "Listo, empecemos de nuevo 🎮")
            enviar_menu_principal(phone)
            return jsonify({"status": "ok"}), 200

        if not text_lower.startswith("sop_") and any(p in text_lower for p in PALABRAS_ASESOR):
            conversaciones[phone]["estado"] = "soporte_asesor"
            conversaciones[phone]["ultima_interaccion"] = time.time()
            send_message(phone,
                "Claro que si 🙌 Un asesor te va a escribir en breve.\n\n"
                "Si prefieres escribirle tu directamente, este es su contacto: "
                + ADMIN_PHONE_DISPLAY + "\n\n"
                "Y si en algun momento quieres volver al menu, escribe *menu*."
            )
            send_message(ADMIN_PHONE,
                "🙋 El cliente " + cli(phone) + " pidio hablar con un asesor.\n"
                "Estado en el que estaba: " + str(conversaciones[phone].get("estado_anterior_registrado", "desconocido"))
            )
            return jsonify({"status": "ok"}), 200

        # Un saludo NO debe borrar un proceso de compra en curso (esto causaba
        # que el bot "olvidara" a clientes que escribian horas despues).
        # Solo reiniciamos al menu si el cliente no tiene nada pendiente.
        if es_saludo:
            estado_saludo = conversaciones[phone].get("estado", "menu")
            conversaciones[phone]["ultima_interaccion"] = time.time()
            if estado_saludo in ESTADOS_SIN_PROCESO_ACTIVO:
                conversaciones[phone]["estado"] = "menu"
                send_message(phone, BIENVENIDA)
                enviar_menu_principal(phone)
            else:
                descripcion_saludo = ESTADO_CLIENTE_MENSAJE.get(
                    estado_saludo, "Tienes un proceso en curso con nosotros."
                )
                send_message(phone,
                    "Hola de nuevo! 👋\n\n" + descripcion_saludo +
                    "\n\nSi quieres iniciar algo nuevo, escribe *menu*."
                )
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

        # ── Detector de bucle ────────────────────────────────────────────────
        # Si el cliente lleva varios mensajes sin que el estado avance, lo mas
        # probable es que el bot no lo este entendiendo. Le ofrecemos la salida
        # sin que tenga que adivinar ninguna palabra magica.
        # "menu" se excluye porque ahi la conversacion libre es normal.
        if estado == conversaciones[phone].get("estado_anterior_registrado"):
            conversaciones[phone]["msgs_mismo_estado"] = conversaciones[phone].get("msgs_mismo_estado", 0) + 1
        else:
            conversaciones[phone]["estado_anterior_registrado"] = estado
            conversaciones[phone]["msgs_mismo_estado"] = 1

        if estado != "menu" and conversaciones[phone].get("msgs_mismo_estado", 0) >= 3:
            conversaciones[phone]["msgs_mismo_estado"] = 0
            send_message(phone,
                "Parece que no estoy logrando ayudarte con lo que necesitas 😕\n\n"
                "Dime como prefieres seguir 👇"
            )
            enviar_botones(phone, "Que quieres hacer?", [
                {"id": "reiniciar_menu", "titulo": "🔄 Volver al menu"},
                {"id": "sop_asesor", "titulo": "🙋 Hablar con asesor"}
            ])
            return jsonify({"status": "ok"}), 200

        if text == "reiniciar_menu":
            conversaciones[phone]["estado"] = "menu"
            conversaciones[phone]["msgs_mismo_estado"] = 0
            send_message(phone, "Listo, empecemos de nuevo 🎮")
            enviar_menu_principal(phone)
            return jsonify({"status": "ok"}), 200

        historial = conversaciones[phone].get("historial", [])
        meses = conversaciones[phone].get("meses", "No especificado")
        tipo_cuenta = conversaciones[phone].get("tipo_cuenta", "No especificado")

        if msg_type == "image":
            if estado in ("esperando_pago_final", "pago_final_enviado", "renovacion_espera_pago",
                          "renovacion_comprobante_enviado", "esperando_comprobante",
                          "comprobante_reserva_enviado"):
                # El comprobante llega aqui y se le reenvia al admin para verificarlo.
                reenviar_imagen(ADMIN_PHONE, foto_id, "🧾 Comprobante de " + cli(phone) + "\nEstado: " + estado)
                send_message(phone, "Gracias! 🙌 Recibimos tu comprobante, nuestro asesor lo verificara.")
                avanzar_tras_pago(phone, estado)
            else:
                send_message(phone, "Recibimos tu imagen, pero en este momento no la necesitamos. Si tienes alguna duda escribenos 😊")
            return jsonify({"status": "ok"}), 200

        if estado == "seleccion_meses":
            m = extraer_meses(text)
            if m:
                conversaciones[phone]["meses"] = m
                conversaciones[phone]["estado"] = "confirmacion_compra"
                enviar_botones(phone,
                    "Vas a contratar *Game Pass Ultimate - " + m + "*\n\n"
                    "📅 Duracion: " + m + "\n"
                    "💰 Precio: $" + str(PRECIOS_GAMEPASS.get(m, "")) + "\n\n"
                    "¿Confirmas tu compra?",
                    [
                        {"id": "confirmar_compra", "titulo": "✅ Sí, confirmo"},
                        {"id": "cancelar_compra", "titulo": "❌ Cancelar"}
                    ]
                )
            else:
                send_message(phone, "No entendi cual plan elegiste 🙏")
                enviar_pregunta_meses(phone)
            return jsonify({"status": "ok"}), 200

        if estado == "confirmacion_compra":
            if text in ("confirmar_compra", "si", "sí", "yes", "confirmo", "dale"):
                conversaciones[phone]["estado"] = "preguntar_consola"
                enviar_pregunta_consola(phone)
            elif text in ("cancelar_compra", "no", "cancelar"):
                conversaciones[phone]["estado"] = "menu"
                send_message(phone, "Entendido! Si cambias de opinion escribe *hola* cuando quieras 😊")
            else:
                enviar_botones(phone,
                    "¿Confirmas tu compra de Game Pass Ultimate - " + conversaciones[phone].get("meses", "") + "?",
                    [
                        {"id": "confirmar_compra", "titulo": "✅ Sí, confirmo"},
                        {"id": "cancelar_compra", "titulo": "❌ Cancelar"}
                    ]
                )
            return jsonify({"status": "ok"}), 200

        # ── ¿TIENE CONSOLA/PC DISPONIBLE? ───────────────────────────────────
        if estado == "preguntar_consola":
            meses = conversaciones[phone].get("meses", "1 mes")

            if text == "consola_si":
                asignacion = asignar_cuenta(phone)
                if not asignacion:
                    send_message(phone,
                        "Lo sentimos, en este momento no tenemos disponibilidad 😔\n\n"
                        "Un asesor te contactara pronto para buscar una solucion."
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
                    "✅ Tu cuenta de Game Pass Ultimate ha sido asignada! 🎮\n\n"
                    "📅 Plan: " + meses + " - Cuenta " + tipo_asig + "\n\n"
                    "📧 *Email:* " + email_asig + "\n"
                    "🔒 *Contraseña:* " + password_asig
                )
                send_message(phone,
                    "PASO 1 - AGREGAR LA CUENTA EN TU CONSOLA:\n\n"
                    "1️⃣ Ve a *Agregar nueva cuenta*\n"
                    "2️⃣ Ingresa el *email y contraseña* que te compartimos arriba\n"
                    "3️⃣ Sigue la configuracion a continuacion 👇"
                )
                send_message(phone, "CONFIGURACION DE TU CUENTA:\n\n" + config_asig)
                send_message(phone,
                    "PASO 2 - REALIZA EL PAGO:\n\n" + mensaje_opciones_pago(link_asig)
                )

                send_message(ADMIN_PHONE,
                    "🎮 NUEVA ASIGNACION Game Line Col\n"
                    "Cliente: " + cli(phone) + "\nPlan: " + meses + " - Cuenta " + tipo_asig +
                    "\n\n📧 Email: " + email_asig +
                    "\n🔒 Contraseña: " + password_asig +
                    "\n\nVerifica que esta cuenta ya este canjeada (con Game Pass activo) antes de que el cliente la use."
                )

                # Registrar la asignacion en Sheets de inmediato (el pago se confirmara despues)
                try:
                    service = get_sheets_service()
                    fecha = datetime.now().strftime("%d/%m/%Y %H:%M")
                    service.spreadsheets().values().append(
                        spreadsheetId=SHEET_ID,
                        range="Compras!A:F",
                        valueInputOption="RAW",
                        body={"values": [[str(phone), fecha, tipo_asig, meses, email_asig + " (pendiente pago)", cli(phone)]]}
                    ).execute()
                except Exception as e:
                    print("Error registrando asignacion: " + str(e))

            elif text == "consola_no":
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

                send_message(phone,
                    "Sin problema! 🎮 Puedes pagar ahora para *apartar* tu plan de " + meses + ".\n\n"
                    "Apenas tengas tu consola o PC disponible y nos avises, te entregamos tu cuenta al instante.\n\n"
                    + mensaje_opciones_pago(link_rsv)
                )
                send_message(ADMIN_PHONE,
                    "📌 RESERVA Game Line Col\nCliente: " + cli(phone) + "\nPlan: " + meses +
                    "\n\nEl cliente aun no tiene consola/PC disponible. Pago pendiente para apartar el cupo."
                )
            else:
                enviar_pregunta_consola(phone)
            return jsonify({"status": "ok"}), 200

        # ── RENOVACIÓN ───────────────────────────────────────────────────────
        if estado == "renovacion_pendiente":
            if text in ("renovar_si", "si", "sí", "yes", "quiero", "dale", "claro"):
                tipo_rv = renovaciones.get(phone, {}).get("tipo_cuenta", "No especificado")
                meses_rv = renovaciones.get(phone, {}).get("meses", "No especificado")
                conversaciones[phone]["estado"] = "renovacion_espera_admin"
                send_message(phone, "Perfecto! En un momento te confirmamos los detalles para tu renovacion 🎮")
                send_message(ADMIN_PHONE,
                    "🔄 RENOVACION Game Line Col\nCliente: " + cli(phone) +
                    "\nServicio actual: " + tipo_rv + " - " + meses_rv +
                    "\n\nResponde:\n✅ misma " + phone[-4:] + " → Misma cuenta\n🔄 cambia " + phone[-4:] + " → Cambiar cuenta"
                )
            elif text in ("renovar_no", "no"):
                conversaciones[phone]["estado"] = "menu"
                send_message(phone, "Entendido! Si en algun momento quieres renovar, aqui estamos 🎮")
            else:
                enviar_botones(phone, "¿Quieres renovar tu Game Pass?", [
                    {"id": "renovar_si", "titulo": "Sí, quiero renovar"},
                    {"id": "renovar_no", "titulo": "No por ahora"}
                ])
            return jsonify({"status": "ok"}), 200

        if estado == "renovacion_espera_admin":
            send_message(phone, "Tu solicitud de renovacion ya fue enviada a nuestro asesor, en breve te respondemos 🙏")
            return jsonify({"status": "ok"}), 200

        if estado == "renovacion_espera_tiempo":
            minutos = parsear_tiempo_minutos(text)
            conversaciones[phone]["renovacion_recordatorio_at"] = time.time() + (minutos * 60)
            conversaciones[phone]["renovacion_recordatorio_enviado"] = False
            conversaciones[phone]["estado"] = "renovacion_espera_pago"
            send_message(phone,
                "Listo! Te enviare un recordatorio en " + str(minutos) + " minuto(s) ⏰\n\n"
                "Cuando hayas pagado, envianos la foto del comprobante aqui 📸"
            )
            return jsonify({"status": "ok"}), 200

        if estado in ("renovacion_espera_pago", "renovacion_comprobante_enviado",
                      "esperando_pago_final", "pago_final_enviado",
                      "esperando_comprobante", "comprobante_reserva_enviado"):
            if text_lower.strip() in CONFIRMACIONES_PAGO:
                avanzar_tras_pago(phone, estado)
            else:
                send_message(phone, RECORDAR_COMPROBANTE_ADMIN)
            return jsonify({"status": "ok"}), 200

        # ── CLIENTE CON RESERVA PAGADA CONFIRMA QUE YA TIENE CONSOLA/PC ─────
        if estado == "esperando_consola":
            confirmaciones_consola = (
                "consola_lista", "si", "sí", "ya", "yes", "listo", "lista",
                "ya tengo", "ya la tengo", "tengo consola", "ya llegue", "ya llegué"
            )
            if text_lower in confirmaciones_consola:
                meses = conversaciones[phone].get("meses", "1 mes")
                asignacion = asignar_cuenta(phone)
                if not asignacion:
                    send_message(phone,
                        "Uy, justo ahorita no tenemos disponibilidad 😔\n\n"
                        "Ya avisamos a un asesor para resolverlo lo antes posible, tu reserva sigue vigente."
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
                    "✅ Perfecto! Tu cuenta de Game Pass Ultimate ha sido asignada! 🎮\n\n"
                    "📅 Plan: " + meses + " - Cuenta " + tipo_asig + "\n\n"
                    "📧 *Email:* " + email_asig + "\n"
                    "🔒 *Contraseña:* " + password_asig
                )
                send_message(phone,
                    "PASO 1 - AGREGAR LA CUENTA EN TU CONSOLA:\n\n"
                    "1️⃣ Ve a *Agregar nueva cuenta*\n"
                    "2️⃣ Ingresa el *email y contraseña* que te compartimos arriba\n"
                    "3️⃣ Sigue la configuracion a continuacion 👇"
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
                enviar_boton_consola_lista(phone,
                    "Avisanos aqui cuando tengas tu consola o PC disponible para entregarte tu cuenta 🎮"
                )
            return jsonify({"status": "ok"}), 200

        if text == "1" or ("game pass" in text_lower and estado == "menu"):
            conversaciones[phone]["estado"] = "gamepass"
            send_message(phone, GAMEPASS)
            enviar_pregunta_contratar(phone)
            return jsonify({"status": "ok"}), 200

        if text == "2" or text_lower in ["juegos", "juego"]:
            conversaciones[phone]["estado"] = "juegos"
            if catalogo_media_id:
                enviar_documento(phone, catalogo_media_id, "🎮 Catalogo Game Line Col - Juegos Xbox")
                send_message(phone,
                    "Ahi tienes nuestro catalogo completo con todos los juegos disponibles y sus precios 👆\n\n"
                    "Dinos el nombre del juego que te interesa y te damos mas informacion 😊"
                )
            else:
                send_message(phone, JUEGOS)
            return jsonify({"status": "ok"}), 200

        # ── SOPORTE: botones sop_* (se procesan ANTES que el menú principal) ─
        SOP_IDS = {"sop_online", "sop_online_p", "sop_online_s",
                   "sop_online_p_marcada", "sop_online_p_no_marcada",
                   "sop_online_p3", "sop_online_p4", "sop_jugando", "sop_jugando_p",
                   "sop_jugando_s", "sop_password", "sop_resuelto", "sop_asesor"}

        # Mapeo de texto libre a IDs de botón para que el bot entienda aunque no toque el botón
        if estado in ("soporte_menu", "sop_online", "sop_jugando") or text in SOP_IDS:
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
                enviar_botones(phone, "Que tipo de cuenta adquiriste?", [
                    {"id": "sop_online_p", "titulo": "Cuenta Principal"},
                    {"id": "sop_online_s", "titulo": "Cuenta Secundaria"}
                ])
                return jsonify({"status": "ok"}), 200

            if text == "sop_online_p":
                conversaciones[phone]["estado"] = "sop_online_p1"
                send_message(phone,
                    "Vamos a verificar la configuracion de tu Xbox Principal 🎮\n\n"
                    "Sigue estos pasos *desde la cuenta de Game Pass* (no desde tu cuenta personal):\n\n"
                    "1️⃣ Ve a *Configuracion*\n"
                    "2️⃣ Luego a *Personalizacion*\n"
                    "3️⃣ Luego a *Xbox Principal*\n\n"
                    "Cuando estes ahi, dime: la casilla de Xbox Principal esta marcada o no?"
                )
                enviar_botones(phone, "Estado de la casilla Xbox Principal:", [
                    {"id": "sop_online_p_marcada", "titulo": "Sí, está marcada"},
                    {"id": "sop_online_p_no_marcada", "titulo": "No está marcada"}
                ])
                return jsonify({"status": "ok"}), 200

            if text in ("sop_online_p_marcada", "sop_online_p_no_marcada"):
                conversaciones[phone]["estado"] = "sop_online_p2"
                instruccion = ("La casilla esta marcada. Haz lo siguiente:\n\n✅ *Desmarca* la casilla\n✅ *Vuelve a marcarla*"
                               if text == "sop_online_p_marcada"
                               else "La casilla no estaba marcada. Haz lo siguiente:\n\n✅ *Marca* la casilla")
                send_message(phone,
                    instruccion + "\n\n"
                    "Luego:\n"
                    "🔄 *Reinicia la consola*\n"
                    "⚠️ Al reiniciar, asegurate de tener *unicamente* iniciada sesion tu cuenta personal\n\n"
                    "Vuelve a probar el online. Como te fue?"
                )
                enviar_botones(phone, "Resultado:", [
                    {"id": "sop_resuelto", "titulo": "🎉 Ya funciona!"},
                    {"id": "sop_online_p3", "titulo": "Sigue el error"}
                ])
                return jsonify({"status": "ok"}), 200

            if text == "sop_online_p3":
                conversaciones[phone]["estado"] = "sop_online_p3"
                send_message(phone,
                    "Vamos con el siguiente paso. *Desde la cuenta de Game Pass* (no desde tu cuenta personal):\n\n"
                    "1️⃣ Ve a *Configuracion*\n"
                    "2️⃣ Luego a *Pago y facturacion*\n"
                    "3️⃣ Busca *Facturacion periodica*\n"
                    "4️⃣ Si esta *activa*, desactivala. Si esta *inactiva*, activala\n"
                    "5️⃣ Repite ese paso al menos *2 veces*\n\n"
                    "Luego:\n"
                    "🔄 *Reinicia la consola*\n"
                    "⚠️ Al reiniciar, *solo* debe estar iniciada tu cuenta personal\n\n"
                    "Vuelve a probar. Como te fue?"
                )
                enviar_botones(phone, "Resultado:", [
                    {"id": "sop_resuelto", "titulo": "🎉 Ya funciona!"},
                    {"id": "sop_online_p4", "titulo": "Sigue el error"}
                ])
                return jsonify({"status": "ok"}), 200

            if text == "sop_online_p4":
                conversaciones[phone]["estado"] = "sop_online_p4"
                send_message(phone,
                    "Tranquilo, ya sabemos que esta pasando 🙌\n\n"
                    "Tu cuenta esta perfecta de nuestro lado. Lo que ocurre es que *tu consola* "
                    "guardo mal la configuracion del servicio online, y hay que reiniciarla por completo.\n\n"
                    "Es un procedimiento un poco mas largo, pero con este queda resuelto. "
                    "Sigue los pasos en orden y sin saltarte ninguno 👇"
                )
                send_message(phone,
                    "🎮 *PASOS PARA ACTIVAR CORRECTAMENTE EL SERVICIO ONLINE EN TU XBOX*\n\n"
                    "1️⃣ Inicia sesion con la *cuenta de Game Pass Ultimate*.\n\n"
                    "2️⃣ Ve a *Configuracion > Personalizacion > Mi Xbox principal*.\n\n"
                    "3️⃣ *Desmarca* la opcion \"Mi Xbox principal\", confirma la eliminacion y "
                    "*reinicia la consola*.\n\n"
                    "4️⃣ Cuando la consola reinicie, vuelve a iniciar sesion con la *cuenta de Game Pass "
                    "Ultimate* y repite la ruta:\n"
                    "*Configuracion > Personalizacion > Mi Xbox principal*\n"
                    "Esta vez *marca* la opcion \"Mi Xbox principal\" y dejala activada.\n\n"
                    "5️⃣ Luego ve a *Configuracion > Red > Configuracion avanzada > Direccion MAC "
                    "alternativa*. Selecciona *Borrar/Limpiar* y *reinicia la consola*.\n\n"
                    "6️⃣ Al encender nuevamente la consola, inicia sesion *unicamente con tu cuenta "
                    "personal*. ⚠️ NO inicies sesion con la cuenta de Game Pass Ultimate.\n\n"
                    "Finalmente, prueba nuevamente el servicio online 🚀"
                )
                enviar_botones(phone, "Como te fue?", [
                    {"id": "sop_resuelto", "titulo": "🎉 Ya funciona!"},
                    {"id": "sop_asesor", "titulo": "Sigue el error"}
                ])
                return jsonify({"status": "ok"}), 200

            if text == "sop_online_s":
                conversaciones[phone]["estado"] = "sop_online_s1"
                send_message(phone,
                    "Para cuenta Secundaria vamos a ajustar la facturacion. "
                    "*Desde la cuenta de Game Pass* (no desde tu cuenta personal):\n\n"
                    "1️⃣ Ve a *Configuracion*\n"
                    "2️⃣ Luego a *Pago y facturacion*\n"
                    "3️⃣ Busca *Facturacion periodica*\n"
                    "4️⃣ Si esta *activa*, desactivala. Si esta *inactiva*, activala\n"
                    "5️⃣ Repite ese paso al menos *2 veces*\n\n"
                    "Luego:\n"
                    "🔄 *Reinicia la consola*\n"
                    "⚠️ Al reiniciar, *solo* debe estar iniciada tu cuenta personal\n\n"
                    "Vuelve a probar. Como te fue?"
                )
                enviar_botones(phone, "Resultado:", [
                    {"id": "sop_resuelto", "titulo": "🎉 Ya funciona!"},
                    {"id": "sop_asesor", "titulo": "Sigue el error"}
                ])
                return jsonify({"status": "ok"}), 200

            if text == "sop_jugando":
                conversaciones[phone]["estado"] = "sop_jugando"
                enviar_botones(phone, "Que tipo de cuenta adquiriste?", [
                    {"id": "sop_jugando_p", "titulo": "Cuenta Principal"},
                    {"id": "sop_jugando_s", "titulo": "Cuenta Secundaria"}
                ])
                return jsonify({"status": "ok"}), 200

            if text == "sop_jugando_p":
                conversaciones[phone]["estado"] = "sop_jugando_p1"
                send_message(phone,
                    "Para cuenta Principal el problema se soluciona cerrando la sesion de la cuenta de Game Pass 🎮\n\n"
                    "Haz esto en tu consola:\n\n"
                    "1️⃣ Ve a la cuenta de Game Pass\n"
                    "2️⃣ *Cierra sesion* completamente de esa cuenta\n"
                    "3️⃣ Asegurate de tener *unicamente* iniciada tu cuenta personal\n\n"
                    "Vuelve a probar. Como te fue?"
                )
                enviar_botones(phone, "Resultado:", [
                    {"id": "sop_resuelto", "titulo": "🎉 Ya funciona!"},
                    {"id": "sop_asesor", "titulo": "Sigue el error"}
                ])
                return jsonify({"status": "ok"}), 200

            if text == "sop_jugando_s":
                conversaciones[phone]["estado"] = "sop_jugando_s1"
                send_message(phone,
                    "Para cuenta Secundaria hay que ajustar la configuracion. "
                    "*Desde la cuenta de Game Pass* (no desde tu cuenta personal):\n\n"
                    "1️⃣ Ve a *Configuracion*\n"
                    "2️⃣ Luego a *Personalizacion*\n"
                    "3️⃣ Luego a *Xbox Principal*\n"
                    "4️⃣ *Marca* la casilla de hacer Xbox Principal ✅\n\n"
                    "Luego:\n"
                    "🔄 *Reinicia la consola*\n"
                    "⚠️ Al reiniciar, *solo* debe estar iniciada tu cuenta personal\n\n"
                    "Vuelve a probar. Como te fue?"
                )
                enviar_botones(phone, "Resultado:", [
                    {"id": "sop_resuelto", "titulo": "🎉 Ya funciona!"},
                    {"id": "sop_asesor", "titulo": "Sigue el error"}
                ])
                return jsonify({"status": "ok"}), 200

            if text in ("sop_password", "sop_asesor"):
                conversaciones[phone]["estado"] = "soporte_asesor"
                send_message(phone, "Entendido, vamos a pasarte con un asesor que te ayudara personalmente 🙏\n\nEn breve te contactamos.")
                estado_desc = conversaciones[phone].get("estado", "desconocido")
                alerta = ("🛠️ SOPORTE - ESCALADO AL ASESOR\nCliente: " + cli(phone) +
                          "\nUltimo estado: " + estado_desc +
                          "\nContactalo para ayudarlo manualmente.")
                send_message(ADMIN_PHONE, alerta)
                return jsonify({"status": "ok"}), 200

            if text == "sop_resuelto":
                conversaciones[phone]["estado"] = "menu"
                send_message(phone, "Perfecto! Nos alegra que haya quedado solucionado 🎮🙌\n\nSi necesitas algo mas, escribe *hola* para volver al menu.")
                return jsonify({"status": "ok"}), 200

            # Si esta en un estado de soporte pero no se reconocio el texto, reenviar menu
            enviar_botones(phone, "No te entendi bien 🙏 Cual es tu problema?", [
                {"id": "sop_online", "titulo": "Online no funciona"},
                {"id": "sop_jugando", "titulo": "Alguien más jugando"},
                {"id": "sop_password", "titulo": "Pide contraseña"}
            ])
            return jsonify({"status": "ok"}), 200

        # ── Menú principal soporte (opcion 3 o texto "soporte") ─────────────
        if text == "3" or "soporte" in text_lower:
            conversaciones[phone]["estado"] = "soporte_menu"
            enviar_botones(phone, "🛠️ Soporte Game Line Col\n\nCual es el problema que tienes?", [
                {"id": "sop_online", "titulo": "Online no funciona"},
                {"id": "sop_jugando", "titulo": "Alguien más jugando"},
                {"id": "sop_password", "titulo": "Pide contraseña"}
            ])
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
            alerta = "COTIZACION Game Line Col\nCliente: " + cli(phone) + "\nJuego: " + nombre_juego
            send_message(ADMIN_PHONE, alerta)
        elif "ALERTA_ASESOR" in reply:
            reply = reply.replace("ALERTA_ASESOR", "").strip()
            alerta = "ALERTA ASESOR Game Line Col\nCliente: " + cli(phone) + "\nPregunta: " + text
            send_message(ADMIN_PHONE, alerta)

        historial.append({"role": "assistant", "content": reply})
        conversaciones[phone]["historial"] = historial[-20:]
        send_message(phone, reply, formato=False)

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
                            "✅ Pago de tu reserva confirmado automaticamente!\n\n"
                            "Cuando tengas tu consola o PC disponible, avisanos aqui para entregarte tu cuenta al instante 🎮"
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
