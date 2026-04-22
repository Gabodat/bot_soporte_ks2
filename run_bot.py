import sys
import time
import subprocess
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class BotReloader(FileSystemEventHandler):
    def __init__(self, bot_script):
        self.bot_script = bot_script
        self.process = None
        self.start_bot()

    def start_bot(self):
        if self.process:
            print("🛑 Deteniendo instancia anterior...")
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        
        print(f"🔄 (Re)Iniciando el bot: {self.bot_script}...")
        self.process = subprocess.Popen([sys.executable, self.bot_script])

    def on_modified(self, event):
        if event.src_path.endswith(".py"):
            # Ignorar cambios en scripts temporales o caches si fuera necesario
            print(f"📝 Cambio detectado en: {event.src_path}")
            self.start_bot()

if __name__ == "__main__":
    bot_script = "main.py"
    event_handler = BotReloader(bot_script)
    observer = Observer()
    observer.schedule(event_handler, path=".", recursive=False)
    
    print(f"👀 Observando cambios en *.py para reiniciar {bot_script}...")
    observer.start()
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        if event_handler.process:
            event_handler.process.terminate()
    observer.join()
