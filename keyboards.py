from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from datetime import datetime

# --- TECLADOS PRINCIPALES ---

def kb_start():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Iniciar Nuevo Reporte", callback_data="command_nuevo")]
    ])

def kb_sistemas():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚇 Sistema Alpha", callback_data="Alpha"), InlineKeyboardButton("🚄 Sistema Beta", callback_data="Beta")],
        [InlineKeyboardButton("🚉 Sistema Gamma", callback_data="Gamma"), InlineKeyboardButton("🚌 Sistema Delta", callback_data="Delta")],
        [InlineKeyboardButton("🚠 Sistema Epsilon", callback_data="Epsilon"), InlineKeyboardButton("🏢 Sedes Administrativas", callback_data="Admin")],
        [InlineKeyboardButton("🚌 Sistema Zeta", callback_data="Zeta")],
        [InlineKeyboardButton("👤 Cambiar Usuario", callback_data="change_user")]
    ])

def kb_lineas_alpha():
    lineas = ["Línea A", "Línea B", "Línea C", "Línea D", "Línea E"]
    botones = []
    for i in range(0, len(lineas), 2):
        row = [InlineKeyboardButton(f"🔴 {lineas[i]}", callback_data=f"linea_{lineas[i]}")]
        if i+1 < len(lineas):
            row.append(InlineKeyboardButton(f"🔴 {lineas[i+1]}", callback_data=f"linea_{lineas[i+1]}"))
        botones.append(row)
    botones.append([InlineKeyboardButton("⬅️ Volver Atrás", callback_data="back_to_systems")])
    return InlineKeyboardMarkup(botones)

def kb_estaciones(sistema, linea=None):
    # Diccionario de sedes/estaciones
    data = {
        "Alpha": {
            "Línea A": [
                "Sede A1", "Sede A2", "Sede A3", "Sede A4", "Sede A5", 
                "Sede A6", "Sede A7", "Sede A8", "Sede A9", "Sede A10", 
                "Sede A11", "Sede A12", "Sede A13", "Sede A14", 
                "Sede A15", "Sede A16", "Sede A17", "Sede A18", "Sede A19", 
                "Sede A20", "Sede A21", "Sede A22"
            ],
            "Línea B": [
                "Sede B1", "Sede B2", "Sede B3", "Sede B4", "Sede B5", 
                "Sede B6", "Sede B7", "Sede B8", "Sede B9", "Sede B10", 
                "Sede B11", "Sede B12", "Sede B13"
            ],
            "Línea C": [
                "Sede C1", "Sede C2", "Sede C3", "Sede C4", 
                "Sede C5", "Sede C6", "Sede C7", "Sede C8", "Sede C9"
            ],
            "Línea D": [
                "Sede D1", "Sede D2", "Sede D3", "Sede D4", "Sede D5"
            ],
            "Línea E": [
                "Sede E1", "Sede E2"
            ]
        },
        "Beta": ["Sede B1", "Sede B2", "Sede B3", "Sede B4"],
        "Gamma": ["Sede G1", "Sede G2", "Sede G3", "Sede G4"],
        "Delta": {
            "Rutas": ["Ruta 01", "Ruta 02", "Ruta 03", "Ruta 04", "Ruta 05", "Ruta 06"],
            "Línea 7": [
                "Parada 01", "Parada 02", "Parada 03", "Parada 04", "Parada 05", 
                "Parada 06", "Parada 07", "Parada 08", "Parada 09", 
                "Parada 10", "Parada 11"
            ]
        },
        "Epsilon":{
            "Sector A": ["Estación A1", "Estación A2"],
            "Sector B": ["Estación B1", "Estación B2","Estación B3"],
            "Sector C": ["Estación C1", "Estación C2"],
        },
        "Admin": ["Sede Principal", "Sede Alterna"],
        "Zeta": ["Zona A", "Zona B", "Zona C","Zona D"]
    }
    
    if sistema == "Alpha":
        if not linea: return kb_lineas_alpha()
        lista = data["Alpha"].get(linea, [])
        back_data = "back_to_lines"
    elif sistema == "Delta":
        if not linea:
            botones = [
                [InlineKeyboardButton("🚇 Línea 7", callback_data="linea_Línea 7")],
                [InlineKeyboardButton("🚌 Rutas", callback_data="linea_Rutas")],
                [InlineKeyboardButton("⬅️ Volver", callback_data="back_to_systems")]
            ]
            return InlineKeyboardMarkup(botones)
        lista = data["Delta"].get(linea, [])
        back_data = "back_to_lines"
    elif sistema == "Epsilon":
        if not linea:
            botones = [
                [InlineKeyboardButton("🚠 Sector A", callback_data="linea_Sector A")],
                [InlineKeyboardButton("🚠 Sector B", callback_data="linea_Sector B")],
                [InlineKeyboardButton("🚠 Sector C", callback_data="linea_Sector C")],
                [InlineKeyboardButton("⬅️ Volver", callback_data="back_to_systems")]
            ]
            return InlineKeyboardMarkup(botones)
        lista = data["Epsilon"].get(linea, [])
        back_data = "back_to_lines"
    else:
        lista = data.get(sistema, [])
        back_data = "back_to_systems"

    botones = []
    for i in range(0, len(lista), 2):
        fila = [InlineKeyboardButton(f"📍 {lista[i]}", callback_data=f"st_{lista[i]}")]
        if i + 1 < len(lista): 
            fila.append(InlineKeyboardButton(f"📍 {lista[i+1]}", callback_data=f"st_{lista[i+1]}"))
        botones.append(fila)

    botones.append([InlineKeyboardButton("⬅️ Volver", callback_data=back_data)])
    return InlineKeyboardMarkup(botones)

