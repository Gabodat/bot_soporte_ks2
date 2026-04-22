import os, certifi, logging
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, PicklePersistence
import config, handlers_user, handlers_survey

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
os.environ['SSL_CERT_FILE'] = certifi.where()

def main():
    # Configuración de Persistencia Robusta
    # update_interval=30: Guarda en disco cada 30 segundos
    persistencia = PicklePersistence(filepath='bot_session.pickle', update_interval=30)

    # Construimos la app activando la JobQueue
    # Construimos la app activando la JobQueue
    app = ApplicationBuilder() \
        .token(config.BOT_TOKEN) \
        .persistence(persistence=persistencia) \
        .read_timeout(30) \
        .connect_timeout(30) \
        .build()
    
    # --- PROGRAMACIÓN DE LIMPIEZA AUTOMÁTICA ---
    # Ejecuta la función cada 3600 segundos (1 hora)
    # Ejecuta la función cada 3600 segundos (1 hora)
    app.job_queue.run_repeating(handlers_user.limpiar_sesiones_antiguas, interval=3600, first=10)

    # 0. DEBUG CALLBACKS (Group -1)
    app.add_handler(CallbackQueryHandler(handlers_user.debug_log_callback), group=-1)

    # 1. Comandos y Clics Globales
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handlers_user.handle_admin_reply), group=2) # Handler para nombre admin manual (lower priority)
    app.add_handler(CallbackQueryHandler(handlers_user.handle_user_cancel, pattern="^user_cancel_"))
    app.add_handler(CallbackQueryHandler(handlers_user.handle_noop, pattern="^noop$"))  # Handler para botones informativos
    app.add_handler(CallbackQueryHandler(handlers_user.handle_admin_buttons, pattern=r"^(admin_|status_)"))
    app.add_handler(CommandHandler("reporte", handlers_user.exportar_reporte_csv))
    app.add_handler(CommandHandler("reset", handlers_user.reset_data))
    app.add_handler(CallbackQueryHandler(handlers_survey.handle_survey, pattern="^(survey_|srv_)"))
    
    # Handler para Confirmación de Encuesta (PRIMERO)
    app.add_handler(MessageHandler(filters.Regex(r"^(⭐ Claro que sí|🚫 Ahora no)"), handlers_survey.handle_survey_confirmation))

    # Handler para Encuesta por Texto
    # Regex ajustado para solo capturar estrellas exactas o tiempos
    app.add_handler(MessageHandler(filters.Regex(r"^(⭐+$|⚡ Rápido|👍 Normal|🐢 Lento)"), handlers_survey.handle_survey_reply))

    
    # 2. Flujo de Reporte
    app.add_handler(handlers_user.get_conv_handler())

    # 3. Bienvenida
    app.add_handler(CommandHandler("start", handlers_user.start))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND) & filters.ChatType.PRIVATE, handlers_user.start))

    print("🚀 Sistema SUVE Operativo con Limpieza Automática...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()