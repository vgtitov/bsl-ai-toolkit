# 1С в Docker и Kubernetes

Контейнеризация даёт воспроизводимое окружение для CI-агентов, тестовых стендов, 1С Fresh и (с оговорками)
прод-сервера. Версии платформы/образов — НАСТРОЙ ПОД СВОЙ ПРОЕКТ. Источник истины по образам — `onec-docker`
(репозиторий firstBitMarksistskaya/onec-docker) и официальная документация; **бинарные дистрибутивы платформы в
образ не кладут** — образ их скачивает с `releases.1c.ru`/`users.v8.1c.ru` под твоей учётной записью.

## 1. Образы (onec-docker)

Сборка управляется `.onec.env` (скопировать из `.onec.env.example`):
- `ONEC_USERNAME` / `ONEC_PASSWORD` — учётка `releases.1c.ru` (секрет!);
- `ONEC_VERSION` — версия платформы 8.3 в образе;
- `EDT_VERSION` — версия EDT (для образов с EDT/замером покрытия).

Типовой набор образов: **server** (сервер 1С), **client** (толстый, в т.ч. с VNC), **thin client** (тонкий),
**config-storage** (хранилище конфигурации, `onec-crs`), **rac-gui**, **gitsync**, **oscript**, **vanessa-runner**,
**EDT**, «Исполнитель». RAS поднимают отдельным процессом/командой в server-образе (см. cluster `ras`). Сборка —
через build-args `ONEC_USERNAME`/`ONEC_PASSWORD`/`ONEC_VERSION` (учётка `releases.1c.ru`); готовые дистрибутивы
кладут в папку `distr/`; NLS-локализация — `--build-arg nls_enabled=true`; поддерживаются Swarm и k8s.
Альтернативные наборы: `pravets/onec-images` и `pravets/oscript-images` (автосборка через GitHub Actions),
`thedemoncat/onec-images-docs` (onec-base / onec-server / onec-full), `ondysss/onec-client`.

