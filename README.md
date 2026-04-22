# 🤖 Bot de Soporte Técnico Universal

Bot de Telegram para la gestión automatizada de tickets de soporte técnico, integrado con **GLPI** como sistema de gestión de incidencias.

Diseñado para organizaciones de transporte público, permite a los usuarios reportar fallas técnicas desde cualquier estación o sede, mientras los técnicos gestionan los casos en tiempo real desde un grupo de Telegram.

---

## ✨ Características Principales

| Módulo | Descripción |
|---|---|
| **🔒 Autenticación** | Login con usuario de red (validación contra GLPI en tiempo real) |
| **📍 Ubicaciones Dinámicas** | Menú jerárquico de ubicaciones cargado desde GLPI (sistemas, líneas, estaciones) |
| **💻 Categorización Inteligente** | Selección de equipo y tipo de falla con subcategorías dinámicas desde GLPI |
| **📸 Evidencia Multimedia** | Soporte para adjuntar fotos/videos como evidencia del reporte |
| **🔄 Sincronización GLPI** | Creación automática de tickets en GLPI con ubicación, categoría y asignación |
| **👨‍🔧 Panel de Técnicos** | Botones inline para atender, reasignar, liberar y resolver tickets |
| **📊 Encuestas de Satisfacción** | Encuesta post-resolución con calificación por estrellas y percepción de tiempo |
| **📈 Reportes CSV** | Exportación de informes con comando `/reporte` |
| **♻️ Hot Reload** | Reinicio automático del bot al detectar cambios en el código (`run_bot.py`) |

---

## 🏗️ Arquitectura

```
bot_soporte/
├── main.py              # Punto de entrada: registra handlers y arranca el polling
├── run_bot.py           # Wrapper con hot-reload (watchdog)
├── config.py            # Configuración: tokens y variables de entorno
├── database.py          # Modelo SQLAlchemy (SQLite) para incidencias
├── handlers_user.py     # Lógica principal: flujo de reporte, panel admin, GLPI API
├── handlers_survey.py   # Encuestas de satisfacción post-resolución
├── keyboards.py         # Teclados inline y reply (ubicaciones, equipos, fallas)
└── check_db.py          # Utilidad para inspeccionar la base de datos
```

---

## 🛠️ Tecnologías

- **Python 3.10+**
- **python-telegram-bot** v20+ (async)
- **SQLAlchemy** — ORM para persistencia local (SQLite)
- **GLPI REST API** — Integración bidireccional (tickets, usuarios, categorías, ubicaciones)
- **watchdog** — Hot reload en desarrollo
- **certifi** — Manejo de certificados SSL

---

## 🚀 Instalación

### 1. Clonar el repositorio

```bash
git clone https://github.com/Gabodat/bot_soporte.git
cd bot_soporte
```

### 2. Crear entorno virtual

```bash
python -m venv .venv
# Windows
.\.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate
```

### 3. Instalar dependencias

```bash
pip install python-telegram-bot python-dotenv sqlalchemy certifi watchdog requests
```

### 4. Configurar variables de entorno

Crea un archivo `test.env` en la raíz:

```env
BOT_TOKEN=tu_token_de_bot_aqui
ADMIN_IDS=123456789,987654321
ADMIN_GROUP_ID=-100XXXXXXXXXX
GLPI_API_KEY=tu_api_key_aqui
```

### 5. Ejecutar

```bash
# Modo desarrollo (con hot reload)
python run_bot.py

# Modo directo
python main.py
```

---

## 📋 Flujo de Uso

### Usuario
1. Inicia el bot con `/start`
2. Se autentica con su usuario de red (GLPI)
3. Selecciona ubicación → equipo → tipo de falla
4. Opcionalmente adjunta evidencia fotográfica
5. Confirma y envía el reporte
6. Recibe notificaciones del progreso de su ticket
7. Completa encuesta de satisfacción al cierre

### Técnico (Admin)
1. Recibe notificación en el grupo de administradores
2. Presiona "Atender Caso" (se asigna automáticamente en GLPI)
3. Marca como "Resuelto" o "Liberar" según corresponda
4. El sistema registra tiempos de atención

---

## 📄 Licencia

MIT © 2026
