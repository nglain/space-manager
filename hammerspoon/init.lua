-- Hammerspoon config for Space Manager
-- Backend for moving windows between macOS Spaces using Mission Control simulation
require("hs.ipc")

-- Load PaperWM mission control module for window moving
local spoonPath = os.getenv("HOME") .. "/.hammerspoon/Spoons/PaperWM.spoon"
MissionControl = dofile(spoonPath .. "/mission_control.lua")

--------------------------------------------------------------------------------
-- API для Space Manager
--------------------------------------------------------------------------------

-- Получить список всех окон с информацией о space
function getWindowsJSON()
    local windows = hs.window.allWindows()
    local result = {}
    for _, win in ipairs(windows) do
        local app = win:application()
        local appName = app and app:name() or "unknown"
        local sp = hs.spaces.windowSpaces(win)
        local spaceId = sp and sp[1] or nil

        -- Найти индекс space
        local spaceIndex = nil
        if spaceId then
            local spaces = hs.spaces.spacesForScreen()
            for idx, sid in ipairs(spaces) do
                if sid == spaceId then
                    spaceIndex = idx
                    break
                end
            end
        end

        table.insert(result, {
            id = win:id(),
            app = appName,
            title = win:title() or "",
            spaceId = spaceId,
            spaceIndex = spaceIndex,
            visible = win:isVisible(),
            minimized = win:isMinimized()
        })
    end
    return hs.json.encode(result)
end

-- Получить информацию о spaces
function getSpacesJSON()
    local spaces = hs.spaces.spacesForScreen()
    local focused = hs.spaces.focusedSpace()
    local focusedIndex = nil

    for idx, sid in ipairs(spaces) do
        if sid == focused then
            focusedIndex = idx
            break
        end
    end

    return hs.json.encode({
        spaces = spaces,
        focused = focused,
        focusedIndex = focusedIndex,
        count = #spaces
    })
end

-- Получить текущий focused space index (1-based)
function getFocusedSpaceIndex()
    local spaces = hs.spaces.spacesForScreen()
    local focused = hs.spaces.focusedSpace()
    for idx, sid in ipairs(spaces) do
        if sid == focused then
            return idx
        end
    end
    return 1
end

-- Переместить окно на space по индексу (1-based)
function moveWindowToSpace(windowId, spaceIndex)
    local win = hs.window.get(windowId)
    if not win then
        return hs.json.encode({success = false, error = "window not found"})
    end

    local spaces = hs.spaces.spacesForScreen()
    if not spaces or spaceIndex < 1 or spaceIndex > #spaces then
        return hs.json.encode({success = false, error = "invalid space index"})
    end

    local spaceId = spaces[spaceIndex]
    local success, err = MissionControl:moveWindowToSpace(win, spaceId)

    return hs.json.encode({success = success, error = err})
end

-- Переключиться на space по индексу
function gotoSpace(spaceIndex)
    local spaces = hs.spaces.spacesForScreen()
    if not spaces or spaceIndex < 1 or spaceIndex > #spaces then
        return hs.json.encode({success = false, error = "invalid space index"})
    end

    local spaceId = spaces[spaceIndex]
    hs.spaces.gotoSpace(spaceId)
    return hs.json.encode({success = true})
end

-- Активировать окно
function focusWindow(windowId)
    local win = hs.window.get(windowId)
    if not win then
        return hs.json.encode({success = false, error = "window not found"})
    end
    win:focus()
    return hs.json.encode({success = true})
end

-- Краткая версия для быстрого вызова
function smListWindows() return getWindowsJSON() end
function smListSpaces() return getSpacesJSON() end
function smFocusedSpace() return getFocusedSpaceIndex() end
function smMoveWindow(wid, idx) return moveWindowToSpace(wid, idx) end
function smGotoSpace(idx) return gotoSpace(idx) end
function smFocusWindow(wid) return focusWindow(wid) end

print("Hammerspoon Space Manager backend ready!")

--------------------------------------------------------------------------------
-- SPACES MENUBAR - Названия рабочих столов в меню баре
--------------------------------------------------------------------------------

-- Файл для хранения названий
local spaceNamesFile = os.getenv("HOME") .. "/.hammerspoon/space_names.json"

-- Загрузить названия из файла
local function loadSpaceNames()
    local file = io.open(spaceNamesFile, "r")
    if file then
        local content = file:read("*all")
        file:close()
        local ok, data = pcall(hs.json.decode, content)
        if ok and data then return data end
    end
    return {}
end

