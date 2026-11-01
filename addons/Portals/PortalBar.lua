--[[---------------------------------------------------------------------------
    Portals - PortalBar
    Builds a secure bar of every portal/teleport the character actually knows.

    Left-click  -> portal (or teleport, if the portal is not learned yet)
    Right-click -> teleport

    The bar is rebuilt from the spellbook, so faction, level and expansion
    differences take care of themselves: a low-level mage simply sees fewer
    buttons, a Horde mage never sees Stormwind, and Shattrath only shows up
    on TBC clients.

    All frames are secure action buttons so the casts survive taint rules.
-----------------------------------------------------------------------------]]

local ADDON_NAME, ns = ...

local MU = ns

local PORTAL_RUNE   = 17032 -- Rune of Portals
local TELEPORT_RUNE = 17031 -- Rune of Teleportation
local GRIP_HEIGHT   = 14

---------------------------------------------------------------------------
-- Spell book
---------------------------------------------------------------------------

-- Destination name -> spell IDs. IDs are locale independent, which is why
-- the spellbook scan below matches on ID instead of spell name.
MU.destinations = {
    ["Stormwind"]     = { teleport = 3561,  portal = 10059 },
    ["Ironforge"]     = { teleport = 3562,  portal = 11416 },
    ["Darnassus"]     = { teleport = 3565,  portal = 11419 },
    ["Exodar"]        = { teleport = 32271, portal = 32266 },
    ["Orgrimmar"]     = { teleport = 3567,  portal = 11417 },
    ["Undercity"]     = { teleport = 3563,  portal = 11418 },
    ["Thunder Bluff"] = { teleport = 3566,  portal = 11420 },
    ["Silvermoon"]    = { teleport = 32272, portal = 32267 },
    ["Shattrath"]     = { teleport = 35715, portal = 35717 },
}

-- Interleaved on purpose: a character only ever knows one faction's cities,
-- so each faction still ends up with its own sensible order.
MU.destinationOrder = {
    "Stormwind", "Orgrimmar",
    "Ironforge", "Undercity",
    "Darnassus", "Thunder Bluff",
    "Exodar", "Silvermoon",
    "Shattrath",
}

MU.portalByID, MU.teleportByID = {}, {}
for destination, info in pairs(MU.destinations) do
    if info.portal then
        MU.portalByID[info.portal] = destination
    end
    if info.teleport then
        MU.teleportByID[info.teleport] = destination
    end
end

local BOOK = BOOKTYPE_SPELL or "spell"

