# Nice Dungeon Master: how we want to use it

Notes from the owner interview (2026-11-15). This is the plan for what the fork should become.

## Who and where
- Played in person: the owner plus a few friends, and sometimes their son, who is learning English.
- Runs on one Windows PC, with a TV or second screen for the table.
- Only the owner types. Everyone else talks and the owner relays it.
- A session lasts about an hour.
- Pain points: starting is too complicated, and display and sound can be flaky.

## Front door: a web app Main Menu
You start the web app through Claude (for example with `/dm:dnd`). From that moment on, the web app has all the controls; you don't go back to the Claude chat during play. The Main Menu offers:
- Start a new campaign or load an existing one
- Manage characters
- Manage settings

Claude keeps running as the DM behind the app. The app sends your choices and actions to Claude, and Claude sends back narration and state. The existing autorun mode already works this way: the display collects player input in a queue and Claude waits for it between turns. That is the starting point for the new design.

## Settings (from day one)
| Setting | Options | Effect |
|---|---|---|
| Flavour and detail | low / medium / high | Caps each DM step at 100 / 250 / 500 words |
| Combat | none / low / medium / high | How many combat encounters and situations occur. Low is very little, high is murder-hobo with almost no roleplay. A published adventure sits at medium |
| Explain in Dutch | on / off | Adds a short, simple Dutch summary under the full English text. English is never shortened or replaced |
| Ruleset | 2014 / 2024 | Per campaign |
| Narration audio | on / off | Spoken narration |

## Characters
Each character has an **Experience** option:
- **New**: simple choices and clear explanations
- **Experienced**: full character options and summarized explanations

## Two screen states
A running adventure is either **Roleplay** or **Combat**. The screen switches automatically when Claude starts or ends combat.

### Roleplay layout
- **Left:** summary info panels
  - Characters: name, race, class, HP, XP
  - All NPCs in the current setting
  - All factions
  - All quests
- **Center:** the narration text
- **Right:** always-visible images of the current situation (scene, setting, active monsters or characters)
- Always visible: party strip, current location and time, active quest or goal
- NPC dialogue is shown as a portrait with a speech bubble

### Combat layout
- **Dominant:** the battle map
  - Published maps are used when an adventure provides them. Otherwise Claude generates a simple, hand-drawn-looking grid from the scene description.
  - The grid has numbered columns and rows, so players can say exactly where to move.
  - Tokens can be moved by dragging on screen or by telling Claude.
- **Around the map:** initiative order bar, party and enemy HP, narration of the action, and the dice roll display

## Open questions
- Menu actions (load campaign, change settings, manage characters) must reach Claude through the input queue. We need to decide which of these the app handles itself and which it passes to Claude.
- Exact visual design of the two layouts (colors, sizes, how the left panels collapse).
- Whether Experience is set per character only, or can also apply to the whole session.

## Manual additions done by Nick in this .md file, read this as "next things" or the backlog.
## -- if I make changes here, always automatically include in the next commit.
## -- remove items from below here once they are built
- Always use local TTS, never the remote API anymore
- 100% of the time I want to play on LAN mode, so that doesn't need to be a question anymore
- Convert the TV Mode / PC Mode buttons to a TV/PC toggle. The TV mode has no inputs but is a 100% exact mirror (real-time, include typing) of the PC mode. Default mode is TV mode and if one session clicks "PC mode" then all other sessions automatically change to TV mode.
- Clearer visualization of character sheets, needs investigation and interviewing
- Performance, waiting for the DM should never take more than 5 seconds (this probably needs data management improvements)

## Status
- **v1 (done):** `/dm:dnd menu` starts the display. The Main Menu lists your campaigns, and clicking Load hands the request to Claude, who loads it and switches the browser to the play screen. "Manage characters" and "Settings" are placeholders.
- **Next:** the settings from the table above, then the Roleplay layout, then Combat.
