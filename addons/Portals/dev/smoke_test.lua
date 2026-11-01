--[[---------------------------------------------------------------------------
    Portals - offline smoke test

    Loads Core.lua and PortalBar.lua against a small mock of the WoW API so the
    spellbook scan, secure button attributes, layout, slash commands and the
    combat deferral path can be checked without logging in.

    Run from the addon folder:  lua5.4 dev/smoke_test.lua
-----------------------------------------------------------------------------]]

local ADDON_DIR = (arg and arg[0] or ""):match("^(.*)[/\\]dev[/\\][^/\\]+$") or "."
local CORE    = ADDON_DIR .. "/Core.lua"
local BAR     = ADDON_DIR .. "/PortalBar.lua"
local SALES   = ADDON_DIR .. "/Sales.lua"
local QUEUE   = ADDON_DIR .. "/Queue.lua"
local MINIMAP = ADDON_DIR .. "/Minimap.lua"
local OPTIONS = ADDON_DIR .. "/Options.lua"

---------------------------------------------------------------------------
-- Mock WoW API
---------------------------------------------------------------------------

local failures = 0
local checks = 0

local function check(condition, message)
    checks = checks + 1
    if not condition then
        failures = failures + 1
        io.write("FAIL: ", message, "\n")
    end
end

local function checkEqual(actual, expected, message)
    checks = checks + 1
    if actual ~= expected then
        failures = failures + 1
        io.write(("FAIL: %s (expected %s, got %s)\n")
            :format(message, tostring(expected), tostring(actual)))
    end
end

-- state used by the stubs
local state = {
    class = "MAGE",
    entries = {},          -- { { id = 3561, name = "Teleport: Stormwind" }, ... }
    withIDs = true,
    counts = { [17031] = 10, [17032] = 20 },
    inCombat = false,
    zone = "Stormwind City",
    time = 1000,
    whispers = {},
    invites = {},
    printed = {},
    frames = {},
    eventFrames = {},
    party = {},
    partyCount = 0,
    unitLevels = {},
    distances = {},
    tradeRanges = {},
    target = nil,
    tradePartner = nil,
    tradeMoney = 0,
    tradesInitiated = {},
    lastMenu = nil,
    nameByID = {
        [3561] = "Teleport: Stormwind",  [10059] = "Portal: Stormwind",
        [3562] = "Teleport: Ironforge",  [11416] = "Portal: Ironforge",
        [3565] = "Teleport: Darnassus",  [11419] = "Portal: Darnassus",
        [32271] = "Teleport: Exodar",    [32266] = "Portal: Exodar",
        [3567] = "Teleport: Orgrimmar",  [11417] = "Portal: Orgrimmar",
        [3563] = "Teleport: Undercity",  [11418] = "Portal: Undercity",
        [3566] = "Teleport: Thunder Bluff", [11420] = "Portal: Thunder Bluff",
        [32272] = "Teleport: Silvermoon", [32267] = "Portal: Silvermoon",
        [35715] = "Teleport: Shattrath",  [35717] = "Portal: Shattrath",
    },
}

local function newRegion(kind)
    local region = { __kind = kind }
    function region:SetPoint() end
    function region:SetAllPoints() end
    function region:SetColorTexture() end
    function region:SetTexCoord() end
    function region:SetTexture(texture) self.__texture = texture end
    function region:SetDrawLayer() end
    function region:SetFont() end
    function region:SetText(text) self.__text = text end
    function region:GetText() return self.__text end
    function region:SetTextColor(r, g, b) self.__color = { r, g, b } end
    function region:SetJustifyH() end
    function region:SetShadowColor() end
    function region:SetShadowOffset() end
    function region:SetWidth() end
    function region:SetHeight() end
    function region:SetSize() end
    function region:SetScale() end
    function region:SetBlendMode() end
    function region:SetAlpha() end
    function region:Show() self.__shown = true end
    function region:Hide() self.__shown = false end
    function region:SetShown(value) self.__shown = value and true or false end
    function region:IsShown() return self.__shown ~= false end
    function region:SetDesaturated(value) self.__desaturated = value end
    function region:SetVertexColor(r, g, b) self.__vertex = { r, g, b } end
    return region
end

local function newFrame(frameType, name, parent, template)
    local frame = {
        __type = frameType, __name = name, __parent = parent, __template = template,
        __attributes = {}, __scripts = {}, __shown = true,
        __width = 0, __height = 0, __points = {}, __events = {},
    }

    function frame:SetSize(width, height) self.__width, self.__height = width, height end
    function frame:GetWidth() return self.__width end
    function frame:GetHeight() return self.__height end
    function frame:SetHeight(height) self.__height = height end
    function frame:SetWidth(width) self.__width = width end

    function frame:SetPoint(point, relativeTo, relativePoint, x, y)
        self.__points = { point, relativeTo, relativePoint, x, y }
    end
    function frame:SetAllPoints() end
    function frame:SetParent() end
    function frame:SetText(text) self.__text = text end
    function frame:GetText() return self.__text end
    function frame:SetToplevel() end
    function frame:ClearAllPoints() self.__points = {} end
    function frame:GetPoint()
        local p = self.__points
        if #p == 0 then
            return "CENTER", nil, "CENTER", 0, 0
        end
        return p[1], p[2], p[3], p[4], p[5]
    end

    function frame:SetScript(script, fn) self.__scripts[script] = fn end
    function frame:GetScript(script) return self.__scripts[script] end
    function frame:RegisterEvent(event)
        self.__events[event] = true
        table.insert(state.eventFrames, self)
    end
    function frame:UnregisterEvent(event) self.__events[event] = nil end

    function frame:RegisterForClicks() end
    function frame:RegisterForDrag() end
    function frame:EnableMouse(value) self.__mouseEnabled = value end
    function frame:SetMovable() end
    function frame:StartMoving() end
    function frame:StopMovingOrSizing() end
    function frame:SetFrameStrata() end
    function frame:SetFrameLevel() end
    function frame:SetClampedToScreen() end
    function frame:SetScale(value) self.__scale = value end
    function frame:SetAlpha() end

    function frame:Show() self.__shown = true end
    function frame:Hide() self.__shown = false end
    function frame:SetShown(value) self.__shown = value and true or false end
    function frame:IsShown() return self.__shown end
    function frame:Enable() self.__enabled = true end
    function frame:Disable() self.__enabled = false end
    function frame:IsEnabled() return self.__enabled ~= false end

    function frame:SetAttribute(key, value) self.__attributes[key] = value end
    function frame:GetAttribute(key) return self.__attributes[key] end

    function frame:CreateTexture(textureName, layer)
        local texture = newRegion("Texture")
        texture.__layer = layer
        return texture
    end
    function frame:CreateFontString(textureName, layer)
        local fontString = newRegion("FontString")
        fontString.__layer = layer
        return fontString
    end
    function frame:SetCooldown(start, duration) self.__cooldown = { start, duration } end

    table.insert(state.frames, frame)
    return frame