-- Сохранить названия в файл
local function saveSpaceNames(names)
    local file = io.open(spaceNamesFile, "w")
    if file then
        file:write(hs.json.encode(names))
        file:close()
    end
end

-- Глобальные переменные
local spaceNames = loadSpaceNames()
local spacesMenubar = hs.menubar.new()

-- Получить название для Space
local function getSpaceName(index)
    return spaceNames[tostring(index)] or ("Desktop " .. index)
end

-- Установить название для Space
local function setSpaceName(index, name)
    spaceNames[tostring(index)] = name
    saveSpaceNames(spaceNames)
end

-- Получить окна для конкретного Space
function getWindowsForSpace(spaceId)  -- ГЛОБАЛЬНАЯ для scanAllSpaces
    local allWindows = hs.window.allWindows()
    local spaceWindows = {}

    for _, win in ipairs(allWindows) do
        if win:isVisible() and win:title() ~= "" then
            local winSpaces = hs.spaces.windowSpaces(win)
            if winSpaces then
                for _, wsid in ipairs(winSpaces) do
                    if wsid == spaceId then
                        local app = win:application()
                        table.insert(spaceWindows, {
                            window = win,
                            title = win:title(),
                            app = app and app:name() or "?"
                        })
                        break
                    end
                end
            end
        end
    end

    return spaceWindows
end

-- Обновить menubar
local function updateSpacesMenubar()
    local spaces = hs.spaces.spacesForScreen()
    local focused = hs.spaces.focusedSpace()
    local currentIndex = 1

    for idx, sid in ipairs(spaces) do
        if sid == focused then
            currentIndex = idx
            break
        end
    end

    local currentName = getSpaceName(currentIndex)
    spacesMenubar:setTitle("📍 " .. currentName)

    -- Построить меню
    local menuItems = {}

    for idx, sid in ipairs(spaces) do
        local name = getSpaceName(idx)
        local isCurrent = (sid == focused)
        local windows = getWindowsForSpace(sid)

        -- Построить submenu с окнами
        local submenu = {}

        if #windows > 0 then
            for _, w in ipairs(windows) do
                local title = w.app .. ": " .. w.title
                if #title > 40 then
                    title = title:sub(1, 37) .. "..."
                end
                table.insert(submenu, {
                    title = title,
                    fn = function()
                        hs.spaces.gotoSpace(sid)
                        w.window:focus()
                    end
                })
            end
        else
            table.insert(submenu, { title = "(пусто)", disabled = true })
        end

        -- Добавить разделитель и пункт переключения
        table.insert(submenu, { title = "-" })
        table.insert(submenu, {
            title = "→ Перейти на " .. name,
            fn = function()
                hs.spaces.gotoSpace(sid)
            end
        })

        local spaceId = sid  -- capture for closure
        table.insert(menuItems, {
            title = (isCurrent and "✓ " or "   ") .. idx .. ". " .. name .. " (" .. #windows .. ")",
            fn = function()
                hs.spaces.gotoSpace(spaceId)
            end,
            menu = submenu
        })
    end

    table.insert(menuItems, { title = "-" })

    -- Пункт переименования текущего
    table.insert(menuItems, {
        title = "✏️ Переименовать «" .. currentName .. "»",
        fn = function()
            local button, newName = hs.dialog.textPrompt(
                "Переименовать Desktop " .. currentIndex,
                "Введите новое название:",
                currentName,
                "OK", "Отмена"
            )
            if button == "OK" and newName and newName ~= "" then
                setSpaceName(currentIndex, newName)
                updateSpacesMenubar()
            end
        end
    })

    spacesMenubar:setMenu(menuItems)
end

-- Следить за переключением Spaces
local spaceWatcher = hs.spaces.watcher.new(updateSpacesMenubar)
spaceWatcher:start()

-- Инициализация
updateSpacesMenubar()

--------------------------------------------------------------------------------
-- ВЫЕЗЖАЮЩАЯ ПАНЕЛЬ СПРАВА (как Dock)
--------------------------------------------------------------------------------

-- ГЛОБАЛЬНЫЕ переменные (чтобы не собрал garbage collector)
SidePanel = nil
SidePanelVisible = false
PANEL_WIDTH = 200
EDGE_TRIGGER = 10  -- пикселей от края для активации (увеличено)

-- Кеш количества окон на каждом Space (обновляется при переключении)
WindowCountCache = {}
ScanInProgress = false

-- Глобальные для сканирования
ScanSpaces = {}
ScanOriginalSpace = nil
ScanIdx = 1
ScanTotal = 0

