#!/bin/bash

# ==============================================================================
# Улучшенный скрипт регламентного обслуживания PostgreSQL для 1С
# Версия: 2.2 (Уточнены рекомендации на основе анализа практик)
# ==============================================================================
# Особенности:
# - Использует vacuumdb для параллельного VACUUM ANALYZE.
# - Ищет и переиндексирует только "распухшие" индексы CONCURRENTLY.
# - Улучшенное логирование с таймингами.
# - Гибкая конфигурация через внешний файл.
# - Базовая обработка ошибок REINDEX CONCURRENTLY с поиском невалидных индексов.
# - Опциональная интеграция с pg_repack для таблиц (требует установки).
# - Использует ~/.pgpass для безопасной аутентификации.
# ==============================================================================
# ВАЖНЕЙШИЕ РЕКОМЕНДАЦИИ ПО НАСТРОЙКЕ ОКРУЖЕНИЯ (postgresql.conf):
# ------------------------------------------------------------------------------
# Этот скрипт ДОПОЛНЯЕТ, но НЕ ЗАМЕНЯЕТ правильно настроенный AUTOCACUUM!
# Для эффективной работы PostgreSQL с 1С КРИТИЧЕСКИ ВАЖНО агрессивно
# настроить параметры Autovacuum в postgresql.conf и перезагрузить конфигурацию
# (SELECT pg_reload_conf();):
#
# 1. Включить Autovacuum:
#    autovacuum = on
#
# 2. Увеличить количество воркеров:
#    autovacuum_max_workers = N   (Рекомендуется N = кол-во ядер CPU / 2)
#
# 3. Увеличить лимит стоимости пропорционально воркерам:
#    autovacuum_vacuum_cost_limit = M (Пример: если workers=6 (было 3), то limit = 400 * (6/3) = 800)
#
# 4. Снизить пороги срабатывания (ОЧЕНЬ ВАЖНО для 1С):
#    autovacuum_vacuum_scale_factor = 0.01  # (1%, вместо стандартных 20%)
#    autovacuum_analyze_scale_factor = 0.005 # (0.5%, вместо стандартных 10%)
#    # Можно также рассмотреть установку абсолютных порогов _threshold для крупных таблиц.
#
# 5. Увеличить память для обслуживания:
#    maintenance_work_mem = 512MB (или 1GB, 2GB+ в зависимости от RAM сервера)
#
# 6. Включить логирование Autovacuum для контроля:
#    log_autovacuum_min_duration = 0 # (Логировать все запуски) или 250ms
#
# Без этих настроек производительность базы 1С на PostgreSQL будет деградировать!
# ------------------------------------------------------------------------------
# РЕКОМЕНДАЦИЯ ПО ЗАМОРОЗКЕ (FREEZE):
# ------------------------------------------------------------------------------
# Операция VACUUM FREEZE важна для предотвращения TXID wraparound и оптимизации
# работы Autovacuum (через карту видимости - visibility map).
# Однако, она интенсивна по I/O и НЕ должна включаться в ежедневный регламент.
# РЕКОМЕНДУЕТСЯ настроить ОТДЕЛЬНОЕ задание (например, в cron) для ее запуска
# ЗНАЧИТЕЛЬНО РЕЖЕ (например, РАЗ В МЕСЯЦ или РАЗ В КВАРТАЛ, либо чаще,
# если мониторинг показывает быстрый рост возраста XID базы данных -
# SELECT age(datfrozenxid) FROM pg_database WHERE datname = 'ваша_база';).
#
# Пример команды для отдельного задания заморозки (запускать от того же пользователя):
# vacuumdb --freeze --analyze --verbose --jobs=N --dbname=your_1c_database_name
# (где N - количество ядер, например, ${VACUUMDB_JOBS} из конфига)
# ------------------------------------------------------------------------------
# УПРАВЛЕНИЕ КЭШЕМ (ПРОГРЕВ):
# ------------------------------------------------------------------------------
# Прогрев кэша СУБД перед началом рабочего дня (эмуляцией пользовательской нагрузки)
# может улучшить стартовую производительность, особенно если наблюдаются "утренние
# тормоза". Однако, это ОТДЕЛЬНАЯ задача, выходящая за рамки данного скрипта
# обслуживания базы данных. Этот скрипт НЕ выполняет операций с кэшем.
# ==============================================================================
# ИНСТРУКЦИЯ ПО АУТЕНТИФИКАЦИИ С ПОМОЩЬЮ ~/.pgpass
# ------------------------------------------------------------------------------
# Этот скрипт использует файл .pgpass для безопасного хранения пароля
# пользователя PostgreSQL (указанного в PGUSER в файле конфигурации).
# Утилиты psql, vacuumdb, pg_repack автоматически прочитают пароль из этого
# файла, если он правильно настроен. Использование переменной PGPASSWORD
# НЕ РЕКОМЕНДУЕТСЯ из соображений безопасности.
#
# ШАГИ НАСТРОЙКИ .pgpass:
#
# 1. Определите пользователя ОС, от имени которого будет запускаться скрипт:
#    - Если вы запускаете вручную: это ваш текущий пользователь (например, 'admin1c').
#    - Если скрипт запускается через cron: это пользователь, указанный в crontab
#      (часто 'postgres' или 'root').
#
# 2. Создайте файл .pgpass в ДОМАШНЕМ каталоге этого пользователя ОС:
#    - Для пользователя 'admin1c': /home/admin1c/.pgpass
#    - Для пользователя 'postgres' (в RED OS обычно): /var/lib/pgsql/.pgpass
#      (Уточните домашний каталог пользователя postgres: grep postgres /etc/passwd)
#    - Для пользователя 'root': /root/.pgpass
#
# 3. Установите СТРОГИЕ права доступа к файлу .pgpass:
#    chmod 0600 /путь/к/домашнему/каталогу/.pgpass
#    (Права: чтение и запись ТОЛЬКО для владельца файла).
#    PostgreSQL проигнорирует файл, если права будут шире!
#
# 4. Добавьте в файл .pgpass строку в следующем формате:
#    hostname:port:database:username:password
#
#    Пример строки для файла .pgpass (замените значения на ваши):
#    localhost:5432:your_1c_database_name:postgres:ваш_очень_секретный_пароль
#
#    - hostname: Хост БД (из PGHOST или localhost, если не указан).
#    - port: Порт БД (из PGPORT или 5432, если не указан).
#    - database: Имя БД (из PGDATABASE). Можно использовать '*' для любой БД.
#    - username: Имя пользователя БД (из PGUSER).
#    - password: Пароль этого пользователя.
#
#    Можно использовать '*' для подстановки любого значения, например:
#    your_db_host:5432:*:postgres:secretpassword1  # Для пользователя postgres к любой БД на хосте
#    *:*:*:script_user:secretpassword2      # Для пользователя script_user к любой БД на любом хосте/порту
#
# Убедитесь, что пользователь PGUSER имеет необходимые права в PostgreSQL!
# ==============================================================================

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


