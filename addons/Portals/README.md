# Portals

A mage portal/teleport bar for **WoW Classic Era** (the permanent "Classic Forever" realms, 1.15.x) and **TBC Classic / Anniversary** (2.5.x). One folder, one code base, both clients. It also watches city chat for portal requests, queues buyers who join your party, and tracks session tips.

Left-click a destination to cast the **portal**. Right-click it to cast the **teleport**. The bar only shows destinations your character has actually trained, so Horde mages never see Stormwind and Classic Era mages never see Shattrath.

```
┌──────────────────────────────────────────────┐
│ Portals - drag to move                   │
├──────┬──────┬──────┬──────┬──────┬───────────┤
│  SW  │  IF  │ Darn │ Exo  │ Shat │           │
│  25  │  25  │  25  │  25  │  25  │           │
└──────┴──────┴──────┴──────┴──────┴───────────┘
```

## Install

Copy the `Portals` folder into the `Interface/AddOns` folder of each client you play:

| Client | Path |
| --- | --- |
| Classic Era / "Classic Forever" | `World of Warcraft/_classic_era_/Interface/AddOns/Portals/` |
| TBC Classic / Anniversary | `World of Warcraft/_classic_/Interface/AddOns/Portals/` |

The folder must be named `Portals` and contain `Portals.toc` directly inside it. Restart the game (or `/reload`) and enable the addon in the AddOns list.

After a major patch the client may flag the addon as out of date. Either tick **Load out of date AddOns** or update the numbers on the `## Interface:` line in `Portals.toc` (`/dump select(4, GetBuildInfo())` prints the current one).

## Usage

- **Left-click** - cast the portal to that city (falls back to the teleport if you have not trained the portal yet).
- **Right-click** - cast the teleport to that city.
- **Drag the top strip** - move the bar while it is unlocked.
- The small number in the corner is your stock of the relevant rune: **Rune of Portals** for portal buttons (left-click) or **Rune of Teleportation** when only the teleport is known. It turns red at zero.
- The tooltip shows both spell names and the live reagent count.
- Cooldowns are shown with the standard sweep.

There is a **minimap button** too: left-click opens the settings window, and dragging moves it around the minimap ring. **Right-click** opens a menu with your open/closed business status, the portal queue, settings, and shortcuts for showing or locking the bar. The icon turns red while you are closed for business. You can turn the button off in the settings window or with `/portal minimap`.

The bar is rebuilt from your spellbook when you learn a spell, so it is always in sync with what you can actually cast. It is hidden for non-mage characters and before level 20 (when mages learn their first teleport).

## Smart portal detection

Portals can watch **Say, Yell, General, Trade and LookingForGroup** for portal requests and answer them automatically. It understands the usual shorthands (`SW`, `IF`, `DARN`, `ORG`, `UC`, `TB`, `SM`, `SHATT`) and arrow notation (`SW -> DARN`).

- `LF PORT SW TO DARN` - origin **Stormwind**, destination **Darnassus**
- `WTB portal to Darn` - destination **Darnassus**, no origin stated
- `Darn portal pls, I'm in SW` - origin **Stormwind**, destination **Darnassus**
- `LF port from Darn to SW` - origin **Darnassus**, destination **Stormwind**

If the request names an origin, it is compared with your real zone (`GetRealZoneText`). In General and Trade the channel's own zone (`Trade - Stormwind`) is used when the origin is not stated, so a plain `WTB portal to Darn` is enough while you stand in Stormwind. LookingForGroup is a global channel, so an unstated origin is ignored there - otherwise you would invite people who cannot reach your portal.

When everything matches, it whispers the buyer with your template and invites them:

```
[Buyer] LF PORT SW TO DARN
Portals: Portal request from Buyer: Stormwind -> Darnassus (whispered + invited)
```

Safeguards:

- Whisper limits: 5 minutes per player, 10 seconds globally, at most 5 per minute.
- Invites: sent for every matched request (60 second re-invite cooldown per player), so several buyers can join the queue at once.
- Skips sellers (`WTS`, `selling`, `LFM`), your own messages, players already in your group, ignored players, and portals you have not trained.
- Never answers a request whose destination is the city you are already standing in.
- `to` / `->` mark the destination; `from` / `in` / `at` mark the origin.

### Location, TOS, and what addons can actually see

Reading chat and calling `SendChatMessage` / `InviteUnit` is normal addon API. Addons **cannot** read another player's zone from a chat message, though. The origin therefore comes from what the player types and, in General/Trade, from the channel's zone name. No position is tracked, stored, or transmitted; the addon only reacts to messages that already ask for a portal.

Automated whispering still falls under Blizzard's spam rules, which is why the rate limits are deliberately conservative. Use it to answer explicit requests, not to advertise.

Open or close for business from the minimap right-click menu, the **Open for business** checkbox in settings, or `/portal sales`. Closing pauses detection (no new whispers or invites) but leaves the bar and the current queue alone; the minimap icon turns red. `/portal test LF PORT SW TO DARN` shows how a line would be parsed without whispering anyone.

## Portal queue and session stats

`/portals show` or `/portal show` (or the **Portal queue** button in settings) opens the queue window.