def kb_ubicaciones_glpi(lista_glpi=None):
    """
    Genera teclado de ubicaciones dinámicamente desde GLPI.
    lista_glpi: Lista de dicts [{'id': 1, 'name': 'Sede X'}, ...]
    """
    if not lista_glpi:
        return InlineKeyboardMarkup([[InlineKeyboardButton("⚠️ Error cargando ubicaciones", callback_data="noop")]])

    botones = []
    # Crear filas de 2 botones
    for i in range(0, len(lista_glpi), 2):
        row = []
        loc1 = lista_glpi[i]
        # Usamos el ID en el callback: loc_{id}
        row.append(InlineKeyboardButton(f"📍 {loc1['name']}", callback_data=f"loc_{loc1['id']}"))
        
        if i + 1 < len(lista_glpi):
            loc2 = lista_glpi[i+1]
            row.append(InlineKeyboardButton(f"📍 {loc2['name']}", callback_data=f"loc_{loc2['id']}"))
        
        botones.append(row)
        
    return InlineKeyboardMarkup(botones)

def kb_ubicaciones_glpi_reply(lista_glpi=None, show_change_user=False):
    """
    Genera ReplyKeyboard de ubicaciones (visible en área del teclado).
    Más prominente para el usuario.
    lista_glpi: Lista de dicts [{'id': 1, 'name': 'Sede X'}, ...]
    show_change_user: Si True, muestra el botón de cambiar usuario (solo en nivel raíz).
    """
    if not lista_glpi:
        return ReplyKeyboardMarkup([["⚠️ Error cargando ubicaciones"]], resize_keyboard=True, one_time_keyboard=True)

    botones = []
    # Crear filas de 2 botones
    for i in range(0, len(lista_glpi), 2):
        row = []
        loc1 = lista_glpi[i]
        # Texto limpio sin ID visible
        row.append(KeyboardButton(f"📍 {loc1['name']}"))
        
        if i + 1 < len(lista_glpi):
            loc2 = lista_glpi[i+1]
            row.append(KeyboardButton(f"📍 {loc2['name']}"))
        
        botones.append(row)
        
    # Opción para volver al nivel anterior
    botones.append([KeyboardButton("⬅️ Volver")])
    # Opción para cambiar usuario (solo en nivel raíz)
    if show_change_user:
        botones.append([KeyboardButton("👤 Cambiar Usuario")])
    # Opción para cancelar
    botones.append([KeyboardButton("❌ Cancelar")])
        
    return ReplyKeyboardMarkup(botones, resize_keyboard=True, one_time_keyboard=True)

def kb_tipo_cedula():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇻🇪 V - Venezolano", callback_data="tipo_V"), InlineKeyboardButton("🌍 E - Extranjero", callback_data="tipo_E")]
    ])

def kb_fechas():
    h = datetime.now().strftime("%d/%m/%Y")
    return InlineKeyboardMarkup([[InlineKeyboardButton(f"📅 Reportar con fecha de Hoy ({h})", callback_data=f"date_{h}")]])

def kb_equipos(lista_glpi=None):
    """
    Genera teclado de equipos (categorías raíz) dinámicamente.
    lista_glpi: Lista de dicts [{'id': 1, 'name': 'Nombre'}, ...]
    """
    if not lista_glpi:
        return InlineKeyboardMarkup([[InlineKeyboardButton("⚠️ Error cargando equipos", callback_data="noop")]])

    botones = []
    # Crear filas de 2 botones
    for i in range(0, len(lista_glpi), 2):
        row = []
        cat1 = lista_glpi[i]
        # Usamos el ID en el callback: eq_ID_Nombre (Nombre para visual en resumen)
        # Cortamos el nombre si es muy largo para el callback data limitado de Telegram (64 bytes)
        # Pero mejor guardamos solo el ID y buscamos el nombre luego si es posible.
        # Por simplicidad ahora: eq_{id}
        row.append(InlineKeyboardButton(f"💻 {cat1['name']}", callback_data=f"eq_{cat1['id']}"))
        
        if i + 1 < len(lista_glpi):
            cat2 = lista_glpi[i+1]
            row.append(InlineKeyboardButton(f"💻 {cat2['name']}", callback_data=f"eq_{cat2['id']}"))
        
        botones.append(row)
        
    return InlineKeyboardMarkup(botones)

