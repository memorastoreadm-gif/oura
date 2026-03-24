from datetime import datetime
import pytz
import logging
import os
import json
import gspread
import threading
from flask import Flask
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
from openai import OpenAI

# Configuración básica de logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# ---------------- CONFIGURACIÓN DE SEGURIDAD ----------------
# NUNCA pongas tus tokens directamente en el código si lo vas a subir a Git.
# En Render, configurarás esto en la sección "Environment Variables".
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', 'TU_TOKEN_DE_PRUEBA_LOCAL')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', 'TU_TOKEN_DE_PRUEBA_LOCAL')

# 1. Inicializamos OpenAI
client_ai = OpenAI(api_key=OPENAI_API_KEY)

# 2. Inicializamos Google Sheets
scope = ["https://spreadsheets.google.com/feeds", 'https://www.googleapis.com/auth/spreadsheets',
         "https://www.googleapis.com/auth/drive.file", "https://www.googleapis.com/auth/drive"]

# En Render, subiremos este archivo como un "Secret File"
CREDENCIALES_PATH = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS', 'credenciales_google.json')
creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENCIALES_PATH, scope)
client_gs = gspread.authorize(creds)

sheet = client_gs.open_by_key("1B7z5USzpV9RgQdEiBJWth2pyw7S_nV7RG3cEzjB_C5E").sheet1

# Carpetas temporales
AUDIO_DIR = "audios_recibidos"
if not os.path.exists(AUDIO_DIR):
    os.makedirs(AUDIO_DIR)

# ---------------- SERVIDOR WEB PARA UPTIMEROBOT ----------------
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Bot de hábitos activo y registrando datos."

def run_web():
    # Render asigna automáticamente un puerto, si no hay, usa el 5000
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

# ---------------- LÓGICA DEL BOT ----------------
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await context.bot.send_message(chat_id=chat_id, text="Procesando registro...")

    voice_file = await update.message.voice.get_file()
    file_path = os.path.join(AUDIO_DIR, f"audio_{update.message.message_id}.ogg")
    await voice_file.download_to_drive(file_path)
    
    try:
        # PASO 1: Transcripción con Whisper
        with open(file_path, "rb") as audio_file:
            transcription = client_ai.audio.transcriptions.create(
                model="whisper-1", 
                file=audio_file
            )
        texto_crudo = transcription.text
        
        # PASO 2: Captura de fecha y hora local (Bogotá)
        fecha_hora_utc = update.message.date
        zona_horaria = pytz.timezone('America/Bogota') 
        fecha_hora_local = fecha_hora_utc.astimezone(zona_horaria)
        
        fecha_exacta = fecha_hora_local.strftime("%Y-%m-%d")
        hora_exacta = fecha_hora_local.strftime("%H:%M")
        
        prompt = f"""
        Eres un asistente de extracción de datos para análisis de hábitos y rendimiento.
        
        CONTEXTO TEMPORAL: El usuario está enviando este reporte en la fecha {fecha_exacta} a la hora {hora_exacta}.
        
        Extrae la información del siguiente texto y devuélvela estrictamente en formato JSON con 4 claves: "fecha" (YYYY-MM-DD), "hora" (HH:MM), "categoria" (ej. alimentacion, sueño, ejercicio, trabajo, lectura, suplementacion), y "detalle" (breve descripción).
        
        REGLAS DE TIEMPO:
        1. Si el texto menciona un tiempo relativo (ej. "hace 20 minutos", "hace 1 hora", "ayer"), calcula la hora/fecha real basándote en el CONTEXTO TEMPORAL provisto.
        2. Si el texto no menciona ninguna hora ni fecha explícita, usa exactamente la fecha ({fecha_exacta}) y la hora ({hora_exacta}) del CONTEXTO TEMPORAL.
        
        Texto del usuario: "{texto_crudo}"
        """
        
        response = client_ai.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            response_format={ "type": "json_object" } 
        )
        
        datos_json = json.loads(response.choices[0].message.content)
        
        # PASO 3: Guardar en Google Sheets
        fila_a_insertar = [
            datos_json.get("fecha", ""), 
            datos_json.get("hora", ""), 
            datos_json.get("categoria", ""), 
            datos_json.get("detalle", "")
        ]
        
        sheet.append_row(fila_a_insertar)
        
        # PASO 4: Confirmación al usuario
        mensaje_exito = (
            f"✅ ¡Guardado en Google Sheets!\n"
            f"Categoría: {datos_json.get('categoria')}\n"
            f"Detalle: {datos_json.get('detalle')}"
        )
        await context.bot.send_message(chat_id=chat_id, text=mensaje_exito)
        
    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text=f"Hubo un error en el pipeline: {e}")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

if __name__ == '__main__':
    # 1. Iniciar el servidor web de Flask en un hilo separado
    threading.Thread(target=run_web, daemon=True).start()
    
    # 2. Iniciar el bot de Telegram
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    voice_handler = MessageHandler(filters.VOICE, handle_voice)
    application.add_handler(voice_handler)
    
    print("Bot de hábitos iniciado, servidor web corriendo y conectado a Google Sheets...")
    application.run_polling()
