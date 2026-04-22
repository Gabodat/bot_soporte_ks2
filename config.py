import os
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv("test.env")

# Token del bot (obligatorio)
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN no configurado. Revise su archivo test.env")

# IDs de administradores
admin_raw = os.getenv("ADMIN_IDS")
if admin_raw:
    ADMIN_IDS = [int(i.strip()) for i in admin_raw.split(",")]
else:
    ADMIN_IDS = []

# ID del grupo de administradores
ADMIN_GROUP_ID = int(os.getenv("ADMIN_GROUP_ID", "0"))

# Configuración de GLPI
GLPI_URL = os.getenv("GLPI_URL", "http://your-glpi-server/glpi")
GLPI_API_KEY = os.getenv("GLPI_API_KEY")
if not GLPI_API_KEY:
    raise ValueError("❌ GLPI_API_KEY no configurado. Revise su archivo test.env")