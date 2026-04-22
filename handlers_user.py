import logging, re, telegram, csv, os, html, json, requests
from datetime import datetime
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto, ForceReply
from telegram.ext import ContextTypes, ConversationHandler, MessageHandler, CallbackQueryHandler, CommandHandler, filters
import config, keyboards, handlers_survey
from database import Session, Incidencia 

# --- SEGURIDAD HTML ---
def safe_html(text):
    if not text: return "N/A"
    return html.escape(str(text))

async def _limpiar_chat(update, context):
    """Borra el mensaje anterior del bot y la respuesta del usuario para mantener el chat limpio"""
    # Borrar respuesta del usuario
    if update.message:
        try: await update.message.delete()
        except: pass
    # Borrar mensaje anterior del bot
    msg_id = context.user_data.get('last_bot_msg')
    if msg_id:
        try: await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=msg_id)
        except: pass
        context.user_data.pop('last_bot_msg', None)

async def _enviar_y_guardar(update, context, text, reply_markup=None, chat_id=None):
    """Envía un mensaje, borra el mensaje anterior del bot Y el mensaje del usuario,
    y registra el nuevo ID. Garantiza el chat siempre limpio en todo el flujo."""
    cid = chat_id or update.effective_chat.id
    # 1. Borrar mensaje del usuario que disparó la acción (si existe)
    if update.message:
        try: await update.message.delete()
        except: pass
    # 2. Borrar mensaje anterior del bot (si existe)
    prev_id = context.user_data.get('last_bot_msg')
    if prev_id:
        try: await context.bot.delete_message(chat_id=cid, message_id=prev_id)
        except: pass
        context.user_data.pop('last_bot_msg', None)
    # 3. Enviar nuevo mensaje y registrar su ID
    sent = await context.bot.send_message(chat_id=cid, text=text, parse_mode="HTML", reply_markup=reply_markup)
    context.user_data['last_bot_msg'] = sent.message_id
    return sent

# --- ESTADOS DEL FLUJO ---
(TRANSPORT, LINE, LOCATION, UNIT_NUMBER, NAME, TIPO_CEDULA, CEDULA, EMAIL, 
 PHONE_NUMBER, DATE_EVENT, EQUIPO, SUB_FALLA, PROBLEM, PHOTO, CONFIRM_PROBLEM, AWAITING_CHOICE) = range(16)

RE_EMAIL = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
RE_TELEFONO = r'^0(412|414|424|416|426|222|422)\d{7}$'

# --- HELPER: DETECCIÓN METROBUS ---
def _es_metrobus(context):
    """Devuelve True si la ubicación seleccionada pertenece a Metrobus.
    Revisa el location_path COMPLETO (ej: 'Metrobus CCS > Ruta 001') para detectar
    cualquier nivel que contenga 'metrobus', no solo el último nivel."""
    path = context.user_data.get("location_path", [])
    ruta_completa = " > ".join(path).lower()
    # Fallback: revisar transport/location también
    fallback = (context.user_data.get("transport", "") or context.user_data.get("location", "")).lower()
    return "metrobus" in ruta_completa or "metrobus" in fallback

# --- FUNCIONES DE APOYO ---
async def back_to_equipos_func(update, context):
    q = update.callback_query; await q.answer()
    equipos_glpi = obtener_categorias_raiz_glpi()
    await q.edit_message_text("💻 <b>Seleccione el Equipo:</b>", parse_mode="HTML", reply_markup=keyboards.agregar_boton_cancelar(keyboards.kb_equipos(equipos_glpi)))
    return EQUIPO

def preservar_admin_name(context, clear=True):
    """Preserva datos de sesión (Login + Admin) al limpiar user_data para reporte nuevo"""
    # Keys a preservar: admin, login user, login data
    keys_to_save = ["admin_name", "name", "user_glpi_data"]
    backups = {k: context.user_data.get(k) for k in keys_to_save}
    
    if clear:
        context.user_data.clear()
        
    # Restaurar
    for k, v in backups.items():
        if v is not None:
            context.user_data[k] = v

def asignar_ticket_glpi(tid, tecnico_nombre, tecnico_id):
    """Centraliza la lógica de asignación de técnico en GLPI"""
    try:
        # IP FIXED: 192.168.4.194
        glpi_assign_url = f"http://192.168.4.194:4444/glpi/tickets/{tid}/assign"
        payload = {
            "username": tecnico_nombre.lower(),
            "users_id": tecnico_id,
            "type": 2,  # 2 = Técnico en GLPI
            "_type": "technician",
            "use_notification": 1
        }
        logging.info(f"🔵 GLPI ASSIGN: Ticket {tid}, Técnico: {tecnico_nombre}, ID: {tecnico_id}")
        r = requests.post(glpi_assign_url, headers={'accept': '*/*', 'Content-Type': 'application/json', 'X-API-KEY': config.GLPI_API_KEY}, json=payload, timeout=5)
        logging.info(f"✅ GLPI Assign: {r.status_code} - {r.text}")
        return r.status_code == 200
    except Exception as e:
        logging.error(f"❌ GLPI Assign Error: {e}")
        return False

def eliminar_tecnico_ticket_glpi(glpi_tid):
    """
    Elimina TODOS los técnicos asignados a un ticket en GLPI.
    Intenta varias estrategias:
    1. DELETE /Ticket_User (Estandar moderno)
    2. PUT /Ticket (Update legacy column)
    """
    exito = False
    
    # ESTRATEGIA 1: Borrar relación Ticket_User (Actores)
    try:
        # IP FIXED: 192.168.4.194
        # Usar endpoint SEARCH genérico porque los nested fallan (404)
        # Usar endpoint SEARCH genérico
        # IMPORTANTE: NO filtrar por type en la query para evitar problemas de API.
        # Traemos todos los actores y filtramos en Python.
        url_search = "http://192.168.4.194:4444/glpi/Ticket_User"
        params = {
            "criteria[0][field]": "tickets_id",
            "criteria[0][searchtype]": "equals",
            "criteria[0][value]": glpi_tid,
            "forcedisplay[0]": 2, # ID
            "forcedisplay[1]": 4 # Type
        }
        
        logging.info(f"GLPI: Buscando actores en Ticket {glpi_tid} (Query amplia)")
        r_search = requests.get(url_search, headers={'accept': 'application/json', 'X-API-KEY': config.GLPI_API_KEY}, params=params, timeout=5)
        
        items_to_delete = []
        if r_search.status_code == 200:
            data = r_search.json()
            if isinstance(data, dict) and 'data' in data: items_to_delete = data['data']
            elif isinstance(data, list): items_to_delete = data
            
            logging.info(f"GLPI: Total actores encontrados: {len(items_to_delete)}")
            
            count = 0
            for item in items_to_delete:
                # Filtrar TÉCNICOS (Type 2) en Python
                # En search responses, los fields vienen como claves numéricas o strings
                # type suele ser campo 4? O 'type' si forcedisplay no funciona como esperamos.
                # Vamos a ser defensivos revisando claves
                
                actor_type = item.get('type') or item.get(4) or item.get('4')
                link_id = item.get('id') or item.get(2) or item.get('2')
                
                logging.info(f"Actor analizado: ID={link_id}, Type={actor_type} (Raw: {item})")
                
                # Check soft matches for type 2
                if str(actor_type) == "2":
                    if link_id:
                        # IP FIXED: 192.168.4.194
                        url_del = f"http://192.168.4.194:4444/glpi/Ticket_User/{link_id}"
                        r_del = requests.delete(url_del, headers={'accept': 'application/json', 'X-API-KEY': config.GLPI_API_KEY}, timeout=5)
                        logging.info(f"GLPI: Eliminando Link {link_id} (Técnico) -> Status {r_del.status_code}")
                        if r_del.status_code in [200, 204]: count += 1
            
            if count > 0: exito = True
        else:
             logging.warning(f"GLPI: Error en search Ticket_User {glpi_tid} (Status {r_search.status_code})")

    except Exception as e:
        logging.error(f"❌ GLPI Error Strategy 1: {e}")

    # ESTRATEGIA 2: Actualizar Ticket directo (Legacy/Column update)
    # Esto limpia la columna 'Asignado a' visualmente en versiones viejas o modos simples
    try:
        # IP FIXED: 192.168.4.194
        urls = [
            f"http://192.168.4.194:4444/glpi/tickets/{glpi_tid}", # Custom Proxy convention
            f"http://192.168.4.194:4444/glpi/Ticket/{glpi_tid}"   # Standard
        ]
        
        payload = {
            "input": {
                "users_id_assign": 0,    # Field standard
                "_users_id_assign": 0,   # Field alternate
                "status": 2              # Cambiar a Processing (assign) o New (incoming)? 2=Processing. 
                                         # Si liberamos, quizás volver a 1 (New)?
                                         # El usuario pide 'liberar', lo cual implica que nadie lo tiene.
            }
        }
        
        for url in urls:
            logging.info(f"GLPI: Intentando Unassign vía PUT {url}")
            r = requests.put(url, headers={'accept': 'application/json', 'Content-Type': 'application/json', 'X-API-KEY': config.GLPI_API_KEY}, json=payload, timeout=5)
            logging.info(f"GLPI PUT response: {r.status_code}")
            if r.status_code in [200, 201, 204]:
                exito = True
                break
            elif r.status_code == 404:
                logging.warning(f"GLPI: Endpoint PUT no encontrado: {url}")
                
    except Exception as e:
        logging.error(f"❌ GLPI Error Strategy 2: {e}")

    return exito

def actualizar_mensaje_ticket(msg_obj, estado_visual, tecnico=None, ticket=None):
    """Genera el texto actualizado para un mensaje de ticket"""
    texto_original = msg_obj.caption if msg_obj.photo else msg_obj.text
    # FIX: Separator standardized to 20 chars to match msg_adm
    partes = texto_original.split("━━━━━━━━━━━━━━━━━━━━")
    # Usamos [:2] para eliminar estados previos y quedarnos con cabecera y cuerpo
    cuerpo = "━━━━━━━━━━━━━━━━━━━━".join(partes[:2]).strip()
    res = f"{cuerpo}\n━━━━━━━━━━━━━━━━━━━━\n📌 <b>ESTADO ACTUAL:</b> <code>{estado_visual}</code>"
    
    if ticket:
        if ticket.inicio_atencion:
            fmt = ticket.inicio_atencion.strftime('%d/%m/%Y %I:%M %p')
            res += f"\n⏱ <b>Inicio:</b> {fmt}"
        if "RESUELTO" in estado_visual and ticket.fin_atencion:
            fmt = ticket.fin_atencion.strftime('%d/%m/%Y %I:%M %p')
            res += f"\n🏁 <b>Cierre:</b> {fmt}"

    # FIX: Only append tecnico if explicitly provided (and not None/Empty)
    if tecnico:
        res += f"\n👨‍🔧 <b>TÉCNICO ASIGNADO:</b> {tecnico}"
    return res

def obtener_ubi(d):
    """Genera string de ubicación basado en datos de GLPI"""
    # Si tenemos un path acumulado, usarlo para mostrar migas de pan (breadcrumb)
    path = d.get("location_path")
    if path and isinstance(path, list) and len(path) > 0:
        return " > ".join(path)
    
    # Fallback: Solo nombre de la ubicación final
    return d.get('location', 'N/A')

def consultar_categoria_glpi(nombre_equipo):
    """
    Consulta el endpoint de categorías de GLPI para obtener el ID correspondiente al equipo.
    Usa el parámetro 'search' para filtrar por nombre y 'type=categories' para categorías principales.
    """
    # Primero intentar búsqueda directa por nombre
    url = f"http://localhost:4444/glpi/categories?type=categories&search={nombre_equipo}"
    try:
        logging.info(f"GLPI: Buscando categoría '{nombre_equipo}'...")
        r = requests.get(url, headers={'accept': 'application/json', 'X-API-KEY': config.GLPI_API_KEY}, timeout=5)
        
        if r.status_code == 200:
            categorias = r.json()
            # Buscar coincidencia exacta en los resultados
            for cat in categorias:
                if cat.get('name') == nombre_equipo:
                    logging.info(f"GLPI: Categoría encontrada: {nombre_equipo} -> ID {cat.get('id')}")
                    return cat.get('id')
            # Si no hay coincidencia exacta pero hay resultados, usar el primero
            if categorias:
                logging.info(f"GLPI: Usando primera coincidencia para '{nombre_equipo}' -> ID {categorias[0].get('id')}")
                return categorias[0].get('id')
            logging.warning(f"GLPI: Categoría '{nombre_equipo}' no encontrada")
        else:
            logging.warning(f"GLPI: Error en API categorías. Status: {r.status_code} - Body: {r.text}")
            
    except Exception as e:
        logging.error(f"GLPI: Error de conexión consultando categorías: {e}")
    return None

def obtener_categorias_raiz_glpi():
    """Obtiene todas las categorías principales (itilcategories_id == 0)"""
    # Usamos el endpoint que devuelve todo y filtramos, para consistencia con locations
    url = "http://192.168.4.194:4444/glpi/categories"
    try:
        r = requests.get(url, headers={'accept': 'application/json', 'X-API-KEY': config.GLPI_API_KEY}, timeout=5)
        if r.status_code == 200:
            data = r.json()
            if data:
                # Filtrar raíces: itilcategories_id == 0
                return [c for c in data if c.get('itilcategories_id', 0) == 0]
            logging.warning("GLPI: API retornó lista vacía.")
        else:
            logging.warning(f"GLPI: Error en API categorías. Status: {r.status_code}")
    except Exception as e:
        logging.error(f"GLPI: Error de conexión consultando categorías: {e}")

    # Fallback si falla la API (IDs estimados genéricos)
    logging.warning("GLPI: API falló, usando categorías de respaldo.")
    return [
        {"id": 1, "name": "Computadoras"},
        {"id": 6, "name": "Impresoras"},
        {"id": 2, "name": "Monitores"},
        {"id": 4, "name": "Redes"},
        {"id": 3, "name": "Software"},
        {"id": 5, "name": "Periféricos"},
        {"id": 99, "name": "Otros"},
        {"id": 100, "name": "TELPO"}
    ]


def obtener_subcategorias_glpi(parent_id):
    """Obtiene subcategorías para un ID padre dado (filtros client-side)"""
    url = f"http://192.168.4.194:4444/glpi/categories"
    try:
        r = requests.get(url, headers={'accept': 'application/json', 'X-API-KEY': config.GLPI_API_KEY}, timeout=5)
        if r.status_code == 200:
            all_cats = r.json()
            # Filtrar hijos: itilcategories_id == parent_id
            pid = int(parent_id)
            return [c for c in all_cats if c.get('itilcategories_id') == pid]
            
        logging.warning(f"GLPI: Subcategorías error status {r.status_code}")
    except Exception as e:
        logging.error(f"GLPI: Error obteniendo subcategorías: {e}")
    return []

