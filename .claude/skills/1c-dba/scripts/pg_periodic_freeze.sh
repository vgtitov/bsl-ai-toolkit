#!/bin/bash
# ============================================================
# Скрипт для периодической заморозки (VACUUM FREEZE ANALYZE)
# Версия: 1.1 (Использует ~/.pgpass и конфигурационный файл основного скрипта)
# ============================================================
# Особенности:
# - Выполняет VACUUM FREEZE ANALYZE для указанной базы.
# - Использует ~/.pgpass для безопасной аутентификации.
# - Загружает параметры подключения и кол-во задач (JOBS)
#   из конфигурационного файла основного скрипта обслуживания
#   (ожидается в том же каталоге).
# ============================================================

# --- Конфигурация ---
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

# --- Функции Управления Блокировкой ---
LOCK_DIR="/var/lock/pg_maintenance_locks" # Общая директория для блокировок
LOCK_FILE_NAME="pg_1c_maintenance.lock"    # Имя файла блокировки (одинаковое для обоих!)
LOCK_FILE_PATH="${LOCK_DIR}/${LOCK_FILE_NAME}"
PID_FILE="${LOCK_FILE_PATH}/pid"

# Функция для логирования сообщений блокировки (можно использовать основную log_msg, если она уже определена)
# Если log_msg еще не определена на этом этапе, определим простую версию
_log_lock_msg() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') - [LOCK] $1" # Добавим префикс [LOCK]
  # Если хотите писать и в основной лог-файл, добавьте | tee -a "$LOGFILE"
  # Но $LOGFILE может быть еще не определен на этом этапе. Пишем пока в stdout/stderr.
}

# Функция создания директории для блокировок, если ее нет
_ensure_lock_dir() {
  if [[ ! -d "$LOCK_DIR" ]]; then
    _log_lock_msg "Создание директории для блокировок: $LOCK_DIR"
    # Создаем с правами, позволяющими пользователю скрипта писать туда
    mkdir -p "$LOCK_DIR" || {
      _log_lock_msg "ОШИБКА: Не удалось создать директорию блокировок $LOCK_DIR. Проверьте права."
      exit 1
    }
    # Можно установить более строгие права, если необходимо
    # chmod 770 "$LOCK_DIR" # Пример: владелец и группа могут писать
  fi
}

# Функция попытки захвата блокировки
acquire_lock() {
  _ensure_lock_dir

  # Атомарная попытка создать директорию блокировки. mkdir - уникальность имени файла/директории
  if mkdir "$LOCK_FILE_PATH" 2>/dev/null; then
    # Успешно создали директорию блокировки
    echo $$ > "$PID_FILE" # Записываем свой PID
    _log_lock_msg "Блокировка успешно установлена ($LOCK_FILE_PATH) PID: $$"
    # Устанавливаем ловушку для удаления блокировки при выходе
    trap release_lock EXIT INT TERM
    return 0 # Успех
  else
    # Директория блокировки уже существует. Проверяем PID.
    if [[ -f "$PID_FILE" ]]; then
      local locked_pid
      locked_pid=$(cat "$PID_FILE")
      # Проверяем, жив ли процесс с этим PID
      if kill -0 "$locked_pid" 2>/dev/null; then
        # Процесс жив, блокировка активна
        _log_lock_msg "ОШИБКА: Обнаружена активная блокировка ($LOCK_FILE_PATH), установленная процессом PID $locked_pid. Выход."
        return 1 # Неудача
      else
        # Процесс не жив, блокировка устарела
        _log_lock_msg "ПРЕДУПРЕЖДЕНИЕ: Обнаружена устаревшая блокировка ($LOCK_FILE_PATH) от PID $locked_pid. Удаляем старую блокировку."
        rm -rf "$LOCK_FILE_PATH" # Удаляем старую директорию
        # Повторная попытка захвата
        if mkdir "$LOCK_FILE_PATH" 2>/dev/null; then
          echo $$ > "$PID_FILE"
          _log_lock_msg "Блокировка успешно установлена ($LOCK_FILE_PATH) PID: $$ после удаления устаревшей."
          trap release_lock EXIT INT TERM
          return 0 # Успех
        else
           _log_lock_msg "ОШИБКА: Не удалось установить блокировку ($LOCK_FILE_PATH) даже после попытки удаления устаревшей. Возможно, конфликт."
           return 1 # Неудача
        fi
      fi
    else
      # Директория есть, а файла с PID нет - странная ситуация, удаляем и пытаемся снова
      _log_lock_msg "ПРЕДУПРЕЖДЕНИЕ: Обнаружена директория блокировки ($LOCK_FILE_PATH) без файла PID. Удаляем и пытаемся установить блокировку."
      rm -rf "$LOCK_FILE_PATH"
       if mkdir "$LOCK_FILE_PATH" 2>/dev/null; then
          echo $$ > "$PID_FILE"
          _log_lock_msg "Блокировка успешно установлена ($LOCK_FILE_PATH) PID: $$ после удаления некорректной."
          trap release_lock EXIT INT TERM
          return 0 # Успех
        else
           _log_lock_msg "ОШИБКА: Не удалось установить блокировку ($LOCK_FILE_PATH) даже после попытки удаления некорректной. Возможно, конфликт."
           return 1 # Неудача
        fi
    fi
  fi
}

