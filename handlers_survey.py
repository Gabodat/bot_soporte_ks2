import logging, re
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import ContextTypes
from database import Session, Incidencia
import config, keyboards

async def _borrar_anterior(context, chat_id):
    """Borra el mensaje anterior de la encuesta si existe"""
    msg_id = context.user_data.get('survey_last_msg')
    if msg_id:
        try: await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except: pass
        context.user_data.pop('survey_last_msg', None)

async def preguntar_encuesta(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id, tid):
    """Paso previo: Preguntar al usuario si desea tomar la encuesta"""
    texto = "👋 <b>¡Hola!</b>\n¿Podrías regalarnos un segundo de tu tiempo para responder una breve encuesta?"
    
    context.user_data['survey_check_tid'] = tid
    kb = keyboards.kb_confirmacion_encuesta_reply()
    
    try:
        sent = await context.bot.send_message(chat_id=user_id, text=texto, parse_mode="HTML", reply_markup=kb)
        context.user_data['survey_last_msg'] = sent.message_id
    except Exception as e:
        logging.error(f"Error enviando pregunta encuesta: {e}")

async def handle_survey_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja la respuesta de si quiere hacer la encuesta o no"""
    text = update.message.text
    tid = context.user_data.get('survey_check_tid')
    
    if not tid: return
    
    # Borrar respuesta del usuario y mensaje anterior
    try: await update.message.delete()
    except: pass
    await _borrar_anterior(context, update.effective_chat.id)
    
    # Patrones
    match_yes = re.match(r"^(⭐ Claro que sí)$", text)
    match_no = re.match(r"^(🚫 Ahora no)$", text)
    
    if match_yes:
        await iniciar_encuesta_internal(update, context, update.effective_user.id, tid)
        context.user_data.pop('survey_check_tid', None)
    elif match_no:
        sent = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="👍 <b>Entendido</b>\n¡Gracias por confiar en nosotros! Que tengas un excelente día.",
            parse_mode="HTML",
            reply_markup=keyboards.kb_post_encuesta_reply()
        )
        context.user_data.pop('survey_check_tid', None)

async def iniciar_encuesta_internal(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id, tid):
    texto = (f"⭐ <b>Encuesta de Satisfacción</b>\n"
             f"Nos gustaría saber su opinión sobre la atención recibida en el ticket <b>#{tid}</b>.\n"
             f"Por favor califique del 1 al 5:")
    
    context.user_data['survey_ticket_id'] = tid
    kb = keyboards.kb_satisfaccion_reply()
    
    try:
        sent = await context.bot.send_message(chat_id=user_id, text=texto, parse_mode="HTML", reply_markup=kb)
        context.user_data['survey_last_msg'] = sent.message_id
    except Exception as e:
        logging.error(f"Error enviando encuesta: {e}")

# Mantener firma original para compatibilidad
async def iniciar_encuesta(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id, tid):
    await iniciar_encuesta_internal(update, context, user_id, tid)


async def handle_survey_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja respuestas de encuesta via texto (ReplyKeyboard)"""
    text = update.message.text
    tid = context.user_data.get('survey_ticket_id')
    
    if not tid: return # No hay encuesta activa
    
    # Patrones Limpios
    match_sat = re.match(r"^(⭐+)$", text) # Coincide con 1 a 5 estrellas
    match_time = re.match(r"^(⚡ Rápido|👍 Normal|🐢 Lento)$", text)
    
    if not (match_sat or match_time): return 

    # Borrar respuesta del usuario y mensaje anterior
    try: await update.message.delete()
    except: pass
    await _borrar_anterior(context, update.effective_chat.id)
    
    session = Session()
    try:
        if match_sat:
            stars = len(match_sat.group(1)) # Contar estrellas
            t = session.query(Incidencia).filter_by(id=tid).first()
            if t:
                t.satisfaccion = stars
                session.commit()
                # Preguntar siguiente paso: Tiempo
                kb_next = keyboards.kb_tiempo_reply()
                sent = await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="⏳ <b>¿Cómo califica el tiempo de respuesta?</b>", 
                    parse_mode="HTML", reply_markup=kb_next
                )
                context.user_data['survey_last_msg'] = sent.message_id
                
        elif match_time:
            val_raw = match_time.group(1)
            map_time = {"⚡ Rápido": "Eficiente", "👍 Normal": "Bueno", "🐢 Lento": "Deficiente"}
            val_clean = map_time.get(val_raw, "Bueno")
            
            t = session.query(Incidencia).filter_by(id=tid).first()
            if t:
                t.tiempo_percibido = val_clean
                session.commit()
                
                # Fin de encuesta
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="✨ <b>¡Muchas Gracias!</b>\nSus comentarios han sido registrados correctamente.", 
                    parse_mode="HTML", 
                    reply_markup=keyboards.kb_post_encuesta_reply()
                )
                # Limpiar contexto
                context.user_data.pop('survey_ticket_id', None)
                context.user_data.pop('survey_last_msg', None)
                
                # Notificar al grupo de admins
                try:
                    tecnico = t.tecnico or "N/A"
                    stars_str = "⭐" * (t.satisfaccion or 0)
                    msg_admin = (
                        f"📊 <b>NUEVA ENCUESTA RECIBIDA</b>\n"
                        f"━━━━━━━━━━━━━━━━\n"
                        f"<b>Ticket:</b> #{tid}\n"
                        f"👨‍🔧 <b>Técnico:</b> {tecnico}\n"
                        f"🌟 <b>Calificación:</b> {stars_str} ({t.satisfaccion}/5)\n"
                        f"⏳ <b>Velocidad:</b> {val_raw}\n"
                        f"━━━━━━━━━━━━━━━━"
                    )
                    await context.bot.send_message(config.ADMIN_GROUP_ID, msg_admin, parse_mode="HTML")
                    logging.info(f"SURVEY: Notificación enviada al grupo de admins")
                except Exception as e:
                    logging.error(f"SURVEY: Error notificando grupo: {e}")
    except Exception as e:
        logging.error(f"Error procesando encuesta texto: {e}")
    finally: session.close()

# Dejar handle_survey para compatibilidad si quedan botones inline viejos flotando
async def handle_survey(update, context):
    pass 