# ... (определение SCRIPT_DIR, функций блокировки, log_msg и т.д.) ...

# --- Захват Блокировки ---
if ! acquire_lock; then
  # Логирование уже произошло внутри acquire_lock
  exit 1 # Завершаем работу, если блокировка не получена
fi


# --- Начало Основной Логики Скрипта ---
# Теперь можно безопасно продолжать: загружать конфиг, выполнять проверки и т.д.
# Ловушка trap уже установлена функцией acquire_lock и вызовет release_lock при выходе.


# Вот эту строку нужно проверить и, если нужно, изменить:
CONFIG_FILE="${SCRIPT_DIR}/pg_1c_daily_maintenance.conf" # <- УБЕДИТЕСЬ, ЧТО ИМЯ ФАЙЛА ВЕРНОЕ
LOGFILE="${SCRIPT_DIR}/pg_maintenance_$(date '+%Y%m%d_%H%M%S').log" # Имя лог-файла можно оставить или тоже изменить





# Загрузка конфигурации
if [[ -f "$CONFIG_FILE" ]]; then
  # Загружаем переменные из файла конфигурации
  # shellcheck source=/dev/null
  source "$CONFIG_FILE"
else
  echo "ОШИБКА: Файл конфигурации '$CONFIG_FILE' не найден!" >&2
  exit 1
fi

# Проверка обязательных переменных из конфига
if [[ -z "$PGDATABASE" || -z "$PGUSER" ]]; then
  echo "ОШИБКА: В файле конфигурации '$CONFIG_FILE' должны быть определены PGDATABASE и PGUSER." >&2
  exit 1
fi