- The queue only contains **party members**. When a request is detected the buyer is auto-invited; their row appears as soon as they accept and join your group. Someone who is already in your group is queued immediately, without a whisper or invite.
- Each row shows the player and level, the destination, the distance in yards, a secure portal button for the correct city, a **Trade** button, and an **X** to remove the request.
- **Trade** is grey while they are out of range and becomes clickable inside trade range (about 11 yards). Clicking it opens a trade window with that party member.
- If someone leaves the group, their row is removed.
- Distance and trade need the buyer in your party because addons cannot query a random player's position. In instances, where exact distance is restricted, the row shows a range band instead.

Session statistics (bottom of the window, reset on `/reload` or with `/portals resetsession`):

| Stat | Meaning |
| --- | --- |
| **Portals cast** | Every successful portal cast, from the queue or the bar |
| **Players served** | Unique players whose queue portal button was used |
| **Tips this session** | Gold offered to you in a completed trade with someone who requested a portal |

| Command | Effect |
| --- | --- |
| `/portals show` / `/portals open` | Open the queue and statistics |
| `/portals hide` / `/portals close` | Close the queue window |
| `/portals clear` | Remove the queue and any pending invites |
| `/portals remove <name>` | Remove one player |
| `/portals resetsession` | Reset portals, players served and tips |

These commands only control the queue window; they never show or hide the portal bar. Use `/portal bar on` and `/portal bar off` for the bar. The queue window contains secure cast buttons, so it cannot be opened or closed during combat.

## Commands

`/portal` on its own opens the settings window. `/portal help` prints the command list.

| Command | Effect |
| --- | --- |
| `/portal` | Open the settings window |
| `/portal show` / `/portal queue` | Open the portal queue and session stats |
| `/portal hide` / `/portal close` | Close the portal queue window |
| `/portal bar [on\|off]` | Show, hide, or toggle the portal bar |
| `/portal lock` / `/portal unlock` | Lock or unlock the bar (unlocked shows the drag strip) |
| `/portal vertical` / `/portal horizontal` | Bar orientation |
| `/portal size <20-80>` | Button size in pixels |
| `/portal spacing <0-20>` | Gap between buttons |
| `/portal scale <0.5-2>` | Overall bar scale |
| `/portal counts` | Toggle reagent counts |
| `/portal minimap` | Toggle the minimap button |
| `/portal sales` / `/portal business` | Open or close for business (pauses detection) |
| `/portal message [text]` | Show or set the auto-whisper template |
| `/portal channel <say\|yell\|general\|trade\|lfg> on\|off` | Enable or disable a watched channel |
| `/portal test <message>` | Parse a sample message without acting |
| `/portal reset` | Reset the bar position |
| `/portal scan` | Print the destinations detected in your spellbook |

The whisper template supports `{name}`, `{origin}` and `{destination}`, for example:

```
/portal message Hi {name}! Portal from {origin} to {destination} is yours - invite accepted.
```

Settings and the minimap-button angle are account-wide; the bar position is saved per character.

## Files

| File | Purpose |
| --- | --- |
| `Portals.toc` | Manifest, with comma-separated `## Interface:` versions for Era and TBC |
| `Core.lua` | Saved variables, defaults, events, slash commands |
| `PortalBar.lua` | Destination data, spellbook scan, secure buttons, layout, tooltips |
| `Sales.lua` | Chat watcher, request parser, whisper + invite, rate limits |
| `Queue.lua` | Party-only portal queue, distance/trade UI, session stats, tips |
| `Minimap.lua` | Draggable minimap button |
| `Options.lua` | Settings window |
| `dev/smoke_test.lua` | Offline test harness (mock WoW API); not loaded by the game |

The `## Interface:` line lists 1.15.7-1.15.9 and 2.5.4-2.5.6, so a single unsuffixed TOC works on both clients. If you later need version-specific code, the client also understands `Portals_Vanilla.toc` and `Portals_TBC.toc` as overrides.

## Design notes

- Casting goes through `SecureActionButtonTemplate` (`type1`/`spell1`, `type2`/`spell2`), so clicks are hardware events and the addon never calls a protected cast function.
- Anything that touches secure frames (rebuilding buttons, changing layout, moving the bar) is deferred while `InCombatLockdown()` is true and replayed on `PLAYER_REGEN_ENABLED`.
- Spells are matched by **spell ID** first, so localized clients work; a localized-name fallback covers clients that do not expose spell IDs from the spellbook.
- Chat detection is event-driven (`CHAT_MSG_SAY`, `CHAT_MSG_YELL`, `CHAT_MSG_CHANNEL`); it stores no chat history and keeps rate-limit state in memory only.

## Roadmap

Still planned for portal sales:

1. A configurable gold price per city, included in the whisper and shown on the bar.
2. Per-buyer tip history and a rolling total across sessions.
3. Optional "portal open" announcement to party chat.
4. Handle direct whispers (`/w mage portal to SW?`) the same way as channel messages.

The modules are split so those can land without touching the bar code.

## Development

```
cd addons/Portals
luac -p Core.lua PortalBar.lua Sales.lua Queue.lua Minimap.lua Options.lua
lua5.4 dev/smoke_test.lua
```

The smoke test stubs the WoW API, loads every addon file and checks the spellbook scan, secure attributes, reagent labels, layout, slash commands, combat deferral, the minimap button, the options window, request parsing, channel filtering, location matching, rate limits, invite promotion, queue pruning, distances, trade buttons, portal-cast tracking and tips.
