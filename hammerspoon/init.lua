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
local function getWindowsForSpace(spaceId)
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
hs.alert.show("Spaces Menubar loaded! 📍", 1)
