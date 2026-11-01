--[[---------------------------------------------------------------------------
    Portals - Settings window

    Opened by /portal or by left-clicking the minimap button. Deliberately
    built from plain frames and UIPanelButtonTemplate only, so it does not
    depend on BackdropTemplate or the newer options widgets that differ
    between Classic Era and TBC.
-----------------------------------------------------------------------------]]

local ADDON_NAME, ns = ...

local MU = ns

local PANEL_WIDTH  = 340
local PANEL_HEIGHT = 540

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

local function CreatePanelButton(parent, text, width, height)
    local button = CreateFrame("Button", nil, parent, "UIPanelButtonTemplate")
    button:SetSize(width or 24, height or 22)
    button:SetText(text)
    return button
end

local function CreateCheck(parent, label, x, y, onChange)
    local check = CreateFrame("Button", nil, parent)
    check:SetSize(18, 18)
    check:SetPoint("TOPLEFT", parent, "TOPLEFT", x, y)

    local box = check:CreateTexture(nil, "BACKGROUND")
    box:SetAllPoints()
    box:SetColorTexture(0.09, 0.1, 0.14, 1)

    local border = check:CreateTexture(nil, "BORDER")
    border:SetPoint("TOPLEFT", -1, 1)
    border:SetPoint("BOTTOMRIGHT", 1, -1)
    border:SetColorTexture(0.45, 0.5, 0.6, 1)

    local mark = check:CreateTexture(nil, "ARTWORK")
    mark:SetTexture("Interface\\Buttons\\UI-CheckBox-Check")
    mark:SetAllPoints()
    check.mark = mark

    local text = parent:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    text:SetPoint("LEFT", check, "RIGHT", 8, 0)
    text:SetText(label)

    function check:SetChecked(value)
        self.checked = value and true or false
        if self.checked then
            self.mark:Show()
        else
            self.mark:Hide()
        end
    end

    function check:GetChecked()
        return self.checked
    end

    check:SetScript("OnClick", function(self)
        self:SetChecked(not self.checked)
        if onChange then
            onChange(self.checked)
        end
    end)

    check:SetChecked(false)
    return check
end

local function CreateStepper(parent, label, y, min, max, step, getValue, setValue, formatValue)
    local text = parent:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    text:SetPoint("TOPLEFT", parent, "TOPLEFT", 20, y)
    text:SetText(label)

    local minus = CreatePanelButton(parent, "-", 24, 22)
    minus:SetPoint("LEFT", text, "RIGHT", 14, 0)

    local value = parent:CreateFontString(nil, "OVERLAY", "GameFontHighlight")
    value:SetPoint("LEFT", minus, "RIGHT", 4, 0)
    value:SetWidth(48)
    value:SetJustifyH("CENTER")
    value:SetText(formatValue(getValue()))

    local plus = CreatePanelButton(parent, "+", 24, 22)
    plus:SetPoint("LEFT", value, "RIGHT", 4, 0)

    local function apply(delta)
        local newValue = getValue() + delta
        if newValue < min then
            newValue = min
        end
        if newValue > max then
            newValue = max
        end
        local steps = math.floor((newValue - min) / step + 0.5)
        newValue = min + steps * step
        setValue(newValue)
        value:SetText(formatValue(newValue))
    end

    minus:SetScript("OnClick", function() apply(-step) end)
    plus:SetScript("OnClick", function() apply(step) end)

    return {
        minus = minus,
        plus = plus,
        value = value,
        display = function(newValue) value:SetText(formatValue(newValue)) end,
    }
end

