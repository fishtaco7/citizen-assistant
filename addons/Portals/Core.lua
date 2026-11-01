--[[---------------------------------------------------------------------------
    Portals - Core
    Saved variables, defaults, event handling and slash commands.

    Targets WoW Classic Era (1.15.x) and Burning Crusade Classic (2.5.x).
    Everything version specific is guarded, so the same files load on both.
-----------------------------------------------------------------------------]]

local ADDON_NAME, ns = ...

local MU = ns
MU.name = ADDON_NAME

local PREFIX = "|cff69ccf0Portals|r: "

function MU:Print(msg)
    if DEFAULT_CHAT_FRAME then
        DEFAULT_CHAT_FRAME:AddMessage(PREFIX .. tostring(msg))
    end
end

-- Every secure-frame operation has to be deferred until combat ends.
function MU:InCombat()
    return InCombatLockdown and InCombatLockdown()
end

function MU:IsMage()
    local _, class = UnitClass("player")
    return class == "MAGE"
end

function MU:ShortName(name)
    if type(name) ~= "string" then
        return ""
    end
    if Ambiguate then
        return Ambiguate(name, "short")
    end
    return name:match("^([^%-]+)") or name
end

function MU:InvitePlayer(name)
    if C_PartyInfo and C_PartyInfo.InviteUnit then
        pcall(C_PartyInfo.InviteUnit, name)
    elseif InviteUnit then
        pcall(InviteUnit, name)
    end
end

MU.channelNames = {
    say = true,
    yell = true,
    general = true,
    trade = true,
    lfg = true,
}

MU.defaultSaleMessage = "Hi {name}, I can portal you from {origin} to {destination}. Invite sent - accept it and I'll open the portal."

MU.defaults = {
    scale       = 1,
    buttonSize  = 40,
    spacing     = 2,
    padding     = 6,
    orientation = "HORIZONTAL",
    locked      = false,
    hidden      = false,
    showCounts  = true,
    showMinimap = true,
    minimapAngle = 220,
    salesEnabled = true,
    autoWhisper  = true,
    autoInvite   = true,
    onlyMyCity   = true,
    saleMessage  = MU.defaultSaleMessage,
}

local function ApplyDefaults(dst, src)
    for k, v in pairs(src) do
        if dst[k] == nil then
            dst[k] = v
        end
    end
end

local function AddonVersion()
    local version
    if C_AddOns and C_AddOns.GetAddOnMetadata then
        version = C_AddOns.GetAddOnMetadata(ADDON_NAME, "Version")
    elseif GetAddOnMetadata then
        version = GetAddOnMetadata(ADDON_NAME, "Version")
    end
    return version or "1.0.0"
end

function MU:InitDB()
    -- One-time migration from the old addon name. Remove this block after a
    -- release or two, along with MageUtilityDB in the TOC.
    if not PortalsDB and MageUtilityDB then
        PortalsDB = MageUtilityDB
        MageUtilityDB = nil
    end

    PortalsDB = PortalsDB or {}
    local db = PortalsDB

    db.settings = db.settings or {}
    ApplyDefaults(db.settings, self.defaults)

    db.positions = db.positions or {}
    local key = tostring(UnitName("player")) .. "-" .. tostring(GetRealmName())
    db.positions[key] = db.positions[key] or {}

    self.db = db.settings
    self.pos = db.positions[key]
    self.version = AddonVersion()
end

function MU:SetSetting(key, value)
    if self.defaults[key] == nil then
        return false
    end
    self.db[key] = value
    if self.ApplySettings then
        self:ApplySettings()
    end
    return true
end

---------------------------------------------------------------------------
-- Slash commands
---------------------------------------------------------------------------

local COMMANDS = {
    "  /portal                     - open the settings window",
    "  /portal show | queue        - open the portal queue and session stats",
    "  /portal hide | close        - close the portal queue window",
    "  /portal bar [on|off]        - show or hide the portal bar",
    "  /portal lock | unlock       - lock or unlock the bar",
    "  /portal vertical | horizontal - bar orientation",
    "  /portal size <20-80>        - button size in pixels",
    "  /portal spacing <0-20>      - gap between buttons",
    "  /portal scale <0.5-2>       - overall bar scale",
    "  /portal counts              - toggle reagent counts",
    "  /portal minimap             - toggle the minimap button",
    "  /portal sales | business    - open or close for business",
    "  /portal message [text]      - show or set the auto-whisper template",
    "  /portal channel <name> on|off - say, yell, general, trade or lfg",
    "  /portal test <message>      - test detection without acting",
    "  /portal reset               - reset the bar position",
    "  /portal scan                - list detected portal spells",
    "  /portals show               - open the portal queue and session stats",
}

function MU:PrintHelp()
    self:Print("v" .. tostring(self.version or "1.4.0") .. " - portal bar, queue and session tips.")
    for _, line in ipairs(COMMANDS) do
        self:Print(line)
    end
end

local NUMBER_SETTINGS = {
    size    = { key = "buttonSize", min = 20, max = 80, integer = true },
    spacing = { key = "spacing",    min = 0,  max = 20, integer = true },
    scale   = { key = "scale",      min = 0.5, max = 2 },
}