# Установка переменных окружения для утилит PostgreSQL
# Эти переменные используются утилитами для определения параметров подключения.
# Пароль НЕ устанавливается через PGPASSWORD, он будет взят из ~/.pgpass.
export PGHOST="${PGHOST:-localhost}"
export PGPORT="${PGPORT:-5432}"
export PGUSER="$PGUSER"
export PGDATABASE="$PGDATABASE"

# Настройки из конфиг. файла (примеры значений по умолчанию)
VACUUMDB_JOBS="${VACUUMDB_JOBS:-4}"             # Кол-во параллельных задач для vacuumdb
MIN_INDEX_SIZE_MB="${MIN_INDEX_SIZE_MB:-100}"   # Минимальный размер индекса для проверки на распухание (МБ)
BLOAT_THRESHOLD_PERCENT="${BLOAT_THRESHOLD_PERCENT:-30}" # % распухания индекса для REINDEX
ENABLE_REINDEX="${ENABLE_REINDEX:-true}"        # Включить/выключить шаг переиндексации
ENABLE_PG_REPACK="${ENABLE_PG_REPACK:-false}"   # Включить/выключить шаг pg_repack
REPACK_MIN_TABLE_SIZE_MB="${REPACK_MIN_TABLE_SIZE_MB:-1024}" # Мин. размер таблицы для repack (МБ)
REPACK_BLOAT_THRESHOLD_PERCENT="${REPACK_BLOAT_THRESHOLD_PERCENT:-25}" # % распухания таблицы для repack
REPACK_JOBS="${REPACK_JOBS:-2}"                 # Кол-во параллельных задач для pg_repack (если применимо)
PG_REPACK_PATH="${PG_REPACK_PATH:-pg_repack}"   # Путь к утилите pg_repack

# --- Функции ---

# Логирование с временными метками
log_msg() {
  local message="$1"
  echo "$(date '+%Y-%m-%d %H:%M:%S') - $message" | tee -a "$LOGFILE"
}

# Измерение времени выполнения команды
time_cmd() {
  local cmd_name="$1"
  shift # Убираем имя команды из аргументов
  local start_time=$(date +%s)
  log_msg "НАЧАЛО: $cmd_name"
  # Запускаем команду. Утилиты сами подхватят параметры подключения из переменных окружения
  # и пароль из ~/.pgpass. Вывод команды направляем в лог.
  "$@" >> "$LOGFILE" 2>&1
  local exit_code=$?
  local end_time=$(date +%s)
  local duration=$((end_time - start_time))

  # Логируем результат в консоль и файл
  if [[ $exit_code -eq 0 ]]; then
    log_msg "ЗАВЕРШЕНО: $cmd_name. Длительность: ${duration} сек."
  else
    log_msg "ОШИБКА: $cmd_name завершилась с кодом $exit_code. Длительность: ${duration} сек. См. лог '$LOGFILE' для деталей."
    # Можно добавить отправку уведомлений администратору
    # exit $exit_code # Решите, прерывать ли весь скрипт при ошибке шага
  fi
  return $exit_code
}

# Выполнение SQL-запроса с обработкой ошибок
run_sql() {
  local sql_command="$1"
  # Используем опции psql: -q (тихий режим), -t (только кортежи), -A (без выравнивания),
  # -v ON_ERROR_STOP=1 (останавливаться при ошибке внутри команды/скрипта psql).
  # Параметры подключения (-d, -U, -h, -p) берутся из переменных окружения. Пароль из ~/.pgpass.
  local psql_options="-q -t -A -v ON_ERROR_STOP=1"

  log_msg "Выполнение SQL: ${sql_command:0:100}..." # Логируем начало запроса (обрезаем длинные)
  # Перенаправляем stdout и stderr в лог-файл
  psql $psql_options -c "$sql_command" >> "$LOGFILE" 2>&1
  local exit_code=$?
  if [[ $exit_code -ne 0 ]]; then
    log_msg "ОШИБКА выполнения SQL. См. лог '$LOGFILE' для деталей."
    # exit 1 # Прерывать или нет - зависит от критичности запроса
  fi
  return $exit_code
}

# Проверка наличия утилиты
check_util() {
  local util_name="$1"
  if ! command -v "$util_name" &> /dev/null; then
    log_msg "ОШИБКА: Утилита '$util_name' не найдена в PATH. Установите соответствующий пакет."
    exit 1
  fi
}

