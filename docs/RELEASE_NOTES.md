## Backburn v0.7.0 — big country

- Every mission is roughly nine times larger (about 3× wider and taller, up to 1440×990
  cells) and is generated natively at that size: more lakes, rivers with bridges, highways,
  gravel spurs, towns of separate buildings and outlying ranches, instead of an upscaled
  small map. The four original incidents were rebuilt from scratch at the new scale.
- Seven new incidents: Canyon Run, Lakeshore Cabins, Highway 9, Timber Ridge, Ember Storm,
  Fire Complex and The Long Watch (survival), plus a Random Incident mode that rolls a
  complete mission from a seed and can be rerolled from the mission room.
- Bulldozers travel at road speed on highways and gravel when they are not cutting, and the
  route planner sends them along roads. Off-road travel is faster too; cutting is unchanged.
- Missions can now be won by holding the line until the clock runs out (`win_on_timeout`),
  which the survival, complex and extreme-weather incidents use.
- Performance: the fire model only computes inside the regions around active fire and wet
  ground, long orders use a block-level route planner with lazy refinement (cross-map orders
  take tens of milliseconds instead of seconds), the map surface is patched in place, trees
  and flames are batched sprites, autosaves are written on a worker thread, and a 16× speed
  is available. Frame times stay under 16 ms on the largest maps.
- Mission room: twelve cards in a grid with cached thumbnails, a reroll button for random
  incidents, and Shift for fast panning on the big maps.

Existing saves keep their layout and continue to load. Replays recorded with v0.6 do not
reproduce exactly under the new fire kernel.

## Backburn v0.6.0 — wind, firebreaks and clearer line previews

- Preview circles are spaced farther apart. Dozers and cutting crews now preview and follow curved firebreaks; engines and hose teams show their suppression radius.
- Planes arrive empty and load off-map. Queue a drop while loading; the aircraft waits until full. Removed the on-map air-base marker.
- New missions have gradual, bounded gusts and direction shifts, preserved through save/load and replay. A compass arrow shows the direction wind is blowing toward, speed and ember risk; airborne embers have visible trails.
- Firebreak terrain is now nonflammable, direct spread cannot slip diagonally through touching cleared cells, and crews clear the last cell of their line.
- Strong wind accelerates downwind spread and can loft embers from grass as well as timber. Embers can cross firebreaks; embers leaving the map no longer ignite its border.

## Backburn v0.5.0 — plan your fleet and draw curved drops

- Aircraft drops follow the route you draw. Coverage circles preview the swath; stroke length is capped to available payload, using the same calculation as the simulation. Short drops conserve unused payload.
- Escape cancels a drop plan; Shift queues another. Planes choose the nearest of all four map edges for exit and reload.
- New missions start without response units. Buy a fleet within the mission budget; setup purchases are immediately ready, while later reinforcements retain arrival delays. Civilians and rescue objectives remain.
- A cleaner roster separates All, Ground and Aircraft, with explicit off-map staged status, clear selection, paging and a Locate control.
- WASD works at overview zoom and beyond map edges within bounded margins. Off-map aircraft can also be selected directly. Home restores the overview.
- Existing saves keep their units and map layout; start a new mission for the fleet setup changes.

## Backburn v0.4.0 — smooth movement and aircraft sorties

- Units animate between simulation ticks without speeding up the fire.
- Planes stage beyond the map sides, fly through their drop runs, and exit before reloading. Helicopters retain hovering and rescue behavior.
- Reload countdowns, READY labels, resource bars and completion notices make availability clear. Orders queued during reloading wait until the aircraft is full.
- Maps are 50% wider and taller than v0.3.2. Existing saves keep their original dimensions.
- Dispatch shows delivery times and an inbound countdown, closes after a purchase, and provides a Locate button on arrival. Bulldozer dispatch is reduced from 150 to 45 simulated seconds.
- Terrain rendering caches unchanged frames and scales only the visible area.

## Backburn v0.3.2 — Windows installation fix

- Install into a fresh version folder and activate it only after the copy completes, avoiding Windows directory-rename permission failures.
- Existing saves and the previous installed version are preserved during updates.

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
