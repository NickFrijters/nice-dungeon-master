# Nice Dungeon Master: how we want to use it

Notes from the owner interview (2026-11-15). This is the plan for what the fork should become.

## Who and where
- Played in person: the owner plus a few friends, and sometimes their son, who is learning English.
- Runs on one Windows PC, with a TV or second screen for the table.
- Only the owner types. Everyone else talks and the owner relays it.
- A session lasts about an hour.
- Pain points: starting is too complicated, and display and sound can be flaky.

## Front door: a web app Main Menu
The web app is where a session starts, not a Claude chat command. The Main Menu offers:
- Start a new campaign or load an existing one
- Manage characters
- Manage settings

Claude plays the DM behind the app.

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
- The web app starting and controlling Claude is a significant change from today, where Claude drives the display. We need to decide how the app launches and talks to Claude.
- Exact visual design of the two layouts (colors, sizes, how the left panels collapse).
- Whether Experience is set per character only, or can also apply to the whole session.
