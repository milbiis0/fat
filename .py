import subprocess
import time
import os
from datetime import datetime
import json
import sys
import threading
import logging
import shutil

# ==================== НАСТРОЙКИ ====================
STREAMERS = {
    "twitch": ["mafanyaking", "kussia88", "n3koglai", "cirilla04"],
    "kick": ["kussia88", "mafanyatopeu", "m0neyglai"]                  
}

QUALITY = "best"
BASE_FOLDER = r"C:\Users\voise\TwitchRecorder"
SAVE_FOLDER = os.path.join(BASE_FOLDER, "Videos")
MIN_RESTART_INTERVAL = 300
CHECK_INTERVAL = 60

PLATFORM_CONFIG = {
    "twitch": {
        "url_template": "https://www.twitch.tv/{username}",
        "extra_args": [
            "--twitch-disable-ads",
            "--hls-segment-threads", "3",
            "--hls-segment-timeout", "60",
            "--hls-playlist-timeout", "120",
            "--hls-live-edge", "10",  # <-- УВЕЛИЧИЛ ДЛЯ СТАБИЛЬНОСТИ
            "--hls-start-offset", "0",  # <-- НАЧИНАТЬ С ТЕКУЩЕГО МОМЕНТА
            "--retry-streams", "20",
            "--retry-open", "10",
            "--stream-timeout", "120"
        ]
    },
    "kick": {
        "url_template": "https://kick.com/{username}",
        "extra_args": [
            "--hls-segment-threads", "3",
            "--hls-segment-timeout", "60",
            "--hls-playlist-timeout", "120",
            "--hls-live-edge", "10",
            "--hls-start-offset", "0",
            "--retry-streams", "20",
            "--retry-open", "10",
            "--stream-timeout", "120"
        ]
    }
}

# ==================== ЦВЕТА ====================
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    PURPLE = '\033[95m'
    CYAN = '\033[96m'
    WHITE = '\033[97m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

# ==================== ЛОГИРОВАНИЕ ====================
os.makedirs(BASE_FOLDER, exist_ok=True)

logging.basicConfig(
    filename=os.path.join(BASE_FOLDER, 'recorder.log'),
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    encoding='utf-8',
    datefmt='%Y-%m-%d %H:%M:%S'
)

class ColoredConsoleHandler(logging.StreamHandler):
    def emit(self, record):
        try:
            msg = self.format(record)
            stream = self.stream
            
            if record.levelno == logging.INFO:
                stream.write(f"{Colors.WHITE}{msg}{Colors.RESET}\n")
            elif record.levelno == logging.WARNING:
                stream.write(f"{Colors.YELLOW}{msg}{Colors.RESET}\n")
            elif record.levelno == logging.ERROR:
                stream.write(f"{Colors.RED}{msg}{Colors.RESET}\n")
            else:
                stream.write(f"{msg}\n")
            stream.flush()
        except Exception:
            self.handleError(record)

