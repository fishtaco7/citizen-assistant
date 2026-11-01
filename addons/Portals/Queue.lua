--[[---------------------------------------------------------------------------
    Portals - Portal queue and session statistics

    /portals show opens a window that:
      * lists party members who are waiting for a portal,
      * shows their destination and distance,
      * has a secure portal button for the right city,
      * has a trade button that greys out until they are within trade range,
      * tracks portals cast, players served and tips received this session.

    Only party members are queued. When a request is detected the buyer is
    invited; they are added to the queue the moment they actually join the
    group. Addons cannot query the position of an arbitrary player, so party
    membership is also what makes the distance and trade range possible.

    Tips are read from the trade window: when a trade completes with someone
    who has asked for a portal, the gold they offered is added to the session.
-----------------------------------------------------------------------------]]

local ADDON_NAME, ns = ...

local MU = ns

local VISIBLE_ROWS = 7
local MAX_TICKETS  = 10
local TICKET_TTL   = 900 -- 15 minutes
local PENDING_TTL  = 300 -- 5 minutes to accept the invite
local ROW_HEIGHT   = 40
local ROW_SPACING  = 2
local WIDTH, HEIGHT = 390, 470
local TRADE_YARDS  = 11.1

MU.queue = {}
MU.pending = {}
MU.session = {
    portals = 0,
    served = {},
    servedCount = 0,
    tips = 0,
    trades = 0,
    customers = {},
}

---------------------------------------------------------------------------
-- Small helpers
---------------------------------------------------------------------------

local function Now()
    return (GetTime and GetTime()) or 0
end

local function NextSeq()
    MU.requestSeq = (MU.requestSeq or 0) + 1
    return MU.requestSeq
end

local function SortQueue()
    table.sort(MU.queue, function(a, b)
        return (a.seq or 0) < (b.seq or 0)
    end)
end

local function Key(name)
    return MU:ShortName(name):lower()
end

local function MoneyText(copper)
    copper = copper or 0
    if GetCoinTextureString then
        return GetCoinTextureString(copper)
    end
    local gold = math.floor(copper / 10000)
    local silver = math.floor((copper % 10000) / 100)
    local rest = copper % 100
    return ("%dg %ds %dc"):format(gold, silver, rest)
end

local function PortalTexture(spell)
    if not spell then
        return "Interface\\Icons\\INV_Misc_QuestionMark"
    end
    if GetSpellTexture then
        local texture = GetSpellTexture(spell.id) or GetSpellTexture(spell.name)
        if texture then
            return texture
        end
    end
    return "Interface\\Icons\\INV_Misc_QuestionMark"
end

local function BuildUnitMap()
    local map = {}
    local function add(unit)
        if UnitExists and UnitExists(unit) then
            local name = UnitName(unit)
            if name then
                map[Key(name)] = unit
            end
        end
    end

    add("player")

    -- Only scan the units that can actually exist. Scanning 40 raid slots on
    -- every tick (or every row hover) is wasted work in a normal party.
    local raidCount = (GetNumRaidMembers and GetNumRaidMembers()) or 0
    if raidCount > 0 then
        for index = 1, raidCount do
            add("raid" .. index)
        end
    else
        local partyCount = (GetNumPartyMembers and GetNumPartyMembers()) or 0
        for index = 1, partyCount do
            add("party" .. index)
        end
    end

    return map
end

local function DistanceYards(unit)
    if not unit or not UnitDistanceSquared then
        return nil
    end
    local ok, squared = pcall(UnitDistanceSquared, unit)
    if ok and type(squared) == "number" and squared >= 0 then
        return math.sqrt(squared)
    end
    return nil
end

local function InTradeRange(unit)
    if not unit then
        return false
    end
    if CheckInteractDistance then
        -- 2 = Trade (about 11 yards in Classic)
        local ok, result = pcall(CheckInteractDistance, unit, 2)
        if ok then
            return result and true or false
        end
    end
    local distance = DistanceYards(unit)
    return distance ~= nil and distance <= TRADE_YARDS
end

local function DescribeRange(unit, inRange)
    if not CheckInteractDistance then
        return "distance unknown"
    end
    if inRange then
        return "< 11 yards"
    end
    local ok4, follow = pcall(CheckInteractDistance, unit, 4)
    if ok4 and follow then
        return "< 28 yards"
    end
    return "> 28 yards"
end