def kb_omitir_foto():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⏩ Omitir y Continuar", callback_data="skip_photo")]])

def kb_satisfaccion(tid):
    botones = [InlineKeyboardButton(f"{i} ⭐", callback_data=f"srv_sat_{i}_{tid}") for i in range(1, 6)]
    return InlineKeyboardMarkup([botones])

def kb_satisfaccion_reply():
    """ReplyKeyboard para estrellas (1-5) LIMPIO (sin ID visible). 
       Layout: 
       [ ⭐⭐⭐⭐⭐ ]
       [ ⭐⭐⭐⭐ ] [ ⭐⭐⭐ ]
       [ ⭐⭐ ] [ ⭐ ]
    """
    row1 = [KeyboardButton("⭐⭐⭐⭐⭐")]
    row2 = [KeyboardButton("⭐⭐⭐⭐"), KeyboardButton("⭐⭐⭐")]
    row3 = [KeyboardButton("⭐⭐"), KeyboardButton("⭐")]
    return ReplyKeyboardMarkup([row1, row2, row3], resize_keyboard=True, one_time_keyboard=True)

def kb_tiempo(tid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Rápido", callback_data=f"srv_time_Eficiente_{tid}")],
        [InlineKeyboardButton("👍 Normal", callback_data=f"srv_time_Bueno_{tid}")],
        [InlineKeyboardButton("🐢 Lento", callback_data=f"srv_time_Deficiente_{tid}")]
    ])

def kb_tiempo_reply():
    """ReplyKeyboard para tiempo LIMPIO (sin ID visible)"""
    return ReplyKeyboardMarkup([
        [KeyboardButton("⚡ Rápido")],
        [KeyboardButton("👍 Normal")],
        [KeyboardButton("🐢 Lento")]
    ], resize_keyboard=True, one_time_keyboard=True)

def kb_confirmacion_encuesta_reply():
    """ReplyKeyboard para confirmar si el usuario quiere hacer la encuesta"""
    return ReplyKeyboardMarkup([
        [KeyboardButton("⭐ Claro que sí"), KeyboardButton("🚫 Ahora no")]
    ], resize_keyboard=True, one_time_keyboard=True)

def agregar_boton_cancelar(markup):
    cancel_btn = InlineKeyboardButton("❌ Cancelar Operación", callback_data="cancel_wizard")
    if not markup: return InlineKeyboardMarkup([[cancel_btn]])
    
    botones = list(markup.inline_keyboard)
    botones.append([cancel_btn])
    return InlineKeyboardMarkup(botones)

# --- SUB-MENÚS DE FALLAS (Dinámicos desde GLPI) ---

def kb_sub_falla(lista_subcategorias, parent_id):
    """
    Genera teclado de fallas (subcategorías) dinámicamente.
    lista_subcategorias: Lista de dicts [{'id': 10, 'name': 'Falla X'}, ...]
    """
    if not lista_subcategorias: 
        # Si no hay subcategorías, permitir seleccionar la categoría padre directamente o indicar "General"
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("⚠️ Sin fallas específicas (Usar General)", callback_data=f"subf_{parent_id}_General")],
            [InlineKeyboardButton("🔙 Volver", callback_data="back_to_equipos")]
        ])
        
    botones = []
    # Crea botones de 1 columna para que se lea bien el texto
    for sub in lista_subcategorias:
        # callback_data: subf_{id_subcategoria}
        botones.append([InlineKeyboardButton(f"🔸 {sub['name']}", callback_data=f"subf_{sub['id']}")])
    
    botones.append([InlineKeyboardButton("🔙 Volver", callback_data="back_to_equipos")])
    return InlineKeyboardMarkup(botones)

def kb_si_no():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Sí, agregar detalles", callback_data="note_yes")],
        [InlineKeyboardButton("⏩ No, continuar", callback_data="note_no")]
    ])

def kb_cancel_back():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Volver al Resumen", callback_data="back_to_summary")],
        [InlineKeyboardButton("❌ Cancelar Operación", callback_data="cancel_wizard")]
    ])

# =============================================================================
# VERSIONES REPLY KEYBOARD (Visibles en área del teclado)
# =============================================================================

