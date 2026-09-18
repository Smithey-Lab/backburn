extends Node2D
## Draws a scenario's terrain as one nearest-filtered texture (blocky, hard colour
## separation — brief §7). Loads grid-mode scenario files from res://data/scenarios.
## The fire layer is a second texture the (future) C# SimulationCore rewrites each tick.

const CELL_PX := 8

var scenario := {}
var width := 0
var height := 0
var terrain_ids: PackedByteArray = PackedByteArray()

@onready var terrain_sprite: Sprite2D = $Terrain
@onready var fire_sprite: Sprite2D = $FireLayer

func load_scenario(path: String) -> bool:
	var f := FileAccess.open(path, FileAccess.READ)
	if f == null:
		push_error("cannot open %s" % path)
		return false
	var parsed = JSON.parse_string(f.get_as_text())
	if not (parsed is Dictionary) or parsed.get("terrain", {}).get("mode", "") != "grid":
		push_error("%s is not a grid-mode scenario; bake it with `backburn gen` first" % path)
		return false
	scenario = parsed
	var rows: Array = scenario["terrain"]["rows"]
	height = rows.size()
	width = String(rows[0]).length()
	terrain_ids.resize(width * height)
	var img := Image.create(width, height, false, Image.FORMAT_RGB8)
	for y in height:
		var row: String = rows[y]
		for x in width:
			var spec: Dictionary = Balance.terrain_by_letter[row[x]]
			terrain_ids[y * width + x] = int(spec["id"])
			img.set_pixel(x, y, Balance.color_of(spec))
	terrain_sprite.texture = ImageTexture.create_from_image(img)
	terrain_sprite.scale = Vector2(CELL_PX, CELL_PX)
	var fire_img := Image.create(width, height, false, Image.FORMAT_RGBA8)
	fire_img.fill(Color(0, 0, 0, 0))
	fire_sprite.texture = ImageTexture.create_from_image(fire_img)
	fire_sprite.scale = Vector2(CELL_PX, CELL_PX)
	# Starting fires, drawn statically until the sim exists.
	for ig in scenario.get("ignitions", []):
		paint_fire(int(ig["x"]), int(ig["y"]), int(ig.get("radius", 0)))
	return true

func paint_fire(cx: int, cy: int, radius: int) -> void:
	var img: Image = fire_sprite.texture.get_image()
	for y in range(max(0, cy - radius), min(height, cy + radius + 1)):
		for x in range(max(0, cx - radius), min(width, cx + radius + 1)):
			if (x - cx) * (x - cx) + (y - cy) * (y - cy) <= (radius + 0.5) * (radius + 0.5):
				img.set_pixel(x, y, Color8(255, 110, 20))
	fire_sprite.texture.update(img)

func cell_at(world_pos: Vector2) -> Vector2i:
	return Vector2i(int(floor(world_pos.x / CELL_PX)), int(floor(world_pos.y / CELL_PX)))

func terrain_at(cell: Vector2i) -> Dictionary:
	if cell.x < 0 or cell.y < 0 or cell.x >= width or cell.y >= height:
		return {}
	return Balance.terrain_by_id[terrain_ids[cell.y * width + cell.x]]