function MU:Slash(input)
    input = (input or ""):gsub("^%s+", ""):gsub("%s+$", "")
    local cmd, arg = input:match("^(%S*)%s*(.-)$")
    cmd = (cmd or ""):lower()

    if cmd == "" then
        if self.OpenOptions then
            self:OpenOptions()
        else
            self:PrintHelp()
        end
    elseif cmd == "help" then
        self:PrintHelp()
    elseif cmd == "options" or cmd == "config" then
        if self.OpenOptions then
            self:OpenOptions()
        end
    elseif cmd == "lock" or cmd == "unlock" then
        self:SetSetting("locked", cmd == "lock")
        self:Print(cmd == "lock" and "Bar locked."
            or "Bar unlocked - drag the top strip to move it.")
    elseif cmd == "show" or cmd == "queue" or cmd == "stats" then
        if self.OpenQueue then
            self:OpenQueue()
        end
    elseif cmd == "hide" or cmd == "close" then
        if self.CloseQueue then
            self:CloseQueue()
        end
    elseif cmd == "bar" then
        local barState = arg:lower()
        if barState == "on" or barState == "show" then
            self:SetSetting("hidden", false)
        elseif barState == "off" or barState == "hide" then
            self:SetSetting("hidden", true)
        else
            self:SetSetting("hidden", not self.db.hidden)
        end
        self:Print("Portal bar " .. (self.db.hidden and "hidden." or "shown."))
    elseif cmd == "toggle" then
        self:SetSetting("hidden", not self.db.hidden)
    elseif cmd == "vertical" or cmd == "horizontal" then
        self:SetSetting("orientation", cmd:upper())
        self:Print("Orientation: " .. cmd .. ".")
    elseif NUMBER_SETTINGS[cmd] then
        local spec = NUMBER_SETTINGS[cmd]
        local value = tonumber(arg)
        if not value then
            self:Print("Usage: /portal " .. cmd .. " <number>")
            return
        end
        value = math.max(spec.min, math.min(spec.max, value))
        if spec.integer then
            value = math.floor(value + 0.5)
        end
        self:SetSetting(spec.key, value)
        self:Print(("Set %s to %s."):format(cmd, tostring(value)))
    elseif cmd == "counts" then
        self:SetSetting("showCounts", not self.db.showCounts)
        self:Print("Reagent counts " .. (self.db.showCounts and "on." or "off."))
    elseif cmd == "minimap" then
        self:SetSetting("showMinimap", not self.db.showMinimap)
        self:Print("Minimap button " .. (self.db.showMinimap and "shown." or "hidden."))
    elseif cmd == "sales" or cmd == "business" then
        self:SetSetting("salesEnabled", not self.db.salesEnabled)
        self:Print("Business " .. (self.db.salesEnabled and "open." or "closed."))
    elseif cmd == "message" then
        if arg == "" then
            self:Print("Auto-whisper template: " .. tostring(self.db.saleMessage))
            self:Print("Placeholders: {name}, {origin}, {destination}")
        else
            if #arg > 240 then
                arg = arg:sub(1, 240)
            end
            self.db.saleMessage = arg
            self:Print("Auto-whisper template updated.")
        end
    elseif cmd == "channel" then
        local channel, channelState = arg:match("^(%S*)%s*(.-)$")
        channel = (channel or ""):lower()
        if channel == "" then
            self:Print("Usage: /portal channel <say|yell|general|trade|lfg> on|off")
        elseif not MU.channelNames[channel] then
            self:Print(("Unknown channel '%s'."):format(channel))
        else
            local channels = self.db.channels or {}
            self.db.channels = channels
            if channelState == "on" then
                channels[channel] = true
            elseif channelState == "off" then
                channels[channel] = false
            end
            self:Print(("Channel %s is %s."):format(channel,
                channels[channel] == false and "off" or "on"))
        end
    elseif cmd == "test" then
        if self.TestRequest then
            self:TestRequest(arg)
        end
    elseif cmd == "reset" then
        if self:ResetPosition() then
            self:Print("Bar position reset.")
        end
    elseif cmd == "scan" then
        if self.PrintScan then
            self:PrintScan()
        end
    else
        self:Print(("Unknown command '%s'. Type /portal help."):format(cmd))
    end
end

SLASH_PORTAL1 = "/portal"
SlashCmdList["PORTAL"] = function(msg)
    MU:Slash(msg)
end

---------------------------------------------------------------------------
-- Events
---------------------------------------------------------------------------

local events = CreateFrame("Frame")
events:RegisterEvent("ADDON_LOADED")
events:RegisterEvent("PLAYER_LOGIN")
events:RegisterEvent("PLAYER_ENTERING_WORLD")
events:RegisterEvent("SPELLS_CHANGED")
events:RegisterEvent("LEARNED_SPELL_IN_TAB")
events:RegisterEvent("PLAYER_REGEN_ENABLED")
events:RegisterEvent("BAG_UPDATE_DELAYED")

events:SetScript("OnEvent", function(_, event, arg1)
    if event == "ADDON_LOADED" then
        if arg1 == ADDON_NAME then
            MU:InitDB()
            if MU.CreateBar then
                MU:CreateBar()
            end
            if MU.CreateMinimapButton then
                MU:CreateMinimapButton()
            end
            if MU.CreateQueueWindow then
                MU:CreateQueueWindow()
            end
            events:UnregisterEvent("ADDON_LOADED")
        end
    elseif event == "PLAYER_LOGIN" then
        if MU.Refresh then
            MU:Refresh()
        end
    elseif event == "PLAYER_ENTERING_WORLD" then
        if MU.Refresh then
            MU:Refresh()
        end
    elseif event == "SPELLS_CHANGED" or event == "LEARNED_SPELL_IN_TAB" then
        if MU.Refresh then
            MU:Refresh()
        end
    elseif event == "PLAYER_REGEN_ENABLED" then
        if MU.pendingRefresh then
            MU.pendingRefresh = false
            if MU.Refresh then
                MU:Refresh()
            end
        elseif MU.pendingApply then
            MU.pendingApply = false
            if MU.ApplySettings then
                MU:ApplySettings()
            end
        end
    elseif event == "BAG_UPDATE_DELAYED" then
        if MU.UpdateReagents then
            MU:UpdateReagents()
        end
    end
end)