def consultar_subcategoria_glpi(parent_id, nombre_subcategoria):
    """
    Busca el ID de la subcategoría (Falla específica) dentro de una categoría padre (Equipo).
    Usa parent_id para filtrar hijos de una categoría específica.
    """
    url = f"http://localhost:4444/glpi/categories?parent_id={parent_id}&search={nombre_subcategoria}"
    try:
        logging.info(f"GLPI: Buscando subcategoría '{nombre_subcategoria}' en padre {parent_id}...")
        r = requests.get(url, headers={'accept': 'application/json', 'X-API-KEY': config.GLPI_API_KEY}, timeout=5)
        
        if r.status_code == 200:
            subcategorias = r.json()
            # Buscar coincidencia exacta
            for sub in subcategorias:
                if sub.get('name') == nombre_subcategoria:
                    logging.info(f"GLPI: Subcategoría encontrada: {nombre_subcategoria} -> ID {sub.get('id')}")
                    return sub.get('id')
            # Si no hay coincidencia exacta pero hay resultados, usar el primero
            if subcategorias:
                logging.info(f"GLPI: Usando primera coincidencia para '{nombre_subcategoria}' -> ID {subcategorias[0].get('id')}")
                return subcategorias[0].get('id')
            logging.warning(f"GLPI: Subcategoría '{nombre_subcategoria}' no encontrada para padre {parent_id}")
        else:
            logging.warning(f"GLPI: Error consultando subcategorías. Status: {r.status_code}")
    except Exception as e:
        logging.error(f"GLPI: Error conexión subcategorías: {e}")
    return None

def verificar_usuario_glpi(nombre_usuario):
    """
    Verifica si un usuario existe en GLPI.
    Retorna los datos del usuario si existe, None si no existe.
    """
    # 1. Intentar GET directo con expand_dropdowns
    url = f"http://localhost:4444/glpi/users/{nombre_usuario.lower()}"
    try:
        logging.info(f"GLPI: Verificando usuario '{nombre_usuario}'...")
        r = requests.get(url, headers={'accept': 'application/json', 'X-API-KEY': config.GLPI_API_KEY}, params={"expand_dropdowns": "true"}, timeout=5)
        
        if r.status_code == 200:
            usuario_data = r.json()
            if usuario_data and usuario_data.get('is_active') == 1:
                logging.info(f"GLPI DATA RAW: {json.dumps(usuario_data)}")
                logging.info(f"GLPI: Usuario válido: {usuario_data.get('name')} ({usuario_data.get('firstname')} {usuario_data.get('realname')})")
                # DEBUG: Check keys for phone
                logging.info(f"KEYS: {list(usuario_data.keys())}")
                return usuario_data
            else:
                logging.warning(f"GLPI: Usuario '{nombre_usuario}' existe pero no está activo")
                return None
        elif r.status_code == 404:
            # 2. Fallback: Intentar búsqueda por criterio si el GET directo falla
            logging.info(f"GLPI: Usuario '{nombre_usuario}' no encontrado por ID directo. Intentando búsqueda...")
            url_search = "http://localhost:4444/glpi/search/User"
            # Criterio: name = nombre_usuario
            params = {
                "criteria[0][field]": "name",
                "criteria[0][searchtype]": "equals",
                "criteria[0][value]": nombre_usuario.lower(),
                "forcedisplay[0]": "9", # Realname
                "forcedisplay[1]": "34" # Firstname
            }
            r_search = requests.get(url_search, headers={'accept': 'application/json', 'X-API-KEY': config.GLPI_API_KEY}, params=params, timeout=5)
            if r_search.status_code == 200:
                data_search = r_search.json()
                if data_search and data_search.get('totalcount', 0) > 0:
                    # Parsear resultado de búsqueda (estructura compleja de GLPI)
                    # GLPI devuelve data: [{'1': 'ID', '9': 'Apellido', '34': 'Nombre'}] o similar
                    # Depende de la versión, pero intentaremos mapearlo a la estructura esperada
                    try:
                        first_match = data_search['data'][0]
                        mapped_user = {
                            'id': first_match.get('2', first_match.get('id')), # ID field (usual is 2)
                            'name': nombre_usuario.lower(),
                            'realname': first_match.get('9'),
                            'firstname': first_match.get('34'),
                            'is_active': 1
                        }
                        logging.info(f"GLPI: Usuario encontrado por búsqueda: {mapped_user}")
                        return mapped_user
                    except Exception as parse_err:
                        logging.error(f"GLPI: Error parseando resultado búsqueda: {parse_err}")
            
            logging.warning(f"GLPI: Usuario '{nombre_usuario}' no encontrado en búsqueda.")
            return None
        else:
            logging.warning(f"GLPI: Error consultando usuario. Status: {r.status_code}")
            return None
        return None
    except Exception as e:
        logging.error(f"GLPI: Error conexión usuarios: {e}")
        return None

def obtener_ubicaciones_glpi():
    """
    Obtiene la lista de ubicaciones (Locations) desde GLPI.
    Endpoint: /locations
    Retorna solo ubicaciones raíz (level 1 o locations_id 0).
    """
    url = "http://192.168.4.194:4444/locations"
    try:
        logging.info("GLPI: Consultando ubicaciones...")
        r = requests.get(url, headers={'accept': '*/*', 'X-API-KEY': config.GLPI_API_KEY}, timeout=5)
        
        if r.status_code == 200:
            locations = r.json()
            # Filtrar solo raíces (locations_id == 0 or level == 1)
            roots = [l for l in locations if l.get('locations_id', 0) == 0]
            logging.info(f"GLPI: {len(roots)} ubicaciones raíz encontradas (de {len(locations)} totales).")
            return roots
        else:
            logging.warning(f"GLPI: Error obteniendo ubicaciones. Status: {r.status_code}")
    except Exception as e:
        logging.error(f"GLPI: Error conexión ubicaciones: {e}")
    return []

def obtener_hijos_ubicacion(parent_id):
    """
    Obtiene sub-ubicaciones (hijas) para un ID de ubicación dado.
    Filtrando localmente la lista completa de ubicaciones.
    """
    url = "http://192.168.4.194:4444/locations"
    try:
        logging.info(f"GLPI: Buscando hijas de ubicación {parent_id}...")
        r = requests.get(url, headers={'accept': '*/*', 'X-API-KEY': config.GLPI_API_KEY}, timeout=5)
        
        if r.status_code == 200:
            all_locs = r.json()
            # Filtrar hijos: locations_id == parent_id
            # Asegurar parent_id sea int
            pid = int(parent_id)
            children = [l for l in all_locs if l.get('locations_id') == pid]
            
            if children:
                logging.info(f"GLPI: {len(children)} sub-ubicaciones encontradas para {parent_id}.")
                return children
            else:
                logging.info(f"GLPI: No se encontraron sub-ubicaciones para {parent_id}.")
        else:
            logging.warning(f"GLPI: Error obteniendo hijas. Status: {r.status_code}")
    except Exception as e:
        logging.error(f"GLPI: Error conexión sub-ubicaciones: {e}")
    return []

# --- FUNCIONES DE ENTRADA Y MANTENIMIENTO ---


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja inicio y LOGIN de usuario"""
    preservar_admin_name(context)
    text = update.message.text
    logging.info(f"LOGIN HANDLER: Texto recibido '{text}'. Waiting: {context.user_data.get('waiting_for_login')}, Name: {context.user_data.get('name')}")
    
    esta_logueado = context.user_data.get("name") is not None
    es_comando_start = text.startswith('/start')
    
    # 1. Si NO está logueado y manda texto (sea comando o no) -> Intentar validar como usuario
    if not esta_logueado and text and not es_comando_start:
        usuario = text.strip().lower()
        usuario_glpi = verificar_usuario_glpi(usuario)
        
        if usuario_glpi:
            # Login Exitoso (Directo)
            context.user_data["name"] = usuario_glpi.get('name', usuario)
            context.user_data["user_glpi_data"] = usuario_glpi
            context.user_data.pop("waiting_for_login", None)
            esta_logueado = True
            logging.info(f"LOGIN EXITO DIRECTO: {usuario}")
        elif context.user_data.get("waiting_for_login"):
            # Si estábamos esperando login y falló
            await update.message.reply_text(
                "❌ <b>Usuario no encontrado</b>\n"
                "Por favor, verifique su usuario de red (GLPI) e intente nuevamente:",
                parse_mode="HTML"
            )
            return

    # 2. Si YA está logueado (o se acaba de loguear)
    if esta_logueado:
        user_glpi = context.user_data.get("user_glpi_data", {})
        nombre = f"{user_glpi.get('firstname', '')} {user_glpi.get('realname', '')}".strip() or context.user_data["name"]
        
        msg = (f"👋 <b>Hola, {nombre}</b>\n\n"
               "Estamos listos para recibir tu reporte.\n"
               "👇 <b>Presione el botón para comenzar:</b>")
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=keyboards.kb_start())
        return

    # 3. NO logueado y no se pudo loguear (ej. comando /start o texto inválido sin waiting)
    context.user_data["waiting_for_login"] = True
    msg = ("👋 <b>Bienvenido al Centro de Soporte Técnico SUVE</b>\n\n"
           "🔒 <b>Autenticación Requerida:</b>\n"
           "Por favor, escriba su <b>Usuario de Red</b> (GLPI) para continuar:")
    await update.message.reply_text(msg, parse_mode="HTML")

async def limpiar_sesiones_antiguas(context: ContextTypes.DEFAULT_TYPE):
    # Nota: La limpieza real de sesiones se maneja con conversation_timeout en el handler
    logging.info(f"🧹 Mantenimiento de JobQueue: Sistema activo. Limpieza automática configurada a 2h. Hora: {datetime.now().strftime('%H:%M:%S')}")

async def cancelar_wizard(update, context):
    query = update.callback_query
    msg = "🗑️ <b>Operación Cancelada.</b>\nSe han descartado los datos no guardados."
    if query:
        await query.answer("Operación cancelada")
        await query.edit_message_text(msg, parse_mode="HTML")
    else:
        await update.message.reply_text(msg, parse_mode="HTML")
    preservar_admin_name(context)
    return ConversationHandler.END

async def reset_data(update, context):
    """Limpia los datos de la sesión del usuario (Debug)"""
    context.user_data.clear()
    await update.message.reply_text("🗑️ <b>Datos de sesión borrados.</b>\nPuede iniciar de nuevo con /start o /nuevo.", parse_mode="HTML")
    return ConversationHandler.END

# --- GESTIÓN DE REPORTES (ADMIN Y USUARIO) ---

async def exportar_reporte_csv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in config.ADMIN_IDS: return 
    status_msg = await update.message.reply_text("⏳ <b>Generando informe de gestión...</b>", parse_mode="HTML")
    filename = f"reporte_suve_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    session = Session()
    try:
        tickets = session.query(Incidencia).all()
        with open(filename, mode='w', newline='', encoding='utf-8-sig') as file:
            writer = csv.writer(file, delimiter=';')
            writer.writerow(["ID", "Fecha", "Usuario", "Cédula", "Teléfono", "Email", "Ubicación", "Estado", "Técnico"])
            for t in tickets:
                writer.writerow([t.id, t.fecha_reporte, t.usuario_nombre, t.cedula, t.telf, getattr(t, 'email', 'N/A'), t.ubicacion, t.estado, t.tecnico])
        with open(filename, 'rb') as doc:
            await update.message.reply_document(document=doc, caption=f"📊 <b>Informe SUVE generado correctamente.</b>", parse_mode="HTML")
        await status_msg.delete()
    finally:
        session.close()
        if os.path.exists(filename): os.remove(filename)

async def handle_user_cancel(update, context):
    q = update.callback_query; await q.answer()
    tid = q.data.split("_")[2]
    session = Session()
    try:
        ticket = session.query(Incidencia).filter_by(id=tid).first()
        if ticket:
            ticket.estado = "Anulado por Usuario 🚫"
            session.commit()
            await q.edit_message_text(f"🚫 Has anulado el <b>Ticket #{tid}</b> correctamente.", parse_mode="HTML")
    finally: session.close()

async def handle_noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para botones informativos que no realizan ninguna acción"""
    q = update.callback_query
    # Intentar mostrar el texto del botón como alerta
    button_text = "Información del ticket"
    for row in q.message.reply_markup.inline_keyboard:
        for btn in row:
            if btn.callback_data == q.data:
                button_text = btn.text
                break
    
    await q.answer(f"ℹ️ {button_text}", show_alert=False)

# Mapa manual de nombres conocidos (Fallback si GLPI falla)
KNOWN_ADMINS = {
    "golivier": "Gabriel Olivier",
    "walvarez": "William Alvarez"
}