end

function CreateFrame(frameType, name, parent, template)
    return newFrame(frameType, name, parent, template)
end

-- globals the addon touches
BOOKTYPE_SPELL = "spell"
MAX_SPELLS = 1024
STANDARD_TEXT_FONT = "Fonts\\FRIZQT__.TTF"
UIParent = newFrame("Frame", "UIParent")
SlashCmdList = {}
UISpecialFrames = {}

function EasyMenu(menuList)
    state.lastMenu = menuList
end

Minimap = newFrame("Frame", "Minimap")
function Minimap:GetCenter() return 100, 100 end
function Minimap:GetEffectiveScale() return 1 end
function GetCursorPosition() return 180, 60 end

GameFontNormal = "GameFontNormal"
GameFontNormalSmall = "GameFontNormalSmall"
GameFontNormalLarge = "GameFontNormalLarge"
GameFontHighlight = "GameFontHighlight"

DEFAULT_CHAT_FRAME = {
    AddMessage = function(_, message) table.insert(state.printed, message) end,
}

GameTooltip = {
    SetOwner = function() end,
    SetText = function() end,
    AddLine = function() end,
    Show = function() end,
    Hide = function() end,
}

function UnitClass() return "Mage", state.class end
function UnitExists(unit)
    if unit == "player" then
        return true
    end
    if unit == "target" then
        return state.target ~= nil
    end
    return state.party[unit] ~= nil
end
function UnitName(unit)
    if not unit or unit == "player" then
        return "Testmage"
    end
    if unit == "target" then
        return state.target
    end
    return state.party[unit]
end
function UnitLevel(unit)
    if UnitExists(unit) then
        return state.unitLevels[unit] or 60
    end
    return -1
end
function UnitDistanceSquared(unit)
    return state.distances[unit]
end
function CheckInteractDistance(unit, index)
    if index == 2 then
        return state.tradeRanges[unit] and 1 or nil
    end
    if index == 4 then
        local squared = state.distances[unit]
        if squared and math.sqrt(squared) <= 28 then
            return 1
        end
        return nil
    end
    return nil
end
function InitiateTrade(unit)
    table.insert(state.tradesInitiated, unit)
end
function GetRealmName() return "Testrealm" end
function InCombatLockdown() return state.inCombat end
function GetAddOnMetadata() return "1.0.0" end
function GetRealZoneText() return state.zone end
function GetZoneText() return state.zone end
function GetTime() return state.time end
function UnitIsDeadOrGhost() return false end
function GetNumPartyMembers() return state.partyCount or 0 end
function GetNumRaidMembers() return 0 end
function Ambiguate(name) return name end
function GetCoinTextureString(copper)
    local gold = math.floor(copper / 10000)
    local silver = math.floor((copper % 10000) / 100)
    local rest = copper % 100
    return ("%dg %ds %dc"):format(gold, silver, rest)
end
function GetTargetTradeMoney() return state.tradeMoney end
TradeFrameRecipientNameText = {
    GetText = function() return state.tradePartner end,
}
function SendChatMessage(message, chatType, language, target)
    table.insert(state.whispers, {
        message = message,
        chatType = chatType,
        target = target,
    })
end
function InviteUnit(name)
    table.insert(state.invites, name)
end
C_FriendList = {
    IsIgnored = function() return false end,
}
function GetItemCount(itemID) return state.counts[itemID] or 0 end
function GetItemInfo(itemID)
    if itemID == 17032 then return "Rune of Portals" end
    if itemID == 17031 then return "Rune of Teleportation" end
end

function GetNumSpellTabs() return 1 end
function GetSpellTabInfo() return "Mage", nil, 0, #state.entries end
function GetSpellBookItemName(index)
    local entry = state.entries[index]
    return entry and entry.name
end
function GetSpellBookItemInfo(index)
    local entry = state.entries[index]
    if not entry then return nil end
    return "SPELL", state.withIDs and entry.id or nil
end
function GetSpellInfo(id) return state.nameByID[id] end
function GetSpellTexture(id) return id and ("Interface\\Icons\\Spell_" .. tostring(id)) end
function GetSpellCooldown() return 0, 0 end

---------------------------------------------------------------------------
-- Harness
---------------------------------------------------------------------------

