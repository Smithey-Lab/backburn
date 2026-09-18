extends Camera2D
## Pan/zoom per brief §9: wheel zoom, middle-drag or WASD pan.

const ZOOM_STEP := 1.15
const PAN_SPEED := 700.0
var _dragging := false

func _process(delta: float) -> void:
	var dir := Vector2.ZERO
	if Input.is_action_pressed("pan_up"): dir.y -= 1
	if Input.is_action_pressed("pan_down"): dir.y += 1
	if Input.is_action_pressed("pan_left"): dir.x -= 1
	if Input.is_action_pressed("pan_right"): dir.x += 1
	position += dir.normalized() * PAN_SPEED * delta / zoom.x

func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventMouseButton:
		if event.button_index == MOUSE_BUTTON_WHEEL_UP and event.pressed:
			_zoom_at(event.position, ZOOM_STEP)
		elif event.button_index == MOUSE_BUTTON_WHEEL_DOWN and event.pressed:
			_zoom_at(event.position, 1.0 / ZOOM_STEP)
		elif event.button_index == MOUSE_BUTTON_MIDDLE:
			_dragging = event.pressed
	elif event is InputEventMouseMotion and _dragging:
		position -= event.relative / zoom.x

func _zoom_at(screen_pos: Vector2, factor: float) -> void:
	var before := get_global_mouse_position()
	zoom = (zoom * factor).clamp(Vector2(0.25, 0.25), Vector2(8, 8))
	var after := get_global_mouse_position()
	position += before - after