console = ColoredConsoleHandler(sys.stdout)
console.setLevel(logging.INFO)
console.setFormatter(logging.Formatter('%(asctime)s | %(levelname)s | %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
logging.getLogger('').addHandler(console)

os.makedirs(SAVE_FOLDER, exist_ok=True)

# ==================== ОСНОВНОЙ КЛАСС ====================
class StreamRecorder:
    def __init__(self):
        self.active_recordings = {}
        self.last_restart_time = {}
        self.lock = threading.Lock()
        self.running = True
        self.ffmpeg_available = self._check_tool("ffmpeg", "--version")
        self.streamlink_available = self._check_tool("streamlink", "--version")
        
        if not self.streamlink_available:
            logging.error("Streamlink не найден! Установите: pip install streamlink")
            sys.exit(1)
        
        if not self.ffmpeg_available:
            logging.warning("FFmpeg не найден! Remux будет пропущен")
    
    def _check_tool(self, tool, arg):
        try:
            subprocess.run([tool, arg], capture_output=True, 
                          creationflags=subprocess.CREATE_NO_WINDOW, timeout=5)
            return True
        except:
            return False
    
    def _get_startupinfo(self):
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        return startupinfo
    
    def get_url(self, platform, username):
        config = PLATFORM_CONFIG.get(platform)
        return config["url_template"].format(username=username) if config else None
    
    def get_extra_args(self, platform):
        config = PLATFORM_CONFIG.get(platform)
        return config.get("extra_args", []) if config else []
    
    def check_disk_space(self, required_bytes=1024*1024*1024):
        try:
            free_space = shutil.disk_usage(SAVE_FOLDER).free
            return free_space >= required_bytes, free_space
        except Exception as e:
            logging.warning(f"Не удалось проверить место: {e}")
            return True, 0
    
    def is_live(self, platform, username):
        try:
            url = self.get_url(platform, username)
            if not url:
                return False
            
            print(f"[{datetime.now().strftime('%H:%M:%S')}] 🔍 Проверка {platform}/{username}...")
            
            result = subprocess.run(
                ["streamlink", url, "--json"],
                capture_output=True,
                text=True,
                timeout=25,
                encoding='utf-8',
                errors='ignore',
                startupinfo=self._get_startupinfo(),
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            
            if result.returncode == 0 and result.stdout.strip():
                data = json.loads(result.stdout)
                if data.get("streams"):
                    print(f"   {Colors.GREEN}✅ {platform}/{username} — СТРИМ ИДЁТ!{Colors.RESET}")
                    logging.info(f"Стрим онлайн: {platform}/{username}")
                    return True
            
            print(f"   {Colors.RED}❌ {platform}/{username} — оффлайн{Colors.RESET}")
            return False
            
        except Exception as e:
            print(f"   {Colors.YELLOW}⚠️ Ошибка проверки {platform}/{username}: {e}{Colors.RESET}")
            logging.error(f"Ошибка проверки {platform}/{username}: {e}")
            return False
    
    def _can_restart(self, key):
        with self.lock:
            last_time = self.last_restart_time.get(key, 0)
            if time.time() - last_time < MIN_RESTART_INTERVAL:
                return False
            self.last_restart_time[key] = time.time()
            return True
    
    def start_recording(self, platform, username, is_continuation=False):
        key = (platform, username.lower())
        
        with self.lock:
            if key in self.active_recordings:
                logging.debug(f"Запись {username} уже активна")
                return
            
            if is_continuation and not self._can_restart(key):
                logging.info(f"Пропущен перезапуск {username} (защита от частых перезапусков)")
                return
        
        has_space, free_space = self.check_disk_space()
        if not has_space:
            logging.error(f"Недостаточно места для {username} (свободно: {free_space//(1024**3)} ГБ)")
            print(f"{Colors.RED}❌ Недостаточно места на диске для {username}{Colors.RESET}")
            return
        
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"{platform}_{username}_{timestamp}.mp4"
        output_path = os.path.join(SAVE_FOLDER, filename)
        
        url = self.get_url(platform, username)
        if not url:
            logging.error(f"Неизвестная платформа: {platform}")
            return
        
        # ФОРМИРУЕМ КОМАНДУ С ПРАВИЛЬНЫМИ ПАРАМЕТРАМИ
        cmd = ["streamlink", url, QUALITY, "--output", output_path]
        cmd.extend(self.get_extra_args(platform))
        
        logging.info(f"Запуск записи: {platform}/{username} → {filename}")
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {Colors.GREEN}🎥 Запуск записи → {platform.upper()} | {username}{Colors.RESET}")
        print(f"   {Colors.CYAN}Команда: {' '.join(cmd)}{Colors.RESET}")
        
        try:
            process = subprocess.Popen(
                cmd,
                creationflags=subprocess.CREATE_NO_WINDOW,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='ignore'
            )
            
            # Даем процессу немного времени, чтобы убедиться что он запустился
            time.sleep(2)
            
            # Проверяем, не завершился ли процесс сразу
            if process.poll() is not None:
                stdout, stderr = process.communicate(timeout=1)
                logging.error(f"Процесс сразу завершился для {username}. stderr: {stderr[:500]}")
                print(f"{Colors.RED}❌ Ошибка: процесс записи сразу завершился!{Colors.RESET}")
                print(f"{Colors.RED}   stderr: {stderr[:200]}{Colors.RESET}")
                return
            
            with self.lock:
                self.active_recordings[key] = {
                    "process": process,
                    "start_time": time.time(),
                    "output_path": output_path,
                    "filename": filename,
                    "platform": platform,
                    "username": username
                }
            
            threading.Thread(
                target=self._monitor_recording,
                args=(key,),
                daemon=True
            ).start()
            
            logging.info(f"Запись успешно запущена для {username}")
            
        except Exception as e:
            logging.error(f"Ошибка запуска записи {username}: {e}")
            print(f"{Colors.RED}❌ Ошибка запуска записи {username}: {e}{Colors.RESET}")
            with self.lock:
                self.active_recordings.pop(key, None)
    
    def _monitor_recording(self, key):
        with self.lock:
            info = self.active_recordings.get(key)
            if not info:
                return
        
        process = info["process"]
        output_path = info["output_path"]
        filename = info["filename"]
        platform = info["platform"]
        username = info["username"]
        
        # Собираем вывод процесса для отладки
        stdout_lines = []
        stderr_lines = []
        
        def read_output():
            try:
                while True:
                    line = process.stdout.readline()
                    if not line:
                        break
                    stdout_lines.append(line)
                    logging.debug(f"[{username}] STDOUT: {line.strip()}")
            except:
                pass
        
        def read_error():
            try:
                while True:
                    line = process.stderr.readline()
                    if not line:
                        break
                    stderr_lines.append(line)
                    if "error" in line.lower() or "failed" in line.lower():
                        logging.warning(f"[{username}] ERROR: {line.strip()}")
                    else:
                        logging.debug(f"[{username}] STDERR: {line.strip()}")
            except:
                pass
        
        # Запускаем потоки для чтения вывода
        stdout_thread = threading.Thread(target=read_output, daemon=True)
        stderr_thread = threading.Thread(target=read_error, daemon=True)
        stdout_thread.start()
        stderr_thread.start()
        
        try:
            exit_code = process.wait(timeout=86400)  # 24 часа максимум
            
            # Ждем завершения потоков чтения
            stdout_thread.join(timeout=2)
            stderr_thread.join(timeout=2)
            
        except subprocess.TimeoutExpired:
            logging.warning(f"Запись {username} превысила лимит времени, завершаю")
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            exit_code = -1
        except Exception as e:
            logging.error(f"Ошибка мониторинга {username}: {e}")
            exit_code = -1
        
        with self.lock:
            self.active_recordings.pop(key, None)
        
        logging.info(f"Запись завершена: {platform}/{username} (код выхода: {exit_code})")
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {Colors.YELLOW}⏹️ Запись {username} завершена (код: {exit_code}){Colors.RESET}")
        
        # Проверяем, что stderr содержит ошибки
        if stderr_lines:
            error_output = ''.join(stderr_lines[-5:])  # Последние 5 строк
            logging.info(f"Последние ошибки для {username}: {error_output[:500]}")
        
        # Обрабатываем файл если он есть и не пустой
        if os.path.exists(output_path):
            file_size = os.path.getsize(output_path)
            if file_size > 1024:  # Больше 1KB
                logging.info(f"Файл {filename} сохранен (размер: {file_size//1024} KB)")
                print(f"[{datetime.now().strftime('%H:%M:%S')}] {Colors.GREEN}💾 Файл сохранен: {filename} ({file_size//1024} KB){Colors.RESET}")
                
                if self.ffmpeg_available:
                    self._process_recorded_file(output_path, filename)
            else:
                logging.warning(f"Файл {filename} слишком маленький ({file_size} байт), удаляю")
                os.remove(output_path)
        else:
            logging.warning(f"Файл {filename} не создан")
        
        # Проверяем перезапуск
        if self.running:
            self._check_continuation(platform, username)
    
    def _process_recorded_file(self, output_path, filename):
        fixed_path = None
        try:
            fixed_path = output_path.replace(".mp4", "_fixed.mp4")
            
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {Colors.CYAN}🔄 Remux: {filename}{Colors.RESET}")
            
            result = subprocess.run(
                ["ffmpeg", "-i", output_path, "-c", "copy", "-movflags", "+faststart", "-y", fixed_path],
                check=True,
                timeout=300,
                creationflags=subprocess.CREATE_NO_WINDOW,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='ignore'
            )
            
            os.remove(output_path)
            os.rename(fixed_path, output_path)
            
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {Colors.GREEN}✅ Remux завершён: {filename}{Colors.RESET}")
            logging.info(f"Remux успешен: {filename}")
            
        except subprocess.TimeoutExpired:
            logging.error(f"Таймаут remux {filename}")
            if fixed_path and os.path.exists(fixed_path):
                os.remove(fixed_path)
        except Exception as e:
            logging.error(f"Ошибка remux {filename}: {e}")
            if fixed_path and os.path.exists(fixed_path):
                os.remove(fixed_path)
    
    def _check_continuation(self, platform, username):
        """Проверить, нужно ли продолжить запись"""
        logging.info(f"Проверка продолжения для {username} через 10 секунд...")
        time.sleep(10)  # Увеличил с 5 до 10 секунд
        
        if not self.running:
            return
        
        # Проверяем, не запущена ли уже запись
        if self.is_recording(platform, username):
            logging.info(f"Запись {username} уже активна, пропускаем")
            return
        
        if self.is_live(platform, username):
            logging.info(f"Стрим {platform}/{username} снова онлайн, перезапускаю запись...")
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {Colors.PURPLE}🔄 Стрим {username} перезапустился, начинаю новую запись{Colors.RESET}")
            self.start_recording(platform, username, is_continuation=True)
        else:
            logging.debug(f"Стрим {platform}/{username} оффлайн, продолжаю мониторинг по расписанию")
    
    def is_recording(self, platform, username):
        key = (platform, username.lower())
        with self.lock:
            return key in self.active_recordings
    
    def check_and_record_all(self):
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] {Colors.BOLD}🔍 Проверка стримеров...{Colors.RESET}")
        
        for platform, usernames in STREAMERS.items():
            for username in usernames:
                if not self.running:
                    return
                
                # Проверяем, не идет ли уже запись
                if self.is_recording(platform, username):
                    print(f"   {Colors.YELLOW}⏭️ {username} уже записывается{Colors.RESET}")
                    continue
                
                if self.is_live(platform, username):
                    self.start_recording(platform, username)
                time.sleep(1.5)
        
        with self.lock:
            if self.active_recordings:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] {Colors.GREEN}📺 Активных записей: {len(self.active_recordings)}{Colors.RESET}")
                for key, info in self.active_recordings.items():
                    duration = time.time() - info["start_time"]
                    duration_str = f"{int(duration//3600):02d}:{int((duration%3600)//60):02d}:{int(duration%60):02d}"
                    print(f"   {Colors.CYAN}▶️ {info['platform']}/{info['username']} ({duration_str}){Colors.RESET}")
            else:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] {Colors.YELLOW}⏸️ Нет активных записей{Colors.RESET}")
        
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {Colors.GREEN}✅ Проверка завершена{Colors.RESET}")
    
    def stop_all_recordings(self):
        self.running = False
        
        with self.lock:
            for key, info in list(self.active_recordings.items()):
                try:
                    process = info["process"]
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                    logging.info(f"Остановлена запись: {info['platform']}/{info['username']}")
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] {Colors.YELLOW}⏹️ Остановлена запись {info['username']}{Colors.RESET}")
                except Exception as e:
                    logging.error(f"Ошибка остановки записи {info.get('username', 'unknown')}: {e}")
            self.active_recordings.clear()
            logging.info("Все записи остановлены")
    
    def run(self):
        logging.info("Сервис записи запущен")
        print(f"{Colors.BOLD}{Colors.GREEN}=== СЕРВИС ЗАПИСИ СТРИМОВ ЗАПУЩЕН ==={Colors.RESET}")
        print(f"{Colors.CYAN}Стримеры: {len(STREAMERS)} платформ, {sum(len(v) for v in STREAMERS.values())} аккаунтов{Colors.RESET}")
        print(f"{Colors.YELLOW}Нажмите Ctrl+C для остановки{Colors.RESET}\n")
        
        try:
            while self.running:
                self.check_and_record_all()
                
                # Ждём до следующей проверки
                for _ in range(CHECK_INTERVAL):
                    if not self.running:
                        break
                    time.sleep(1)
                    
        except KeyboardInterrupt:
            logging.info("Получен сигнал остановки (Ctrl+C)")
            print(f"\n{Colors.YELLOW}Остановка сервиса...{Colors.RESET}")
        finally:
            self.stop_all_recordings()
            logging.info("Сервис записи остановлен")
            print(f"{Colors.GREEN}Сервис записи остановлен{Colors.RESET}")

# ===============