-- Older and current Classic builds spell the spellbook bank differently.
-- Try the legacy string first, then the Enum value where one exists.
local BOOK_TYPES = { BOOK }
if Enum and Enum.SpellBookSpellBank and Enum.SpellBookSpellBank.Player ~= nil then
    BOOK_TYPES[#BOOK_TYPES + 1] = Enum.SpellBookSpellBank.Player
end

local function SpellbookItemName(index)
    if GetSpellBookItemName then
        local name = GetSpellBookItemName(index, BOOK)
        if name then
            return name
        end
    end
    if C_SpellBook and C_SpellBook.GetSpellBookItemName then
        for _, bookType in ipairs(BOOK_TYPES) do
            local ok, name = pcall(C_SpellBook.GetSpellBookItemName, index, bookType)
            if ok and name then
                return name
            end
        end
    end
end

local function SpellbookItemID(index)
    local function unwrap(first, second)
        if type(second) == "number" then
            return second
        end
        if type(first) == "number" then
            return first
        end
    end

    if GetSpellBookItemInfo then
        local id = unwrap(GetSpellBookItemInfo(index, BOOK))
        if id then
            return id
        end
    end
    if C_SpellBook and C_SpellBook.GetSpellBookItemInfo then
        for _, bookType in ipairs(BOOK_TYPES) do
            local ok, first, second = pcall(C_SpellBook.GetSpellBookItemInfo, index, bookType)
            local id = ok and unwrap(first, second)
            if id then
                return id
            end
        end
    end
end

local function SpellNameByID(id)
    if not id then
        return nil
    end
    if C_Spell and C_Spell.GetSpellInfo then
        local info = C_Spell.GetSpellInfo(id)
        if type(info) == "table" and info.name then
            return info.name
        end
    end
    if GetSpellInfo then
        return (GetSpellInfo(id))
    end
end

local function SpellTextureByID(id)
    if not id then
        return nil
    end
    if GetSpellTexture then
        local texture = GetSpellTexture(id)
        if texture then
            return texture
        end
    end
    if C_Spell and C_Spell.GetSpellTexture then
        return C_Spell.GetSpellTexture(id)
    end
end

local function SpellCooldown(id, name)
    if C_Spell and C_Spell.GetSpellCooldown then
        local info = C_Spell.GetSpellCooldown(id)
        if type(info) == "table" then
            return info.startTime, info.duration, info.isEnabled
        end
    end
    if GetSpellCooldown then
        -- Classic accepts either a name or an ID; the name is accepted by
        -- every 1.15/2.5 build, so prefer it.
        return GetSpellCooldown(name or id)
    end
end

local function ItemCount(itemID)
    if C_Item and C_Item.GetItemCount then
        return C_Item.GetItemCount(itemID) or 0
    end
    return (GetItemCount(itemID)) or 0
end

local function ItemName(itemID, fallback)
    return (GetItemInfo(itemID)) or fallback
end

-- Resolve spell ID -> destination + "portal"/"teleport". Falls back to a
-- localized name lookup for clients where the spellbook API gives no ID.
function MU:LookupSpell(spellID, spellName)
    if spellID then
        local destination = MU.portalByID[spellID]
        if destination then
            return destination, "portal"
        end
        destination = MU.teleportByID[spellID]
        if destination then
            return destination, "teleport"
        end
    end
    if spellName and self.nameToID then
        local id = self.nameToID[spellName]
        if id then
            if MU.portalByID[id] then
                return MU.portalByID[id], "portal"
            end
            if MU.teleportByID[id] then
                return MU.teleportByID[id], "teleport"
            end
        end
    end
end

function MU:BuildNameIndex()
    self.nameToID = self.nameToID or {}
    for _, info in pairs(MU.destinations) do
        for _, id in pairs({ info.portal, info.teleport }) do
            local name = SpellNameByID(id)
            if name then
                self.nameToID[name] = id
            end
        end
    end
    self.nameIndexBuilt = true
end

local function ScanSpellbook()
    local known = {}

    local function consider(index)
        local name = SpellbookItemName(index)
        if not name or name == "" then
            return
        end
        local spellID = SpellbookItemID(index)
        local destination, mode = MU:LookupSpell(spellID, name)
        if destination and mode then
            known[destination] = known[destination] or {}
            known[destination][mode] = { id = spellID, name = name }
        end
    end

    local scannedTabs = false
    if GetNumSpellTabs and GetSpellTabInfo then
        for tab = 1, GetNumSpellTabs() do
            local _, _, offset, numSpells = GetSpellTabInfo(tab)
            if offset and numSpells and numSpells > 0 then
                for index = offset + 1, offset + numSpells do
                    consider(index)
                end
                scannedTabs = true
            end
        end
    end

    if not scannedTabs then
        for index = 1, (MAX_SPELLS or 1024) do
            if not SpellbookItemName(index) then
                break
            end
            consider(index)
        end
    end

    return known
end

---------------------------------------------------------------------------
-- Bar
---------------------------------------------------------------------------

local function UpdateCooldown(button)
    local info = button.info
    if not info then
        return
    end
    local spell = info.portal or info.teleport
    if not spell then
        return
    end
    local start, duration = SpellCooldown(spell.id, spell.name)
    if start and duration and duration > 0 then
        button.cooldown:SetCooldown(start, duration)
    else
        button.cooldown:SetCooldown(0, 0)
    end
end

local function CreateButton(index)
    local button = CreateFrame("Button", "PortalsButton" .. index, MU.bar,
        "SecureActionButtonTemplate")
    button:RegisterForClicks("AnyUp")
    button:SetSize(MU.db.buttonSize, MU.db.buttonSize)

    local border = button:CreateTexture(nil, "BACKGROUND")
    border:SetPoint("TOPLEFT", -1, 1)
    border:SetPoint("BOTTOMRIGHT", 1, -1)
    border:SetColorTexture(0, 0, 0, 0.9)

    local icon = button:CreateTexture(nil, "ARTWORK")
    icon:SetPoint("TOPLEFT")
    icon:SetPoint("BOTTOMRIGHT")
    icon:SetTexCoord(0.08, 0.92, 0.08, 0.92)
    button.icon = icon

    local highlight = button:CreateTexture(nil, "HIGHLIGHT")
    highlight:SetAllPoints(icon)
    highlight:SetColorTexture(1, 1, 1, 0.12)

    local cooldown = CreateFrame("Cooldown", nil, button, "CooldownFrameTemplate")
    cooldown:SetAllPoints(icon)
    button.cooldown = cooldown

    local count = button:CreateFontString(nil, "OVERLAY")
    count:SetFont(STANDARD_TEXT_FONT, 10, "OUTLINE")
    count:SetPoint("BOTTOMRIGHT", button, "BOTTOMRIGHT", -1, 1)
    button.count = count

    button:SetScript("OnEnter", function(self)
        MU:ShowTooltip(self)
    end)
    button:SetScript("OnLeave", function()
        GameTooltip:Hide()
    end)

    return button
end

function MU:UpdateReagentLabel(button)
    local info = button.info
    local label = button.count
    if not info or not self.db.showCounts or self.db.buttonSize < 30 then
        label:SetText("")
        return
    end

    local itemID = info.portal and PORTAL_RUNE or TELEPORT_RUNE
    local count = ItemCount(itemID)
    label:SetText(tostring(count))
    if count <= 0 then
        label:SetTextColor(1, 0.25, 0.25)
    else
        label:SetTextColor(0.85, 0.95, 0.85)
    end
end

function MU:UpdateReagents()
    local bar = self.bar
    if not bar then
        return
    end
    for _, button in ipairs(bar.buttons) do
        if button.info then
            self:UpdateReagentLabel(button)
        end
    end
end

function MU:UpdateButton(button, destination)
    local info = self.known[destination]
    if not info then
        return
    end

    button.dest = destination
    button.info = info

    local primary = info.portal or info.teleport
    button:SetAttribute("type1", "spell")
    button:SetAttribute("spell1", primary.name)

    if info.teleport then
        button:SetAttribute("type2", "spell")
        button:SetAttribute("spell2", info.teleport.name)
    else
        button:SetAttribute("type2", nil)
        button:SetAttribute("spell2", nil)
    end

    local texture = SpellTextureByID(primary.id)
        or (info.teleport and SpellTextureByID(info.teleport.id))
    if texture then
        button.icon:SetTexture(texture)
    end

    button:SetSize(self.db.buttonSize, self.db.buttonSize)
    self:UpdateReagentLabel(button)
    UpdateCooldown(button)
    button:Show()
end

function MU:ShowTooltip(button)
    local destination = button.dest
    local info = button.info
    if not destination or not info then
        return
    end

    GameTooltip:SetOwner(button, "ANCHOR_RIGHT")
    GameTooltip:SetText(destination, 1, 1, 1)

    if info.portal then
        GameTooltip:AddLine("Left-click:  " .. info.portal.name, 0.4, 1, 0.4)
    elseif info.teleport then
        GameTooltip:AddLine("Left-click:  " .. info.teleport.name .. "  (portal not learned)",
            0.75, 0.75, 0.75)
    end
    if info.teleport then
        GameTooltip:AddLine("Right-click: " .. info.teleport.name, 1, 0.82, 0)
    end

    GameTooltip:AddLine(" ")

    if info.portal then
        local count = ItemCount(PORTAL_RUNE)
        local name = ItemName(PORTAL_RUNE, "Rune of Portals")
        if count > 0 then
            GameTooltip:AddLine(("%s: %d"):format(name, count), 0.8, 0.9, 0.8)
        else
            GameTooltip:AddLine(("%s: 0  - out of reagents"):format(name), 1, 0.3, 0.3)
        end
    end
    if info.teleport then
        local count = ItemCount(TELEPORT_RUNE)
        local name = ItemName(TELEPORT_RUNE, "Rune of Teleportation")
        if count > 0 then
            GameTooltip:AddLine(("%s: %d"):format(name, count), 0.8, 0.9, 0.8)
        else
            GameTooltip:AddLine(("%s: 0  - out of reagents"):format(name), 1, 0.3, 0.3)
        end
    end

    GameTooltip:Show()
end

function MU:Layout()
    local bar = self.bar
    if not bar then
        return
    end

    local db = self.db
    local count = #(self.visible or {})
    local size = db.buttonSize
    local gap = db.spacing
    local padding = db.padding
    local gripHeight = db.locked and 0 or GRIP_HEIGHT
    local horizontal = db.orientation ~= "VERTICAL"
    local slots = math.max(count, 1)

    local width, height
    if horizontal then
        width = padding * 2 + slots * size + (slots - 1) * gap
        height = padding * 2 + gripHeight + size
    else
        width = padding * 2 + size
        height = padding * 2 + gripHeight + slots * size + (slots - 1) * gap
    end

    bar:SetSize(width, height)
    if count > 0 then
        bar.bg:Show()
    else
        bar.bg:Hide()
    end

    bar.grip:ClearAllPoints()
    bar.grip:SetPoint("TOPLEFT", bar, "TOPLEFT", 0, 0)
    bar.grip:SetPoint("TOPRIGHT", bar, "TOPRIGHT", 0, 0)
    bar.grip:SetHeight(math.max(gripHeight, 1))
    bar.grip:SetShown(gripHeight > 0)
    bar:EnableMouse(not db.locked)

    for index, button in ipairs(bar.buttons) do
        if index <= count then
            button:SetSize(size, size)
            button:ClearAllPoints()
            if horizontal then
                button:SetPoint("TOPLEFT", bar, "TOPLEFT",
                    padding + (index - 1) * (size + gap), -(padding + gripHeight))
            else
                button:SetPoint("TOPLEFT", bar, "TOPLEFT",
                    padding, -(padding + gripHeight + (index - 1) * (size + gap)))
            end
            button:Show()
        else
            button:Hide()
        end
    end
end

function MU:UpdateVisibility()
    local bar = self.bar
    if not bar then
        return
    end
    if self:InCombat() then
        self.pendingRefresh = true
        return
    end
    local show = not self.db.hidden and self:IsMage() and self.visible and #self.visible > 0
    bar:SetShown(show)
end

function MU:ApplySettings()
    local bar = self.bar
    if not bar then
        return
    end
    if self:InCombat() then
        self.pendingApply = true
        return
    end
    bar:SetScale(self.db.scale)
    self:Layout()
    self:UpdateReagents()
    self:UpdateVisibility()
    if self.RefreshOptions then
        self:RefreshOptions()
    end
    if self.UpdateMinimapButton then
        self:UpdateMinimapButton()
    end
    if self.UpdateQueueStats then
        self:UpdateQueueStats()
    end
end

function MU:SavePosition()
    local bar = self.bar
    local pos = self.pos
    if not bar or not pos then
        return
    end
    local point, _, relativePoint, x, y = bar:GetPoint(1)
    if not point then
        return
    end
    pos.point = point
    pos.relativePoint = relativePoint
    pos.x = x
    pos.y = y
end

function MU:RestorePosition()
    local bar = self.bar
    if not bar then
        return
    end
    if self:InCombat() then
        self.pendingApply = true
        return
    end
    local pos = self.pos or {}
    bar:ClearAllPoints()
    bar:SetPoint(pos.point or "CENTER", UIParent, pos.relativePoint or "CENTER",
        pos.x or 0, pos.y or -180)
end

function MU:ResetPosition()
    if self:InCombat() then
        self:Print("You cannot move the bar while in combat.")
        return false
    end
    if self.pos then
        self.pos.point = nil
        self.pos.relativePoint = nil
        self.pos.x = nil
        self.pos.y = nil
    end
    self:RestorePosition()
    return true
end

function MU:CreateBar()
    if self.bar then
        return self.bar
    end

    local bar = CreateFrame("Frame", "PortalsBar", UIParent)
    bar:SetSize(120, 60)
    bar:SetFrameStrata("MEDIUM")
    bar:SetClampedToScreen(true)
    bar:SetMovable(true)
    bar:EnableMouse(true)
    bar:RegisterForDrag("LeftButton")

    local background = bar:CreateTexture(nil, "BACKGROUND")
    background:SetAllPoints(bar)
    background:SetColorTexture(0, 0, 0, 0.35)
    bar.bg = background

    local grip = CreateFrame("Frame", nil, bar)
    grip:SetHeight(GRIP_HEIGHT)
    grip:SetPoint("TOPLEFT")
    grip:SetPoint("TOPRIGHT")
    grip:EnableMouse(true)
    grip:RegisterForDrag("LeftButton")

    local gripText = grip:CreateFontString(nil, "OVERLAY")
    gripText:SetFont(STANDARD_TEXT_FONT, 10, "OUTLINE")
    gripText:SetPoint("CENTER")
    gripText:SetText("Portals - drag to move")
    gripText:SetTextColor(0.55, 0.8, 1)
    bar.grip = grip

    local function OnDragStart()
        if MU:InCombat() then
            MU:Print("You cannot move the bar while in combat.")
            return
        end
        bar:StartMoving()
    end

    local function OnDragStop()
        bar:StopMovingOrSizing()
        MU:SavePosition()
    end

    bar:SetScript("OnDragStart", OnDragStart)
    bar:SetScript("OnDragStop", OnDragStop)
    grip:SetScript("OnDragStart", OnDragStart)
    grip:SetScript("OnDragStop", OnDragStop)

    bar:SetScript("OnUpdate", function(_, elapsed)
        bar.elapsed = (bar.elapsed or 0) + elapsed
        if bar.elapsed < 0.25 then
            return
        end
        bar.elapsed = 0
        for _, button in ipairs(bar.buttons) do
            if button:IsShown() and button.info then
                UpdateCooldown(button)
            end
        end
    end)

    bar.buttons = {}
    self.bar = bar

    self:RestorePosition()
    self:ApplySettings()

    return bar
end

---------------------------------------------------------------------------
-- Refresh
---------------------------------------------------------------------------

function MU:Refresh()
    if not self.db then
        return
    end
    if self:InCombat() then
        self.pendingRefresh = true
        return
    end

    if not self:IsMage() then
        if not self.classWarned then
            self.classWarned = true
            self:Print("Portals is for mages - the bar is hidden for other classes.")
        end
        if self.bar then
            self.bar:Hide()
        end
        return
    end

    if not self.bar then
        self:CreateBar()
    end
    if not self.nameIndexBuilt then
        self:BuildNameIndex()
    end

    self.known = ScanSpellbook()

    local visible = {}
    for _, destination in ipairs(MU.destinationOrder) do
        if self.known[destination] then
            visible[#visible + 1] = destination
        end
    end
    self.visible = visible

    local bar = self.bar
    for index, destination in ipairs(visible) do
        local button = bar.buttons[index]
        if not button then
            button = CreateButton(index)
            bar.buttons[index] = button
        end
        self:UpdateButton(button, destination)
    end
    for index = #visible + 1, #bar.buttons do
        bar.buttons[index]:Hide()
    end

    self:Layout()
    self:UpdateVisibility()
    if self.RefreshOptions then
        self:RefreshOptions()
    end

    if #visible == 0 and not self.warnedNoSpells then
        self.warnedNoSpells = true
        self:Print("No portal or teleport spells in your spellbook yet. Mages learn Teleport at level 20 and Portals at level 40.")
    end
end

function MU:PrintScan()
    local known = self.known or {}
    local lines = {}
    for _, destination in ipairs(MU.destinationOrder) do
        local info = known[destination]
        if info then
            local modes = {}
            if info.portal then
                modes[#modes + 1] = "portal"
            end
            if info.teleport then
                modes[#modes + 1] = "teleport"
            end
            lines[#lines + 1] = ("%s (%s)"):format(destination, table.concat(modes, " + "))
        end
    end
    if #lines == 0 then
        self:Print("No portal or teleport spells found in the spellbook.")
    else
        self:Print("Detected: " .. table.concat(lines, ", "))
    end
end
