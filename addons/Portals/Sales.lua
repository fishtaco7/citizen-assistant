--[[---------------------------------------------------------------------------
    Portals - Portal request detection

    Watches Say, Yell, General, Trade and LookingForGroup for messages like:

        LF PORT SW TO DARN
        WTB portal to Darnassus
        need port from SW to IF

    and, when the requested origin matches where the mage is standing,
    whispers the buyer and invites them.

    Location note
    -------------
    AddOns cannot read another player's zone from a chat message. The origin
    is therefore taken from what the player types, and for General/Trade also
    from the zone in the channel name ("Trade - Stormwind"). A request only
    triggers when that origin matches the mage's real zone (unless the
    "only when they are in my city" option is turned off).

    Everything is rate limited per player and globally so the addon behaves
    like a careful shopkeeper rather than a whisper bot.
-----------------------------------------------------------------------------]]

local ADDON_NAME, ns = ...

local MU = ns

local WHISPER_LIMIT       = 250
local PER_PLAYER_COOLDOWN = 300
local GLOBAL_COOLDOWN     = 10
local BURST_WINDOW        = 60
local BURST_LIMIT         = 5
local MARKER_WINDOW       = 16

local DEFAULT_MESSAGE = "Hi {name}, I can portal you from {origin} to {destination}. Invite sent - accept it and I'll open the portal."
local NO_INVITE_MESSAGE = "Hi {name}, I can portal you from {origin} to {destination}. Let me know if you still need it."

---------------------------------------------------------------------------
-- City aliases
---------------------------------------------------------------------------

local ALIASES = {
    ["Stormwind"]     = { "stormwind", "sw" },
    ["Ironforge"]     = { "ironforge" },
    ["Darnassus"]     = { "darnassus", "darn", "darna" },
    ["Exodar"]        = { "exodar", "exo" },
    ["Orgrimmar"]     = { "orgrimmar", "orgri", "org" },
    ["Undercity"]     = { "undercity", "uc" },
    ["Thunder Bluff"] = { "thunder bluff", "thunderbluff", "tb" },
    ["Silvermoon"]    = { "silvermoon", "silver moon", "sm" },
    ["Shattrath"]     = { "shattrath", "shattra", "shatt", "shat" },
}

local KEYWORD_PATTERNS = {
    "%f[%a]portal%f[%A]",
    "%f[%a]portals%f[%A]",
    "%f[%a]port%f[%A]",
    "%f[%a]porting%f[%A]",
    "%f[%a]teleport%f[%A]",
    "%f[%a]teleports%f[%A]",
}

local DEST_MARKERS = {
    "%f[%a]to%f[%A]",
    "%-%>",   -- ->
    "=>",
    "→",
    "%s>%s",  -- SW > Darn
}

local ORIGIN_MARKERS = {
    "%f[%a]from%f[%A]",
    "%f[%a]in%f[%A]",
    "%f[%a]at%f[%A]",
}

local ALIAS_LIST = {}
local function EscapePattern(text)
    return (text:gsub("(%W)", "%%%1"))
end

for city, aliases in pairs(ALIASES) do
    for _, alias in ipairs(aliases) do
        ALIAS_LIST[#ALIAS_LIST + 1] = {
            city = city,
            alias = alias,
            pattern = "%f[%a]" .. EscapePattern(alias) .. "%f[%A]",
        }
    end
end

-- "IF" is the usual abbreviation for Ironforge, but "if" is far too common
-- as an English word. Only accept it when the player typed it in capitals.
ALIAS_LIST[#ALIAS_LIST + 1] = {
    city = "Ironforge",
    alias = "IF",
    pattern = "%f[%a]IF%f[%A]",
    caseSensitive = true,
}

---------------------------------------------------------------------------
-- Small helpers
---------------------------------------------------------------------------

local function Normalize(text)
    if type(text) ~= "string" then
        return ""
    end
    return text:lower()
end

local function ShortName(name)
    if type(name) ~= "string" then
        return ""
    end
    if Ambiguate then
        return Ambiguate(name, "short")
    end
    return name:match("^([^%-]+)") or name
end

local function MarkerBefore(text, patterns, position, window)
    for _, pattern in ipairs(patterns) do
        local init = 1
        while true do
            local startPos, endPos = text:find(pattern, init)
            if not startPos or startPos >= position then
                break
            end
            if position - endPos <= window then
                return true
            end
            init = endPos + 1
        end
    end
    return false
