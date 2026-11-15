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
## -- remove items from this list once they are built
- PERSISTENCY: When opening a campaign from the main menu, always start with a recap instead of showing the whole history.
- The main menu doesn't have an option to start a new Campaign. (it should be able to let Claude create one, or provide a PDF with a (published) adventure.
- INTERFACE: The text font that's currently used for narration is flavourful and clear, I want to use that font as the only font used throughout the whole app (do I? interview me first about the consequences)
- INTERFACE: Clearer visualization of character sheets, needs investigation and interviewing
- BUG: when creating a new campaign, the dropdowns for tone, magic level, setting, danger levels and Ruleset are empty. When clicking "Create campaign" it returns with "That did not work. Try again."
- INTERFACE: Build the roleplay screen
- INTERFACE: Build the combat screen
- INTERFACE: Screenshot all interfaces and look for graphical inconsistencies or things that don't look nice - fix them (interview me where you think that's necessary).
- INTERFACE: Interview me about the following: It should not be possible that text scrolls out of the screen without the players confirming that they've read it, or the voice being played. Now on large texts, not everything is visible on TV - which is an issue.
- CLEANUP: No "PHONE" screen will ever be used, remove that function from the screen and code completely.
- PERSISTENCY: After every DM input, and after every players-submit, the game state should be processed and saved. So when the browser closes or the app goes back to the main menu, nothing is lost.
- TTS: Please do further research of we can use another one TTS model (requirements: super fast) for a more realistic English voice, preferably a voice that would be great for a DM. Try Kokoro-82M (hexgrad/Kokoro-82M) first.
- INTERFACE: Interview me about the visualization of the text (being narrated on screen) in sync with the TTS.
- PERFORMANCE: Waiting for the DM should never take more than 10 seconds (this probably needs data management improvements). That means that if we need larger pieces of text which take long to generate, cut it into pieces and continue working while the characters read or hear the first part.
- PERSISTENCY: Do an extremely thorough analysis on the continuity and persistent memory of our data keeping and the DM narration. For example: If a character destroys the door of a local inn, that should have effects when the same character visits the inn again 50 sessions later. The inn keeper won't forget that!
- PERSISTENCY: Do a full analysis on adventure/campaign persistency. For example: Making sure that we're aiming towards (but not bound to) the general adventure/campaign setting. So that we don't drift off further from it with every session.
- PERFORMANCE: Persistent image saving and rendering. Keep a very thorough database of previously downloaded images. Save all provided campaign/adventure images and all self-downloaded images from internet. To be able to use those in all future campaigns or on request.
- CLEANUP: We will always use the Vellum style instead of the Editorial style. So remove the Editorial style from the interface and code completely.
- PRETTIFY: The "DM Help" info block that pops up, and the "Save and Exit" error block that pops up (can you check what I mean) doesn't match the rest of the interface style.
- FLAVOUR: Add ambient sounds to match with the background visuals.
- INTERFACE: Build the character creation screen

## Status
- **v1 (done):** `/dm:dnd menu` starts the display. The Main Menu lists your campaigns, and clicking Load hands the request to Claude, who loads it and switches the browser to the play screen. "Manage characters" and "Settings" are placeholders.
- **Next:** the settings from the table above, then the Roleplay layout, then Combat.