local function setSpellbook(ids)
    state.entries = {}
    for _, id in ipairs(ids) do
        state.entries[#state.entries + 1] = { id = id, name = state.nameByID[id] }
    end
end

local function addSpells(ids)
    for _, id in ipairs(ids) do
        state.entries[#state.entries + 1] = { id = id, name = state.nameByID[id] }
    end
end

local function fire(event, ...)
    local seen = {}
    for _, frame in ipairs(state.eventFrames) do
        if frame.__events[event] and not seen[frame] then
            seen[frame] = true
            local handler = frame.__scripts.OnEvent
            if handler then
                handler(frame, event, ...)
            end
        end
    end
end

local function chat(event, text, sender, channelName, channelBaseName)
    fire(event, text, sender or "Buyer", "Common", channelName, nil, nil, nil, nil, channelBaseName)
end

local function loadAddon(legacyDB)
    -- reset per-load state
    state.frames = {}
    state.eventFrames = {}
    state.printed = {}
    state.whispers = {}
    state.invites = {}
    state.time = 1000
    state.party = {}
    state.partyCount = 0
    state.unitLevels = {}
    state.distances = {}
    state.tradeRanges = {}
    state.target = nil
    state.tradePartner = nil
    state.tradeMoney = 0
    state.tradesInitiated = {}
    state.lastMenu = nil
    PortalsDB = nil
    MageUtilityDB = legacyDB
    UIParent = newFrame("Frame", "UIParent")

    local ns = {}
    local core = assert(loadfile(CORE))
    core("Portals", ns)
    local bar = assert(loadfile(BAR))
    bar("Portals", ns)
    local sales = assert(loadfile(SALES))
    sales("Portals", ns)
    local queue = assert(loadfile(QUEUE))
    queue("Portals", ns)
    local minimap = assert(loadfile(MINIMAP))
    minimap("Portals", ns)
    local options = assert(loadfile(OPTIONS))
    options("Portals", ns)

    fire("ADDON_LOADED", "Portals")
    fire("PLAYER_LOGIN")

    return ns
end

local function buttonTextures(addon)
    local icons = {}
    for _, button in ipairs(addon.bar.buttons) do
        icons[#icons + 1] = button.icon.__texture
    end
    return icons
end

local function testTbcHorde()
    state.class = "MAGE"
    state.withIDs = true
    state.inCombat = false
    state.counts = { [17031] = 40, [17032] = 25 }
    setSpellbook({ 3567, 11417, 3563, 11418, 3566, 11420, 32272, 32267, 35715, 35717 })

    local addon = loadAddon()

    local expected = { "Orgrimmar", "Undercity", "Thunder Bluff", "Silvermoon", "Shattrath" }
    checkEqual(#addon.visible, #expected, "TBC Horde: visible button count")
    for index, destination in ipairs(expected) do
        checkEqual(addon.visible[index], destination, "TBC Horde: order at " .. index)
    end

    local first = addon.bar.buttons[1]
    checkEqual(first:GetAttribute("type1"), "spell", "left click is a spell")
    checkEqual(first:GetAttribute("spell1"), "Portal: Orgrimmar", "left click casts the portal")
    checkEqual(first:GetAttribute("type2"), "spell", "right click is a spell")
    checkEqual(first:GetAttribute("spell2"), "Teleport: Orgrimmar", "right click casts the teleport")
    checkEqual(first.count:GetText(), "25", "shows portal rune count")
    check(addon.bar:IsShown(), "bar is shown for a mage")

    local width = 6 * 2 + 5 * addon.db.buttonSize + 4 * addon.db.spacing
    checkEqual(addon.bar:GetWidth(), width, "horizontal width accounts for buttons and grip")

    -- cooldown frame is wired up
    check(first.cooldown ~= nil, "portal button has a cooldown frame")
end

local function testAllianceTeleportOnly()
    state.class = "MAGE"
    state.withIDs = true
    state.counts = { [17031] = 3, [17032] = 0 }
    setSpellbook({ 3561 })

    local addon = loadAddon()
    checkEqual(#addon.visible, 1, "teleport-only: one button")
    local button = addon.bar.buttons[1]
    checkEqual(button:GetAttribute("spell1"), "Teleport: Stormwind",
        "teleport-only: left click falls back to teleport")
    checkEqual(button:GetAttribute("spell2"), "Teleport: Stormwind",
        "teleport-only: right click still teleports")
    checkEqual(button.count:GetText(), "3", "teleport-only: shows teleport rune count")
    checkEqual(button.count.__color[1], 0.85, "available runes are not red")

    state.counts[17031] = 0
    fire("BAG_UPDATE_DELAYED")
    checkEqual(button.count:GetText(), "0", "teleport-only: count follows the bags")
    checkEqual(button.count.__color[1], 1, "zero runes are shown in red")
end

local function testNonMage()
    state.class = "WARRIOR"
    state.withIDs = true
    setSpellbook({ 3561 })
    local addon = loadAddon()
    check(addon.bar and not addon.bar:IsShown(), "bar is hidden for non-mages")
end

local function testCombatDeferral()
    state.class = "MAGE"
    state.withIDs = true
    state.inCombat = false
    setSpellbook({ 3561 })
    local addon = loadAddon()
    checkEqual(#addon.visible, 1, "combat: starts with one button")

    state.inCombat = true
    addSpells({ 10059 })
    fire("SPELLS_CHANGED")
    check(addon.pendingRefresh, "combat: refresh is deferred")
    checkEqual(#addon.visible, 1, "combat: buttons are not rebuilt in combat")

    state.inCombat = false
    fire("PLAYER_REGEN_ENABLED")
    check(not addon.pendingRefresh, "combat: pending refresh cleared")
    checkEqual(#addon.visible, 1, "combat: still one destination")
    checkEqual(addon.bar.buttons[1]:GetAttribute("spell1"), "Portal: Stormwind",
        "combat: portal appears after combat")
end

local function testSlashCommands()
    state.class = "MAGE"
    state.withIDs = true
    state.inCombat = false
    setSpellbook({ 3561 })
    local addon = loadAddon()

    SlashCmdList["PORTAL"]("size 500")
    checkEqual(addon.db.buttonSize, 80, "size is clamped to 80")
    SlashCmdList["PORTAL"]("size 2")
    checkEqual(addon.db.buttonSize, 20, "size is clamped to 20")

    SlashCmdList["PORTAL"]("scale 0.1")
    checkEqual(addon.db.scale, 0.5, "scale is clamped to 0.5")

    SlashCmdList["PORTAL"]("vertical")
    checkEqual(addon.db.orientation, "VERTICAL", "vertical orientation")
    checkEqual(addon.bar:GetHeight(),
        2 * addon.db.padding + 14 + addon.db.buttonSize, "vertical height")

    SlashCmdList["PORTAL"]("lock")
    check(addon.db.locked, "lock sets the flag")
    check(not addon.bar.grip:IsShown(), "lock hides the grip")

    SlashCmdList["PORTAL"]("unlock")
    check(addon.bar.grip:IsShown(), "unlock shows the grip")

    addon.bar:SetPoint("TOPLEFT", UIParent, "TOPLEFT", 12, -34)
    addon:SavePosition()
    checkEqual(addon.pos.point, "TOPLEFT", "position saves the anchor")
    checkEqual(addon.pos.x, 12, "position saves x")

    SlashCmdList["PORTAL"]("reset")
    checkEqual(addon.pos.point, nil, "reset clears the saved anchor")
    local point = addon.bar:GetPoint()
    checkEqual(point, "CENTER", "reset re-centres the bar")
end

local function testHideShow()
    state.class = "MAGE"
    state.withIDs = true
    state.inCombat = false
    setSpellbook({ 3561, 10059 })
    local addon = loadAddon()
    check(addon.bar:IsShown(), "bar starts shown")

    SlashCmdList["PORTAL"]("bar off")
    check(not addon.bar:IsShown(), "/portal bar off hides the bar")
    check(addon.db.hidden, "bar off is saved")

    SlashCmdList["PORTAL"]("bar on")
    check(addon.bar:IsShown(), "/portal bar on shows the bar")
    check(not addon.db.hidden, "bar on is saved")

    SlashCmdList["PORTAL"]("bar")
    check(not addon.bar:IsShown(), "/portal bar toggles the bar")
    SlashCmdList["PORTAL"]("bar")
    check(addon.bar:IsShown(), "/portal bar toggles it back")
end

local function testMinimapButton()
    state.class = "MAGE"
    state.withIDs = true
    state.inCombat = false
    setSpellbook({ 3561, 10059 })
    local addon = loadAddon()

    local button = addon.minimapButton
    check(button ~= nil, "minimap button exists")
    check(button:IsShown(), "minimap button is shown by default")
    checkEqual(addon.db.minimapAngle, 220, "minimap angle default")

    local point, relativeTo = button:GetPoint()
    checkEqual(point, "CENTER", "minimap button anchors by CENTER")
    check(relativeTo == Minimap, "minimap button is positioned around the minimap")

    local function findMenuItem(text)
        for _, item in ipairs(state.lastMenu or {}) do
            if item.text and item.text:find(text, 1, true) then
                return item
            end
        end
    end

    button.__scripts.OnClick(button, "RightButton")
    check(state.lastMenu ~= nil, "minimap right-click opens the menu")

    local business = findMenuItem("Open for business")
    check(business ~= nil, "minimap menu has an open-business item")
    business.func()
    check(not addon.db.salesEnabled, "minimap menu closes for business")

    button.__scripts.OnClick(button, "RightButton")
    check(findMenuItem("Closed for business") ~= nil, "minimap menu shows the closed status")
    findMenuItem("Closed for business").func()
    check(addon.db.salesEnabled, "minimap menu reopens for business")

    button.__scripts.OnClick(button, "RightButton")
    findMenuItem("Settings").func()
    check(addon.options and addon.options:IsShown(), "minimap menu opens settings")
    addon.options:Hide()

    button.__scripts.OnClick(button, "RightButton")
    findMenuItem("Portal queue & stats").func()
    check(addon.queueWindow and addon.queueWindow:IsShown(), "minimap menu opens the queue")
    addon.queueWindow:Hide()

    button.__scripts.OnClick(button, "RightButton")
    findMenuItem("Show portal bar").func()
    check(addon.db.hidden, "minimap menu hides the bar")
    button.__scripts.OnClick(button, "RightButton")
    findMenuItem("Show portal bar").func()
    check(not addon.db.hidden, "minimap menu shows the bar again")

    button.__scripts.OnClick(button, "RightButton")
    findMenuItem("Lock the bar").func()
    check(addon.db.locked, "minimap menu locks the bar")
    button.__scripts.OnClick(button, "RightButton")
    findMenuItem("Lock the bar").func()
    check(not addon.db.locked, "minimap menu unlocks the bar")

    button.__scripts.OnClick(button, "LeftButton")
    check(addon.options and addon.options:IsShown(), "minimap left-click opens settings")
    button.__scripts.OnClick(button, "LeftButton")
    check(not addon.options:IsShown(), "minimap left-click toggles settings closed")

    button.__scripts.OnDragStart(button)
    check(button.__scripts.OnUpdate ~= nil, "dragging installs an OnUpdate")
    button.__scripts.OnUpdate()
    local expected = math.deg(math.atan(60 - 100, 180 - 100))
    check(math.abs(addon.db.minimapAngle - expected) < 0.01,
        "dragging updates the minimap angle")
    button.__scripts.OnDragStop(button)
    check(button.__scripts.OnUpdate == nil, "drag stop removes the OnUpdate")

    addon:SetSetting("showMinimap", false)
    check(not button:IsShown(), "showMinimap=false hides the button")
    addon:SetSetting("showMinimap", true)
    check(button:IsShown(), "showMinimap=true shows the button")
end

local function testOptionsPanel()
    state.class = "MAGE"
    state.withIDs = true
    state.inCombat = false
    setSpellbook({ 3561, 10059 })
    local addon = loadAddon()

    SlashCmdList["PORTAL"]("")
    check(addon.options ~= nil, "/portal creates the settings window")
    check(addon.options:IsShown(), "/portal opens the settings window")
    check(addon.options.checks.showBar:GetChecked(), "show bar checkbox reflects db")
    check(addon.options.checks.minimap:GetChecked(), "minimap checkbox reflects db")
    check(addon.options.checks.sales:GetChecked(), "sales checkbox reflects db")
    check(addon.options.salesStatus:GetText():find("Open for business", 1, true) ~= nil,
        "sales status line is populated")

    addon.options.checks.sales.__scripts.OnClick(addon.options.checks.sales)
    check(not addon.db.salesEnabled, "sales checkbox saves the setting")
    addon.options.checks.sales.__scripts.OnClick(addon.options.checks.sales)
    check(addon.db.salesEnabled, "sales checkbox can be switched back on")

    SlashCmdList["PORTAL"]("bar off")
    check(not addon.options.checks.showBar:GetChecked(), "panel follows /portal bar off")

    addon.options.checks.showBar.__scripts.OnClick(addon.options.checks.showBar)
    check(not addon.db.hidden, "checkbox click saves the setting")
    check(addon.bar:IsShown(), "checkbox click shows the bar")

    local before = addon.db.buttonSize
    addon.options.size.plus.__scripts.OnClick()
    checkEqual(addon.db.buttonSize, before + 2, "size stepper increments")
    checkEqual(addon.options.size.value:GetText(), tostring(before + 2),
        "size stepper updates its label")

    addon.options.orientation.__scripts.OnClick()
    checkEqual(addon.db.orientation, "VERTICAL", "orientation button flips to vertical")
    checkEqual(addon.options.orientation:GetText(), "Vertical",
        "orientation button label updates")

    check(addon.options.status:GetText():find("Stormwind", 1, true) ~= nil,
        "status line lists detected spells")

    SlashCmdList["PORTAL"]("help")
    check(#state.printed > 0 and state.printed[#state.printed]:find("/portal", 1, true) ~= nil,
        "/portal help prints the new command name")
end

local function testParser()
    state.class = "MAGE"
    state.withIDs = true
    state.inCombat = false
    state.zone = "Stormwind City"
    setSpellbook({ 3561, 10059, 3565, 11419, 3562, 11416 })
    local addon = loadAddon()

    local cases = {
        { "LF PORT SW TO DARN", "Stormwind", "Darnassus" },
        { "WTB portal to Darn", nil, "Darnassus" },
        { "Darn portal pls, I'm in SW", "Stormwind", "Darnassus" },
        { "LF port from Darn to SW", "Darnassus", "Stormwind" },
        { "port me to IF pls", nil, "Ironforge" },
        { "LF PORT SW->DARN", "Stormwind", "Darnassus" },
        { "port to thunder bluff", nil, "Thunder Bluff" },
        { "teleport to shatt please", nil, "Shattrath" },
    }

    for _, case in ipairs(cases) do
        local request = addon:ParsePortalRequest(case[1])
        check(request ~= nil, "parser detects: " .. case[1])
        if request then
            checkEqual(request.origin, case[2], "parser origin: " .. case[1])
            checkEqual(request.destination, case[3], "parser destination: " .. case[1])
        end
    end

    check(addon:ParsePortalRequest("support portal") == nil, "parser ignores 'support portal'")
    check(addon:ParsePortalRequest("LF port") == nil, "parser needs a city name")
    check(addon:ParsePortalRequest("port SW to SW") == nil, "parser rejects same-city requests")
    check(addon:ParsePortalRequest("LF port in SW") == nil, "parser needs a destination")

    local interested = addon:ParsePortalRequest("interested in a port to Darn")
    check(interested ~= nil, "parser handles 'interested in a port to Darn'")
    checkEqual(interested and interested.origin, nil, "parser does not see 'in' as an origin there")
end

local function testChatDetection()
    state.class = "MAGE"
    state.withIDs = true
    state.inCombat = false
    state.zone = "Stormwind City"
    setSpellbook({ 3561, 10059, 3565, 11419, 3562, 11416 })
    local addon = loadAddon()

    chat("CHAT_MSG_SAY", "LF PORT SW TO DARN", "Buyer")
    checkEqual(#state.whispers, 1, "say: whispers the buyer")
    checkEqual(#state.invites, 1, "say: invites the buyer")
    checkEqual(state.whispers[1] and state.whispers[1].target, "Buyer", "say: whisper target")
    check(state.whispers[1] and state.whispers[1].message:find("Darnassus", 1, true) ~= nil,
        "say: whisper names the destination")

    state.whispers, state.invites = {}, {}
    state.time = state.time + 20
    chat("CHAT_MSG_CHANNEL", "WTB portal to Darn", "Trader", "Trade - Stormwind", "Trade")
    checkEqual(#state.whispers, 1, "trade: the local channel supplies the origin")

    state.whispers, state.invites = {}, {}
    state.time = state.time + 20
    chat("CHAT_MSG_CHANNEL", "WTB portal to Darn", "Random", "LookingForGroup", "LookingForGroup")
    checkEqual(#state.whispers, 0, "lfg: skips requests without an origin")

    state.time = state.time + 20
    chat("CHAT_MSG_CHANNEL", "LF PORT SW TO DARN", "Random", "LookingForGroup", "LookingForGroup")
    checkEqual(#state.whispers, 1, "lfg: acts when the origin matches")

    state.whispers, state.invites = {}, {}
    state.zone = "Ironforge"
    state.time = state.time + 20
    chat("CHAT_MSG_SAY", "LF PORT SW TO DARN", "WrongCity")
    checkEqual(#state.whispers, 0, "say: skips a different origin city")

    state.zone = "Stormwind City"
    state.time = state.time + 20
    chat("CHAT_MSG_CHANNEL", "WTB portal to Darn", "OtherChannel", "Trade - Ironforge", "Trade")
    checkEqual(#state.whispers, 0, "trade: skips another city's channel")

    state.time = state.time + 20
    chat("CHAT_MSG_SAY", "WTS portal to Darn", "Seller")
    checkEqual(#state.whispers, 0, "say: ignores people selling portals")

    state.whispers, state.invites = {}, {}
    state.time = state.time + 20
    chat("CHAT_MSG_SAY", "LF PORT SW TO SHATT", "OutOfReach")
    checkEqual(#state.whispers, 0, "say: skips portals the mage does not know")

    state.time = state.time + 20
    chat("CHAT_MSG_SAY", "LF port to SW", "AlreadyHere")
    checkEqual(#state.whispers, 0, "say: skips a portal to the mage's own city")

    state.time = state.time + 20
    chat("CHAT_MSG_SAY", "LF port to Darn", "Nearby")
    checkEqual(#state.whispers, 1, "say: unstated origin is fine in a local channel")
end

local function testSalesRateLimit()
    state.class = "MAGE"
    state.withIDs = true
    state.inCombat = false
    state.zone = "Stormwind City"
    setSpellbook({ 3561, 10059, 3565, 11419 })
    local addon = loadAddon()

    chat("CHAT_MSG_SAY", "LF PORT SW TO DARN", "Buyer")
    checkEqual(#state.whispers, 1, "rate: first request goes through")

    state.time = state.time + 20
    chat("CHAT_MSG_SAY", "LF PORT SW TO DARN", "Buyer")
    checkEqual(#state.whispers, 1, "rate: the same player is on cooldown")

    state.time = state.time + 301
    chat("CHAT_MSG_SAY", "LF PORT SW TO DARN", "Buyer")
    checkEqual(#state.whispers, 2, "rate: the player cooldown expires")

    state.time = state.time + 1
    chat("CHAT_MSG_SAY", "LF PORT SW TO DARN", "Second")
    checkEqual(#state.whispers, 2, "rate: the global cooldown applies")

    state.time = state.time + 11
    chat("CHAT_MSG_SAY", "LF PORT SW TO DARN", "Second")
    checkEqual(#state.whispers, 3, "rate: the global cooldown expires")
end

local function testSalesSettings()
    state.class = "MAGE"
    state.withIDs = true
    state.inCombat = false
    state.zone = "Stormwind City"
    setSpellbook({ 3561, 10059, 3565, 11419 })
    local addon = loadAddon()

    SlashCmdList["PORTAL"]("sales")
    check(not addon.db.salesEnabled, "/portal sales turns detection off")
    chat("CHAT_MSG_SAY", "LF PORT SW TO DARN", "Buyer")
    checkEqual(#state.whispers, 0, "sales off: nothing is whispered")

    SlashCmdList["PORTAL"]("sales")
    check(addon.db.salesEnabled, "/portal sales turns detection back on")

    SlashCmdList["PORTAL"]("channel trade off")
    chat("CHAT_MSG_CHANNEL", "WTB portal to Darn", "Trader", "Trade - Stormwind", "Trade")
    checkEqual(#state.whispers, 0, "channel off: trade is ignored")

    SlashCmdList["PORTAL"]("channel trade on")
    state.time = state.time + 20
    chat("CHAT_MSG_CHANNEL", "WTB portal to Darn", "Trader", "Trade - Stormwind", "Trade")
    checkEqual(#state.whispers, 1, "channel on: trade works again")

    state.whispers, state.invites = {}, {}
    state.time = state.time + 20
    addon:SetSetting("onlyMyCity", false)
    chat("CHAT_MSG_CHANNEL", "WTB portal to Darn", "Global", "LookingForGroup", "LookingForGroup")
    checkEqual(#state.whispers, 1, "onlyMyCity off: global requests are accepted")

    state.whispers, state.invites = {}, {}
    state.time = state.time + 20
    addon:SetSetting("autoInvite", false)
    chat("CHAT_MSG_SAY", "LF PORT SW TO DARN", "NoInvite")
    checkEqual(#state.invites, 0, "auto-invite off: nobody is invited")
    check(state.whispers[1] and state.whispers[1].message:find("Invite sent", 1, true) == nil,
        "auto-invite off: the default message no longer promises an invite")
    addon:SetSetting("autoInvite", true)

    state.whispers = {}
    state.time = state.time + 20
    SlashCmdList["PORTAL"]("message Portal to {destination} from {origin}, {name}!")
    chat("CHAT_MSG_SAY", "LF PORT SW TO DARN", "Templated")
    checkEqual(#state.whispers, 1, "template: whisper sent")
    check(state.whispers[1].message:find("Portal to Darnassus from Stormwind, Templated!", 1, true) ~= nil,
        "template: placeholders are replaced")

    SlashCmdList["PORTAL"]("test LF PORT SW TO DARN")
    check(state.printed[#state.printed]:find("Darnassus", 1, true) ~= nil,
        "/portal test reports the parsed destination")
end

local function testQueue()
    state.class = "MAGE"
    state.withIDs = true
    state.inCombat = false
    state.zone = "Stormwind City"
    setSpellbook({ 3561, 10059, 3565, 11419, 3562, 11416 })
    local addon = loadAddon()

    SlashCmdList["PORTALS"]("show")
    check(addon.queueWindow ~= nil, "queue: window created")
    check(addon.queueWindow:IsShown(), "queue: /portals show opens it")
    checkEqual(#addon.queue, 0, "queue: starts empty")

    chat("CHAT_MSG_SAY", "LF PORT SW TO DARN", "Buyer")
    checkEqual(#addon.queue, 0, "queue: strangers are not queued yet")
    check(addon.pending["buyer"] ~= nil, "queue: stranger is tracked as pending")
    checkEqual(#state.invites, 1, "queue: stranger is invited")

    state.party = { party1 = "Buyer" }
    state.partyCount = 1
    state.unitLevels = { party1 = 60 }
    state.distances = { party1 = 275.4 * 275.4 }
    state.tradeRanges = { party1 = false }
    fire("GROUP_ROSTER_UPDATE")

    checkEqual(#addon.queue, 1, "queue: accepting the invite adds a ticket")
    check(addon.pending["buyer"] == nil, "queue: pending entry is consumed")
    checkEqual(addon.queue[1].destination, "Darnassus", "queue: ticket destination")

    local row = addon.queueWindow.rows[1]
    check(row:IsShown(), "queue: row is shown")
    checkEqual(row.name:GetText(), "Buyer (60)", "queue: row shows level")
    checkEqual(row.dest:GetText(), "Darnassus", "queue: row shows destination")
    checkEqual(row.distance:GetText(), "275.4 yards", "queue: row shows distance")
    check(not row.trade:IsEnabled(), "queue: trade is grey out of range")

    state.tradeRanges["party1"] = true
    addon:RefreshQueue()
    check(row.trade:IsEnabled(), "queue: trade unlocks in range")
    row.trade.__scripts.OnClick()
    checkEqual(#state.tradesInitiated, 1, "queue: trade button opens a trade")
    checkEqual(state.tradesInitiated[1], "party1", "queue: trades with the right unit")

    row.cast.__scripts.OnMouseDown(row.cast)
    fire("UNIT_SPELLCAST_SUCCEEDED", "player", "cast-guid", 11419)
    checkEqual(addon.session.portals, 1, "queue: portal cast counted")
    checkEqual(addon.session.servedCount, 1, "queue: player counted as served")
    checkEqual(#addon.queue, 0, "queue: served ticket is removed")
    check(addon.queueWindow.statsPortals:GetText():find("Players served: 1", 1, true) ~= nil,
        "queue: stats text updates")

    state.tradePartner = "Buyer"
    state.tradeMoney = 150000
    fire("TRADE_SHOW")
    fire("TRADE_ACCEPT_UPDATE", 1, 1)
    fire("TRADE_CLOSED")
    checkEqual(addon.session.tips, 150000, "queue: completed trade adds the tip")
    checkEqual(addon.session.trades, 1, "queue: trade counted")
    check(addon.queueWindow.statsTips:GetText():find("15g", 1, true) ~= nil,
        "queue: tips text updates")

    local whispersBefore = #state.whispers
    local invitesBefore = #state.invites
    chat("CHAT_MSG_SAY", "LF port to IF", "Buyer")
    checkEqual(#addon.queue, 1, "queue: grouped buyer queues immediately")
    checkEqual(addon.queue[1].destination, "Ironforge", "queue: grouped buyer destination")
    checkEqual(#state.whispers, whispersBefore, "queue: grouped buyer is not whispered")
    checkEqual(#state.invites, invitesBefore, "queue: grouped buyer is not invited")

    state.party = {}
    state.partyCount = 0
    fire("GROUP_ROSTER_UPDATE")
    checkEqual(#addon.queue, 0, "queue: leaving the party removes the ticket")

    SlashCmdList["PORTALS"]("resetsession")
    checkEqual(addon.session.portals, 0, "queue: session reset clears portals")
    checkEqual(addon.session.tips, 0, "queue: session reset clears tips")
end

local function testQueueMultiple()
    state.class = "MAGE"
    state.withIDs = true
    state.inCombat = false
    state.zone = "Stormwind City"
    setSpellbook({ 3561, 10059, 3565, 11419, 3562, 11416 })
    local addon = loadAddon()

    chat("CHAT_MSG_SAY", "LF PORT SW TO DARN", "First")
    state.time = state.time + 2
    chat("CHAT_MSG_SAY", "LF port to IF", "Second")

    checkEqual(#state.invites, 2, "queue: every buyer is invited")
    checkEqual(#state.whispers, 1, "queue: whispers stay rate limited")
    check(addon.pending["first"] ~= nil and addon.pending["second"] ~= nil,
        "queue: both buyers are pending")

    state.party = { party1 = "First", party2 = "Second" }
    state.partyCount = 2
    state.distances = { party1 = 100, party2 = 400 }
    state.tradeRanges = { party1 = true, party2 = false }
    fire("GROUP_ROSTER_UPDATE")

    checkEqual(#addon.queue, 2, "queue: both buyers join the queue")
    checkEqual(addon.queue[1].name, "First", "queue: first request is first")
    checkEqual(addon.queue[2].name, "Second", "queue: second request follows")
    check(addon.queueWindow.rows[1]:IsShown() and addon.queueWindow.rows[2]:IsShown(),
        "queue: both rows are shown")
end

local function testQueuePendingExpiry()
    state.class = "MAGE"
    state.withIDs = true
    state.inCombat = false
    state.zone = "Stormwind City"
    setSpellbook({ 3561, 10059, 3565, 11419 })
    local addon = loadAddon()

    chat("CHAT_MSG_SAY", "LF PORT SW TO DARN", "Slow")
    check(addon.pending["slow"] ~= nil, "queue: invite is pending")
    state.time = state.time + 301
    fire("GROUP_ROSTER_UPDATE")
    check(addon.pending["slow"] == nil, "queue: stale invites expire")
    checkEqual(#addon.queue, 0, "queue: expired invite never becomes a ticket")
end

local function testQueueCombat()
    state.class = "MAGE"
    state.withIDs = true
    state.inCombat = false
    state.zone = "Stormwind City"
    setSpellbook({ 3561, 10059, 3565, 11419 })
    local addon = loadAddon()

    state.inCombat = true
    SlashCmdList["PORTALS"]("show")
    check(addon.queueWindow and not addon.queueWindow:IsShown(),
        "queue: cannot open in combat")

    state.inCombat = false
    SlashCmdList["PORTALS"]("show")
    check(addon.queueWindow:IsShown(), "queue: opens out of combat")

    state.party = { party1 = "Buyer" }
    state.partyCount = 1
    state.distances = { party1 = 100 }
    state.tradeRanges = { party1 = false }
    state.inCombat = true
    chat("CHAT_MSG_SAY", "LF port to Darn", "Buyer")
    checkEqual(#addon.queue, 1, "queue: tickets are recorded in combat")
    check(addon.queueDirty, "queue: visual refresh is deferred in combat")

    state.inCombat = false
    fire("PLAYER_REGEN_ENABLED")
    check(not addon.queueDirty, "queue: refresh runs after combat")

    state.inCombat = true
    SlashCmdList["PORTALS"]("hide")
    check(addon.queueWindow:IsShown(), "queue: cannot close in combat")
    state.inCombat = false
    SlashCmdList["PORTALS"]("hide")
    check(not addon.queueWindow:IsShown(), "queue: closes out of combat")
end

local function testQueueBarIndependence()
    state.class = "MAGE"
    state.withIDs = true
    state.inCombat = false
    state.zone = "Stormwind City"
    setSpellbook({ 3561, 10059, 3565, 11419 })
    local addon = loadAddon()

    SlashCmdList["PORTAL"]("bar off")
    check(not addon.bar:IsShown(), "independence: /portal bar off hides the bar")
    check(addon.db.hidden, "independence: hidden flag is set")

    SlashCmdList["PORTAL"]("show")
    check(addon.queueWindow:IsShown(), "independence: /portal show opens the queue")
    check(not addon.bar:IsShown(), "independence: /portal show leaves the bar hidden")
    check(addon.db.hidden, "independence: /portal show does not change the bar setting")

    SlashCmdList["PORTAL"]("hide")
    check(not addon.queueWindow:IsShown(), "independence: /portal hide closes the queue")
    check(not addon.bar:IsShown(), "independence: /portal hide leaves the bar hidden")

    SlashCmdList["PORTAL"]("bar on")
    check(addon.bar:IsShown(), "independence: /portal bar on shows the bar")

    SlashCmdList["PORTAL"]("show")
    check(addon.queueWindow:IsShown(), "independence: queue opens with the bar shown")
    check(addon.bar:IsShown(), "independence: /portal show does not hide the bar")

    SlashCmdList["PORTALS"]("hide")
    check(not addon.queueWindow:IsShown(), "independence: /portals hide closes the queue")
    check(addon.bar:IsShown(), "independence: /portals hide leaves the bar shown")

    SlashCmdList["PORTALS"]("open")
    check(addon.queueWindow:IsShown(), "independence: /portals open works")
    check(addon.bar:IsShown(), "independence: /portals open leaves the bar shown")

    SlashCmdList["PORTAL"]("bar")
    check(not addon.bar:IsShown(), "independence: /portal bar toggles the bar")
    check(addon.queueWindow:IsShown(), "independence: toggling the bar leaves the queue open")

    SlashCmdList["PORTAL"]("bar")
    check(addon.bar:IsShown(), "independence: /portal bar toggles back")
    check(addon.queueWindow:IsShown(), "independence: queue remains open")

    SlashCmdList["PORTALS"]("close")
    check(not addon.queueWindow:IsShown(), "independence: /portals close closes the queue")
    check(addon.bar:IsShown(), "independence: /portals close leaves the bar shown")
end

local function testLegacyMigration()
    state.class = "MAGE"
    state.withIDs = true
    state.inCombat = false
    setSpellbook({ 3561 })
    local addon = loadAddon({
        settings = { hidden = true, buttonSize = 55 },
        positions = {},
    })

    checkEqual(addon.db.buttonSize, 55, "migration: legacy settings preserved")
    check(addon.db.hidden, "migration: legacy hidden flag preserved")
    check(PortalsDB ~= nil, "migration: PortalsDB created")
    check(MageUtilityDB == nil, "migration: old saved variable cleared")
end

local function testLocalizedNameFallback()
    state.class = "MAGE"
    state.withIDs = false -- spellbook API returns names but no IDs
    state.inCombat = false
    setSpellbook({ 3561, 10059 })
    local addon = loadAddon()
    checkEqual(#addon.visible, 1, "name fallback: one destination")
    checkEqual(addon.bar.buttons[1]:GetAttribute("spell1"), "Portal: Stormwind",
        "name fallback: finds spells without IDs")
end

local function testCSpellBookFallback()
    state.class = "MAGE"
    state.withIDs = true
    state.inCombat = false
    setSpellbook({ 3561, 10059 })

    local savedName, savedInfo = GetSpellBookItemName, GetSpellBookItemInfo
    GetSpellBookItemName, GetSpellBookItemInfo = nil, nil
    C_SpellBook = {
        GetSpellBookItemName = function(index, bookType)
            assert(bookType == "spell", "unexpected book type " .. tostring(bookType))
            local entry = state.entries[index]
            return entry and entry.name
        end,
        GetSpellBookItemInfo = function(index)
            local entry = state.entries[index]
            if not entry then
                return nil
            end
            return "SPELL", entry.id
        end,
    }

    local addon = loadAddon()
    checkEqual(#addon.visible, 1, "C_SpellBook fallback: one destination")
    checkEqual(addon.bar.buttons[1]:GetAttribute("spell1"), "Portal: Stormwind",
        "C_SpellBook fallback: finds the portal")

    C_SpellBook = nil
    GetSpellBookItemName, GetSpellBookItemInfo = savedName, savedInfo
end

local function testBagUpdate()
    state.class = "MAGE"
    state.withIDs = true
    state.inCombat = false
    state.counts = { [17031] = 10, [17032] = 20 }
    setSpellbook({ 3561, 10059 })
    local addon = loadAddon()
    checkEqual(addon.bar.buttons[1].count:GetText(), "20", "count starts at 20")
    state.counts[17032] = 4
    fire("BAG_UPDATE_DELAYED")
    checkEqual(addon.bar.buttons[1].count:GetText(), "4", "bags update the count")
end

testTbcHorde()
testAllianceTeleportOnly()
testNonMage()
testCombatDeferral()
testSlashCommands()
testHideShow()
testMinimapButton()
testOptionsPanel()
testParser()
testChatDetection()
testSalesRateLimit()
testSalesSettings()
testQueue()
testQueueMultiple()
testQueuePendingExpiry()
testQueueCombat()
testQueueBarIndependence()
testLegacyMigration()
testLocalizedNameFallback()
testCSpellBookFallback()
testBagUpdate()

io.write(("%d checks, %d failure(s)\n"):format(checks, failures))
os.exit(failures == 0 and 0 or 1)