# Поиск невалидных индексов (оставшихся после сбоя REINDEX CONCURRENTLY)
find_invalid_indexes() {
    log_msg "Поиск невалидных индексов..."
    local invalid_indexes
    # Выполняем psql для поиска невалидных индексов. Параметры подключения из окружения, пароль из .pgpass.
    invalid_indexes=$(psql -q -t -A -c \
    "SELECT n.nspname || '.' || ci.relname FROM pg_index i JOIN pg_class ci ON i.indexrelid = ci.oid JOIN pg_class ct ON i.indrelid = ct.oid JOIN pg_namespace n ON ct.relnamespace = n.oid WHERE i.indisvalid = false;")

    if [[ -n "$invalid_indexes" ]]; then
        log_msg "ПРЕДУПРЕЖДЕНИЕ: Найдены невалидные индексы! Требуется ручное вмешательство:"
        # Логируем каждый невалидный индекс
        echo "$invalid_indexes" | while IFS= read -r idx; do log_msg " - $idx"; done
        log_msg "Возможные действия: DROP INDEX CONCURRENTLY \"<schema>.<index_name>\"; или REINDEX INDEX CONCURRENTLY \"<schema>.<index_name>\";"
        # exit 1 # Раскомментируйте, если хотите прерывать скрипт при наличии невалидных индексов
    else
        log_msg "Невалидных индексов не найдено."
    fi
}

# --- Проверки перед запуском ---
log_msg "Запуск скрипта обслуживания базы данных '$PGDATABASE' на хосте '$PGHOST:$PGPORT' от имени пользователя ОС '$(whoami)'"
log_msg "Используется пользователь БД '$PGUSER'. Пароль ожидается в файле ~/.pgpass пользователя '$(whoami)'."
check_util "psql"
check_util "vacuumdb"
if [[ "$ENABLE_PG_REPACK" == "true" ]]; then
    check_util "$PG_REPACK_PATH"
fi

# Проверка на наличие невалидных индексов *перед* началом работ
find_invalid_indexes

# --- Основные Шаги Обслуживания ---

# 1. VACUUM и ANALYZE базы данных
log_msg "Запуск VACUUM ANALYZE с $VACUUMDB_JOBS параллельными задачами..."
# vacuumdb использует переменные окружения PG* и пароль из ~/.pgpass
time_cmd "VACUUM ANALYZE (vacuumdb)" vacuumdb --analyze --verbose --jobs="$VACUUMDB_JOBS" --dbname="$PGDATABASE"