async def debug_log_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Debug handler to log ALL callbacks"""
    q = update.callback_query
    logging.info(f"🔧 DEBUG CALLBACK RECEIVED: data='{q.data}', from_user='{update.effective_user.username}'")
    # No return or return None allows fallthrough? 
    # Actually handlers in DIFFERENT groups trigger?
    # We will register this in group -1.

async def handle_admin_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    # NO hacer q.answer() aquí - se responde en cada caso específico
    admin_name = update.effective_user.first_name
    parts = q.data.split("_")
    accion, tid = parts[1], parts[2]
    session = Session()
    try:
        ticket = session.query(Incidencia).filter_by(id=tid).first()
        if not ticket: return
        nuevo_kb, estado_visual = None, ""

        # Intento de Auto-Login usando Username de Telegram
        saved_name = context.user_data.get("admin_name")
        if not saved_name and update.effective_user.username:
            posible_user = update.effective_user.username.lower()
            # Asumimos que el username de Telegram puede ser el usuario GLPI
            # (Se verificará más adelante con verificar_usuario_glpi)
            context.user_data["admin_name"] = posible_user
            saved_name = posible_user
            logging.info(f"AUTO-LOGIN: Admin identificado como {saved_name}")

        if accion == "proceso":
            logging.info(f"ADMIN: {admin_name} ha presionado 'Atender Caso' para Ticket #{tid}")
            
            # CAMBIO: Permitir reasignación (Steal ticket)
            if ticket.estado == "En Proceso" and ticket.tecnico:
                logging.info(f"REASSIGN: Admin {admin_name} está tomando el ticket {tid} de {ticket.tecnico}")
                # No retornamos, dejamos pasar para que se reasigne
            
            # CHECK PERSISTENCE - Debug log
            logging.info(f"DEBUG PROCESO: admin_name guardado = '{saved_name}', user_data completo = {context.user_data}")
            
            if saved_name:
                # Ya tiene nombre guardado - Asignar directamente sin preguntar
                estado_visual = "EN PROCESO ⏳"
                ticket.estado, ticket.tecnico, ticket.inicio_atencion = "En Proceso", saved_name, datetime.now()
                
                # Obtener datos del técnico de GLPI
                tecnico_glpi = verificar_usuario_glpi(saved_name)
                tecnico_id = tecnico_glpi.get('id') if tecnico_glpi else None
                # Obtener nombre completo del técnico
                tecnico_nombre_display = saved_name
                # 1. Intentar desde GLPI
                if tecnico_glpi:
                    nombre_tec = f"{tecnico_glpi.get('firstname', '')} {tecnico_glpi.get('realname', '')}".strip()
                    if nombre_tec:
                        tecnico_nombre_display = nombre_tec
                
                # 2. Si sigue siendo igual al username (ej: golivier), intentar mapa manual
                if tecnico_nombre_display == saved_name:
                    tecnico_nombre_display = KNOWN_ADMINS.get(saved_name.lower(), saved_name)
                
                # Usar glpi_ticket_id si existe, sino usar ID local
                glpi_tid = ticket.glpi_ticket_id or tid
                
                # PRIMERO: Liberar técnicos previos
                # DEBUG: Trying to Unassign GLPI ID: {glpi_tid} (Local: {tid})
                res = eliminar_tecnico_ticket_glpi(glpi_tid)
                # DEBUG: Unassign Result: {res}
                
                asignar_ticket_glpi(glpi_tid, saved_name, tecnico_id)
                
                session.commit()
                
                # Actualizar mensaje GRUPO (Mantener botones inline actualizados)
                kb_inline_clean = keyboards.kb_admin_acciones(tid, "En Proceso")
                
                res_text = actualizar_mensaje_ticket(q.message, estado_visual, tecnico_nombre_display, ticket=ticket)
                
                try:
                    await q.answer("✅ Ticket asignado")
                    if q.message.photo: 
                        await q.edit_message_caption(caption=res_text, parse_mode="HTML", reply_markup=kb_inline_clean)
                    else: 
                        await q.edit_message_text(text=res_text, parse_mode="HTML", reply_markup=kb_inline_clean)
                except Exception as e:
                    logging.error(f"Error editando mensaje inline: {e}")
                
                # ENVIAR TECLADO PERSONAL AL ADMIN (Reply Keyboard) - DESACTIVADO POR CLUTTER
                # kb_reply = keyboards.kb_admin_acciones_reply(tid, "En Proceso")
                # try:
                #     # Enviar mensaje privado al admin para activar su teclado
                #     sent_priv = await context.bot.send_message(
                #         chat_id=q.from_user.id,
                #         text=f"✅ <b>Asignado:</b> Has tomado el Ticket #{tid}.\nUsa el teclado abajo para gestionarlo.",
                #         parse_mode="HTML",
                #         reply_markup=kb_reply
                #     )
                #     context.user_data[f"last_card_{tid}"] = sent_priv.message_id
                # except Exception as e:
                #     logging.error(f"Error enviando teclado reply al admin: {e}")
                
                # Notificar al usuario con nombre completo
                if ticket.user_id:
                    try: await context.bot.send_message(ticket.user_id, f"👨‍🔧 <b>Actualización Ticket #{tid}:</b>\nTu caso está siendo atendido por <b>{tecnico_nombre_display}</b>.", parse_mode="HTML")
                    except: pass
                
                return
            else:
                # Primera vez: Pedir nombre de usuario
                context.user_data["admin_assigning_tid"] = tid
                await q.answer()
                sent = await q.message.reply_text(
                    f"👋 <b>Bienvenido</b>\n\n"
                    f"Por favor ingresa tu nombre de usuario:",
                    parse_mode="HTML",
                    reply_markup=ForceReply(selective=True)
                )
                context.user_data['admin_login_msg'] = sent.message_id
                return



        elif accion == "liberar":
            saved_name = context.user_data.get("admin_name")
            # VERIFICACIÓN DE PROPIEDAD: Solo el técnico asignado puede liberar
            if ticket.tecnico and (not saved_name or ticket.tecnico.lower() != saved_name.lower()):
               await q.answer(f"❌ Solo {ticket.tecnico} puede liberar este ticket.", show_alert=True)
               return
            await q.answer("🔓 Ticket liberado")
            estado_visual = "ABIERTO 📂"
            ticket.estado, ticket.tecnico, ticket.inicio_atencion = "Abierto", None, None
            
            # --- GLPI UNASSIGN ---
            try:
                glpi_tid = ticket.glpi_ticket_id or tid
                eliminar_tecnico_ticket_glpi(glpi_tid)
            except Exception as e:
                logging.error(f"Error liberando GLPI: {e}")
            
            nuevo_kb = keyboards.kb_admin_acciones(tid, "Abierto")
            
            if ticket.user_id:
                try: await context.bot.send_message(ticket.user_id, f"🔄 <b>Actualización Ticket #{tid}:</b>\nEl caso ha sido reasignado a la cola de espera.", parse_mode="HTML")
                except: pass
                
        elif accion == "resuelto":
            # VERIFICAR que el admin actual es el técnico asignado
            saved_name = context.user_data.get("admin_name")
            
            # VERIFICACIÓN DE PROPIEDAD: Solo el técnico asignado puede resolver
            if ticket.tecnico and (not saved_name or ticket.tecnico.lower() != saved_name.lower()):
               await q.answer(f"❌ Solo {ticket.tecnico} puede resolver este ticket.", show_alert=True)
               return
            
            await q.answer("✅ Ticket resuelto")
            estado_visual = "RESUELTO ✅"
            ticket.estado, ticket.fin_atencion = "Resuelto", datetime.now()
            
            # --- CALL GLPI COMPLETE API ---
            try:
                # Usar glpi_ticket_id si existe, sino usar ID local
                glpi_tid = ticket.glpi_ticket_id or tid
                glpi_complete_url = f"http://localhost:4444/glpi/tickets/{glpi_tid}/complete"
                r = requests.post(glpi_complete_url, headers={'accept': '*/*', 'Content-Type': 'application/json', 'X-API-KEY': config.GLPI_API_KEY}, json={}, timeout=5)
                logging.info(f"GLPI Complete: {r.status_code} - {r.text} (GLPI ID: {glpi_tid})")
            except Exception as e:
                logging.error(f"GLPI Complete Error: {e}")
            
            # Sin botones (caso cerrado)
            nuevo_kb = None
            
            if ticket.user_id:
                try: 
                    await context.bot.send_message(ticket.user_id, f"✅ <b>Caso Resuelto (#{tid})</b>\nEl soporte técnico ha finalizado. Gracias por confiar en nosotros.", parse_mode="HTML")
                    logging.info(f"DEBUG: Intentando llamar a preguntar_encuesta para User {ticket.user_id}, TID {tid}")
                    await handlers_survey.preguntar_encuesta(update, context, ticket.user_id, tid)
                except: pass
        session.commit()
        
        msg_obj = q.message
        res_text = actualizar_mensaje_ticket(q.message, estado_visual, ticket.tecnico, ticket=ticket)
        if q.message.photo: await q.edit_message_caption(caption=res_text, parse_mode="HTML", reply_markup=nuevo_kb)
        else: await q.edit_message_text(text=res_text, parse_mode="HTML", reply_markup=nuevo_kb)
    finally: session.close()

# --- FLUJO DE CAPTURA (NUEVO REPORTE) ---

async def nuevo_start(update, context):
    """Punto de entrada: Primero pide usuario GLPI, luego sistema de transporte"""
    user_id = str(update.effective_user.id)
    if not context.user_data.get("is_editing"):
        preservar_admin_name(context)
        context.user_data["evidencias"] = []
        session = Session()
    # Buscar ticket previo con usuario válido SOLO si no tenemos uno en memoria
    if not context.user_data.get("name"):
        previo = session.query(Incidencia).filter(
            Incidencia.user_id == user_id,
            Incidencia.usuario_nombre.isnot(None),
            Incidencia.usuario_nombre != 'N/A'
        ).order_by(Incidencia.id.desc()).first()
        
        if previo:
            context.user_data.update({"cached_name": previo.usuario_nombre, "has_cache": True})
        else: 
            context.user_data["has_cache"] = False
    else:
        # Si ya tenemos nombre en memoria, asumimos cache=True para que verificar_usuario_inicio lo use
        context.user_data["has_cache"] = True
        context.user_data["cached_name"] = context.user_data["name"]
        
    session.close()

    # NUEVO FLUJO GLOBAL: El usuario YA debe estar logueado desde /start
    if not context.user_data.get("name"):
        await update.effective_message.reply_text(
            "⚠️ <b>Sesión no iniciada</b>\n\n"
            "Por favor, escribe /start para identificarte antes de iniciar un reporte.",
            parse_mode="HTML"
        )
        return ConversationHandler.END

    # Si ya está logueado, ir directo a transporte
    return await ir_a_transporte(update, context)

# (verificar_usuario_inicio YA NO SE USA, PERO LO DEJAMOS POR COMPATIBILIDAD O BORRAMOS)
async def verificar_usuario_inicio(update, context):
    return await ir_a_transporte(update, context)
    """Verifica usuario cacheado o pide nuevo usuario GLPI al inicio"""
    # Si hay un usuario guardado previamente, validarlo contra GLPI
    if context.user_data.get("has_cache"):
        cached_user = context.user_data.get('cached_name', '').lower()
        
        # Validar usuario cacheado contra GLPI
        usuario_glpi = verificar_usuario_glpi(cached_user)
        if usuario_glpi:
            # Usuario válido - usarlo y continuar a transporte
            context.user_data["name"] = usuario_glpi.get('name', cached_user)
            context.user_data["user_glpi_data"] = usuario_glpi
            
            # Saludo personalizado
            nombre_completo = f"{usuario_glpi.get('firstname', '')} {usuario_glpi.get('realname', '')}".strip()
            if nombre_completo:
                saludo = f"👋 Hola <b>{nombre_completo}</b>\n\n"
            else:
                saludo = f"👋 Hola <b>{cached_user}</b>\n\n"
            
            # Continuar a selección de transporte
            kb = keyboards.agregar_boton_cancelar(keyboards.kb_sistemas())
            text = f"{saludo}🏢 <b>Ubicación del Incidente:</b>\nPor favor, seleccione el Sistema o Sede Administrativa:"
            if update.callback_query: 
                await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
            else: 
                await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
            return TRANSPORT
        else:
            # Usuario cacheado ya no es válido en GLPI - pedir nombre nuevo
            logging.warning(f"Usuario cacheado '{cached_user}' ya no es válido en GLPI")
    
    # Primera vez o usuario inválido - pedir usuario GLPI
    text = "👤 <b>Identificación:</b>\nPor favor, ingrese su nombre de usuario:"
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboards.agregar_boton_cancelar(None))
    else:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboards.agregar_boton_cancelar(None))
    return NAME

async def ir_a_transporte(update, context):
    """Muestra selección de ubicación (Sede) desde GLPI usando ReplyKeyboard visible"""
    # 1. Obtener ubicaciones desde GLPI
    locations = obtener_ubicaciones_glpi()
    
    # Cachear nombres para uso posterior
    context.user_data["locations_cache"] = {str(l['id']): l['name'] for l in locations}
    
    # Iniciar path de ubicación
    context.user_data["location_path"] = []
    
    # Usar ReplyKeyboard (visible en área del teclado)
    # show_change_user=True porque estamos en el nivel raíz
    kb = keyboards.kb_ubicaciones_glpi_reply(locations, show_change_user=True)
    text = "🏢 <b>Ubicación del Incidente:</b>\nPor favor, seleccione la Sede o Sistema correspondiente:"
    
    if update.callback_query:
        await update.callback_query.answer()
        sent = await context.bot.send_message(update.effective_chat.id, text, parse_mode="HTML", reply_markup=kb)
    else:
        sent = await context.bot.send_message(update.effective_chat.id, text, parse_mode="HTML", reply_markup=kb)
    context.user_data['last_bot_msg'] = sent.message_id
    return TRANSPORT

async def nuevo_transport_text(update, context):
    """Procesa selección de ubicación desde ReplyKeyboard (texto del usuario)"""
    text = update.message.text.strip()
    
    # Limpiar chat (borrar mensaje anterior del bot y respuesta del usuario)
    # "Volver" maneja su propia limpieza internamente para poder borrar también el botón presionado
    if text not in ["❌ Cancelar", "👤 Cambiar Usuario", "⬅️ Volver"]:
        await _limpiar_chat(update, context)
    
    # Manejar cancelar
    if text == "❌ Cancelar":
        await update.message.reply_text(
            "🗑️ <b>Operación Cancelada.</b>\nSe han descartado los datos no guardados.",
            parse_mode="HTML",
            reply_markup=keyboards.ReplyKeyboardRemove()
        )
        preservar_admin_name(context)
        return ConversationHandler.END
    
    # Manejar cambio de usuario
    if text == "👤 Cambiar Usuario":
        context.user_data["has_cache"] = False
        context.user_data.pop("name", None)
        context.user_data.pop("user_glpi_data", None)
        context.user_data["waiting_for_login"] = True
        
        await update.message.reply_text(
            "👤 <b>Cambio de Usuario</b>\n\n"
            "Por favor, ingrese su nuevo <b>Usuario de Red</b> (GLPI) para autenticarse:",
            parse_mode="HTML",
            reply_markup=keyboards.ReplyKeyboardRemove()
        )
        return ConversationHandler.END
    
    # Manejar botón Volver
    if text == "⬅️ Volver":
        # Limpiar chat: borrar mensaje anterior del bot y el mensaje del usuario (el botón presionado)
        await _limpiar_chat(update, context)

        path = context.user_data.get("location_path", [])
        if path:
            # Quitar último nivel seleccionado
            path.pop()
            context.user_data["location_path"] = path
        
        if not path:
            # Estamos en el nivel raíz -> Volver a las ubicaciones principales
            locations = obtener_ubicaciones_glpi()
            context.user_data["locations_cache"] = {str(l['id']): l['name'] for l in locations}
            # En raíz mostramos el botón de cambiar usuario
            kb = keyboards.kb_ubicaciones_glpi_reply(locations, show_change_user=True)
            sent = await context.bot.send_message(
                update.effective_chat.id,
                "🏢 <b>Ubicación del Incidente:</b>\nPor favor, seleccione la Sede o Sistema correspondiente:",
                parse_mode="HTML",
                reply_markup=kb
            )
            context.user_data['last_bot_msg'] = sent.message_id
        else:
            # Volver al nivel padre
            parent_name = path[-1]
            # Buscar el ID del padre en el caché
            cache = context.user_data.get("locations_cache", {})
            parent_id = None
            for id_key, name_value in cache.items():
                if name_value == parent_name:
                    parent_id = id_key
                    break
            
            if parent_id:
                hijas = obtener_hijos_ubicacion(parent_id)
                if hijas:
                    new_cache = {str(l['id']): l['name'] for l in hijas}
                    context.user_data["locations_cache"].update(new_cache)
                    # Sub-niveles sin botón de cambiar usuario
                    kb = keyboards.kb_ubicaciones_glpi_reply(hijas, show_change_user=False)
                    sent = await context.bot.send_message(
                        update.effective_chat.id,
                        f"📍 <b>{parent_name}:</b>\nSeleccione el área específica:",
                        parse_mode="HTML",
                        reply_markup=kb
                    )
                    context.user_data['last_bot_msg'] = sent.message_id
                else:
                    # Sin hijas, ir a raíz
                    locations = obtener_ubicaciones_glpi()
                    context.user_data["locations_cache"] = {str(l['id']): l['name'] for l in locations}
                    kb = keyboards.kb_ubicaciones_glpi_reply(locations, show_change_user=True)
                    sent = await context.bot.send_message(
                        update.effective_chat.id,
                        "🏢 <b>Ubicación del Incidente:</b>\nSeleccione la Sede o Sistema correspondiente:",
                        parse_mode="HTML",
                        reply_markup=kb
                    )
                    context.user_data['last_bot_msg'] = sent.message_id
            else:
                # Fallback: ir a raíz
                locations = obtener_ubicaciones_glpi()
                context.user_data["locations_cache"] = {str(l['id']): l['name'] for l in locations}
                kb = keyboards.kb_ubicaciones_glpi_reply(locations, show_change_user=True)
                sent = await context.bot.send_message(
                    update.effective_chat.id,
                    "🏢 <b>Ubicación del Incidente:</b>\nSeleccione la Sede o Sistema correspondiente:",
                    parse_mode="HTML",
                    reply_markup=kb
                )
                context.user_data['last_bot_msg'] = sent.message_id
        return TRANSPORT
    
    # Parsear ubicación del formato: "📍 Nombre" (sin ID visible)
    if not text.startswith("📍"):
        await _enviar_y_guardar(update, context,
            "⚠️ Por favor, use los botones del teclado para seleccionar una ubicación."
        )
        return TRANSPORT
    
    # Extraer nombre quitando el emoji
    loc_name = text.replace("📍", "").strip()
    
    # Buscar ID en el caché usando el nombre
    cache = context.user_data.get("locations_cache", {})
    loc_id = None
    for id_key, name_value in cache.items():
        if name_value == loc_name:
            loc_id = id_key
            break
    
    if not loc_id:
        logging.warning(f"Ubicación '{loc_name}' no encontrada en caché")
        await _enviar_y_guardar(update, context,
            "⚠️ No se pudo identificar la ubicación. Intente de nuevo."
        )
        return TRANSPORT
    
    # Guardar selección actual en contexto
    context.user_data["location"] = loc_name
    
    # Actualizar Path
    if "location_path" not in context.user_data: 
        context.user_data["location_path"] = []
    context.user_data["location_path"].append(loc_name)
    
    context.user_data["transport"] = loc_name  # Mantener compatibilidad
    context.user_data["location_id"] = loc_id
    
    # Actualizar caché
    if "locations_cache" not in context.user_data:
        context.user_data["locations_cache"] = {}
    context.user_data["locations_cache"][loc_id] = loc_name
    
    # VERIFICAR SI TIENE HIJAS (Líneas, estaciones, pisos, etc.)
    hijas = obtener_hijos_ubicacion(loc_id)
    
    if hijas:
        # Si tiene hijas, bajamos un nivel
        logging.info(f"Navegando a sub-ubicaciones de {loc_name} ({loc_id})")
        
        # Actualizar caché con las nuevas ubicaciones
        new_cache = {str(l['id']): l['name'] for l in hijas}
        context.user_data["locations_cache"].update(new_cache)
        
        # Mostrar sub-ubicaciones CON ReplyKeyboard
        kb = keyboards.kb_ubicaciones_glpi_reply(hijas)
        await _enviar_y_guardar(update, context,
            f"📍 <b>{loc_name}:</b>\nSeleccione el área específica:",
            reply_markup=kb
        )
        return TRANSPORT  # Nos quedamos en el mismo estado para seguir navegando
    
    else:
        # Si NO tiene hijas, es el nivel final -> Quitar teclado y continuar
        logging.info(f"Ubicación final seleccionada: {loc_name} ({loc_id})")
        
        # Confirmación de ubicación
        await _enviar_y_guardar(update, context,
            f"✅ Ubicación: <b>{' > '.join(context.user_data.get('location_path', [loc_name]))}</b>",
            reply_markup=keyboards.ReplyKeyboardRemove()
        )
        
        # METROBUS: Pedir número de unidad antes de continuar
        ubicacion_final = ' > '.join(context.user_data.get('location_path', [loc_name]))
        if 'metrobus' in ubicacion_final.lower():
            await _enviar_y_guardar(update, context,
                "🚌 <b>Número de Unidad:</b>\n"
                "Por favor, escriba el número de la unidad Metrobus afectada:",
                reply_markup=keyboards.ReplyKeyboardRemove()
            )
            return UNIT_NUMBER
        
        return await verificar_datos_previos(update, context)

async def nuevo_transport(update, context):
    q = update.callback_query; await q.answer()
    
    # Manejo de "Volver" o cambios si fuera necesario
    if q.data == "change_user":
        # Limpiar usuario guardado y pedir nuevo (Global Login)
        context.user_data["has_cache"] = False
        context.user_data.pop("name", None)
        context.user_data.pop("user_glpi_data", None)
        context.user_data["waiting_for_login"] = True
        
        await q.edit_message_text(
            "👤 <b>Cambio de Usuario</b>\n\n"
            "Por favor, ingrese su nuevo <b>Usuario de Red</b> (GLPI) para autenticarse:", 
            parse_mode="HTML"
        )
        return ConversationHandler.END

    if q.data.startswith("loc_"):
        # Selección de ubicación directa desde GLPI
        loc_id = q.data.replace("loc_", "")
        
        # Recuperar nombre de la caché (o usar ID si no hay caché de este nivel)
        cache = context.user_data.get("locations_cache", {})
        loc_name = cache.get(loc_id, f"Location ID {loc_id}")
        
        # Guardar selección actual en contexto
        context.user_data["location"] = loc_name
        
        # Actualizar Path
        if "location_path" not in context.user_data: context.user_data["location_path"] = []
        context.user_data["location_path"].append(loc_name)
        
        context.user_data["transport"] = loc_name # Mantener compatibilidad
        context.user_data["location_id"] = loc_id
        
        # VERIFICAR SI TIENE HIJAS (Líneas, estaciones, pisos, etc.)
        hijas = obtener_hijos_ubicacion(loc_id)
        
        if hijas:
            # Si tiene hijas, bajamos un nivel
            logging.info(f"Navegando a sub-ubicaciones de {loc_name} ({loc_id})")
            
            # Actualizar caché con las nuevas ubicaciones para poder resolver nombres
            new_cache = {str(l['id']): l['name'] for l in hijas}
            context.user_data["locations_cache"].update(new_cache) # Merge con cache existente
            
            # Mostrar sub-ubicaciones
            kb = keyboards.agregar_boton_cancelar(keyboards.kb_ubicaciones_glpi(hijas))
            await q.edit_message_text(f"📍 <b>{loc_name}:</b>\nSeleccione el área específica:", parse_mode="HTML", reply_markup=kb)
            return TRANSPORT # Nos quedamos en el mismo estado para seguir navegando
            
        else:
            # Si NO tiene hijas, es el nivel final -> Continuar normal
            logging.info(f"Ubicación final seleccionada: {loc_name} ({loc_id})")
            return await verificar_datos_previos(update, context)

    # Fallback por si acaso (no debería ocurrir con el nuevo teclado)
    await q.edit_message_text("⚠️ Opción no válida. Intente de nuevo.", parse_mode="HTML")
    return await ir_a_transporte(update, context)

async def nuevo_linea(update, context):
    # DEPRECADO en favor de flujo directo GLPI, pero mantenido para no romper dependencias inmediatas
    q = update.callback_query; await q.answer()
    return await ir_a_transporte(update, context)

async def nuevo_location(update, context):
    # DEPRECADO: Logic moved to nuevo_transport via GLPI Locations
    return await verificar_datos_previos(update, context)

async def nuevo_unit(update, context):
    """Recibe el número de unidad Metrobus escrito por el usuario"""
    text = update.message.text.strip()
    
    # Manejar cancelar
    if text == "❌ Cancelar":
        try: await update.message.delete()
        except: pass
        await _limpiar_chat(update, context)
        await context.bot.send_message(
            update.effective_chat.id,
            "🗑️ <b>Operación Cancelada.</b>\nSe han descartado los datos no guardados.",
            parse_mode="HTML",
            reply_markup=keyboards.ReplyKeyboardRemove()
        )
        preservar_admin_name(context)
        return ConversationHandler.END
    
    # Validar que sea numérico (solo dígitos, puede tener guion o espacio)
    if not text or not re.match(r'^[\d\s\-]+$', text):
        await _enviar_y_guardar(update, context,
            "⚠️ <b>Formato inválido:</b>\n"
            "El número de unidad debe contener solo dígitos.\n"
            "Por favor, ingrese el número correctamente:"
        )
        return UNIT_NUMBER
    
    # Guardar número de unidad
    context.user_data["unit_number"] = text
    logging.info(f"Unidad Metrobus capturada: {text}")
    
    return await verificar_datos_previos(update, context)

async def verificar_datos_previos(update, context):
    """Después de ubicación, continua a fecha. Modificado para flujo simplificado GLPI"""
    if context.user_data.get("is_editing"): return await mostrar_resumen(update, context)
    
    # AUTO-FECHA
    context.user_data["date_event"] = datetime.now().strftime("%d/%m/%Y")
    
    # Cargar equipos desde GLPI
    equipos_glpi = obtener_categorias_raiz_glpi()
    
    # FILTRO METROBUS: Si es ubicación Metrobus, solo mostrar Bio500 y Telpo
    if _es_metrobus(context):
        equipos_glpi = [e for e in equipos_glpi if "bio500" in e["name"].lower() or "telpo" in e["name"].lower()]
        logging.info(f"FILTRO METROBUS activo - Equipos disponibles: {[e['name'] for e in equipos_glpi]}")
    
    context.user_data["equipos_cache"] = {str(c['id']): c['name'] for c in equipos_glpi}
    
    # Borrar mensaje anterior del bot
    prev = context.user_data.get('last_bot_msg')
    if prev:
        try: await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=prev)
        except: pass
        context.user_data.pop('last_bot_msg', None)
    
    # Usar ReplyKeyboard (visible en área del teclado)
    await _enviar_y_guardar(update, context,
        "🛠️ <b>Equipo Afectado:</b>\nSeleccione el tipo de dispositivo:", 
        reply_markup=keyboards.kb_equipos_reply(equipos_glpi)
    )
    return EQUIPO


async def nuevo_name(update, context):
    """Recibe el nombre de usuario del reportante y valida contra GLPI, luego va a transporte"""
    usuario = update.message.text.strip().lower()  # Convertir a minúsculas
    if len(usuario) < 3:
        await update.message.reply_text(
            "⚠️ <b>Error:</b> El nombre de usuario debe tener al menos 3 caracteres.", 
            parse_mode="HTML",
            reply_markup=keyboards.agregar_boton_cancelar(None)
        )
        return NAME
    
    # Validar usuario contra GLPI
    usuario_glpi = verificar_usuario_glpi(usuario)
    if not usuario_glpi:
        await update.message.reply_text(
            "❌ <b>Usuario no encontrado</b>\n\n"
            "El nombre de usuario ingresado no existe en el sistema o no está activo.\n"
            "Por favor, verifique e intente nuevamente:",
            parse_mode="HTML",
            reply_markup=keyboards.agregar_boton_cancelar(None)
        )
        return NAME
    
    # Usuario válido - guardar datos
    context.user_data["name"] = usuario_glpi.get('name', usuario)
    context.user_data["user_glpi_data"] = usuario_glpi  # Guardar datos completos por si se necesitan
    
    if context.user_data.get("is_editing"): 
        return await mostrar_resumen(update, context)
    
    # Saludo personalizado y continuar a TRANSPORTE (no a fecha)
    nombre_completo = f"{usuario_glpi.get('firstname', '')} {usuario_glpi.get('realname', '')}".strip()
    if nombre_completo:
        saludo = f"👋 Hola <b>{nombre_completo}</b>\n\n"
    else:
        saludo = f"👋 Hola <b>{usuario}</b>\n\n"
    
    # Continuar a selección de transporte
    return await ir_a_transporte(update, context)



async def nuevo_date(update, context):
    if update.callback_query: q = update.callback_query; await q.answer(); context.user_data["date_event"] = q.data.replace("date_", "")
    else: context.user_data["date_event"] = update.message.text
    if context.user_data.get("is_editing"): return await mostrar_resumen(update, context)
    
    # Cargar equipos desde GLPI
    equipos_glpi = obtener_categorias_raiz_glpi()
    if not equipos_glpi:
        await context.bot.send_message(update.effective_chat.id, "⚠️ Error conectando con sistema de tickets. Intente más tarde.")
        return ConversationHandler.END
    
    # FILTRO METROBUS: Usar helper que revisa el path completo
    if _es_metrobus(context):
        equipos_glpi = [e for e in equipos_glpi if "bio500" in e["name"].lower() or "telpo" in e["name"].lower()]

    # Guardar equipos en contexto para poder buscar el nombre luego
    context.user_data["equipos_cache"] = {str(c['id']): c['name'] for c in equipos_glpi}
    
    # Usar _enviar_y_guardar para registrar el mensaje
    await _enviar_y_guardar(update, context,
        "🛠️ <b>Equipo Afectado:</b>\nSeleccione el tipo de dispositivo:",
        reply_markup=keyboards.kb_equipos_reply(equipos_glpi)
    )
    return EQUIPO

async def selec_equipo_text(update, context):
    """Procesa selección de equipo desde ReplyKeyboard"""
    text = update.message.text.strip()
    
    # Limpiar chat
    if text not in ["❌ Cancelar"]:
        await _limpiar_chat(update, context)
    
    # Manejar cancelar
    if text == "❌ Cancelar":
        await update.message.reply_text(
            "🗑️ <b>Operación Cancelada.</b>\nSe han descartado los datos no guardados.",
            parse_mode="HTML",
            reply_markup=keyboards.ReplyKeyboardRemove()
        )
        preservar_admin_name(context)
        return ConversationHandler.END
    
    # Parsear equipo del formato: "💻 Nombre" (sin ID visible)
    if not text.startswith("💻"):
        await _enviar_y_guardar(update, context,
            "⚠️ Por favor, use los botones del teclado para seleccionar un equipo."
        )
        return EQUIPO
    
    # Extraer nombre quitando el emoji
    nombre_equipo = text.replace("💻", "").strip()
    
    # Buscar ID en el caché usando el nombre
    cache = context.user_data.get("equipos_cache", {})
    equipo_id = None
    for id_key, name_value in cache.items():
        if name_value == nombre_equipo:
            equipo_id = id_key
            break
    
    if not equipo_id:
        logging.warning(f"Equipo '{nombre_equipo}' no encontrado en caché")
        await _enviar_y_guardar(update, context,
            "⚠️ No se pudo identificar el equipo. Intente de nuevo."
        )
        return EQUIPO
    
    # Guardar ID y Nombre
    context.user_data["equipo_id"] = equipo_id
    context.user_data["equipo"] = nombre_equipo
    
    # Cargar subcategorías desde GLPI
    subcategorias = obtener_subcategorias_glpi(equipo_id) if equipo_id else []
    
    # Guardar subcategorías en contexto
    context.user_data["fallas_cache"] = {str(s['id']): s['name'] for s in subcategorias}

    # Usar ReplyKeyboard para sub-fallas
    kb_sub = keyboards.kb_sub_falla_reply(subcategorias, equipo_id)
    
    await _enviar_y_guardar(update, context,
        f"✅ Equipo: <b>{nombre_equipo}</b>\n\n🔍 <b>Detalle del Problema:</b>\nSeleccione la falla específica:", 
        reply_markup=kb_sub
    )
    return SUB_FALLA

async def selec_equipo(update, context):
    if update.callback_query: 
        q = update.callback_query; await q.answer()
        equipo_id = q.data.replace("eq_", "")
        
        # Recuperar nombre del equipo
        cache = context.user_data.get("equipos_cache", {})
        nombre_equipo = cache.get(equipo_id, f"Equipo ID {equipo_id}")
        
        # Guardar ID y Nombre
        context.user_data["equipo_id"] = equipo_id
        context.user_data["equipo"] = nombre_equipo
    else: 
        # Fallback por si escriben texto manual (no debería pasar con menu)
        context.user_data["equipo"] = update.message.text
        context.user_data["equipo_id"] = consultar_categoria_glpi(update.message.text) # Intentar buscar ID
    
    # Cargar subcategorías desde GLPI
    equipo_id = context.user_data.get("equipo_id")
    subcategorias = obtener_subcategorias_glpi(equipo_id) if equipo_id else []
    
    # Guardar subcategorías en contexto
    context.user_data["fallas_cache"] = {str(s['id']): s['name'] for s in subcategorias}

    # Usar ReplyKeyboard para sub-fallas
    kb_sub = keyboards.kb_sub_falla_reply(subcategorias, equipo_id)
    
    # Usar _enviar_y_guardar para limpiar anterior y registrar nuevo
    await _enviar_y_guardar(update, context,
        "🔍 <b>Detalle del Problema:</b>\nSeleccione la falla específica:",
        reply_markup=kb_sub
    )
    return SUB_FALLA

async def selec_sub_falla_text(update, context):
    """Procesa selección de sub-falla desde ReplyKeyboard"""
    text = update.message.text.strip()
    
    # Limpiar chat
    if text not in ["❌ Cancelar"]:
        await _limpiar_chat(update, context)
    
    # Manejar cancelar
    if text == "❌ Cancelar":
        await update.message.reply_text(
            "🗑️ <b>Operación Cancelada.</b>\nSe han descartado los datos no guardados.",
            parse_mode="HTML",
            reply_markup=keyboards.ReplyKeyboardRemove()
        )
        preservar_admin_name(context)
        return ConversationHandler.END
    
    # Manejar volver
    if text == "🔙 Volver":
        # Volver a selección de equipos
        equipos_glpi = obtener_categorias_raiz_glpi()
        context.user_data["equipos_cache"] = {str(c['id']): c['name'] for c in equipos_glpi}
        
        await _enviar_y_guardar(update, context,
            "🛠️ <b>Equipo Afectado:</b>\nSeleccione el tipo de dispositivo:", 
            reply_markup=keyboards.kb_equipos_reply(equipos_glpi)
        )
        return EQUIPO
    
    # Manejar "Sin fallas específicas (General)"
    if "Sin fallas específicas" in text or "General" in text:
        # Usar el ID del equipo padre como categoría final
        equipo_id = context.user_data.get("equipo_id")
        context.user_data["sub_falla_seleccion"] = "General"
        context.user_data["categoria_final_id"] = equipo_id
        context.user_data["problem_prefix"] = "[General] "
        
        await _enviar_y_guardar(update, context,
            f"✅ Falla: <b>General</b>\n\n📝 <b>Escriba para agregar descripción adicional:</b>\nAgregue más detalles o presione Omitir:", 
            reply_markup=keyboards.kb_descripcion_reply()
        )
        return PROBLEM
    
    # Parsear sub-falla del formato: "🔸 Nombre" (sin ID visible)
    if not text.startswith("🔸"):
        await _enviar_y_guardar(update, context,
            "⚠️ Por favor, use los botones del teclado para seleccionar una falla."
        )
        return SUB_FALLA
    
    # Extraer nombre quitando el emoji
    seleccion = text.replace("🔸", "").strip()
    
    # Buscar ID en el caché usando el nombre
    cache = context.user_data.get("fallas_cache", {})
    sub_id = None
    for id_key, name_value in cache.items():
        if name_value == seleccion:
            sub_id = id_key
            break
    
    if not sub_id:
        # Si no está en caché, usar el ID del equipo padre
        logging.warning(f"Sub-falla '{seleccion}' no encontrada en caché, usando equipo padre")
        sub_id = context.user_data.get("equipo_id")
    
    # Guardar datos
    context.user_data["sub_falla_seleccion"] = seleccion
    context.user_data["categoria_final_id"] = sub_id
    context.user_data["problem_prefix"] = f"[{seleccion}] "
    
    # Pedir descripción con ReplyKeyboard
    await _enviar_y_guardar(update, context,
        f"✅ Falla: <b>{seleccion}</b>\n\n📝 <b>Escriba para agregar descripción adicional:</b>\nAgregue más detalles o presione Omitir:", 
        reply_markup=keyboards.kb_descripcion_reply()
    )
    return PROBLEM

async def selec_sub_falla(update, context):
    q = update.callback_query; await q.answer()
    
    # Format: subf_SubID o subf_ParentID_General
    parts = q.data.split("_")
    
    seleccion = "General"
    sub_id = parts[1]
    
    if len(parts) > 2 and parts[2] == "General":
        seleccion = "General"
        # Si es general, usamos el ID del padre como categoría final
        context.user_data["categoria_final_id"] = sub_id
    else:
        # Recuperar nombre de la subcategoría
        cache = context.user_data.get("fallas_cache", {})
        seleccion = cache.get(sub_id, f"Falla ID {sub_id}")
        context.user_data["categoria_final_id"] = sub_id

    context.user_data["sub_falla_seleccion"] = seleccion
    context.user_data["problem_prefix"] = f"[{seleccion}] "
    
    # Pedir descripción con ReplyKeyboard
    await _enviar_y_guardar(update, context,
        f"🔸 <b>Seleccionado:</b> {seleccion}\n\n📝 <b>Escriba para agregar descripción adicional:</b>\nAgregue más detalles o presione Omitir:",
        reply_markup=keyboards.kb_descripcion_reply()
    )
    return PROBLEM

async def skip_description(update, context):
    """Maneja omitir descripción desde texto o callback"""
    if update.callback_query:
        q = update.callback_query; await q.answer()
    
    # User chose "Skip", set problem to just the prefix (or a default)
    prefix = context.user_data.get("problem_prefix", "")
    # Remove trailing space for clean save
    context.user_data["problem"] = prefix.strip()
    if "problem_prefix" in context.user_data: del context.user_data["problem_prefix"]
    
    # If Editing, return to summary (skip photo)
    if context.user_data.get("is_editing"): return await mostrar_resumen(update, context)

    # Skip to Photo con ReplyKeyboard
    await _enviar_y_guardar(update, context,
        "📸 <b>Evidencia Fotográfica (Opcional):</b>\nAdjunte una foto/video del error o presione Omitir:", 
        reply_markup=keyboards.kb_omitir_foto_reply()
    )
    return PHOTO

async def nuevo_problem(update, context):
    """Procesa descripción del problema (texto libre o botón omitir)"""
    text = update.message.text.strip()
    
    # Limpiar chat
    if text not in ["❌ Cancelar"]:
        await _limpiar_chat(update, context)
    
    # Manejar cancelar
    if text == "❌ Cancelar":
        await update.message.reply_text(
            "🗑️ <b>Operación Cancelada.</b>\nSe han descartado los datos no guardados.",
            parse_mode="HTML",
            reply_markup=keyboards.ReplyKeyboardRemove()
        )
        preservar_admin_name(context)
        return ConversationHandler.END
    
    # Manejar omitir descripción
    if text == "⏩ Omitir Descripción":
        return await skip_description(update, context)
    
    if len(text) < 10:
        await _enviar_y_guardar(update, context,
            "⚠️ <b>Detalle insuficiente:</b> Por favor explique la falla con más palabras.",
            reply_markup=keyboards.kb_descripcion_reply()
        )
        return PROBLEM
    
    prefix = context.user_data.get("problem_prefix", "")
    context.user_data["problem"] = prefix + text
    
    # Clean up temp keys
    if "problem_prefix" in context.user_data: del context.user_data["problem_prefix"]
    
    if context.user_data.get("is_editing"): return await mostrar_resumen(update, context)
    
    # Ir a foto con ReplyKeyboard
    await _enviar_y_guardar(update, context,
        "📸 <b>Evidencia Fotográfica (Opcional):</b>\nAdjunte una foto/video del error o presione Omitir:", 
        reply_markup=keyboards.kb_omitir_foto_reply()
    )
    return PHOTO

async def nuevo_photo_text(update, context):
    """Procesa texto desde ReplyKeyboard en estado PHOTO"""
    text = update.message.text.strip()
    
    # Limpiar chat
    if text not in ["❌ Cancelar"]:
        await _limpiar_chat(update, context)
    
    # Manejar cancelar
    if text == "❌ Cancelar":
        await update.message.reply_text(
            "🗑️ <b>Operación Cancelada.</b>\nSe han descartado los datos no guardados.",
            parse_mode="HTML",
            reply_markup=keyboards.ReplyKeyboardRemove()
        )
        preservar_admin_name(context)
        return ConversationHandler.END
    
    # Manejar omitir foto
    if text == "⏩ Omitir y Continuar":
        return await mostrar_resumen(update, context)
    
    # Si escribe cualquier otra cosa, ignorar
    await update.message.reply_text(
        "📸 Por favor, adjunte una foto/video o presione el botón para omitir.",
        parse_mode="HTML"
    )
    return PHOTO

async def nuevo_photo(update, context):
    if update.callback_query: await update.callback_query.answer(); return await mostrar_resumen(update, context)
    
    # Detectar si es Foto o Video
    new_file = None
    tipo = "photo"
    
    MAX_SIZE = 10 * 1024 * 1024 # 10 MB
    file_size = 0

    if update.message.photo:
        new_file = update.message.photo[-1].file_id
        file_size = update.message.photo[-1].file_size
    elif update.message.video:
        new_file = update.message.video.file_id
        tipo = "video"
        file_size = update.message.video.file_size
    
    if file_size > MAX_SIZE:
        await update.message.reply_text(
            "⚠️ <b>Archivo muy pesado:</b>\n"
            "El límite es de 10 MB. Por favor intente con un archivo más ligero o una foto.", 
            parse_mode="HTML",
            reply_markup=keyboards.kb_omitir_foto_reply()
        )
        return PHOTO
        
    if new_file:
        if "evidencias" not in context.user_data: context.user_data["evidencias"] = []
        
        # Guardamos diccionario con tipo y file_id
        if len(context.user_data["evidencias"]) >= 3:
            await update.message.reply_text("⚠️ <b>Límite alcanzado:</b> Se aceptan máximo 3 archivos.", parse_mode="HTML")
            return await mostrar_resumen(update, context)

        context.user_data["evidencias"].append({"type": tipo, "file_id": new_file})
        
        cant = len(context.user_data["evidencias"])
        if cant >= 3:
            await update.message.reply_text(
                "✅ <b>Archivos completos.</b> Avanzando...", 
                parse_mode="HTML",
                reply_markup=keyboards.ReplyKeyboardRemove()
            )
            return await mostrar_resumen(update, context)

        # Mostrar cuántas fotos lleva con ReplyKeyboard para continuar
        await update.message.reply_text(
            f"✅ <b>Archivo recibido ({cant}/3).</b> ¿Desea adjuntar otro (Foto/Video)?", 
            parse_mode="HTML", 
            reply_markup=keyboards.kb_omitir_foto_reply()
        )
        return PHOTO
    
    return PHOTO

async def mostrar_resumen(update, context):
    context.user_data["is_editing"] = False
    d = context.user_data; ubi = obtener_ubi(d)
    evidencias = d.get("evidencias", [])
    
    # Obtener nombre completo de GLPI si está disponible
    user_glpi = d.get("user_glpi_data", {})
    nombre_completo = f"{user_glpi.get('firstname', '')} {user_glpi.get('realname', '')}".strip()
    # Mostrar nombre completo si existe, sino username
    usuario_display = safe_html(nombre_completo) if nombre_completo else safe_html(d.get('name'))
    
    # Mostrar número de unidad solo si la ubicación es Metrobus
    ubi_str = obtener_ubi(d)
    unit_number = d.get('unit_number')
    es_metrobus = 'metrobus' in ubi_str.lower()
    linea_unidad = f"🚌 <b>Unidad N°:</b> {safe_html(unit_number)}\n" if (es_metrobus and unit_number) else ""
    
    resumen = (
        f"📋 <b>RESUMEN DE SU REPORTE</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Usuario:</b> {usuario_display}\n"
        f"📍 <b>Ubicación:</b> {ubi_str}\n"
        f"{linea_unidad}"
        f"💻 <b>Equipo:</b> {safe_html(d.get('equipo'))}\n"
        f"⚠️ <b>Falla:</b> {safe_html(d.get('problem'))}\n"
        f"📸 <b>Evidencias:</b> {len(evidencias)} archivo(s)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Use los botones del teclado para editar o enviar:"
    )

    # Usar ReplyKeyboard
    kb = keyboards.kb_confirmar_reply()
    
    # Enviar resumen registrando el mensaje para poder borrarlo luego
    await _enviar_y_guardar(update, context, resumen, reply_markup=kb)
    return CONFIRM_PROBLEM

async def handle_confirm_text(update, context):
    """Procesa selección del resumen desde ReplyKeyboard"""
    text = update.message.text.strip()
    
    # Manejar cancelar
    if text == "❌ CANCELAR":
        await update.message.reply_text(
            "🗑️ <b>Operación Cancelada.</b>\nSe han descartado los datos no guardados.",
            parse_mode="HTML",
            reply_markup=keyboards.ReplyKeyboardRemove()
        )
        preservar_admin_name(context)
        return ConversationHandler.END
    
    # Manejar enviar reporte
    if text == "✅ ENVIAR REPORTE":
        return await finish(update, context)
    
    # Manejar ediciones
    context.user_data["is_editing"] = True
    
    if text == "✏️ Ubicación":
        # Limpiar datos de ubicación anteriores
        context.user_data.pop("transport", None)
        context.user_data.pop("linea", None)
        context.user_data.pop("location", None)
        context.user_data.pop("unit", None)
        context.user_data.pop("location_path", None)
        return await ir_a_transporte(update, context)
    
    if text == "✏️ Equipo":
        equipos = obtener_categorias_raiz_glpi()
        
        # FILTRO METROBUS: Usar helper que revisa el path completo
        if _es_metrobus(context):
            equipos = [e for e in equipos if "bio500" in e["name"].lower() or "telpo" in e["name"].lower()]
        
        context.user_data["equipos_cache"] = {str(c['id']): c['name'] for c in equipos}
        await _enviar_y_guardar(update, context,
            "🛠️ <b>Seleccione el Equipo:</b>",
            reply_markup=keyboards.kb_equipos_reply(equipos)
        )
        return EQUIPO
    
    if text == "✏️ Descripción":
        await _enviar_y_guardar(update, context,
            "✍️ <b>Modo Edición:</b>\nEscriba la nueva descripción del problema:",
            reply_markup=keyboards.kb_descripcion_reply()
        )
        return PROBLEM
    
    if text == "📸 Evidencias":
        context.user_data["evidencias"] = []  # Reset evidencias
        await _enviar_y_guardar(update, context,
            "📸 <b>Nueva Evidencia:</b>\nEnvíe Fotos o Videos del problema:",
            reply_markup=keyboards.kb_omitir_foto_reply()
        )
        return PHOTO
    
    # Si no reconoce el texto, volver a mostrar resumen
    return await mostrar_resumen(update, context)

async def handle_confirm(update, context):
    q = update.callback_query; await q.answer()
    if q.data == "final_check":
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🚀 CONFIRMAR ENVÍO", callback_data="finish_now")], [InlineKeyboardButton("🔙 VOLVER AL RESUMEN", callback_data="back_to_summary")]])
        await q.edit_message_text("⚠️ <b>¿Está seguro de procesar su solicitud?</b>\nUna vez enviada, se generará un ticket de soporte inmediatamente.", parse_mode="HTML", reply_markup=kb)
        return AWAITING_CHOICE
    if q.data == "back_to_summary": return await mostrar_resumen(update, context)
    context.user_data["is_editing"] = True
    map_edit = {"edit_equipo": EQUIPO, "edit_problem": PROBLEM}
    
    if q.data == "edit_photo":
        context.user_data["evidencias"] = [] # Reset evidencias
        await _enviar_y_guardar(update, context,
            "📸 <b>Nueva Evidencia:</b>\nEnvíe Fotos o Videos del problema:",
            reply_markup=keyboards.kb_omitir_foto_reply()
        )
        return PHOTO

    # Special handling for Equipment Edit (needs Keyboard)
    if q.data == "edit_equipo":
        equipos = obtener_categorias_raiz_glpi()
        # FILTRO METROBUS: Usar helper que revisa el path completo
        if _es_metrobus(context):
            equipos = [e for e in equipos if "bio500" in e["name"].lower() or "telpo" in e["name"].lower()]
        
        context.user_data["equipos_cache"] = {str(c['id']): c['name'] for c in equipos}
        await _enviar_y_guardar(update, context,
            "🛠️ <b>Seleccione el Equipo:</b>",
            reply_markup=keyboards.kb_equipos_reply(equipos)
        )
        return EQUIPO
    
    if q.data in map_edit:
        await _enviar_y_guardar(update, context,
            "✍️ <b>Modo Edición:</b>\nPor favor ingrese el nuevo valor:",
            reply_markup=keyboards.kb_descripcion_reply()
        )
        return map_edit[q.data]
    if q.data == "edit_location":
        # Limpiar datos de ubicación anteriores
        context.user_data.pop("transport", None)
        context.user_data.pop("linea", None)
        context.user_data.pop("location", None)
        context.user_data.pop("unit", None)
        context.user_data.pop("location_path", None)
        # Ir directamente a selección de transporte con ReplyKeyboard
        return await ir_a_transporte(update, context)
    return CONFIRM_PROBLEM

async def finish(update, context):
    # Soportar tanto callback (InlineKeyboard) como texto (ReplyKeyboard)
    if update.callback_query:
        q = update.callback_query
        await q.answer()
    
    # Quitar ReplyKeyboard
    if update.message:
        await update.message.reply_text(
            "⏳ <b>Procesando su reporte...</b>",
            parse_mode="HTML",
            reply_markup=keyboards.ReplyKeyboardRemove()
        )
    
    d = context.user_data; session = Session()
    try:
        ubi_f = obtener_ubi(d)
        nueva = Incidencia(usuario_nombre=d.get('name','N/A'), cedula=d.get('cedula','N/A'), telf=d.get('phone','N/A'), email=d.get('email','N/A'),
                           ubicacion=ubi_f, unidad=d.get('unit_number', d.get('unit','N/A')), equipo=d.get('equipo','N/A'), falla=d.get('problem','N/A'), 
                           user_id=str(update.effective_chat.id), estado="Abierto")
        session.add(nueva); session.commit(); tid = nueva.id
        
        # --- ENVIAR A GLPI ---
        # --- ENVIAR A GLPI ---
        try:
            # 1. Intentar usar ID ya capturado (Subcategoría o Equipo raíz)
            cat_id = d.get("categoria_final_id") or d.get("equipo_id")
            
            # Convertir a int si es string, manejar errores
            if cat_id:
                try: cat_id = int(cat_id)
                except: cat_id = None
            
            equipo_seleccionado = d.get('equipo')
            
            # Fallback: Si no hay ID capturado, intentar buscar por nombre (lógica legado)
            if not cat_id and equipo_seleccionado:
                cat_id = consultar_categoria_glpi(equipo_seleccionado)
                sub_falla = d.get("sub_falla_seleccion")
                if cat_id and sub_falla:
                    sub_id = consultar_subcategoria_glpi(cat_id, sub_falla)
                    if sub_id: cat_id = sub_id

            # 3. Fallback / Respaldo si no hay ID
            if not cat_id:
                GLPI_CATEGORIA_MAP = {
                    "Biopago": 1,
                    "Falla con Bio500": 106,
                    "Falla en EVCR": 125,
                    "Falla en Futronic": 108,
                    "Kioscos": 109,
                    "Falla en Telpo": 127
                }
                cat_id = GLPI_CATEGORIA_MAP.get(equipo_seleccionado, 0) # 0 es Default/Raíz si no existe
                logging.info(f"GLPI: Usando ID de respaldo/mapa local: {cat_id}")
            
            logging.info(f"GLPI Debug: Enviando Payload -> ID Cat: {cat_id}")

            title_ticket = f"Reporte #{tid}: {d.get('equipo', 'Equipo')} - {d.get('problem', 'Falla')}"
            
            # Obtener datos de contacto extra de GLPI
            user_glpi = d.get("user_glpi_data", {})
            # Intentar obtener full name, o usar usuario_display que ya tiene nombre formateado, o username raw
            full_name = f"{user_glpi.get('firstname', '')} {user_glpi.get('realname', '')}".strip() or d.get('usuario_display') or d.get('name')
            # Fix: Usar OR para manejar valores None (null en JSON) correctamente
            phone_glpi = user_glpi.get('mobile') or user_glpi.get('phone') or 'N/A'
            
            # Formato mejorado para GLPI (incluir Nº unidad solo si es Metrobus)
            unit_number = d.get('unit_number')
            linea_unidad_glpi = f"🚌 Unidad N°: {unit_number}\n" if ('metrobus' in ubi_f.lower() and unit_number) else ""
            content_ticket = (
                f"👤 Usuario: {full_name}\n"
                f"📱 Teléfono GLPI: {phone_glpi}\n"
                f"📍 Ubicación: {ubi_f}\n"
                f"{linea_unidad_glpi}"
                f"💻 Equipo: {d.get('equipo')}\n"
                f"📝 Descripción: {d.get('problem')}"
            )
            
            # Obtener ID del usuario solicitante de GLPI
            user_glpi_data = d.get("user_glpi_data", {})
            solicitante_id = user_glpi_data.get("id", 0)  # ID del usuario en GLPI
            solicitante_username = d.get('name', '')
            
            # Obtener ID de ubicación GLPI
            loc_id = d.get("location_id")
            if loc_id:
                try: loc_id = int(loc_id)
                except: loc_id = None
            
            # Construir payload en variable para poder imprimirlo
            payload_glpi = {
                # MODO COMPATIBILIDAD (Proxy actual)
                "name": title_ticket,
                "content": content_ticket,
                "urgency": 3,
                "requester_id": solicitante_id,  # ID del solicitante
                "requester_username": solicitante_username,  # Username del solicitante
                "itilcategories_id": cat_id,
                "category_id": cat_id,
                "locations_id": loc_id, # Campo ID ubicación
                
                # MODO ESTÁNDAR GLPI (Estructura anidada)
                "input": {
                    "name": title_ticket,
                    "content": content_ticket,
                    "itilcategories_id": cat_id,
                    "locations_id": loc_id, # Campo ID ubicación
                    "urgency": 3,
                    "_users_id_requester": solicitante_id,  # Campo estándar GLPI para solicitante
                    "type": 1 # 1=Incidente
                }
            }
            
            # --- CONSOLE LOG DEL PAYLOAD ---
            # logging.info(f"🚀 ENVIANDO A GLPI (PAYLOAD): {json.dumps(payload_glpi, indent=2, ensure_ascii=False)}")
            # print(f"🚀 ENVIANDO A GLPI (PAYLOAD): {json.dumps(payload_glpi, indent=2, ensure_ascii=False)}")

            r = requests.post(
                'http://localhost:4444/glpi/tickets',
                headers={'accept': '*/*', 'Content-Type': 'application/json', 'X-API-KEY': config.GLPI_API_KEY},
                json=payload_glpi,
                timeout=5
            )
            logging.info(f"GLPI Respuesta: {r.status_code} - {r.text}")
            
            # Capturar y guardar el ID del ticket en GLPI
            if r.status_code in [200, 201]:
                try:
                    glpi_response = r.json()
                    glpi_tid = glpi_response.get('id')
                    if glpi_tid:
                        nueva.glpi_ticket_id = glpi_tid
                        session.commit()
                        logging.info(f"✅ GLPI Ticket ID guardado: {glpi_tid} (local: {tid})")
                except Exception as parse_err:
                    logging.error(f"Error parseando respuesta GLPI: {parse_err}")
        except Exception as e:
            logging.error(f"Error enviando a GLPI: {e}")

        # Mensaje de confirmación - usar send_message para soportar ReplyKeyboard
        await context.bot.send_message(
            update.effective_chat.id,
            f"✅ <b>¡Solicitud Registrada Exitosamente!</b>\n\n📌 <b>Su Nro. de Ticket: #{tid}</b>\nHemos notificado a nuestro equipo técnico. Le informaremos sobre el avance de su caso.", 
            parse_mode="HTML"
        )
        
        # Fecha formateada
        fecha_reporte = datetime.now().strftime('%d/%m/%Y %I:%M %p')
        evidencias = d.get("evidencias", [])
        # Retrocompatibilidad por si hay sesion vieja con photo_ids
        if not evidencias and "photo_ids" in d:
            evidencias = [{"type": "photo", "file_id": pid} for pid in d["photo_ids"]]

        # Obtener teléfono de los datos de GLPI
        user_glpi = d.get("user_glpi_data", {})
        telefono = user_glpi.get("mobile") or user_glpi.get("phone") or "No disponible"
        nombre_completo = f"{user_glpi.get('firstname', '')} {user_glpi.get('realname', '')}".strip()
        
        msg_adm = (
            f"🚨 <b>NUEVO TICKET DE SOPORTE</b> 🚨\n"
            f"<b>ID:</b> <code>#{tid}</code> | 🕒 {fecha_reporte}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 <b>Usuario:</b> {safe_html(nombre_completo) if nombre_completo else safe_html(d.get('name'))}"
        )
        msg_adm += f"\n📱 <b>Teléfono:</b> {safe_html(telefono)}\n\n"
        # Línea de unidad Metrobus (solo si aplica)
        unit_number_adm = d.get('unit_number')
        linea_unidad_adm = f"🚌 <b>Unidad N°:</b> {safe_html(unit_number_adm)}\n" if ('metrobus' in ubi_f.lower() and unit_number_adm) else ""
        msg_adm += (
            f"📍 <b>Ubicación:</b> {safe_html(ubi_f)}\n"
            f"{linea_unidad_adm}"
            f"💻 <b>Equipo:</b> {safe_html(d.get('equipo'))}\n\n"
            f"⚠️ <b>REPORTE DE FALLA:</b>\n"
            f"<blockquote>{safe_html(d.get('problem'))}</blockquote>"
        )
        
        # CAMBIO: Usar InlineKeyboard (en el mensaje) en lugar de Reply
        kb_adm = keyboards.kb_admin_acciones(tid, "Abierto")
        
        # Enviar al GRUPO de admins (un solo mensaje para todos)
        try:
            sent_msg = None
            has_photo = False
            group_id = config.ADMIN_GROUP_ID
            
            if evidencias:
                # 1. Enviar primera evidencia con el TEXTO y BOTONES
                primera = evidencias[0]
                kwargs = {
                    "chat_id": group_id,
                    "caption": msg_adm,
                    "parse_mode": "HTML",
                    "reply_markup": kb_adm
                }
                if primera["type"] == "video":
                    sent_msg = await context.bot.send_video(video=primera["file_id"], **kwargs)
                else:
                    sent_msg = await context.bot.send_photo(photo=primera["file_id"], **kwargs)
                has_photo = True

                # 2. Enviar evidencias restantes (si existen)
                for i, evi in enumerate(evidencias[1:], start=2):
                    caption_extra = f"📂 <b>Evidencia Adicional ({i}/{len(evidencias)})</b> - Ticket #{tid}"
                    if evi["type"] == "video":
                        await context.bot.send_video(chat_id=group_id, video=evi["file_id"], caption=caption_extra, parse_mode="HTML")
                    else:
                        await context.bot.send_photo(chat_id=group_id, photo=evi["file_id"], caption=caption_extra, parse_mode="HTML")
            else:
                sent_msg = await context.bot.send_message(group_id, msg_adm, parse_mode="HTML", reply_markup=kb_adm)
            
            # Guardar info del mensaje para actualizar después
            if sent_msg:
                nueva.group_message_id = sent_msg.message_id
                nueva.has_photo = has_photo
                session.commit()
                
        except Exception as e:
            logging.error(f"Error notificando grupo de admins: {e}")
    except Exception as e:
        session.rollback(); logging.error(f"DB Error: {e}")
        for adm in config.ADMIN_IDS: await context.bot.send_message(adm, f"🚨 <b>Error Crítico DB:</b> {e}", parse_mode="HTML")
    finally: session.close()
    return ConversationHandler.END

# --- HANDLER DE CONVERSACIÓN ---

async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja la respuesta de texto del admin (Comandos de Teclado o Asignación de Nombre)"""
    text = update.message.text
    # Log para depuración
    logging.info(f"handle_admin_reply: '{text}' (Chat: {update.effective_chat.id}, User: {update.effective_user.first_name})")
    
    # 1. COMANDOS DE TECLADO (Prioridad Alta)
    # Patrón flexible: Palabras clave + #ID (ignorando mayúsculas y espacios extra)
    match = re.search(r".*(Atender|Resuelto|Liberar).*#(\d+)", text, re.IGNORECASE)
    
    if match:
        # Borrar el mensaje del comando para no saturar el chat
        try: await update.message.delete()
        except: pass
        
        accion_text, tid = match.groups()
        logging.info(f"COMANDO ADMIN DETECTADO: {accion_text} en Ticket #{tid}")
        
        session = Session()
        try:
            ticket = session.query(Incidencia).filter_by(id=tid).first()
            if not ticket: 
                await update.message.reply_text("❌ Ticket no encontrado.")
                return

            admin_name = context.user_data.get("admin_name")
            
            # CASO ESPECIAL: "Atender" sin nombre guardado -> Iniciar flujo de pedir nombre
            if not admin_name and "Atender" in accion_text:
                 context.user_data["admin_assigning_tid"] = tid
                 sent = await update.message.reply_text(
                    "👋 <b>Bienvenido</b>\nPor favor, escribe tu <b>usuario de GLPI</b> para asignarte este caso:",
                    parse_mode="HTML", reply_markup=ForceReply(selective=True)
                 )
                 context.user_data['admin_login_msg'] = sent.message_id
                 return

            # Mapear acción textual a clave interna
            act = accion_text.lower()
            accion = "proceso" if "atender" in act else "resuelto" if "resuelto" in act else "liberar"
            estado_visual = ""
            nuevo_kb = None
            
            if accion == "proceso":
                if not admin_name: return # Seguridad extra

                estado_visual = "EN PROCESO ⏳"
                ticket.estado, ticket.tecnico, ticket.inicio_atencion = "En Proceso", admin_name, datetime.now()
                
                # GLPI
                tecnico_glpi = verificar_usuario_glpi(admin_name)
                tecnico_id = tecnico_glpi.get('id') if tecnico_glpi else None
                tecnico_nombre_display = admin_name
                if tecnico_glpi:
                     tecnico_nombre_display = f"{tecnico_glpi.get('firstname', '')} {tecnico_glpi.get('realname', '')}".strip() or admin_name
                
                if tecnico_nombre_display == admin_name:
                     tecnico_nombre_display = KNOWN_ADMINS.get(admin_name.lower(), admin_name)

                glpi_tid = ticket.glpi_ticket_id or tid
                eliminar_tecnico_ticket_glpi(glpi_tid)
                asignar_ticket_glpi(glpi_tid, admin_name, tecnico_id)
                
                nuevo_kb = keyboards.kb_admin_acciones_reply(tid, "En Proceso")
                
                if ticket.user_id:
                     try: await context.bot.send_message(ticket.user_id, f"👨‍🔧 <b>Actualización Ticket #{tid}:</b>\nTu caso está siendo atendido por <b>{tecnico_nombre_display}</b>.", parse_mode="HTML")
                     except: pass
                
                # RESPUESTA AL ADMIN (EN EL MISMO CHAT)
                chat_id = update.effective_chat.id
                
                # Borrar carta anterior si existe
                last_id = context.user_data.get(f"last_card_{tid}")
                if last_id:
                    try: await context.bot.delete_message(chat_id=chat_id, message_id=last_id)
                    except: pass
                
                # Enviar al grupo (donde el usuario interactua) - DESACTIVADO
                # user_mention = update.effective_user.mention_html()
                # sent_card = await context.bot.send_message(chat_id, f"🚀 {user_mention} Has tomado el <b>Ticket #{tid}</b>.\nUsa el teclado para gestionarlo.", parse_mode="HTML", reply_markup=nuevo_kb)
                # context.user_data[f"last_card_{tid}"] = sent_card.message_id

            elif accion == "liberar":
                if ticket.tecnico and (not admin_name or ticket.tecnico.lower() != admin_name.lower()):
                     await update.message.reply_text(f"❌ Solo {ticket.tecnico} puede liberar este ticket.")
                     return

                estado_visual = "ABIERTO 📂"
                ticket.estado, ticket.tecnico = "Abierto", None
                
                glpi_tid = ticket.glpi_ticket_id or tid
                eliminar_tecnico_ticket_glpi(glpi_tid)
                
                nuevo_kb = keyboards.kb_admin_acciones_reply(tid, "Abierto")

                if ticket.user_id:
                     try: await context.bot.send_message(ticket.user_id, f"🔄 <b>Actualización Ticket #{tid}:</b>\nEl caso ha sido reasignado a la cola de espera.", parse_mode="HTML")
                     except: pass
                
                # RESPUESTA AL ADMIN (EN EL MISMO CHAT)
                chat_id = update.effective_chat.id
                
                last_id = context.user_data.get(f"last_card_{tid}")
                if last_id:
                    try: await context.bot.delete_message(chat_id=chat_id, message_id=last_id)
                    except: pass
                    
                # user_mention = update.effective_user.mention_html()
                # sent_card = await context.bot.send_message(chat_id, f"🔓 {user_mention} <b>Ticket #{tid} liberado.</b>\nEl caso ha vuelto a la cola general.", parse_mode="HTML", reply_markup=nuevo_kb)
                # context.user_data[f"last_card_{tid}"] = sent_card.message_id

            elif accion == "resuelto":
                if ticket.tecnico and (not admin_name or ticket.tecnico.lower() != admin_name.lower()):
                     await update.message.reply_text(f"❌ Solo {ticket.tecnico} puede resolver este ticket.")
                     return

                estado_visual = "RESUELTO ✅"
                ticket.estado, ticket.fin_atencion = "Resuelto", datetime.now()
                
                glpi_tid = ticket.glpi_ticket_id or tid
                try:
                    glpi_complete_url = f"http://localhost:4444/glpi/tickets/{glpi_tid}/complete"
                    requests.post(glpi_complete_url, headers={'accept': '*/*', 'Content-Type': 'application/json', 'X-API-KEY': config.GLPI_API_KEY}, json={}, timeout=5)
                except: pass
                
                nuevo_kb = keyboards.ReplyKeyboardRemove()
                
                if ticket.user_id:
                     try: 
                         await context.bot.send_message(ticket.user_id, f"✅ <b>Caso Resuelto (#{tid})</b>\nEl soporte técnico ha finalizado.", parse_mode="HTML")
                         logging.info(f"DEBUG: Intentando llamar a preguntar_encuesta para User {ticket.user_id}, TID {tid} (ReplyHandler)")
                         await handlers_survey.preguntar_encuesta(update, context, ticket.user_id, tid)
                     except: pass
                
                # RESPUESTA AL ADMIN (EN EL MISMO CHAT)
                chat_id = update.effective_chat.id
                
                last_id = context.user_data.get(f"last_card_{tid}")
                if last_id:
                    try: await context.bot.delete_message(chat_id=chat_id, message_id=last_id)
                    except: pass
                    context.user_data.pop(f"last_card_{tid}", None)
                
                # user_mention = update.effective_user.mention_html()
                # await context.bot.send_message(chat_id, f"✅ {user_mention} <b>Ticket #{tid} cerrado.</b>\nSe ha enviado la encuesta al usuario.", parse_mode="HTML", reply_markup=nuevo_kb)

            session.commit()
            
            # Actualizar mensaje grupo
            if ticket.group_message_id:
                info_tec = f"\n👨‍🔧 <b>TÉCNICO ASIGNADO:</b> {ticket.tecnico}" if ticket.tecnico else ""
                fecha_fmt = str(ticket.fecha_reporte)
                if hasattr(ticket.fecha_reporte, 'strftime'):
                    fecha_fmt = ticket.fecha_reporte.strftime('%d/%m/%Y %I:%M %p')
                else:
                    try: fecha_fmt = datetime.fromisoformat(str(ticket.fecha_reporte)).strftime('%d/%m/%Y %I:%M %p')
                    except: pass
                time_str = ""
                if ticket.inicio_atencion:
                     time_str += f"\n⏱ <b>Inicio:</b> {ticket.inicio_atencion.strftime('%d/%m/%Y %I:%M %p')}"
                if "RESUELTO" in estado_visual and ticket.fin_atencion:
                     time_str += f"\n🏁 <b>Cierre:</b> {ticket.fin_atencion.strftime('%d/%m/%Y %I:%M %p')}"

                res_text = (
                    f"🚨 <b>TICKET DE SOPORTE #{tid}</b>\n"
                    f"<b>ID:</b> <code>#{tid}</code> | 🕒 {fecha_fmt}\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"👤 <b>Usuario:</b> {safe_html(ticket.usuario_nombre or 'N/A')}\n"
                    f"📍 <b>Ubicación:</b> {safe_html(ticket.ubicacion or 'N/A')}\n"
                    f"💻 <b>Equipo:</b> {safe_html(ticket.equipo or 'N/A')}\n\n"
                    f"⚠️ <b>REPORTE DE FALLA:</b>\n"
                    f"<blockquote>{safe_html(ticket.falla or 'N/A')}</blockquote>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"📌 <b>ESTADO ACTUAL:</b> <code>{estado_visual}</code>"
                    f"{time_str}"
                    f"{info_tec}"
                )
                kb_inline = None
                if ticket.estado == "Abierto": kb_inline = keyboards.kb_admin_acciones(tid, "Abierto")
                elif ticket.estado == "En Proceso": kb_inline = keyboards.kb_admin_acciones(tid, "En Proceso")

                try:
                    if ticket.has_photo:
                        await context.bot.edit_message_caption(chat_id=config.ADMIN_GROUP_ID, message_id=ticket.group_message_id, caption=res_text, parse_mode="HTML", reply_markup=kb_inline)
                    else:
                        await context.bot.edit_message_text(chat_id=config.ADMIN_GROUP_ID, message_id=ticket.group_message_id, text=res_text, parse_mode="HTML", reply_markup=kb_inline)
                except Exception:
                    # Si no se puede editar, borrar el viejo y enviar uno nuevo
                    try: await context.bot.delete_message(chat_id=config.ADMIN_GROUP_ID, message_id=ticket.group_message_id)
                    except: pass
                    try:
                        new_msg = await context.bot.send_message(config.ADMIN_GROUP_ID, res_text, parse_mode="HTML")
                        ticket.group_message_id = new_msg.message_id
                        ticket.has_photo = False
                        session.commit()
                    except Exception as e:
                        logging.warning(f"Grupo: No se pudo reenviar mensaje: {e}")

        finally: session.close()
        return

    # 2. INGRESO MANUAL DE NOMBRE (Flujo Secundario)
    tid_manual = context.user_data.get("admin_assigning_tid")
    
    if not match and not tid_manual:
         # Si no es match de comando ni asignación pendiente, lo ignoramos (podría ser chat normal)
         return
    
    if tid_manual:
        # --- LOGICA DE NOMBRE MANUAL ---
        nuevo_nombre = text.strip().lower()
        
        # Borrar el mensaje de usuario (su nombre)
        try: await update.message.delete()
        except: pass
        
        tecnico_glpi = verificar_usuario_glpi(nuevo_nombre)
        if not tecnico_glpi:
            await update.message.reply_text(
                "❌ <b>Usuario GLPI no encontrado</b>\nIntente nuevamente:",
                parse_mode="HTML", reply_markup=ForceReply(selective=True)
            )
            return
        
        context.user_data["admin_name"] = nuevo_nombre
        del context.user_data["admin_assigning_tid"]
        
        session = Session()
        try:
            ticket = session.query(Incidencia).filter_by(id=tid_manual).first()
            if ticket and ticket.estado == "Abierto":
                ticket.estado, ticket.tecnico, ticket.inicio_atencion = "En Proceso", nuevo_nombre, datetime.now()
                session.commit()
                
                glpi_tid = ticket.glpi_ticket_id or tid_manual
                eliminar_tecnico_ticket_glpi(glpi_tid)
                asignar_ticket_glpi(glpi_tid, nuevo_nombre, tecnico_glpi.get('id'))
                
                # Borrar prompt de bienvenida anterior
                last_id = context.user_data.get('admin_login_msg')
                if last_id:
                    try: await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=last_id)
                    except: pass
                    context.user_data.pop('admin_login_msg', None)
                
                # nuevo_kb = keyboards.kb_admin_acciones_reply(tid_manual, "En Proceso")
                # sent = await context.bot.send_message(
                #     chat_id=update.effective_chat.id,
                #     text=f"✅ <b>Asignado:</b> Ahora eres el técnico del Ticket #{tid_manual}.",
                #     parse_mode="HTML", reply_markup=nuevo_kb
                # )
                # context.user_data[f"last_card_{tid_manual}"] = sent.message_id
                
                if ticket.user_id:
                     try: await context.bot.send_message(ticket.user_id, f"👨‍🔧 <b>Actualización Ticket #{tid_manual}:</b>\nTu caso está siendo atendido por <b>{nuevo_nombre}</b>.", parse_mode="HTML")
                     except: pass

                if ticket.group_message_id:
                    fecha_fmt = str(ticket.fecha_reporte)
                    if hasattr(ticket.fecha_reporte, 'strftime'):
                        fecha_fmt = ticket.fecha_reporte.strftime('%d/%m/%Y %I:%M %p')
                    else:
                        try: fecha_fmt = datetime.fromisoformat(str(ticket.fecha_reporte)).strftime('%d/%m/%Y %I:%M %p')
                        except: pass
                    time_str = ""
                    if ticket.inicio_atencion:
                         time_str = f"\n⏱ <b>Inicio:</b> {ticket.inicio_atencion.strftime('%d/%m/%Y %I:%M %p')}"

                    res_text = (
                        f"🚨 <b>TICKET DE SOPORTE #{tid_manual}</b>\n"
                        f"<b>ID:</b> <code>#{tid_manual}</code> | 🕒 {fecha_fmt}\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"👤 <b>Usuario:</b> {safe_html(ticket.usuario_nombre or 'N/A')}\n"
                        f"📍 <b>Ubicación:</b> {safe_html(ticket.ubicacion or 'N/A')}\n"
                        f"💻 <b>Equipo:</b> {safe_html(ticket.equipo or 'N/A')}\n\n"
                        f"⚠️ <b>REPORTE DE FALLA:</b>\n"
                        f"<blockquote>{safe_html(ticket.falla or 'N/A')}</blockquote>\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"📌 <b>ESTADO ACTUAL:</b> <code>EN PROCESO ⏳</code>\n"
                        f"{time_str}"
                        f"👨‍🔧 <b>TÉCNICO ASIGNADO:</b> {nuevo_nombre}"
                    )
                    kb_inline = None
                    if ticket.estado == "Abierto": kb_inline = keyboards.kb_admin_acciones(tid_manual, "Abierto")
                    elif ticket.estado == "En Proceso": kb_inline = keyboards.kb_admin_acciones(tid_manual, "En Proceso")

                    try:
                        if ticket.has_photo:
                            await context.bot.edit_message_caption(chat_id=config.ADMIN_GROUP_ID, message_id=ticket.group_message_id, caption=res_text, parse_mode="HTML", reply_markup=kb_inline)
                        else:
                            await context.bot.edit_message_text(chat_id=config.ADMIN_GROUP_ID, message_id=ticket.group_message_id, text=res_text, parse_mode="HTML", reply_markup=kb_inline)
                    except Exception:
                        try: await context.bot.delete_message(chat_id=config.ADMIN_GROUP_ID, message_id=ticket.group_message_id)
                        except: pass
                        try:
                            new_msg = await context.bot.send_message(config.ADMIN_GROUP_ID, res_text, parse_mode="HTML")
                            ticket.group_message_id = new_msg.message_id
                            ticket.has_photo = False
                            session.commit()
                        except: pass
        finally: session.close()
    if match:
        accion_text, tid = match.groups()
        session = Session()
        try:
            ticket = session.query(Incidencia).filter_by(id=tid).first()
            if not ticket: 
                await update.message.reply_text("❌ Ticket no encontrado.")
                return

            admin_name = context.user_data.get("admin_name")
            # Si no tiene nombre guardado, pedirlo (Solo para Atender)
            if not admin_name and "Atender" in accion_text:
                 context.user_data["admin_assigning_tid"] = tid
                 sent = await update.message.reply_text(
                    "👋 <b>Bienvenido</b>\nPor favor ingresa tu nombre de usuario para atender el ticket:",
                    parse_mode="HTML", reply_markup=ForceReply(selective=True)
                 )
                 context.user_data['admin_login_msg'] = sent.message_id
                 return

            # Mapear acción textual a clave interna
            accion = "proceso" if "Atender" in accion_text else "resuelto" if "Resuelto" in accion_text else "liberar"
            
            estado_visual = ""
            nuevo_kb = None
            
            # --- Lógica Copiada/Adaptada de handle_admin_buttons ---
            
            if accion == "proceso":
                # Check persistence
                if not admin_name: # Fallback (ya cubierto arriba pero por seguridad)
                     await update.message.reply_text("Error: Falta nombre de admin.")
                     return

                estado_visual = "EN PROCESO ⏳"
                ticket.estado, ticket.tecnico, ticket.inicio_atencion = "En Proceso", admin_name, datetime.now()
                
                # GLPI APLICACIÓN
                tecnico_glpi = verificar_usuario_glpi(admin_name)
                tecnico_id = tecnico_glpi.get('id') if tecnico_glpi else None
                tecnico_nombre_display = admin_name
                if tecnico_glpi:
                     tecnico_nombre_display = f"{tecnico_glpi.get('firstname', '')} {tecnico_glpi.get('realname', '')}".strip() or admin_name
                
                if tecnico_nombre_display == admin_name:
                     tecnico_nombre_display = KNOWN_ADMINS.get(admin_name.lower(), admin_name)

                glpi_tid = ticket.glpi_ticket_id or tid
                eliminar_tecnico_ticket_glpi(glpi_tid)
                asignar_ticket_glpi(glpi_tid, admin_name, tecnico_id)
                
                nuevo_kb = keyboards.kb_admin_acciones_reply(tid, "En Proceso")
                
                # Notificar usuario
                if ticket.user_id:
                     try: await context.bot.send_message(ticket.user_id, f"👨‍🔧 <b>Actualización Ticket #{tid}:</b>\nTu caso está siendo atendido por <b>{tecnico_nombre_display}</b>.", parse_mode="HTML")
                     except: pass
                
                # Borrar carta anterior
                last_id = context.user_data.get(f"last_card_{tid}")
                if last_id:
                    try: await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=last_id)
                    except: pass
                
                # sent = await context.bot.send_message(update.effective_chat.id, f"✅ Has tomado el Ticket #{tid}", reply_markup=nuevo_kb)
                # context.user_data[f"last_card_{tid}"] = sent.message_id

            elif accion == "liberar":
                # Verificación Propiedad
                if ticket.tecnico and (not admin_name or ticket.tecnico.lower() != admin_name.lower()):
                     await update.message.reply_text(f"❌ Solo {ticket.tecnico} puede liberar este ticket.")
                     return

                estado_visual = "ABIERTO 📂"
                ticket.estado, ticket.tecnico = "Abierto", None
                
                glpi_tid = ticket.glpi_ticket_id or tid
                eliminar_tecnico_ticket_glpi(glpi_tid)
                
                nuevo_kb = keyboards.ReplyKeyboardRemove() # Quitar teclado o volver a estado inicial?
                # Mejor mostrar el teclado de "Abierto" para que otro pueda tomarlo... 
                # Pero ReplyKeyboard es PERSONAL. Si yo lo libero, ¿quiero ver el botón de atender? Sí.
                nuevo_kb = keyboards.kb_admin_acciones_reply(tid, "Abierto")

                if ticket.user_id:
                     try: await context.bot.send_message(ticket.user_id, f"🔄 <b>Actualización Ticket #{tid}:</b>\nEl caso ha sido reasignado a la cola de espera.", parse_mode="HTML")
                     except: pass
                
                # Borrar carta anterior
                last_id = context.user_data.get(f"last_card_{tid}")
                if last_id:
                    try: await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=last_id)
                    except: pass
                
                # sent = await context.bot.send_message(update.effective_chat.id, f"🔓 Ticket #{tid} liberado.", reply_markup=nuevo_kb)
                # context.user_data[f"last_card_{tid}"] = sent.message_id

            elif accion == "resuelto":
                # Verificación propiedad
                if ticket.tecnico and (not admin_name or ticket.tecnico.lower() != admin_name.lower()):
                     await update.message.reply_text(f"❌ Solo {ticket.tecnico} puede resolver este ticket.")
                     return

                estado_visual = "RESUELTO ✅"
                ticket.estado, ticket.fin_atencion = "Resuelto", datetime.now()
                
                glpi_tid = ticket.glpi_ticket_id or tid
                try:
                    glpi_complete_url = f"http://localhost:4444/glpi/tickets/{glpi_tid}/complete"
                    requests.post(glpi_complete_url, headers={'accept': '*/*', 'Content-Type': 'application/json', 'X-API-KEY': config.GLPI_API_KEY}, json={}, timeout=5)
                except: pass
                
                nuevo_kb = keyboards.ReplyKeyboardRemove()
                
                if ticket.user_id:
                     try: 
                         await context.bot.send_message(ticket.user_id, f"✅ <b>Caso Resuelto (#{tid})</b>\nEl soporte técnico ha finalizado.", parse_mode="HTML")
                         await handlers_survey.iniciar_encuesta(update, context, ticket.user_id, tid)
                     except: pass
                
                # Borrar carta anterior
                last_id = context.user_data.get(f"last_card_{tid}")
                if last_id:
                    try: await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=last_id)
                    except: pass
                    context.user_data.pop(f"last_card_{tid}", None)
                
                # await context.bot.send_message(update.effective_chat.id, f"✅ Ticket #{tid} cerrado.", reply_markup=nuevo_kb)

            session.commit()
            
            # --- ACTUALIZAR MENSAJE DEL GRUPO (SI ES POSIBLE) ---
            if ticket.group_message_id:
                info_tec = f"\n👨‍🔧 <b>TÉCNICO ASIGNADO:</b> {ticket.tecnico}" if ticket.tecnico else ""
                fecha_fmt = str(ticket.fecha_reporte)
                if hasattr(ticket.fecha_reporte, 'strftime'):
                    fecha_fmt = ticket.fecha_reporte.strftime('%d/%m/%Y %I:%M %p')
                else:
                    try: fecha_fmt = datetime.fromisoformat(str(ticket.fecha_reporte)).strftime('%d/%m/%Y %I:%M %p')
                    except: pass
                time_str = ""
                if ticket.inicio_atencion:
                     time_str += f"\n⏱ <b>Inicio:</b> {ticket.inicio_atencion.strftime('%d/%m/%Y %I:%M %p')}"
                if "RESUELTO" in estado_visual and ticket.fin_atencion:
                     time_str += f"\n🏁 <b>Cierre:</b> {ticket.fin_atencion.strftime('%d/%m/%Y %I:%M %p')}"

                res_text = (
                    f"🚨 <b>TICKET DE SOPORTE #{tid}</b>\n"
                    f"<b>ID:</b> <code>#{tid}</code> | 🕒 {fecha_fmt}\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"👤 <b>Usuario:</b> {safe_html(ticket.usuario_nombre or 'N/A')}\n"
                    f"📍 <b>Ubicación:</b> {safe_html(ticket.ubicacion or 'N/A')}\n"
                    f"💻 <b>Equipo:</b> {safe_html(ticket.equipo or 'N/A')}\n\n"
                    f"⚠️ <b>REPORTE DE FALLA:</b>\n"
                    f"<blockquote>{safe_html(ticket.falla or 'N/A')}</blockquote>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"📌 <b>ESTADO ACTUAL:</b> <code>{estado_visual}</code>"
                    f"{time_str}"
                    f"{info_tec}"
                )
                kb_inline = None
                if ticket.estado == "Abierto": kb_inline = keyboards.kb_admin_acciones(tid, "Abierto")
                elif ticket.estado == "En Proceso": kb_inline = keyboards.kb_admin_acciones(tid, "En Proceso")

                try:
                    if ticket.has_photo:
                        await context.bot.edit_message_caption(chat_id=config.ADMIN_GROUP_ID, message_id=ticket.group_message_id, caption=res_text, parse_mode="HTML", reply_markup=kb_inline)
                    else:
                        await context.bot.edit_message_text(chat_id=config.ADMIN_GROUP_ID, message_id=ticket.group_message_id, text=res_text, parse_mode="HTML", reply_markup=kb_inline)
                except Exception:
                    try: await context.bot.delete_message(chat_id=config.ADMIN_GROUP_ID, message_id=ticket.group_message_id)
                    except: pass
                    try:
                        new_msg = await context.bot.send_message(config.ADMIN_GROUP_ID, res_text, parse_mode="HTML")
                        ticket.group_message_id = new_msg.message_id
                        ticket.has_photo = False
                        session.commit()
                    except Exception as e:
                        logging.warning(f"Grupo: No se pudo reenviar mensaje: {e}")

        finally: session.close()
        return

    # 2. INGRESO MANUAL DE NOMBRE (Flujo Secundario)
    tid_manual = context.user_data.get("admin_assigning_tid")
    
    if not tid_manual: return # Nada que hacer
    
    nuevo_nombre = text.strip().lower()
    
    tecnico_glpi = verificar_usuario_glpi(nuevo_nombre)
    if not tecnico_glpi:
        await update.message.reply_text(
            "❌ <b>Usuario GLPI no encontrado</b>\nIntente nuevamente:",
            parse_mode="HTML", reply_markup=ForceReply(selective=True)
        )
        return
    
    context.user_data["admin_name"] = nuevo_nombre
    del context.user_data["admin_assigning_tid"]
    
    session = Session()
    try:
        ticket = session.query(Incidencia).filter_by(id=tid_manual).first()
        if ticket and ticket.estado == "Abierto":
            ticket.estado, ticket.tecnico, ticket.inicio_atencion = "En Proceso", nuevo_nombre, datetime.now()
            session.commit()
            
            glpi_tid = ticket.glpi_ticket_id or tid_manual
            eliminar_tecnico_ticket_glpi(glpi_tid)
            asignar_ticket_glpi(glpi_tid, nuevo_nombre, tecnico_glpi.get('id'))
            
            nuevo_kb = keyboards.kb_admin_acciones_reply(tid_manual, "En Proceso")
            await update.message.reply_text(
                f"✅ <b>Asignado:</b> Ahora eres el técnico del Ticket #{tid_manual}.",
                parse_mode="HTML", reply_markup=nuevo_kb
            )
            
            if ticket.user_id:
                 try: await context.bot.send_message(ticket.user_id, f"👨‍🔧 <b>Actualización Ticket #{tid_manual}:</b>\nTu caso está siendo atendido por <b>{nuevo_nombre}</b>.", parse_mode="HTML")
                 except: pass

            if ticket.group_message_id:
                try:
                     res_text = (
                         f"🚨 <b>TICKET DE SOPORTE #{tid_manual}</b>\n"
                         f"🕒 {ticket.fecha_reporte}\n"
                         f"👤 {ticket.usuario_nombre} | 📍 {ticket.ubicacion}\n"
                         f"💻 {ticket.equipo}\n"
                         f"⚠️ {ticket.falla}\n"
                         f"━━━━━━━━━━━━━━━━━━━━\n"
                         f"📌 <b>ESTADO ACTUAL:</b> <code>EN PROCESO ⏳</code>\n"
                         f"👨‍🔧 <b>TÉCNICO ASIGNADO:</b> {nuevo_nombre}"
                     )
                     if ticket.has_photo:
                         await context.bot.edit_message_caption(chat_id=config.ADMIN_GROUP_ID, message_id=ticket.group_message_id, caption=res_text, parse_mode="HTML")
                     else:
                         await context.bot.edit_message_text(chat_id=config.ADMIN_GROUP_ID, message_id=ticket.group_message_id, text=res_text, parse_mode="HTML")
                except: pass
    finally: session.close()