end

local function FindCityMentions(lower, raw)
    local found = {}

    for _, info in ipairs(ALIAS_LIST) do
        local haystack = info.caseSensitive and raw or lower
        local init = 1
        while true do
            local startPos, endPos = haystack:find(info.pattern, init)
            if not startPos then
                break
            end
            found[#found + 1] = {
                city = info.city,
                alias = info.alias,
                start = startPos,
                finish = endPos,
            }
            init = endPos + 1
        end
    end

    table.sort(found, function(a, b)
        if a.start == b.start then
            return (a.finish - a.start) > (b.finish - b.start)
        end
        return a.start < b.start
    end)

    local unique = {}
    for _, mention in ipairs(found) do
        local previous = unique[#unique]
        if not previous or mention.start > previous.finish then
            unique[#unique + 1] = mention
        elseif (mention.finish - mention.start) > (previous.finish - previous.start) then
            unique[#unique] = mention
        end
    end

    return unique
end

function MU:GetZoneName()
    if GetRealZoneText then
        return GetRealZoneText() or ""
    end
    if GetZoneText then
        return GetZoneText() or ""
    end
    return ""
end

function MU:IsMyZone(city)
    local zone = Normalize(self:GetZoneName())
    local target = Normalize(city)
    if zone == "" or target == "" then
        return false
    end
    return zone == target or zone:find(target, 1, true) ~= nil
end

function MU:MyCity()
    for _, city in ipairs(MU.destinationOrder) do
        if self:IsMyZone(city) then
            return city
        end
    end
    return nil
end

local function ZonesMatch(first, second)
    first, second = Normalize(first), Normalize(second)
    if first == "" or second == "" then
        return false
    end
    return first == second
        or first:find(second, 1, true) ~= nil
        or second:find(first, 1, true) ~= nil
end

local function ChannelZone(channelName)
    if type(channelName) ~= "string" then
        return nil
    end
    local zone = channelName:match("^.-%s*%-%s*(.+)$")
    if zone and zone ~= "" then
        return zone
    end
    return nil
end

local function ClassifyChannel(event, channelName, channelBaseName)
    if event == "CHAT_MSG_SAY" then
        return "say"
    end
    if event == "CHAT_MSG_YELL" then
        return "yell"
    end
    if event ~= "CHAT_MSG_CHANNEL" then
        return nil
    end

    local lower = Normalize(channelBaseName) .. "|" .. Normalize(channelName)
    if lower:find("lookingforgroup", 1, true)
        or lower:find("looking for group", 1, true)
        or lower:find("lfg", 1, true) then
        return "lfg"
    end
    if lower:find("trade", 1, true) then
        return "trade"
    end
    if lower:find("general", 1, true) then
        return "general"
    end
    return nil
end

local function InMyGroup(name)
    name = Normalize(ShortName(name))
    if name == Normalize(UnitName("player")) then
        return true
    end

    if GetNumPartyMembers and GetNumPartyMembers() > 0 then
        for index = 1, GetNumPartyMembers() do
            if Normalize(UnitName("party" .. index)) == name then
                return true
            end
        end
    end
    if GetNumRaidMembers and GetNumRaidMembers() > 0 then
        for index = 1, GetNumRaidMembers() do
            if Normalize(UnitName("raid" .. index)) == name then
                return true
            end
        end
    end
    return false
end

local function InvitePlayer(name)
    if C_PartyInfo and C_PartyInfo.InviteUnit then
        pcall(C_PartyInfo.InviteUnit, name)
    elseif InviteUnit then
        pcall(InviteUnit, name)
    end
end

local function FormatMessage(template, name, origin, destination)
    local message = template or DEFAULT_MESSAGE
    message = message:gsub("{name}", function() return name end)
    message = message:gsub("{origin}", function() return origin end)
    message = message:gsub("{destination}", function() return destination end)
    if #message > WHISPER_LIMIT then
        message = message:sub(1, WHISPER_LIMIT - 3) .. "..."
    end
    return message
end

---------------------------------------------------------------------------
-- Parser
---------------------------------------------------------------------------

function MU:ParsePortalRequest(text)
    if not text or text == "" then
        return nil
    end

    local raw = text
    local lower = text:lower()

    local hasKeyword = false
    for _, pattern in ipairs(KEYWORD_PATTERNS) do
        if lower:find(pattern) then
            hasKeyword = true
            break
        end
    end
    if not hasKeyword then
        return nil
    end

    local mentions = FindCityMentions(lower, raw)
    if #mentions == 0 then
        return nil
    end

    for _, mention in ipairs(mentions) do
        mention.isDestination = MarkerBefore(lower, DEST_MARKERS, mention.start, MARKER_WINDOW)
        mention.isOrigin = (not mention.isDestination)
            and MarkerBefore(lower, ORIGIN_MARKERS, mention.start, MARKER_WINDOW)
    end

    local origin, destination
    for _, mention in ipairs(mentions) do
        if mention.isDestination and not destination then
            destination = mention
        elseif mention.isOrigin and not origin then
            origin = mention
        end
    end

    -- "LF PORT SW TO DARN": the city before "to" is where the buyer is.
    if destination and not origin then
        for index = #mentions, 1, -1 do
            local mention = mentions[index]
            if mention.start < destination.start and not mention.isDestination then
                origin = mention
                break
            end
        end
    end

    -- "Darn portal pls, I'm in SW": the only other city is the destination.
    if origin and not destination then
        for _, mention in ipairs(mentions) do
            if mention ~= origin and not mention.isOrigin then
                destination = mention
                break
            end
        end
    end

    if not origin and not destination then
        if #mentions == 1 then
            destination = mentions[1]
        else
            origin = mentions[1]
            destination = mentions[2]
        end
    end

    if not destination then
        return nil
    end
    if origin and origin.city == destination.city then
        return nil
    end

    return {
        origin = origin and origin.city or nil,
        destination = destination.city,
    }
end

---------------------------------------------------------------------------
-- Rate limiting (session only, deliberately not saved)
---------------------------------------------------------------------------

local runtime = {
    lastWhisper = {},
    lastGlobalWhisper = 0,
    lastInvite = {},
    burstStart = 0,
    burstCount = 0,
}

local function Now()
    return (GetTime and GetTime()) or 0
end

local function IsWhisperLimited(name, now)
    local last = runtime.lastWhisper[name]
    if last and now - last < PER_PLAYER_COOLDOWN then
        return true
    end
    if now - runtime.lastGlobalWhisper < GLOBAL_COOLDOWN then
        return true
    end
    if now - runtime.burstStart >= BURST_WINDOW then
        runtime.burstStart = now
        runtime.burstCount = 0
    end
    return runtime.burstCount >= BURST_LIMIT
end

local function RecordWhisper(name, now)
    runtime.lastWhisper[name] = now
    runtime.lastGlobalWhisper = now
    runtime.burstCount = runtime.burstCount + 1
end

local function ShouldInvite(name, now)
    local last = runtime.lastInvite[name]
    return not last or now - last >= 60
end

local function RecordInvite(name, now)
    runtime.lastInvite[name] = now
end

-- Session cooldown tables are unbounded while the addon is loaded, so drop
-- entries that are far older than any cooldown we actually use.
local function CleanupRuntime(now)
    for name, stamp in pairs(runtime.lastWhisper) do
        if now - stamp > 3600 then
            runtime.lastWhisper[name] = nil
        end
    end
    for name, stamp in pairs(runtime.lastInvite) do
        if now - stamp > 3600 then
            runtime.lastInvite[name] = nil
        end
    end
end

---------------------------------------------------------------------------
-- Chat handling
---------------------------------------------------------------------------

function MU:HandleChatMessage(event, text, sender, channelName, channelBaseName)
    local db = self.db
    if not db or not db.salesEnabled then
        return
    end
    if not self:IsMage() then
        return
    end
    if not text or not sender or sender == "" then
        return
    end

    local name = ShortName(sender)
    if not name or name == "" or Normalize(name) == Normalize(UnitName("player")) then
        return
    end

    local kind = ClassifyChannel(event, channelName, channelBaseName)
    if not kind then
        return
    end
    if db.channels and db.channels[kind] == false then
        return
    end

    -- In a zone-local channel, make sure the channel itself belongs to the
    -- zone we are standing in (Trade - Stormwind, General - Ironforge, ...).
    if kind ~= "lfg" then
        local zone = ChannelZone(channelName)
        if zone and not ZonesMatch(zone, self:GetZoneName()) then
            return
        end
    end

    local request = self:ParsePortalRequest(text)
    if not request then
        return
    end

    -- Skip people advertising their own portal service.
    local lower = text:lower()
    if lower:find("%f[%a]wts%f[%A]")
        or lower:find("%f[%a]selling%f[%A]")
        or lower:find("%f[%a]sell%f[%A]")
        or lower:find("%f[%a]lfm%f[%A]") then
        return
    end

    if db.onlyMyCity then
        if request.origin then
            if not self:IsMyZone(request.origin) then
                return
            end
        elseif kind == "lfg" then
            -- Global channel, no stated origin: we cannot know they are here.
            return
        end
    end

    local known = self.known and self.known[request.destination]
    if not known or not known.portal then
        return
    end
    if self:IsMyZone(request.destination) then
        return
    end
    if UnitIsDeadOrGhost and UnitIsDeadOrGhost("player") then
        return
    end
    if C_FriendList and C_FriendList.IsIgnored then
        local ok, ignored = pcall(C_FriendList.IsIgnored, name)
        if ok and ignored then
            return
        end
    end

    local origin = request.origin or self:MyCity() or self:GetZoneName()
    local alreadyGrouped = InMyGroup(name)

    -- AddTicket only creates a queue row for party members. Everyone else is
    -- kept as a pending request until they accept the invite.
    if self.AddTicket then
        self:AddTicket(name, sender, request.destination, origin, text)
    end

    if alreadyGrouped then
        self:Print(("Portal request from %s: %s -> %s (already in your group)")
            :format(name, origin, request.destination))
        return
    end

    local now = Now()
    local key = Normalize(name)

    -- Invites are what let buyers join the queue, so they are not tied to the
    -- whisper cooldown. Only re-inviting the same player is throttled.
    local invited = false
    if db.autoInvite and ShouldInvite(key, now) then
        InvitePlayer(sender)
        RecordInvite(key, now)
        invited = true
    end

    local whispered = false
    if db.autoWhisper and not IsWhisperLimited(key, now) then
        local template = db.saleMessage
        if not db.autoInvite and template == MU.defaultSaleMessage then
            template = NO_INVITE_MESSAGE
        end
        local message = FormatMessage(template, name, origin, request.destination)
        SendChatMessage(message, "WHISPER", nil, sender)
        RecordWhisper(key, now)
        whispered = true
    end

    local actions = {}
    if invited then
        actions[#actions + 1] = "invited"
    end
    if whispered then
        actions[#actions + 1] = "whispered"
    end
    local actionText = #actions > 0 and table.concat(actions, " + ") or "detected"
    self:Print(("Portal request from %s: %s -> %s (%s)")
        :format(name, origin, request.destination, actionText))
end

---------------------------------------------------------------------------
-- Debug command: /portal test LF PORT SW TO DARN
---------------------------------------------------------------------------

function MU:TestRequest(text)
    if not text or text == "" then
        self:Print("Usage: /portal test <message>   e.g. /portal test LF PORT SW TO DARN")
        return
    end

    local request = self:ParsePortalRequest(text)
    if not request then
        self:Print(("No complete portal request found in: %s"):format(text))
        return
    end

    local known = request.destination and self.known and self.known[request.destination]
    local canCast = known and known.portal and true or false
    self:Print(("You are in %s. Detected origin: %s. Destination: %s. Portal known: %s.")
        :format(self:GetZoneName(), request.origin or "not stated",
            request.destination, canCast and "yes" or "no"))
end

---------------------------------------------------------------------------
-- Event hook
---------------------------------------------------------------------------

local watcher = CreateFrame("Frame")
watcher:RegisterEvent("CHAT_MSG_SAY")
watcher:RegisterEvent("CHAT_MSG_YELL")
watcher:RegisterEvent("CHAT_MSG_CHANNEL")
watcher:RegisterEvent("PLAYER_ENTERING_WORLD")

watcher:SetScript("OnEvent", function(_, event, ...)
    if event == "PLAYER_ENTERING_WORLD" then
        CleanupRuntime(Now())
        return
    end

    local text, sender, _, channelName, _, _, _, _, channelBaseName = ...
    MU:HandleChatMessage(event, text, sender, channelName, channelBaseName)
end)
