extends Node2D
## Game shell. Owns the sim clock and routes input into commands.
## The simulation itself will live in a C# class (Sim/SimulationCore.cs, ported from
## backburn/fire.py — see PORTING.md). Until it exists this scene draws the terrain and
## starting fires, moves the camera, and shows what's under the cursor.

const TICKS_PER_SECOND := 10.0
const DEFAULT_SCENARIO := "res://data/scenarios/prairie_fire.json"

var speed := 1.0
var paused := false
var _acc := 0.0
var sim = null  # SimulationCore (C#) once ported

@onready var map: Node2D = $MapView
@onready var hud: Label = $UI/HUD/Status
@onready var cam: Camera2D = $CameraRig

func _ready() -> void:
	var path := DEFAULT_SCENARIO
	for arg in OS.get_cmdline_user_args():
		if arg.ends_with(".json"):
			path = arg
	if map.load_scenario(path):
		cam.position = Vector2(map.width, map.height) * map.CELL_PX * 0.5
		hud.text = "%s — %dx%d  (sim not ported yet; camera and map only)" % [
			map.scenario.get("name", "?"), map.width, map.height]
		print("[backburn] loaded ", hud.text, "  sample cell(10,10)=", map.terrain_at(Vector2i(10, 10)).get("name", "?"))

func _process(delta: float) -> void:
	if paused or sim == null:
		return
	_acc += delta * TICKS_PER_SECOND * speed
	var n := int(_acc)
	if n > 0:
		sim.step(n)
		_acc -= n

func _unhandled_input(event: InputEvent) -> void:
	if event.is_action_pressed("pause"):
		paused = not paused
	elif event is InputEventKey and event.pressed:
		match event.keycode:
			KEY_1: speed = 1.0
			KEY_2: speed = 3.0
			KEY_3: speed = 8.0
	elif event is InputEventMouseMotion:
		var cell: Vector2i = map.cell_at(get_global_mouse_position())
		var t: Dictionary = map.terrain_at(cell)
		if not t.is_empty():
			hud.text = "%s  cell %s  %s" % [map.scenario.get("name", "?"), cell, t.get("name", "?")]