function MU:RefreshOptions()
    local panel = self.options
    if not panel then
        return
    end

    local db = self.db
    panel.checks.showBar:SetChecked(not db.hidden)
    panel.checks.lock:SetChecked(db.locked)
    panel.checks.counts:SetChecked(db.showCounts)
    panel.checks.minimap:SetChecked(db.showMinimap)
    panel.checks.sales:SetChecked(db.salesEnabled)
    panel.checks.whisper:SetChecked(db.autoWhisper)
    panel.checks.invite:SetChecked(db.autoInvite)
    panel.checks.onlyMyCity:SetChecked(db.onlyMyCity)

    panel.orientation:SetText(db.orientation == "VERTICAL" and "Vertical" or "Horizontal")
    panel.size.display(db.buttonSize)
    panel.spacing.display(db.spacing)
    panel.scale.display(db.scale)

    local detected = self.visible or {}
    if #detected > 0 then
        panel.status:SetText("Detected: " .. table.concat(detected, ", "))
    else
        panel.status:SetText("No portal or teleport spells detected yet.")
    end

    if not db.salesEnabled then
        panel.salesStatus:SetText("|cffff4040Closed for business.|r")
    else
        local actions = {}
        if db.autoWhisper then
            actions[#actions + 1] = "whisper"
        end
        if db.autoInvite then
            actions[#actions + 1] = "invite"
        end
        local what = #actions > 0 and table.concat(actions, " + ") or "notify only"
        panel.salesStatus:SetText(("|cff40ff40Open for business|r: %s%s")
            :format(what, db.onlyMyCity and ", local players only" or ""))
    end
end

local function SectionLabel(parent, text, y)
    local label = parent:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    label:SetPoint("TOPLEFT", parent, "TOPLEFT", 20, y)
    label:SetText(text)
    label:SetTextColor(0.55, 0.75, 1)
    return label
end

function MU:CreateOptions()
    if self.options then
        return self.options
    end

    local panel = CreateFrame("Frame", "PortalsOptions", UIParent)
    panel:SetSize(PANEL_WIDTH, PANEL_HEIGHT)
    panel:SetPoint("CENTER", UIParent, "CENTER", 0, 40)
    panel:SetFrameStrata("DIALOG")
    panel:SetToplevel(true)
    panel:SetClampedToScreen(true)
    panel:SetMovable(true)
    panel:EnableMouse(true)
    panel:RegisterForDrag("LeftButton")
    panel:SetScript("OnDragStart", panel.StartMoving)
    panel:SetScript("OnDragStop", panel.StopMovingOrSizing)
    panel:Hide()

    local background = panel:CreateTexture(nil, "BACKGROUND")
    background:SetAllPoints()
    background:SetColorTexture(0.035, 0.04, 0.06, 0.96)
    panel.bg = background

    AddBorder(panel, 0.35, 0.4, 0.5)

    local title = panel:CreateFontString(nil, "OVERLAY", "GameFontNormalLarge")
    title:SetPoint("TOP", panel, "TOP", 0, -12)
    title:SetText("Portals")

    local subtitle = panel:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    subtitle:SetPoint("TOP", title, "BOTTOM", 0, -2)
    subtitle:SetText("Left-click a portal  -  right-click a teleport")
    subtitle:SetTextColor(0.65, 0.7, 0.8)

    local close = CreateFrame("Button", nil, panel, "UIPanelCloseButton")
    close:SetPoint("TOPRIGHT", panel, "TOPRIGHT", -4, -4)
    close:SetScript("OnClick", function()
        panel:Hide()
    end)

    SectionLabel(panel, "PORTAL BAR", -52)

    local checks = {}
    checks.showBar = CreateCheck(panel, "Show the portal bar", 20, -70, function(checked)
        MU:SetSetting("hidden", not checked)
    end)
    checks.lock = CreateCheck(panel, "Lock the bar in place", 20, -94, function(checked)
        MU:SetSetting("locked", checked)
    end)
    checks.counts = CreateCheck(panel, "Show rune counts on buttons", 20, -118, function(checked)
        MU:SetSetting("showCounts", checked)
    end)
    checks.minimap = CreateCheck(panel, "Show the minimap button", 20, -142, function(checked)
        MU:SetSetting("showMinimap", checked)
    end)

    local divider = panel:CreateTexture(nil, "ARTWORK")
    divider:SetPoint("TOPLEFT", panel, "TOPLEFT", 16, -166)
    divider:SetPoint("TOPRIGHT", panel, "TOPRIGHT", -16, -166)
    divider:SetHeight(1)
    divider:SetColorTexture(0.3, 0.35, 0.45, 0.8)

    SectionLabel(panel, "PORTAL SALES", -178)

    checks.sales = CreateCheck(panel, "Open for business", 20, -196, function(checked)
        MU:SetSetting("salesEnabled", checked)
    end)
    checks.whisper = CreateCheck(panel, "Auto-whisper the buyer", 20, -220, function(checked)
        MU:SetSetting("autoWhisper", checked)
    end)
    checks.invite = CreateCheck(panel, "Auto-invite the buyer", 20, -244, function(checked)
        MU:SetSetting("autoInvite", checked)
    end)
    checks.onlyMyCity = CreateCheck(panel, "Only when they are in my city", 20, -268, function(checked)
        MU:SetSetting("onlyMyCity", checked)
    end)

    local divider2 = panel:CreateTexture(nil, "ARTWORK")
    divider2:SetPoint("TOPLEFT", panel, "TOPLEFT", 16, -292)
    divider2:SetPoint("TOPRIGHT", panel, "TOPRIGHT", -16, -292)
    divider2:SetHeight(1)
    divider2:SetColorTexture(0.3, 0.35, 0.45, 0.8)

    local orientationLabel = panel:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    orientationLabel:SetPoint("TOPLEFT", panel, "TOPLEFT", 20, -306)
    orientationLabel:SetText("Orientation")

    local orientation = CreatePanelButton(panel, "", 110, 22)
    orientation:SetPoint("LEFT", orientationLabel, "RIGHT", 14, 0)
    orientation:SetScript("OnClick", function()
        MU:SetSetting("orientation",
            MU.db.orientation == "VERTICAL" and "HORIZONTAL" or "VERTICAL")
    end)

    local size = CreateStepper(panel, "Button size", -338, 20, 80, 2,
        function() return MU.db.buttonSize end,
        function(newValue) MU:SetSetting("buttonSize", newValue) end,
        function(newValue) return tostring(newValue) end)

    local spacing = CreateStepper(panel, "Button spacing", -370, 0, 20, 1,
        function() return MU.db.spacing end,
        function(newValue) MU:SetSetting("spacing", newValue) end,
        function(newValue) return tostring(newValue) end)

    local scale = CreateStepper(panel, "Bar scale", -402, 0.5, 2, 0.05,
        function() return MU.db.scale end,
        function(newValue) MU:SetSetting("scale", newValue) end,
        function(newValue) return ("%.2f"):format(newValue) end)

    local reset = CreatePanelButton(panel, "Reset bar position", 170, 24)
    reset:SetPoint("TOPLEFT", panel, "TOPLEFT", 20, -436)
    reset:SetScript("OnClick", function()
        MU:ResetPosition()
        MU:RefreshOptions()
    end)

    local queueButton = CreatePanelButton(panel, "Portal queue", 122, 24)
    queueButton:SetPoint("TOPLEFT", panel, "TOPLEFT", 198, -436)
    queueButton:SetScript("OnClick", function()
        if MU.OpenQueue then
            MU:OpenQueue()
        end
    end)

    local status = panel:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    status:SetPoint("TOPLEFT", panel, "TOPLEFT", 20, -468)
    status:SetWidth(PANEL_WIDTH - 40)
    status:SetJustifyH("LEFT")
    status:SetTextColor(0.7, 0.75, 0.85)
    status:SetText("")

    local salesStatus = panel:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    salesStatus:SetPoint("TOPLEFT", panel, "TOPLEFT", 20, -502)
    salesStatus:SetWidth(PANEL_WIDTH - 40)
    salesStatus:SetJustifyH("LEFT")
    salesStatus:SetTextColor(0.6, 0.85, 0.65)
    salesStatus:SetText("")

    panel.checks = checks
    panel.orientation = orientation
    panel.size = size
    panel.spacing = spacing
    panel.scale = scale
    panel.status = status
    panel.salesStatus = salesStatus

    panel:SetScript("OnShow", function()
        MU:RefreshOptions()
    end)

    if UISpecialFrames then
        local listed = false
        for _, name in ipairs(UISpecialFrames) do
            if name == "PortalsOptions" then
                listed = true
                break
            end
        end
        if not listed then
            table.insert(UISpecialFrames, "PortalsOptions")
        end
    end

    self.options = panel
    self:RefreshOptions()

    return panel
end

function MU:OpenOptions()
    self:CreateOptions()
    self:RefreshOptions()
    self.options:Show()
end

function MU:ToggleOptions()
    self:CreateOptions()
    if self.options:IsShown() then
        self.options:Hide()
    else
        self:RefreshOptions()
        self.options:Show()
    end
end