-- Увеличиваем время ожидания Mission Control
hs.spaces.setDefaultMCwaitTime(0.6)

-- Глобальный таймер для сканирования
ScanTimer = nil

-- Сканирование с doEvery (надёжнее чем рекурсия)
function scanAllSpaces()
    if ScanInProgress then return end

    local spaces = hs.spaces.spacesForScreen()
    local totalSpaces = #spaces
    local originalSpace = hs.spaces.focusedSpace()

    -- Фильтруем только user spaces (не fullscreen)
    local userSpaces = {}
    local userIndices = {}
    for i, sid in ipairs(spaces) do
        local spaceType = hs.spaces.spaceType(sid)
        if spaceType == "user" then
            table.insert(userSpaces, sid)
            table.insert(userIndices, i)
        else
            -- Fullscreen = 1 окно
            WindowCountCache[i] = 1
        end
    end

    if #userSpaces == 0 then
        hs.alert.show("Нет user spaces", 1)
        return
    end

    ScanInProgress = true
    local currentIdx = 1

    hs.alert.show("Сканирую " .. #userSpaces .. " spaces...", 1)

    -- Используем doEvery - он не зависит от рекурсии
    -- Сначала переходим, в СЛЕДУЮЩЕМ цикле считаем
    local lastSpaceId = nil
    local lastRealIdx = nil

    ScanTimer = hs.timer.doEvery(0.6, function()
        -- Считаем окна от ПРЕДЫДУЩЕГО перехода (Space уже переключился)
        if lastSpaceId then
            local windows = getWindowsForSpace(lastSpaceId)
            WindowCountCache[lastRealIdx] = #windows
        end

        if currentIdx > #userSpaces then
            -- Готово
            ScanTimer:stop()
            ScanTimer = nil
            hs.spaces.gotoSpace(originalSpace)
            ScanInProgress = false
            hs.timer.doAfter(0.3, function()
                if SidePanelVisible then updateSidePanel() end
                hs.alert.show("✓ " .. #userSpaces .. " spaces!", 0.5)
            end)
            return
        end

        local spaceId = userSpaces[currentIdx]
        local realIdx = userIndices[currentIdx]

        -- Переходим
        hs.spaces.gotoSpace(spaceId)

        -- Запоминаем для подсчёта в следующем цикле
        lastSpaceId = spaceId
        lastRealIdx = realIdx
        currentIdx = currentIdx + 1
    end)
end

-- Создать панель (ГЛОБАЛЬНАЯ)
function createSidePanel()
    local screen = hs.screen.mainScreen():frame()

    SidePanel = hs.canvas.new({
        x = screen.w - PANEL_WIDTH,
        y = 0,
        w = PANEL_WIDTH,
        h = screen.h
    })

    -- Фон панели
    SidePanel[1] = {
        type = "rectangle",
        fillColor = { red = 0.1, green = 0.1, blue = 0.1, alpha = 0.95 },
        roundedRectRadii = { xRadius = 10, yRadius = 10 }
    }

    SidePanel:level(hs.canvas.windowLevels.floating)
    SidePanel:behavior(hs.canvas.windowBehaviors.canJoinAllSpaces)
end

-- Обновить содержимое панели (ГЛОБАЛЬНАЯ для scanAllSpaces)
function updateSidePanel()
    if not SidePanel then createSidePanel() end

    -- Очистить всё кроме фона
    while #SidePanel > 1 do
        SidePanel:removeElement(2)
    end

    local spaces = hs.spaces.spacesForScreen()
    local focused = hs.spaces.focusedSpace()
    local y = 20
    local itemHeight = 50

    -- Заголовок + кнопка сканирования
    SidePanel:appendElements({
        type = "text",
        text = "Spaces",
        textColor = { white = 0.6 },
        textSize = 12,
        frame = { x = 15, y = y, w = PANEL_WIDTH - 60, h = 20 }
    })
    -- Кнопка 🔄 для сканирования всех Spaces
    SidePanel:appendElements({
        type = "text",
        text = "🔄",
        textSize = 14,
        frame = { x = PANEL_WIDTH - 40, y = y - 2, w = 25, h = 25 },
        trackMouseDown = true,
        id = "scanButton"
    })
    y = y + 30

    for idx, sid in ipairs(spaces) do
        local name = getSpaceName(idx)
        local isCurrent = (sid == focused)
        local windows = getWindowsForSpace(sid)
        local windowCount = #windows

        -- Обновляем кеш для текущего Space (только он показывает реальные окна)
        if isCurrent then
            WindowCountCache[idx] = windowCount
        end
        -- Используем кеш если есть, иначе показываем что нашли
        local displayCount = WindowCountCache[idx] or windowCount

        -- Фон для текущего Space
        if isCurrent then
            SidePanel:appendElements({
                type = "rectangle",
                frame = { x = 10, y = y - 5, w = PANEL_WIDTH - 20, h = itemHeight - 5 },
                fillColor = { red = 0.2, green = 0.4, blue = 0.8, alpha = 0.5 },
                roundedRectRadii = { xRadius = 8, yRadius = 8 }
            })
        end

        -- Название Space
        SidePanel:appendElements({
            type = "text",
            text = idx .. ". " .. name,
            textColor = { white = isCurrent and 1 or 0.8 },
            textSize = 14,
            textFont = ".AppleSystemUIFont",
            frame = { x = 15, y = y, w = PANEL_WIDTH - 30, h = 20 }
        })

        -- Количество окон (из кеша или текущее)
        local countText = displayCount .. " окон"
        if not isCurrent and not WindowCountCache[idx] then
            countText = "?" -- Ещё не посещали этот Space
        end
        SidePanel:appendElements({
            type = "text",
            text = countText,
            textColor = { white = 0.5 },
            textSize = 11,
            frame = { x = 15, y = y + 18, w = PANEL_WIDTH - 30, h = 16 }
        })

        y = y + itemHeight
    end

    -- Добавить обработчик кликов
    SidePanel:clickActivating(false)
    SidePanel:canvasMouseEvents(true, true, true, true)  -- ВАЖНО: включаем все mouse events!
    SidePanel:mouseCallback(function(canvas, event, id, x, y)
        if event ~= "mouseDown" then return end  -- Реагируем только на нажатие

        -- Клик на кнопку 🔄 сканирования (в области заголовка справа)
        if y < 50 and x > PANEL_WIDTH - 50 then
            scanAllSpaces()
            return
        end

        local spaces = hs.spaces.spacesForScreen()
        local clickedIdx = math.floor((y - 50) / 50) + 1

        if clickedIdx >= 1 and clickedIdx <= #spaces then
            -- Проверяем какая кнопка нажата
            local buttons = hs.eventtap.checkMouseButtons()

            if buttons.right then
                -- Правый клик - переименование (сайдбар остаётся видимым)
                local currentName = getSpaceName(clickedIdx)
                hs.timer.doAfter(0.1, function()
                    local button, newName = hs.dialog.textPrompt(
                        "Переименовать Desktop " .. clickedIdx,
                        "Введите новое название:",
                        currentName,
                        "OK", "Отмена"
                    )
                    if button == "OK" and newName and newName ~= "" then
                        setSpaceName(clickedIdx, newName)
                        updateSpacesMenubar()
                        updateSidePanel()  -- Обновить панель с новым именем
                    end
                    hideSidePanel()  -- Скрыть после закрытия диалога
                end)
            else
                -- Левый клик - переход на Space
                hs.spaces.gotoSpace(spaces[clickedIdx])
                hideSidePanel()
            end
        end
    end)
end

-- Показать панель (ГЛОБАЛЬНАЯ)
function showSidePanel()
    if SidePanelVisible then return end
    updateSidePanel()
    SidePanel:show()
    SidePanelVisible = true
end

-- Скрыть панель
function hideSidePanel()
    if not SidePanelVisible then return end
    if SidePanel then SidePanel:hide() end
    SidePanelVisible = false
end

-- Отслеживать мышь (ГЛОБАЛЬНАЯ переменная!)
MouseTracker = hs.eventtap.new({hs.eventtap.event.types.mouseMoved}, function(e)
    local pos = hs.mouse.absolutePosition()
    local screen = hs.screen.mainScreen():frame()

    -- Мышь у правого края
    if pos.x >= screen.w - EDGE_TRIGGER then
        showSidePanel()
    -- Мышь ушла от панели
    elseif SidePanelVisible and pos.x < screen.w - PANEL_WIDTH - 20 then
        hideSidePanel()
    end

    return false
end)

MouseTracker:start()

-- Обновлять панель при смене Space (ГЛОБАЛЬНАЯ!)
SidePanelWatcher = hs.spaces.watcher.new(function()
    if SidePanelVisible then
        updateSidePanel()
    end
end)
SidePanelWatcher:start()

hs.alert.show("Spaces: Menubar + Side Panel loaded! 📍", 1)