# 2. Переиндексация распухших индексов (если включено)
if [[ "$ENABLE_REINDEX" == "true" ]]; then
    log_msg "Поиск распухших индексов (размер > ${MIN_INDEX_SIZE_MB}MB, распухание > ${BLOAT_THRESHOLD_PERCENT}%)..."

    # Запрос для поиска распухших индексов (адаптированная версия)
    # Используем heredoc для читаемости SQL
    BLOAT_QUERY=$(cat <<-'EOF'
WITH constants AS (
    SELECT current_setting('block_size')::numeric AS bs, 23 AS hdr, 8 AS ma -- Defaults, adjust if needed
), index_stats AS (
    SELECT
        n.nspname,
        ct.relname AS tablename,
        ci.relname AS indexname,
        ci.reltuples,
        ci.relpages
    FROM pg_index i
    JOIN pg_class ci ON ci.oid = i.indexrelid
    JOIN pg_class ct ON ct.oid = i.indrelid
    JOIN pg_namespace n ON n.oid = ct.relnamespace
    WHERE ci.relam = (SELECT oid FROM pg_am WHERE amname = 'btree') -- Only B-Tree indexes
      AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
      AND ci.relpages > 0 -- Exclude empty indexes
), bloat_info AS (
    SELECT
        nspname, tablename, indexname,
        (bs * relpages)::bigint AS index_bytes,
        (bs * relpages / 1024.0 / 1024.0)::numeric(18,2) AS index_size_mb,
        -- Estimate based on average leaf density (requires pgstattuple for accuracy, this is a rough estimate)
        -- Using a simplified placeholder logic here. Replace with a proper query if pgstattuple is available.
        CASE
          WHEN relpages > 10 AND reltuples > 0 THEN
            -- Very rough estimation: if pages > tuples (highly fragmented) or size is large
            (100 * (1 - (reltuples / (relpages * (bs / 32)::float + 1) ))) -- Example rough formula
          ELSE 0
        END AS estimate_bloat_pct
    FROM index_stats
    CROSS JOIN constants
)
SELECT nspname || '.' || indexname
FROM bloat_info
WHERE index_size_mb >= $1 -- Use placeholders for variables
  AND estimate_bloat_pct >= $2
ORDER BY index_bytes DESC;
EOF
)
    # Получаем список индексов для перестроения, передавая параметры безопасно
    indexes_to_reindex=$(psql -q -t -A \
        -v min_size_mb="$MIN_INDEX_SIZE_MB" \
        -v min_bloat_pct="$BLOAT_THRESHOLD_PERCENT" \
        -c "$BLOAT_QUERY")

    if [[ $? -ne 0 ]]; then
      log_msg "ОШИБКА при выполнении запроса поиска распухших индексов. Пропускаем шаг REINDEX."
    elif [[ -n "$indexes_to_reindex" ]]; then
        log_msg "Найдены следующие индексы для перестроения CONCURRENTLY:"
        echo "$indexes_to_reindex" | while IFS= read -r index_name; do log_msg " - $index_name"; done

        processed_tables="" # Для отслеживания таблиц, которым уже делали ANALYZE

        echo "$indexes_to_reindex" | while IFS= read -r index_qname; do
            # Извлечение схемы и имени индекса (безопаснее)
            schema_name=$(echo "$index_qname" | cut -d'.' -f1)
            index_name=$(echo "$index_qname" | cut -d'.' -f2)
            # Формирование полного имени с кавычками для безопасности
            full_index_name="\"$schema_name\".\"$index_name\""

            log_msg "Попытка REINDEX INDEX CONCURRENTLY для $full_index_name..."
            sql_cmd="REINDEX INDEX CONCURRENTLY $full_index_name;"

            time_cmd "REINDEX CONCURRENTLY $full_index_name" run_sql "$sql_cmd"
            reindex_exit_code=$?

            if [[ $reindex_exit_code -ne 0 ]]; then
                log_msg "ОШИБКА: REINDEX CONCURRENTLY для $full_index_name завершился неудачно. Проверьте логи PostgreSQL на сервере для деталей."
                find_invalid_indexes # Проверяем, не остался ли мусор
            else
                 log_msg "REINDEX CONCURRENTLY для $full_index_name успешно завершен."
                 # Получаем имя таблицы для ANALYZE
                 table_qname=$(psql -q -t -A -c "SELECT quote_ident(n.nspname) || '.' || quote_ident(ct.relname) FROM pg_index i JOIN pg_class ci ON i.indexrelid = ci.oid JOIN pg_class ct ON i.indrelid = ct.oid JOIN pg_namespace n ON ct.relnamespace = n.oid WHERE n.nspname = '$schema_name' AND ci.relname = '$index_name';")

                 if [[ -n "$table_qname" && $? -eq 0 && $(echo "$processed_tables" | grep -Fxq "$table_qname"; echo $?) -ne 0 ]]; then
                     log_msg "Выполнение ANALYZE VERBOSE для таблицы $table_qname после реиндексации."
                     # Используем run_sql, который сам добавит кавычки к команде не нужно
                     time_cmd "ANALYZE $table_qname" run_sql "ANALYZE VERBOSE $table_qname;"
                     processed_tables+="$table_qname"$'\n' # Добавляем таблицу в список обработанных
                 elif [[ -z "$table_qname" ]]; then
                     log_msg "ПРЕДУПРЕЖДЕНИЕ: Не удалось определить таблицу для индекса $full_index_name для выполнения ANALYZE."
                 fi
            fi
            # Пауза между реиндексами (опционально)
            # sleep 5
        done

        # Дополнительный легкий VACUUM после всех REINDEX CONCURRENTLY
        log_msg "Запуск легкого VACUUM после REINDEX CONCURRENTLY для очистки..."
        time_cmd "VACUUM (post-reindex)" vacuumdb --verbose --jobs="$VACUUMDB_JOBS" --dbname="$PGDATABASE"

    else
        log_msg "Распухших индексов, требующих перестроения, не найдено."
    fi
else
    log_msg "Шаг переиндексации отключен (ENABLE_REINDEX=false)."
fi