def get_conv_handler():
    ch = CallbackQueryHandler(cancelar_wizard, pattern="^cancel_wizard$")
    bh = CallbackQueryHandler(mostrar_resumen, pattern="^back_to_summary$")
    
    # Handler para ignorar texto inesperado en estados que solo usan botones
    async def ignorar_texto(update, context):
        await update.message.reply_text(
            "⚠️ Por favor use los botones del menú para continuar.",
            parse_mode="HTML"
        )
        # Retorna None para mantener el estado actual
        return None
    
    ignore_text = MessageHandler(filters.TEXT & ~filters.COMMAND, ignorar_texto)
    
    return ConversationHandler(
        entry_points=[CommandHandler("nuevo", nuevo_start), CallbackQueryHandler(nuevo_start, pattern="^command_nuevo$"), MessageHandler(filters.Regex("^🚀 Nuevo Reporte$"), nuevo_start)],
        states={
            TRANSPORT: [ch, CallbackQueryHandler(nuevo_transport), MessageHandler(filters.TEXT & ~filters.COMMAND, nuevo_transport_text)],
            LINE: [ch, CallbackQueryHandler(nuevo_linea), ignore_text],
            LOCATION: [ch, CallbackQueryHandler(nuevo_location), MessageHandler(filters.TEXT & ~filters.COMMAND, nuevo_location)],
            UNIT_NUMBER: [ch, MessageHandler(filters.TEXT & ~filters.COMMAND, nuevo_unit)],
            NAME: [ch, bh, MessageHandler(filters.TEXT & ~filters.COMMAND, nuevo_name)],
            EQUIPO: [ch, bh, CallbackQueryHandler(selec_equipo), MessageHandler(filters.TEXT & ~filters.COMMAND, selec_equipo_text)],
            SUB_FALLA: [ch, CallbackQueryHandler(back_to_equipos_func, pattern="^back_to_equipos$"), CallbackQueryHandler(selec_sub_falla, pattern="^subf_"), MessageHandler(filters.TEXT & ~filters.COMMAND, selec_sub_falla_text)],
            PROBLEM: [ch, bh, CallbackQueryHandler(skip_description, pattern="^skip_desc$"), MessageHandler(filters.TEXT & ~filters.COMMAND, nuevo_problem)],
            PHOTO: [ch, MessageHandler(filters.PHOTO | filters.VIDEO, nuevo_photo), CallbackQueryHandler(nuevo_photo, pattern="^skip_photo$"), MessageHandler(filters.TEXT & ~filters.COMMAND, nuevo_photo_text)],
            CONFIRM_PROBLEM: [CallbackQueryHandler(finish, pattern="^finish_now$"), CallbackQueryHandler(cancelar_wizard, pattern="^cancel_wizard$"), CallbackQueryHandler(handle_confirm, pattern="^edit_"), MessageHandler(filters.TEXT & ~filters.COMMAND, handle_confirm_text)],
            AWAITING_CHOICE: [CallbackQueryHandler(finish, pattern="^finish_now$"), CallbackQueryHandler(mostrar_resumen, pattern="^back_to_summary$"), CallbackQueryHandler(cancelar_wizard, pattern="^cancel_wizard$"), MessageHandler(filters.TEXT & ~filters.COMMAND, handle_confirm_text)],
        },
        fallbacks=[CommandHandler("cancel", cancelar_wizard), ch],
        persistent=True, name="protocolo_suve_v2",
        allow_reentry=True,
        conversation_timeout=7200 # Limpieza automática tras 2 horas de inactividad
    )
    