`Dockerfile` — ключевые секции (по методичке): **FROM** (базовый образ), **COPY** (файлы в образ), **RUN**
(команды → слой образа; объединяй через `&& \`, чтобы не плодить слои), **ENTRYPOINT** (команда запуска — указывай
**абсолютный путь** к скрипту, иначе контейнер падает «no such file»), **USER** (под каким пользователем работать —
сервер 1С не запускают от root без нужды), **LABEL**. Разница CMD/ENTRYPOINT: ENTRYPOINT — жёсткая команда запуска,
CMD — аргументы по умолчанию (переопределяются в `docker run`).

```dockerfile
# концептуально, версии/пути — под свой проект
FROM onec-base:<версия>
RUN set -eux; \
    addgroup --system grp1cv8 && adduser --system --ingroup grp1cv8 usr1cv8
COPY entrypoint.sh /entrypoint.sh
USER usr1cv8
ENTRYPOINT [ "/entrypoint.sh" ]      # абсолютный путь обязателен
```

## 2. docker-compose

Инфраструктуру (сервер 1С + СУБД + публикация) описывают в `docker-compose.yml`: сервисы, `image`, `environment`,
`ports`, `volumes`, `volumes:`-секция для именованных томов. Сеть compose даёт сервисам DNS по имени сервиса.
Базовый шаблон:
```yaml
services:
  db:
    image: postgrespro-1c:<версия>          # PostgresPro для 1С
    environment: [ POSTGRES_PASSWORD=<секрет-из-env> ]
    volumes: [ pgdata:/var/lib/pgpro ]
  srv:
    image: onec-server:<версия>
    depends_on: [ db ]
    ports: [ "1540:1540", "1541:1541", "1560-1591:1560-1591" ]
    volumes:
      - srvdata:/home/usr1cv8/.1cv8
      - ./conf/nethasp.ini:/opt/1cv8/current/conf/nethasp.ini:ro   # лицензии HASP
volumes: { pgdata: {}, srvdata: {} }
```
Тома обязательны для всего, что должно пережить пересоздание контейнера: кластерные данные `srv1cv8`, данные СУБД.
Без томов пересборка = потеря ИБ.

**Базовые правила compose** (для сервисов 1С и сопутствующих, напр. MCP):
- порты биндить на `127.0.0.1`, а не `0.0.0.0`, если внешний доступ не нужен;
- тома для данных/индексов обязательны (иначе теряются при пересоздании), `:ro` где можно;
- секреты — только в `.env` (он в `.gitignore`), НЕ в самом `compose`;
- `restart: unless-stopped` (иначе после ребута сервис не поднимется);
- образ фиксировать по тегу/хешу, **НЕ `latest`** (воспроизводимость).

## 3. Лицензирование в контейнере

Три пути (выбери под ОС/тип лицензий):
- **Проброс HASP** (только Linux-хост): установить драйверы HASP на хосте → появляется `/tmp/.aksusb` →
  смонтировать его в контейнер сервера в `docker-compose.yml` (`/tmp/.aksusb:/tmp/.aksusb`). На не-Linux хостах
  проброс ключа не поддерживается.
- **Сетевой сервер лицензий через `nethasp.ini`**: раскомментировать строки и указать имя реального сервера
  лицензий; файл монтировать в `…/conf/nethasp.ini`. В **onec-docker** монтируется в
  `/opt/1cv8/current/conf/nethasp.ini`; в **docker_fresh** лежит в `docker_fresh/conf/core/nethasp.ini`. В Jenkins
  (Docker Swarm plugin) файл кладут как **docker config**: `docker config create nethasp.ini ./nethasp.ini`, в
  шаблоне агента (Configs) указывают `nethasp.ini:/opt/1cv8/current/conf/nethasp.ini`. Владелец каталога лицензий
  внутри контейнера — `chown -R usr1cv8:grp1cv8 /var/1C/licenses`.
- **Программные лицензии** — активация утилитой **`ring license activate`** внутри образа (`ring` есть в core-образе
  Fresh), с пробросом каталога лицензий в том, чтобы активация пережила пересоздание контейнера:
  ```bash
  docker run --rm -it -v <локальный_каталог>:/var/1C/licenses fresh/core \
    bash -l -c 'ring license activate --serial "<серийный>" --pin "<PIN>" \
                --first-name "..." --last-name "..." --email "..." --country "Russia" ... \
                --send-statistics "false"'
  ```
  **Внимание:** программная лицензия привязана к параметрам машины — при пересоздании контейнера/смене хоста она
  может «слететь». Каталог `/var/1C/licenses` обязательно в персистентный том. Серийник/PIN — секреты, не в репо.
- **Комьюнити-лицензия** — бесплатная, ограничения по числу пользователей и функциональности; активация через
  клиент или `reg_lic --community` (см. `cluster-and-licensing.md`).
- **СЛК** — для защищённых сторонних решений собирают отдельный образ службы СЛК (по методичке «Рарус» — глава о
  сборке образа СЛК: свой `Dockerfile` + `entrypoint.sh` + `docker-compose.yml`).

## 4. 1С Fresh (облачная подсистема) в Docker

Стенд Fresh (`docker_fresh`, репозиторий `1C-Company/docker_fresh`) разворачивается набором контейнеров —
**centos / core / db (PostgreSQL) / site / forum / gate** — за ~30 мин, скриптами `install.py`/`start.py`:
```bash
sudo python3 install.py -v <версия>            # установка нужной версии платформы/конфигурации
sudo python3 start.py -new -h <имя-стенда>     # новый стенд → https://<имя>.1cfresh-dev.ru
sudo python3 start.py                           # перезапуск существующего
sudo python3 start.py -debug                    # подробный лог запуска
docker-compose down                             # остановить (из каталога workdir)
```
Нужно: настроить лицензирование (`docker_fresh/conf/core/nethasp.ini` или `ring`), прописать `hosts`
(`<ip> <имя>.1cfresh-dev.ru srv.<имя>... s3.1cfresh-dev.ru`), имена ИБ — в `params.json`. Доступ: сайт,
менеджер сервиса (`/a/adm`), конфигуратор через `Srvr="srv.<имя>...";Ref="<ИБ>";`. В WSL2 ip берут
`ip -4 addr show eth0`. Fresh подразумевает S3 для файлов (см. `linux-server-ops.md`, S3).

## 5. Kubernetes (с оговорками)

Сервер 1С — stateful, плохо ложится на «эфемерные поды». Что реально делают:
- **Stateless-нагрузку** (RAS-прокси, веб-публикацию, CI-агенты, тонкий клиент, gitsync/vanessa) — в k8s нормально.
- **Сервер 1С + СУБД** — через **StatefulSet + PersistentVolume** (кластерные данные `srv1cv8`, данные СУБД на PV),
  фиксированные сетевые идентичности. Лицензирование — сетевой сервер лицензий/`nethasp.ini` (проброс USB-HASP в
  k8s практически невозможен → программные/сетевые лицензии). [проверить под свою инсталляцию]
- Секреты (пароли СУБД, учётка releases, токены) — `Secret`, не в манифестах/образах.

**Реальные ограничения контейнеризации сервера 1С:** лицензии (HASP не пробросишь куда угодно; программные
«слетают» при смене окружения); stateful-природа (нужны тома, иначе теряются данные кластера); ресурсы (сервер 1С
прожорлив по памяти — задавай limits аккуратно, OOM-kill `rphost` = упавшие сеансы); поддержка вендора (официально
1С контейнеризацию сервера полноценно не «благословляет» — взвесь риски для прода).

## 6. Контрприёмы
- Контейнер пересоздали — пропала ИБ/лицензия. **Контр:** всё состояние в именованные тома/PV (`srv1cv8`, СУБД,
  `/var/1C/licenses`).
- ENTRYPOINT без абсолютного пути → контейнер не стартует. **Контр:** абсолютный путь к скрипту.
- Логин `releases.1c.ru` / пароль СУБД / PIN лицензии в `Dockerfile`/compose/манифесте. **Контр:** build-args из
  CI-секретов, `docker secret`/k8s `Secret`, плейсхолдеры в репозитории.
- Сервер 1С от root «чтобы заработало». **Контр:** отдельный `usr1cv8`/`USER`.
- В Docker «тормозит» — диагностика та же, что на железе (ТЖ, счётчики, см. `monitoring-and-backup.md`), плюс
  проверь cgroup-лимиты памяти/CPU контейнера.

## Готовые инструменты (опенсорс)
Сборка образов 1С — **firstBitMarksistskaya/onec-docker** (эталон, сервер/клиент/RAS/веб/CRS). Простой стек — **alexanderfefelov/docker-1c-server**. Фреш — официальный **1C-Company/docker_fresh**. k8s — **thedemoncat/kubeonec** (только пример: вендором НЕ поддерживается). Полный проверенный список — `docs/ops-tools-catalog.md`.
