## Backburn v0.3.1 — planning time and larger maps

- Normal speed now advances one simulation second per real second, six times slower than v0.3.0.
- Every new incident opens paused so you can inspect the terrain and queue orders before resuming.
- Tree positions stay fixed when other trees burn; surviving trees no longer reshuffle.
- Desktop maps are twice as wide and tall, preserving the original geography and objectives.
- The camera starts at tactical zoom near staging and the fire. WASD, wheel zoom, Home and the minimap navigate the larger map.
- Civilian markers are now visible on the minimap. Old saves retain their original map dimensions.

## Backburn v0.3.0 — first desktop demo

An original top-down wildfire tactics game, built from the supplied v0.2 simulation.

- Four playable incidents: Prairie Fire, Stranded Hikers, Refinery Row, Wall of Fire.
- Mission room, pre-mission briefings, field guide, clickable unit roster and dispatch.
- Original terrain treatment, trees, buildings, crew/vehicle/aircraft sprites and fire animation.
- Pause and plan, 1x/3x/8x speeds, camera zoom/pan, minimap, overlays, queued commands.
- Helicopter rescues, line drops, suppression, firebreaks, resource budgets and weather events.
- Sandbox mode with terrain/fire tools and adjustable wind.
- Atomic quicksaves, autosaves, personal best scores and synthesized UI sounds.
- Standalone Windows installer/launcher with GitHub updates and offline play.
- Fixes for premature containment before timed ignitions and replay commands at the final tick.

### Install

Download **Backburn-Setup.exe**, open it, and click **Install Backburn**. The installer
adds a Desktop and Start Menu shortcut without needing administrator access. Open the
same shortcut later and choose **Install update** when a release is available.

Alternatively, extract **Backburn-Windows-x64.zip** and run **Backburn.exe** for portable
play. The portable game does not include the updater; use the Setup download for that.

### First mission

Select an incident and click Deploy. Space pauses at any time. Select a unit, then
right-click to act; right-drag for firebreaks and aircraft drops. Shift queues orders.
In Stranded Hikers, select the helicopter and click each hiker, using Shift to queue;
right-click in the green rescue zone to unload. F6 saves and F7 loads.

### Limits

Windows 10/11 x64 demo, unsigned binaries (Windows may display a publisher warning).
Single player; no multiplayer/voice. Balance remains experimental. The model is a game,
not an operational firefighting tool. Saves from this release are preserved across
installs; compatibility with later format changes will be documented per release.