def kb_equipos_reply(lista_glpi=None):
    """
    Genera ReplyKeyboard de equipos (visible en área del teclado).
    lista_glpi: Lista de dicts [{'id': 1, 'name': 'Nombre'}, ...]
    """
    if not lista_glpi:
        return ReplyKeyboardMarkup([["⚠️ Error cargando equipos"]], resize_keyboard=True, one_time_keyboard=True)

    botones = []
    # Crear filas de 2 botones
    for i in range(0, len(lista_glpi), 2):
        row = []
        cat1 = lista_glpi[i]
        row.append(KeyboardButton(f"💻 {cat1['name']}"))
        
        if i + 1 < len(lista_glpi):
            cat2 = lista_glpi[i+1]
            row.append(KeyboardButton(f"💻 {cat2['name']}"))
        
        botones.append(row)
    
    # Cancelar
    botones.append([KeyboardButton("❌ Cancelar")])
        
    return ReplyKeyboardMarkup(botones, resize_keyboard=True, one_time_keyboard=True)

def kb_sub_falla_reply(lista_subcategorias, parent_id):
    """
    Genera ReplyKeyboard de fallas (visible en área del teclado).
    lista_subcategorias: Lista de dicts [{'id': 10, 'name': 'Falla X'}, ...]
    """
    if not lista_subcategorias: 
        return ReplyKeyboardMarkup([
            ["⚠️ Sin fallas específicas (General)"],
            ["🔙 Volver"],
            ["❌ Cancelar"]
        ], resize_keyboard=True, one_time_keyboard=True)
        
    botones = []
    # Crear botones de 1 columna para mejor lectura
    for sub in lista_subcategorias:
        botones.append([KeyboardButton(f"🔸 {sub['name']}")])
    
    botones.append([KeyboardButton("🔙 Volver")])
    botones.append([KeyboardButton("❌ Cancelar")])
    return ReplyKeyboardMarkup(botones, resize_keyboard=True, one_time_keyboard=True)

def kb_omitir_foto_reply():
    """ReplyKeyboard para omitir foto"""
    return ReplyKeyboardMarkup([
        [KeyboardButton("⏩ Omitir y Continuar")],
        [KeyboardButton("❌ Cancelar")]
    ], resize_keyboard=True, one_time_keyboard=True)

def kb_confirmar_reply():
    """ReplyKeyboard para confirmar envío"""
    return ReplyKeyboardMarkup([
        [KeyboardButton("✅ ENVIAR REPORTE")],
        [KeyboardButton("✏️ Ubicación"), KeyboardButton("✏️ Equipo")],
        [KeyboardButton("✏️ Descripción"), KeyboardButton("📸 Evidencias")],
        [KeyboardButton("❌ CANCELAR")]
    ], resize_keyboard=True, one_time_keyboard=True)

def kb_descripcion_reply():
    """ReplyKeyboard para la pantalla de descripción"""
    return ReplyKeyboardMarkup([
        [KeyboardButton("⏩ Omitir Descripción")],
        [KeyboardButton("❌ Cancelar")]
    ], resize_keyboard=True, one_time_keyboard=True)

# --- ADMIN BUTTONS ---

def kb_admin_acciones(tid, estado="Abierto"):
    """
    Genera el teclado de acciones para administradores de forma dinámica.
    - Abierto: Solo botón 'Atender Caso'
    - En Proceso: Botones 'Reasignar', 'Liberar', 'Resuelto'
    """
    if estado == "Abierto":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("⏳ Atender Caso", callback_data=f"status_proceso_{tid}")]
        ])
    
    # Estado: En Proceso
    # Botón reasignar eliminado por solicitud
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Resuelto", callback_data=f"status_resuelto_{tid}"), InlineKeyboardButton("🔓 Liberar", callback_data=f"status_liberar_{tid}")]
    ])

def kb_admin_acciones_reply(tid, estado="Abierto"):
    """
    Genera ReplyKeyboardMarkup para administradores (en el teclado).
    Incluye el ID del ticket en el texto para identificar la acción.
    """
    if estado == "Abierto":
        return ReplyKeyboardMarkup([
            [KeyboardButton(f"⏳ Atender #{tid}")]
        ], resize_keyboard=True, one_time_keyboard=False)
    
    # Estado: En Proceso
    return ReplyKeyboardMarkup([
        [KeyboardButton(f"✅ Resuelto #{tid}"), KeyboardButton(f"🔓 Liberar #{tid}")]
    ], resize_keyboard=True, one_time_keyboard=False)

def kb_post_encuesta_reply():
    """ReplyKeyboard para mostrar despues de la encuesta"""
    return ReplyKeyboardMarkup([
        [KeyboardButton("🚀 Nuevo Reporte")]
    ], resize_keyboard=True, one_time_keyboard=True)