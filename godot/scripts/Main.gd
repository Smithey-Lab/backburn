extends Node2D
## Game shell. Owns the sim clock and routes input into commands.
## The simulation itself lives in a C# class (Sim/SimulationCore.cs, to be ported
## from backburn/fire.py — see PORTING.md). Until it exists this scene only
## draws the terrain and moves the camera.

const TICKS_PER_SECOND := 10.0
const CELL_PX := 8

var speed := 1.0
var paused := false
var _acc := 0.0
var sim  # SimulationCore (C#) once ported

func _ready() -> void:
	# TODO: sim = SimulationCore.new(); sim.load_scenario("res://data/scenarios/prairie_fire.json")
	$FireLayer.position = Vector2.ZERO

func _process(delta: float) -> void:
	if paused or sim == null:
		return
	_acc += delta * TICKS_PER_SECOND * speed
	var n := int(_acc)
	if n > 0:
		sim.step(n)
		_acc -= n
		$FireLayer.texture = sim.fire_texture()

func _unhandled_input(event: InputEvent) -> void:
	if event.is_action_pressed("pause"):
		paused = not paused
	elif event is InputEventKey and event.pressed:
		match event.keycode:
			KEY_1: speed = 1.0
			KEY_2: speed = 3.0
			KEY_3: speed = 8.0
