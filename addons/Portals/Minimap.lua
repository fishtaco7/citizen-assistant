--[[---------------------------------------------------------------------------
    Portals - Minimap button

    A draggable button around the minimap:
      left-click  -> open the settings window
      right-click -> menu: open/closed for business, queue, settings, bar toggles
      drag        -> move it around the minimap ring

    No libraries: the icon + tracking border are the same textures the default
    UI uses for the tracking button, so it looks native on both clients.
-----------------------------------------------------------------------------]]

local ADDON_NAME, ns = ...

local MU = ns

local ICON   = "Interface\\Icons\\Spell_Arcane_PortalStormwind"
local BORDER = "Interface\\Minimap\\MiniMap-TrackingBorder"
local RADIUS = 80

-- math.atan2 exists in WoW's Lua 5.1; Lua 5.3+ folds it into math.atan(y, x).
local atan2 = math.atan2 or function(y, x)
    return math.atan(y, x)
end

function MU:UpdateMinimapButton()
    local button = self.minimapButton
    local db = self.db
    if not button or not db then
        return
    end

    local angle = math.rad(db.minimapAngle or 220)
    local x = math.cos(angle) * RADIUS
    local y = math.sin(angle) * RADIUS

    button:ClearAllPoints()
    button:SetPoint("CENTER", Minimap, "CENTER", x, y)

    if db.salesEnabled then
        button.icon:SetDesaturated(false)
        button.icon:SetVertexColor(1, 1, 1)
    else
        button.icon:SetDesaturated(true)
        button.icon:SetVertexColor(1, 0.45, 0.45)
    end

    if db.showMinimap then
        button:Show()
    else
        button:Hide()
    end
end

function MU:ShowMinimapMenu()
    if not EasyMenu then
        self:Print("The dropdown menu is unavailable in this client.")
        return
    end

    if not self.minimapMenu then
        self.minimapMenu = CreateFrame("Frame", "PortalsMinimapMenu", UIParent,
            "UIDropDownMenuTemplate")
    end

    local open = self.db.salesEnabled
    EasyMenu({
        { text = "Portals", isTitle = true, notCheckable = true },
        {
            text = open and "|cff40ff40Open for business|r" or "|cffff4040Closed for business|r",
            notCheckable = true,
            func = function()
                MU:SetSetting("salesEnabled", not MU.db.salesEnabled)
            end,
        },
        {
            text = "Portal queue & stats",
            notCheckable = true,
            func = function()
                MU:OpenQueue()
            end,
        },
        {
            text = "Settings",
            notCheckable = true,
            func = function()
                MU:OpenOptions()
            end,
        },
        {
            text = "Show portal bar",
            checked = not self.db.hidden,
            isNotRadio = true,
            func = function()
                MU:SetSetting("hidden", not MU.db.hidden)
            end,
        },
        {
            text = "Lock the bar",
            checked = self.db.locked,
            isNotRadio = true,
            func = function()
                MU:SetSetting("locked", not MU.db.locked)
            end,
        },
    }, self.minimapMenu, "cursor", 0, 0, "MENU")
end

function MU:CreateMinimapButton()
    if self.minimapButton then
        return self.minimapButton
    end

    local button = CreateFrame("Button", "PortalsMinimapButton", Minimap)
    button:SetSize(32, 32)
    button:SetFrameStrata("MEDIUM")
    button:SetFrameLevel(8)
    button:SetMovable(true)
    button:EnableMouse(true)
    button:RegisterForClicks("LeftButtonUp", "RightButtonUp")
    button:RegisterForDrag("LeftButton")

    local icon = button:CreateTexture(nil, "BACKGROUND")
    icon:SetTexture(ICON)
    icon:SetSize(20, 20)
    icon:SetPoint("CENTER")
    icon:SetTexCoord(0.05, 0.95, 0.05, 0.95)
    button.icon = icon

    local border = button:CreateTexture(nil, "OVERLAY")
    border:SetTexture(BORDER)
    border:SetSize(54, 54)
    border:SetPoint("TOPLEFT")
    button.border = border

    local highlight = button:CreateTexture(nil, "HIGHLIGHT")
    highlight:SetTexture(ICON)
    highlight:SetSize(20, 20)
    highlight:SetPoint("CENTER")
    highlight:SetTexCoord(0.05, 0.95, 0.05, 0.95)
    highlight:SetBlendMode("ADD")
    highlight:SetAlpha(0.35)
    button.highlight = highlight

    button:SetScript("OnClick", function(_, mouseButton)
        if mouseButton == "RightButton" then
            MU:ShowMinimapMenu()
        else
            MU:ToggleOptions()
        end
    end)

    button:SetScript("OnEnter", function(self)
        GameTooltip:SetOwner(self, "ANCHOR_LEFT")
        GameTooltip:SetText("Portals", 1, 1, 1)
        if MU.db and MU.db.salesEnabled then
            GameTooltip:AddLine("Status: open for business", 0.4, 1, 0.4)
        else
            GameTooltip:AddLine("Status: closed for business", 1, 0.4, 0.4)
        end
        GameTooltip:AddLine("Left-click: settings", 0.4, 1, 0.4)
        GameTooltip:AddLine("Right-click: menu", 1, 0.82, 0)
        GameTooltip:AddLine("Drag: move this button", 0.7, 0.7, 0.7)
        GameTooltip:Show()
    end)
    button:SetScript("OnLeave", function()
        GameTooltip:Hide()
    end)

    button:SetScript("OnDragStart", function(self)
        self:SetScript("OnUpdate", function()
            local scale = Minimap:GetEffectiveScale()
            if not scale or scale == 0 then
                return
            end
            local cursorX, cursorY = GetCursorPosition()
            local centerX, centerY = Minimap:GetCenter()
            if not cursorX or not centerX then
                return
            end
            cursorX, cursorY = cursorX / scale, cursorY / scale
            MU.db.minimapAngle = math.deg(atan2(cursorY - centerY, cursorX - centerX))
            MU:UpdateMinimapButton()
        end)
    end)

    button:SetScript("OnDragStop", function(self)
        self:SetScript("OnUpdate", nil)
        if MU.db.minimapAngle == nil then
            MU.db.minimapAngle = 220
        end
    end)

    self.minimapButton = button
    self:UpdateMinimapButton()

    return button
end