local function RowUnit(row, ticket)
    local unit = row.unit
    if unit and UnitExists and UnitExists(unit) then
        return unit
    end
    return BuildUnitMap()[ticket.key]
end

---------------------------------------------------------------------------
-- Queue data
---------------------------------------------------------------------------

function MU:GetTicketByName(name)
    local key = Key(name)
    for _, ticket in ipairs(self.queue) do
        if ticket.key == key then
            return ticket
        end
    end
end

function MU:PruneTickets()
    local now = Now()
    for index = #self.queue, 1, -1 do
        local ticket = self.queue[index]
        if now - ticket.added > TICKET_TTL then
            table.remove(self.queue, index)
        end
    end
end

function MU:AddPendingRequest(name, fullName, destination, origin, message)
    if not name or name == "" or not destination then
        return
    end

    local key = Key(name)
    local pending = self.pending[key]
    if pending then
        pending.destination = destination
        pending.origin = origin
        pending.message = message
        pending.updated = Now()
        pending.added = Now()
    else
        self.pending[key] = {
            name = self:ShortName(name),
            fullName = fullName or name,
            key = key,
            destination = destination,
            origin = origin,
            message = message,
            added = Now(),
            seq = NextSeq(),
        }
    end
end

-- Only players who are actually in the group become queue entries. Everyone
-- else is remembered as a pending request until they accept the invite.
function MU:AddTicket(name, fullName, destination, origin, message, seq, groupMap)
    if not name or name == "" or not destination then
        return nil
    end

    local key = Key(name)
    local inGroup
    if groupMap then
        inGroup = groupMap[key]
    else
        inGroup = BuildUnitMap()[key]
    end
    if not inGroup then
        self:AddPendingRequest(name, fullName, destination, origin, message)
        return nil
    end

    self.session.customers[key] = true

    local ticket = self:GetTicketByName(name)
    if ticket then
        ticket.destination = destination
        ticket.origin = origin
        ticket.message = message
        ticket.updated = Now()
    else
        ticket = {
            name = self:ShortName(name),
            fullName = fullName or name,
            key = key,
            destination = destination,
            origin = origin,
            message = message,
            added = Now(),
            seq = seq or NextSeq(),
        }
        self.queue[#self.queue + 1] = ticket
        SortQueue()
        while #self.queue > MAX_TICKETS do
            table.remove(self.queue, 1)
        end
    end

    self:PruneTickets()
    if not self.deferQueueRefresh then
        self:RefreshQueue()
    end
    return ticket
end

function MU:PromotePendingRequests()
    local group = BuildUnitMap()
    local now = Now()
    local ready = {}

    for key, pending in pairs(self.pending) do
        if group[key] then
            ready[#ready + 1] = pending
        elseif now - pending.added > PENDING_TTL then
            self.pending[key] = nil
        end
    end

    if #ready == 0 then
        return
    end

    table.sort(ready, function(a, b)
        return (a.seq or 0) < (b.seq or 0)
    end)

    self.deferQueueRefresh = true
    for _, pending in ipairs(ready) do
        self.pending[pending.key] = nil
        self:AddTicket(pending.name, pending.fullName,
            pending.destination, pending.origin, pending.message, pending.seq, group)
    end
    self.deferQueueRefresh = false

    self:RefreshQueue()
end

function MU:PruneQueueForRoster()
    local group = BuildUnitMap()
    local changed = false

    for index = #self.queue, 1, -1 do
        if not group[self.queue[index].key] then
            table.remove(self.queue, index)
            changed = true
        end
    end

    if changed then
        self:RefreshQueue()
    end
end

function MU:RemoveTicketByKey(key)
    if not key then
        return false
    end
    for index, ticket in ipairs(self.queue) do
        if ticket.key == key then
            table.remove(self.queue, index)
            self:RefreshQueue()
            return true
        end
    end
    return false
end

function MU:RemoveTicket(name)
    return self:RemoveTicketByKey(Key(name))
end

function MU:ClearQueue()
    for index = #self.queue, 1, -1 do
        table.remove(self.queue, index)
    end
    for key in pairs(self.pending) do
        self.pending[key] = nil
    end
    self:RefreshQueue()
end

function MU:MarkServed(key)
    if not key then
        return
    end
    if not self.session.served[key] then
        self.session.served[key] = true
        self.session.servedCount = self.session.servedCount + 1
    end
end

function MU:ResetSession()
    self.session = {
        portals = 0,
        served = {},
        servedCount = 0,
        tips = 0,
        trades = 0,
        customers = {},
    }
    for _, ticket in ipairs(self.queue) do
        self.session.customers[ticket.key] = true
    end
    self:UpdateQueueStats()
end

---------------------------------------------------------------------------
-- Window pieces
---------------------------------------------------------------------------

local function AddBorder(frame, r, g, b)
    local function line()
        local texture = frame:CreateTexture(nil, "BORDER")
        texture:SetColorTexture(r, g, b, 1)
        return texture
    end

    local top = line()
    top:SetPoint("TOPLEFT", 0, 0)
    top:SetPoint("TOPRIGHT", 0, 0)
    top:SetHeight(1)

    local bottom = line()
    bottom:SetPoint("BOTTOMLEFT", 0, 0)
    bottom:SetPoint("BOTTOMRIGHT", 0, 0)
    bottom:SetHeight(1)

    local left = line()
    left:SetPoint("TOPLEFT", 0, 0)
    left:SetPoint("BOTTOMLEFT", 0, 0)
    left:SetWidth(1)

    local right = line()
    right:SetPoint("TOPRIGHT", 0, 0)
    right:SetPoint("BOTTOMRIGHT", 0, 0)
    right:SetWidth(1)
end

local function CreateCastButton(row, index)
    local cast = CreateFrame("Button", "PortalsQueueCast" .. index, row,
        "SecureActionButtonTemplate")
    cast:SetSize(30, 30)
    cast:SetPoint("LEFT", row, "LEFT", 6, 0)
    cast:RegisterForClicks("AnyUp")

    local border = cast:CreateTexture(nil, "BACKGROUND")
    border:SetPoint("TOPLEFT", -1, 1)
    border:SetPoint("BOTTOMRIGHT", 1, -1)
    border:SetColorTexture(0, 0, 0, 0.9)

    local icon = cast:CreateTexture(nil, "ARTWORK")
    icon:SetPoint("TOPLEFT")
    icon:SetPoint("BOTTOMRIGHT")
    icon:SetTexCoord(0.08, 0.92, 0.08, 0.92)
    cast.icon = icon

    -- Remember which ticket this click was for, so the cast-success event can
    -- mark the right buyer as served. OnMouseDown runs before the secure cast.
    cast:SetScript("OnMouseDown", function(self)
        if self.ticketKey then
            MU.activeTicketKey = self.ticketKey
            MU.activeTicketDestination = self.ticketDestination
            MU.activeTicketAt = Now()
        end
    end)

    cast:SetScript("OnEnter", function(self)
        GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
        if self.ticketDestination then
            GameTooltip:SetText("Portal: " .. self.ticketDestination, 1, 1, 1)
        else
            GameTooltip:SetText("Portal", 1, 1, 1)
        end
        GameTooltip:AddLine("Click to cast the portal.", 0.4, 1, 0.4)
        GameTooltip:AddLine("The buyer clicks your portal to travel.", 0.7, 0.7, 0.7)
        GameTooltip:Show()
    end)
    cast:SetScript("OnLeave", function()
        GameTooltip:Hide()
    end)

    return cast
end

local function CreateRow(parent, index)
    local row = CreateFrame("Frame", nil, parent)
    row:SetSize(WIDTH - 24, ROW_HEIGHT)
    row:SetPoint("TOPLEFT", parent, "TOPLEFT", 12,
        -46 - (index - 1) * (ROW_HEIGHT + ROW_SPACING))

    local background = row:CreateTexture(nil, "BACKGROUND")
    background:SetAllPoints()
    background:SetColorTexture(0.07, 0.08, 0.11, 0.85)
    row.bg = background

    row.cast = CreateCastButton(row, index)

    local name = row:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    name:SetPoint("TOPLEFT", row, "TOPLEFT", 44, -5)
    name:SetWidth(92)
    name:SetJustifyH("LEFT")
    name:SetTextColor(1, 0.82, 0)
    row.name = name

    local destination = row:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    destination:SetPoint("TOPLEFT", row, "TOPLEFT", 140, -5)
    destination:SetWidth(140)
    destination:SetJustifyH("LEFT")
    destination:SetTextColor(0.5, 1, 0.5)
    row.dest = destination

    local distance = row:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    distance:SetPoint("TOPLEFT", row, "TOPLEFT", 44, -21)
    distance:SetWidth(170)
    distance:SetJustifyH("LEFT")
    distance:SetTextColor(0.75, 0.75, 0.75)
    row.distance = distance

    local trade = CreateFrame("Button", nil, row, "UIPanelButtonTemplate")
    trade:SetSize(66, 20)
    trade:SetPoint("TOPRIGHT", row, "TOPRIGHT", -20, -10)
    trade:SetText("Trade")
    trade:SetScript("OnClick", function()
        local ticket = row.ticket
        if not ticket then
            return
        end
        local unit = RowUnit(row, ticket)
        if not unit then
            MU:Print(ticket.name .. " is no longer in your group.")
            return
        end
        if InTradeRange(unit) then
            pcall(InitiateTrade, unit)
        else
            MU:Print(("%s is too far away to trade."):format(ticket.name))
        end
    end)
    trade:SetScript("OnEnter", function(self)
        local ticket = row.ticket
        if not ticket then
            return
        end
        GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
        local unit = RowUnit(row, ticket)
        if not unit then
            GameTooltip:SetText("Not in your group", 1, 1, 1)
        elseif InTradeRange(unit) then
            GameTooltip:SetText("Trade with " .. ticket.name)
            GameTooltip:AddLine("Click to open a trade window.", 0.4, 1, 0.4)
        else
            GameTooltip:SetText("Trade with " .. ticket.name)
            GameTooltip:AddLine("Too far away. Get within about 11 yards.", 1, 0.3, 0.3)
        end
        GameTooltip:Show()
    end)
    trade:SetScript("OnLeave", function()
        GameTooltip:Hide()
    end)
    row.trade = trade

    local remove = CreateFrame("Button", nil, row)
    remove:SetSize(16, 16)
    remove:SetPoint("TOPRIGHT", row, "TOPRIGHT", -2, -2)
    local removeText = remove:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    removeText:SetPoint("CENTER")
    removeText:SetText("X")
    removeText:SetTextColor(0.9, 0.25, 0.25)
    remove:SetScript("OnClick", function()
        if row.ticket then
            MU:RemoveTicketByKey(row.ticket.key)
        end
    end)
    remove:SetScript("OnEnter", function(self)
        GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
        GameTooltip:SetText("Remove from queue", 1, 1, 1)
        GameTooltip:Show()
    end)
    remove:SetScript("OnLeave", function()
        GameTooltip:Hide()
    end)
    row.remove = remove

    row:Hide()
    return row
end

local function UpdateRowSecure(row, ticket)
    local known = MU.known and MU.known[ticket.destination]
    local spell = known and known.portal

    if spell then
        row.cast:SetAttribute("type1", "spell")
        row.cast:SetAttribute("spell1", spell.name)
        row.cast.icon:SetTexture(PortalTexture(spell))
        row.cast:Enable()
    else
        row.cast:SetAttribute("type1", nil)
        row.cast:SetAttribute("spell1", nil)
        row.cast.icon:SetTexture(PortalTexture(nil))
        row.cast:Disable()
    end

    row.cast.ticketKey = ticket.key
    row.cast.ticketDestination = ticket.destination
end

local function UpdateRowLive(row, ticket, unitMap)
    local unit = unitMap and unitMap[ticket.key] or nil
    local distance = unit and DistanceYards(unit) or nil
    local inRange = unit and InTradeRange(unit) or false
    local level = unit and UnitLevel(unit) or nil

    local nameText = ticket.name
    if level and level > 0 then
        nameText = nameText .. " (" .. level .. ")"
    end
    row.name:SetText(nameText)
    row.dest:SetText(ticket.destination)

    if unit then
        if distance then
            row.distance:SetText(("%.1f yards"):format(distance))
        else
            row.distance:SetText(DescribeRange(unit, inRange))
        end
        if inRange then
            row.distance:SetTextColor(0.55, 1, 0.55)
        else
            row.distance:SetTextColor(0.8, 0.8, 0.8)
        end
    else
        row.distance:SetText("not in group")
        row.distance:SetTextColor(0.65, 0.65, 0.65)
    end

    if row.tradeEnabled ~= inRange then
        row.tradeEnabled = inRange
        if inRange then
            row.trade:Enable()
        else
            row.trade:Disable()
        end
    end

    if inRange then
        row.bg:SetColorTexture(0.08, 0.18, 0.09, 0.9)
    else
        row.bg:SetColorTexture(0.07, 0.08, 0.11, 0.85)
    end
    row.ticket = ticket
    row.unit = unit
end

function MU:UpdateQueueStats()
    local window = self.queueWindow
    if not window then
        return
    end

    if self.db.salesEnabled then
        window.subtitle:SetText("|cff40ff40Open for business|r - take a request, cast the portal, then trade.")
    else
        window.subtitle:SetText("|cffff4040Closed for business|r - detection is paused.")
    end

    local session = self.session
    window.statsPortals:SetText(("Portals cast: %d    Players served: %d")
        :format(session.portals, session.servedCount))
    window.statsTips:SetText("Tips this session: " .. MoneyText(session.tips))
end

function MU:RefreshQueue()
    local window = self.queueWindow
    if not window then
        return
    end
    if self:InCombat() then
        self.queueDirty = true
        return
    end

    self:PruneTickets()
    local unitMap = BuildUnitMap()

    for index, row in ipairs(window.rows) do
        local ticket = self.queue[index]
        if ticket then
            UpdateRowSecure(row, ticket)
            UpdateRowLive(row, ticket, unitMap)
            row:Show()
        else
            row.ticket = nil
            row:Hide()
        end
    end

    if #self.queue > VISIBLE_ROWS then
        window.more:SetText(("+%d more waiting"):format(#self.queue - VISIBLE_ROWS))
        window.more:Show()
    else
        window.more:Hide()
    end

    if #self.queue == 0 then
        window.empty:Show()
    else
        window.empty:Hide()
    end

    self.queueDirty = false
    self:UpdateQueueStats()
end

function MU:CreateQueueWindow()
    if self.queueWindow then
        return self.queueWindow
    end

    local window = CreateFrame("Frame", "PortalsQueue", UIParent)
    window:SetSize(WIDTH, HEIGHT)
    window:SetPoint("CENTER", UIParent, "CENTER", 0, 20)
    window:SetFrameStrata("DIALOG")
    window:SetToplevel(true)
    window:SetClampedToScreen(true)
    window:SetMovable(true)
    window:EnableMouse(true)
    window:RegisterForDrag("LeftButton")
    window:SetScript("OnDragStart", function(self)
        if MU:InCombat() then
            return
        end
        self:StartMoving()
    end)
    window:SetScript("OnDragStop", function(self)
        self:StopMovingOrSizing()
    end)
    window:Hide()

    local background = window:CreateTexture(nil, "BACKGROUND")
    background:SetAllPoints()
    background:SetColorTexture(0.03, 0.035, 0.05, 0.97)
    AddBorder(window, 0.35, 0.4, 0.5)

    local title = window:CreateFontString(nil, "OVERLAY", "GameFontNormalLarge")
    title:SetPoint("TOP", window, "TOP", 0, -12)
    title:SetText("Portal Queue")

    local subtitle = window:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    subtitle:SetPoint("TOP", title, "BOTTOM", 0, -2)
    subtitle:SetText("Take a request, cast the portal, then trade.")
    subtitle:SetTextColor(0.65, 0.7, 0.8)
    window.subtitle = subtitle

    local close = CreateFrame("Button", nil, window, "UIPanelCloseButton")
    close:SetPoint("TOPRIGHT", window, "TOPRIGHT", -4, -4)
    close:SetScript("OnClick", function()
        MU:CloseQueue()
    end)

    window.rows = {}
    for index = 1, VISIBLE_ROWS do
        window.rows[index] = CreateRow(window, index)
    end

    local empty = window:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    empty:SetPoint("CENTER", window, "CENTER", 0, 30)
    empty:SetWidth(WIDTH - 50)
    empty:SetJustifyH("CENTER")
    empty:SetTextColor(0.7, 0.7, 0.7)
    empty:SetText("No party members waiting for a portal.\nBuyers appear here after accepting your invite.")
    window.empty = empty

    local more = window:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    more:SetPoint("TOPLEFT", window, "TOPLEFT", 14, -344)
    more:SetTextColor(0.6, 0.8, 1)
    window.more = more

    local divider = window:CreateTexture(nil, "ARTWORK")
    divider:SetPoint("TOPLEFT", window, "TOPLEFT", 12, -358)
    divider:SetPoint("TOPRIGHT", window, "TOPRIGHT", -12, -358)
    divider:SetHeight(1)
    divider:SetColorTexture(0.3, 0.35, 0.45, 0.8)

    local statsPortals = window:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    statsPortals:SetPoint("TOPLEFT", window, "TOPLEFT", 14, -372)
    statsPortals:SetText("Portals cast: 0    Players served: 0")
    window.statsPortals = statsPortals

    local statsTips = window:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    statsTips:SetPoint("TOPLEFT", window, "TOPLEFT", 14, -392)
    statsTips:SetText("Tips this session: 0c")
    window.statsTips = statsTips

    local hint = window:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    hint:SetPoint("TOPLEFT", window, "TOPLEFT", 14, -414)
    hint:SetWidth(WIDTH - 28)
    hint:SetJustifyH("LEFT")
    hint:SetTextColor(0.6, 0.65, 0.75)
    hint:SetText("Only party members are queued. Trade unlocks within about 11 yards.")

    local clear = CreateFrame("Button", nil, window, "UIPanelButtonTemplate")
    clear:SetSize(100, 22)
    clear:SetPoint("BOTTOMLEFT", window, "BOTTOMLEFT", 14, 14)
    clear:SetText("Clear queue")
    clear:SetScript("OnClick", function()
        MU:ClearQueue()
        MU:Print("Portal queue cleared.")
    end)

    local closeButton = CreateFrame("Button", nil, window, "UIPanelButtonTemplate")
    closeButton:SetSize(80, 22)
    closeButton:SetPoint("BOTTOMRIGHT", window, "BOTTOMRIGHT", -14, 14)
    closeButton:SetText("Close")
    closeButton:SetScript("OnClick", function()
        MU:CloseQueue()
    end)

    window:SetScript("OnUpdate", function(self, elapsed)
        self.tick = (self.tick or 0) + elapsed
        if self.tick < 0.5 then
            return
        end
        self.tick = 0

        if MU.queueDirty and not MU:InCombat() then
            MU:RefreshQueue()
        end

        local unitMap = BuildUnitMap()
        for _, row in ipairs(self.rows) do
            if row:IsShown() and row.ticket then
                UpdateRowLive(row, row.ticket, unitMap)
            end
        end
    end)

    self.queueWindow = window
    self:RefreshQueue()
    return window
end

function MU:OpenQueue()
    if self:InCombat() then
        self:Print("The portal queue cannot be opened while in combat.")
        return
    end
    -- Deliberately independent of the portal bar: only the queue window is
    -- shown here. The bar stays exactly as /portal left it.
    self:PromotePendingRequests()
    self:PruneQueueForRoster()
    self:CreateQueueWindow()
    self:RefreshQueue()
    self.queueWindow:Show()
end

function MU:CloseQueue()
    if self:InCombat() then
        self:Print("The portal queue cannot be closed while in combat.")
        return
    end
    -- Also bar-independent: hide the queue window, never the portal bar.
    if self.queueWindow then
        self.queueWindow:Hide()
    end
end

---------------------------------------------------------------------------
-- Portal casts
---------------------------------------------------------------------------

function MU:OnPortalCast(spellID)
    local destination = spellID and MU.portalByID[spellID]
    if not destination then
        return
    end

    self.session.portals = self.session.portals + 1

    local key = self.activeTicketKey
    local expected = self.activeTicketDestination
    local since = self.activeTicketAt or 0
    self.activeTicketKey = nil
    self.activeTicketDestination = nil
    self.activeTicketAt = nil

    -- Only credit the ticket if this cast was actually started from its row
    -- and finished recently (a portal has a 10 second cast time).
    if key and destination == expected and (Now() - since) <= 20 then
        self:MarkServed(key)
        self:RemoveTicketByKey(key)
    end

    self:UpdateQueueStats()
end

---------------------------------------------------------------------------
-- Tips from completed trades
---------------------------------------------------------------------------

local trade = {
    partner = nil,
    money = 0,
    accepted = false,
}

function MU:OnTradeShow()
    local partner
    if TradeFrameRecipientNameText and TradeFrameRecipientNameText.GetText then
        partner = TradeFrameRecipientNameText:GetText()
    end
    trade.partner = partner or UnitName("target")
    trade.money = 0
    trade.accepted = false
end

function MU:OnTradeAcceptUpdate(myAccepted, targetAccepted)
    local function accepted(value)
        return value == 1 or value == true
    end

    if accepted(myAccepted) and accepted(targetAccepted) then
        trade.money = (GetTargetTradeMoney and GetTargetTradeMoney()) or 0
        trade.accepted = true
    end
end

function MU:OnTradeClosed()
    if trade.accepted and trade.partner and trade.money and trade.money > 0 then
        local key = Key(trade.partner)
        if self.session.customers[key] then
            self.session.tips = self.session.tips + trade.money
            self.session.trades = self.session.trades + 1
            self:UpdateQueueStats()
        end
    end
    trade.partner, trade.money, trade.accepted = nil, 0, false
end

---------------------------------------------------------------------------
-- Slash command: /portals
---------------------------------------------------------------------------

function MU:PortalsSlash(input)
    input = (input or ""):gsub("^%s+", ""):gsub("%s+$", "")
    local cmd, arg = input:match("^(%S*)%s*(.-)$")
    cmd = (cmd or ""):lower()

    if cmd == "" or cmd == "show" or cmd == "open" or cmd == "queue" then
        self:OpenQueue()
    elseif cmd == "hide" or cmd == "close" then
        self:CloseQueue()
    elseif cmd == "clear" then
        self:ClearQueue()
        self:Print("Portal queue cleared.")
    elseif cmd == "remove" then
        if arg == "" then
            self:Print("Usage: /portals remove <name>")
        elseif self:RemoveTicket(arg) then
            self:Print("Removed " .. arg .. " from the queue.")
        else
            self:Print("No queued player named " .. arg .. ".")
        end
    elseif cmd == "resetsession" or cmd == "resetstats" then
        self:ResetSession()
        self:Print("Session statistics reset.")
    elseif cmd == "help" then
        self:Print("/portals show | open - open the queue window")
        self:Print("/portals hide | close - close the queue window")
        self:Print("/portals clear - remove the queue and pending invites")
        self:Print("/portals remove <name> - remove one player")
        self:Print("/portals resetsession - reset portals, players and tips")
        self:Print("These commands never touch the portal bar. Use /portal bar on and /portal bar off for the bar.")
    else
        self:Print(("Unknown command '%s'. Try /portals help."):format(cmd))
    end
end

SLASH_PORTALS1 = "/portals"
SlashCmdList["PORTALS"] = function(msg)
    MU:PortalsSlash(msg)
end

---------------------------------------------------------------------------
-- Events
---------------------------------------------------------------------------

local events = CreateFrame("Frame")
events:RegisterEvent("UNIT_SPELLCAST_SUCCEEDED")
events:RegisterEvent("TRADE_SHOW")
events:RegisterEvent("TRADE_ACCEPT_UPDATE")
events:RegisterEvent("TRADE_CLOSED")
events:RegisterEvent("GROUP_ROSTER_UPDATE")
events:RegisterEvent("PARTY_MEMBERS_CHANGED")
events:RegisterEvent("PLAYER_REGEN_ENABLED")
events:RegisterEvent("PLAYER_ENTERING_WORLD")

events:SetScript("OnEvent", function(_, event, ...)
    if event == "UNIT_SPELLCAST_SUCCEEDED" then
        local unit, _, spellID = ...
        if unit == "player" then
            MU:OnPortalCast(spellID)
        end
    elseif event == "TRADE_SHOW" then
        MU:OnTradeShow()
    elseif event == "TRADE_ACCEPT_UPDATE" then
        local myAccepted, targetAccepted = ...
        MU:OnTradeAcceptUpdate(myAccepted, targetAccepted)
    elseif event == "TRADE_CLOSED" then
        MU:OnTradeClosed()
    elseif event == "GROUP_ROSTER_UPDATE" or event == "PARTY_MEMBERS_CHANGED" then
        MU:PromotePendingRequests()
        MU:PruneQueueForRoster()
        if MU.queueWindow and MU.queueWindow:IsShown() then
            MU:RefreshQueue()
        end
    elseif event == "PLAYER_REGEN_ENABLED" then
        if MU.queueDirty then
            MU:RefreshQueue()
        end
    elseif event == "PLAYER_ENTERING_WORLD" then
        MU:PromotePendingRequests()
        MU:PruneQueueForRoster()
        MU:PruneTickets()
        if MU.queueWindow and MU.queueWindow:IsShown() then
            MU:RefreshQueue()
        end
    end
end)