# 3. Опциональный шаг с использованием pg_repack (если включено)
if [[ "$ENABLE_PG_REPACK" == "true" ]]; then
    log_msg "Поиск распухших таблиц для pg_repack (размер > ${REPACK_MIN_TABLE_SIZE_MB}MB, распухание > ${REPACK_BLOAT_THRESHOLD_PERCENT}%)..."
    # Здесь нужен запрос для поиска распухших *таблиц*. Пример оценочного запроса.
    # Для точности используйте pgstattuple.
    TABLE_BLOAT_QUERY=$(cat <<-'EOF'
WITH constants AS (
  SELECT current_setting('block_size')::numeric AS bs
), table_stats AS (
    SELECT
        n.nspname,
        tbl.relname AS tablename,
        tbl.reltuples,
        tbl.relpages,
        (tbl.relpages * bs)::bigint AS table_bytes,
        (tbl.relpages * bs / 1024.0 / 1024.0)::numeric(18,2) as table_size_mb,
        -- Estimate tuple width (adjust hdr/ma if needed for specific PG versions/settings)
        ( 4 + 23 + CASE WHEN MAX(length(t::text)) IS NULL THEN 0 ELSE MAX(length(t::text))::numeric END ) AS est_tuple_width -- Very rough estimate
    FROM pg_class tbl
    JOIN pg_namespace n ON n.oid = tbl.relnamespace
    LEFT JOIN pg_attribute a ON a.attrelid = tbl.oid AND a.attnum > 0 AND NOT a.attisdropped
    LEFT JOIN pg_type t ON a.atttypid = t.oid
    CROSS JOIN constants
    WHERE tbl.relkind = 'r'
      AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
      AND tbl.reltuples > 0 AND tbl.relpages > 0
    GROUP BY n.nspname, tbl.relname, tbl.reltuples, tbl.relpages, bs
), table_bloat_info AS (
    SELECT
        nspname, tablename, table_size_mb,
        -- Estimate bloat based on expected vs actual pages
        CASE
            WHEN reltuples > 0 AND est_tuple_width > 0 THEN
                GREATEST(0, (100 * ( 1 - (reltuples * est_tuple_width) / (table_bytes + 1) )))::numeric(6,2)
            ELSE 0
        END AS estimate_bloat_pct
    FROM table_stats
)
SELECT nspname || '.' || tablename
FROM table_bloat_info
WHERE table_size_mb >= $1
  AND estimate_bloat_pct >= $2
ORDER BY table_bytes DESC;
EOF
)
    tables_to_repack=$(psql -q -t -A \
        -v min_size_mb="$REPACK_MIN_TABLE_SIZE_MB" \
        -v min_bloat_pct="$REPACK_BLOAT_THRESHOLD_PERCENT" \
        -c "$TABLE_BLOAT_QUERY")

    if [[ $? -ne 0 ]]; then
        log_msg "ОШИБКА при выполнении запроса поиска распухших таблиц. Пропускаем шаг pg_repack."
    elif [[ -n "$tables_to_repack" ]]; then
        log_msg "Найдены следующие таблицы для обработки с помощью pg_repack:"
        echo "$tables_to_repack" | while IFS= read -r table_qname; do log_msg " - $table_qname"; done

        echo "$tables_to_repack" | while IFS= read -r table_qname; do
            log_msg "Запуск pg_repack для таблицы '$table_qname'..."
            # pg_repack использует PG* переменные окружения и ~/.pgpass
            repack_cmd_opts=("--dbname=$PGDATABASE" "--table=$table_qname" "--jobs=${REPACK_JOBS}" "--no-order" "--wait-timeout=600")

            time_cmd "pg_repack $table_qname" "$PG_REPACK_PATH" "${repack_cmd_opts[@]}"
            repack_exit_code=$?

            if [[ $repack_exit_code -ne 0 ]]; then
                log_msg "ОШИБКА: pg_repack для таблицы '$table_qname' завершился неудачно. Проверьте логи pg_repack и PostgreSQL."
            else
                log_msg "pg_repack для таблицы '$table_qname' успешно завершен."
                # После pg_repack таблица уже проанализирована
            fi
        done
    else
        log_msg "Распухших таблиц, требующих pg_repack, не найдено."
    fi
else
    log_msg "Шаг pg_repack отключен (ENABLE_PG_REPACK=false)."
fi

# --- Завершение ---
# Финальная проверка невалидных индексов
find_invalid_indexes

log_msg "Регламентное обслуживание базы данных '$PGDATABASE' завершено."
exit 0