# kompas — плагин Claude Code для КОМПАС-3D

Claude работает с документами КОМПАС-3D через COM API: создаёт и правит чертежи, фрагменты и детали,
ставит размеры, заполняет основную надпись и **сам смотрит на результат** — документ рендерится в картинку
(временный файл удаляется сразу, на диске ничего не остаётся).

Состав плагина:
- MCP-сервер `kompas` (`server.py`) — инструменты `kompas_*`;
- скилл `kompas` (`skills/kompas/SKILL.md`) — порядок работы, координаты листа, правила ЕСКД, работа с диалогами.

## Требования

- Windows, КОМПАС-3D (проверено на v25, в том числе учебной версии).
- Python 3.10+ в `PATH` (команда `python`).
- Зависимости: `pip install -r requirements.txt`

## Установка

С GitHub:

```bash
pip install mcp pywin32 pillow pydantic comtypes
claude plugin marketplace add MyNameIsEldar/kompas-mcp
claude plugin install kompas@kompas-local
```

Из локальной копии репозитория:

```bash
pip install -r requirements.txt
claude plugin marketplace add <путь к папке kompas-mcp>
claude plugin install kompas@kompas-local
```

После установки перезапустите сессию Claude Code. Проверка — попросите Claude: «проверь подключение к КОМПАСу».

Обновление после правок кода:

```bash
claude plugin marketplace update kompas-local
claude plugin update kompas@kompas-local
```

## Инструменты

| Инструмент | Что делает |
|---|---|
| `kompas_status` | Подключиться (при необходимости запустить КОМПАС), версия и список документов |
| `kompas_new`, `kompas_open`, `kompas_activate`, `kompas_save`, `kompas_close` | Документы |
| `kompas_preview` | Картинка документа: весь лист, участок `region` в мм, 3D-ориентация |
| `kompas_window_screenshot` | Снимок окна КОМПАСа (интерфейс, диалоги) |
| `kompas_dialogs` | Найти модальный диалог, прочитать текст и кнопки, нажать кнопку |
| `kompas_draw` | Отрезки, окружности, дуги, прямоугольники, полилинии, точки, текст |
| `kompas_dimensions` | Линейные, диаметральные, радиальные, угловые размеры (с привязкой к дуге/окружности) |
| `kompas_list_objects`, `kompas_delete` | Объекты вида с координатами и reference; удаление |
| `kompas_stamp`, `kompas_sheet`, `kompas_views` | Основная надпись, формат листа, виды с масштабом |
| `kompas_3d_feature` | Эскиз на плоскости + выдавливание/вращение (добавить или вырезать) |
| `kompas_run_python` | Произвольный код против API7/API5 — для всего остального |
| `kompas_api_search` | Поиск интерфейсов, методов и констант API КОМПАСа |

## Как это устроено

- Все COM-вызовы идут через один поток (`kompas_com.ComWorker`).
- Пока операция выполняется, сервер раз в секунду проверяет, не заблокировано ли окно КОМПАСа
  модальным диалогом. Если да — вызов сразу возвращает текст диалога и его кнопки (MCP выполняет
  вызовы по очереди, поэтому ждать тайм-аута нельзя). Кнопку нажимает `kompas_dialogs` — через
  Win32 или UI Automation (диалоги КОМПАСа v25 сделаны на WPF).
- Для просмотра используется растровый экспорт API5 (`SaveAsToRasterFormat`) — он работает без
  диалогов, даже если окно свёрнуто.
- Учебная версия КОМПАСа не запускается через `CoCreateInstance`, поэтому сервер при необходимости
  запускает exe (путь из реестра) и подключается к запущенному экземпляру.

## Тесты

Тесты работают с живым КОМПАСом и создают в нём документы. Во время `dialog_test.py` ничего не нажимайте в КОМПАСе.

```bash
python tests/smoke_test.py
python tests/dialog_test.py
```