# Функция освобождения блокировки (вызывается через trap)
release_lock() {
  if [[ -d "$LOCK_FILE_PATH" ]]; then
     # Проверяем, что удаляем блокировку, установленную именно нами
     if [[ -f "$PID_FILE" ]] && [[ "$(cat "$PID_FILE")" -eq "$$" ]]; then
         _log_lock_msg "Освобождение блокировки ($LOCK_FILE_PATH) PID: $$"
         rm -rf "$LOCK_FILE_PATH"
     elif [[ ! -f "$PID_FILE" ]]; then
         # Блокировка без PID, но мы ее владелец (создали директорию)
         _log_lock_msg "Освобождение некорректной блокировки ($LOCK_FILE_PATH) PID: $$"
         rm -rf "$LOCK_FILE_PATH"
     else
         # Это не наша блокировка, не трогаем (на всякий случай)
         _log_lock_msg "ПРЕДУПРЕЖДЕНИЕ: Попытка освободить блокировку ($LOCK_FILE_PATH), но PID в файле ($(cat "$PID_FILE")) не совпадает с текущим ($$). Блокировка не удалена."
     fi
  fi
  # Убираем ловушку, чтобы избежать повторного вызова при нормальном выходе
  trap - EXIT INT TERM
}
# --- Конец Функций Управления Блокировкой ---


# --- Захват Блокировки ---
if ! acquire_lock; then
  # Логирование уже произошло внутри acquire_lock
  exit 1 # Завершаем работу, если блокировка не получена
fi



# --- Начало Основной Логики Скрипта ---
# Теперь можно безопасно продолжать: загружать конфиг, выполнять проверки и т.д.
# Ловушка trap уже установлена функцией acquire_lock и вызовет release_lock при выходе.


# Имя конфигурационного файла основного скрипта (ожидается в той же директории)
MAIN_CONFIG_FILE="${SCRIPT_DIR}/pg_1c_daily_maintenance.conf"
# Файл лога для этого скрипта
LOGFILE="${SCRIPT_DIR}/pg_periodic_freeze_$(date '+%Y%m%d_%H%M%S').log"

# Функция логирования
log_msg() {
  local message="$1"
  echo "$(date '+%Y-%m-%d %H:%M:%S') - $message" | tee -a "$LOGFILE"
}

log_msg "Запуск скрипта периодической заморозки от имени пользователя ОС '$(whoami)'"

# Проверка и загрузка конфигурации из основного файла
if [[ -f "$MAIN_CONFIG_FILE" ]]; then
  log_msg "Загрузка конфигурации из файла: $MAIN_CONFIG_FILE"
  # Загружаем переменные из основного файла конфигурации
  # shellcheck source=/dev/null
  source "$MAIN_CONFIG_FILE"
else
  log_msg "ОШИБКА: Основной файл конфигурации '$MAIN_CONFIG_FILE' не найден!"
  exit 1
fi

# Проверка обязательных переменных из конфига
# (PGUSER и PGDATABASE должны быть определены в основном конфиге)
if [[ -z "$PGDATABASE" || -z "$PGUSER" ]]; then
  log_msg "ОШИБКА: В файле конфигурации '$MAIN_CONFIG_FILE' должны быть определены PGDATABASE и PGUSER."
  exit 1
fi

# Установка параметров подключения (пароль будет взят из ~/.pgpass)
# Используем значения из конфиг. файла или значения по умолчанию, если они там не заданы
export PGHOST="${PGHOST:-localhost}"
export PGPORT="${PGPORT:-5432}"
export PGUSER="$PGUSER"         # Загружен из конфига
export PGDATABASE="$PGDATABASE"   # Загружен из конфига

# Параметры для vacuumdb (используем из конфига или значение по умолчанию)
# VACUUMDB_JOBS должен быть определен в основном конфиге, но добавим дефолт на всякий случай
VACUUMDB_JOBS="${VACUUMDB_JOBS:-4}" # Кол-во параллельных задач

log_msg "Параметры подключения: HOST=$PGHOST, PORT=$PGPORT, DBNAME=$PGDATABASE, USER=$PGUSER"
log_msg "Пароль будет взят из ~/.pgpass пользователя '$(whoami)'."
log_msg "Количество параллельных задач для vacuumdb: $VACUUMDB_JOBS"

# --- Выполнение Заморозки ---
log_msg "Начало VACUUM FREEZE ANALYZE для базы $PGDATABASE"
start_time=$(date +%s)

# Выполняем vacuumdb. Параметры подключения из переменных окружения, пароль из .pgpass.
# Используем загруженное или дефолтное значение VACUUMDB_JOBS.
vacuumdb --freeze --analyze --verbose --jobs="$VACUUMDB_JOBS" --dbname="$PGDATABASE" >> "$LOGFILE" 2>&1
exit_code=$?

end_time=$(date +%s)
duration=$((end_time - start_time))

if [[ $exit_code -eq 0 ]]; then
  log_msg "ЗАВЕРШЕНО: VACUUM FREEZE ANALYZE. Длительность: ${duration} сек."
else
  # Добавляем имя лог-файла в сообщение об ошибке для удобства
  log_msg "ОШИБКА: VACUUM FREEZE ANALYZE завершился с кодом $exit_code. Длительность: ${duration} сек. См. лог '$LOGFILE'."
fi

exit $exit_code