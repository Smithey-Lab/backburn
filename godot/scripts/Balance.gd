extends Node
## Autoload "Balance": loads data/terrain.json and data/units.json — the same files the
## Python prototype uses — so colours, letters and numbers never diverge between the two.

var terrain := {}          # name -> dict
var terrain_by_id := []    # id -> dict
var terrain_by_letter := {}
var fire := {}
var units := {}

const LETTERS := {"W": "WATER", "G": "GRASS", "S": "SHRUB", "F": "FOREST", "D": "DENSE_FOREST",
	"R": "ROAD", "V": "GRAVEL", "B": "STRUCTURE", "X": "FIREBREAK", "A": "SAND"}

func _ready() -> void:
	var t := _load_json("res://data/terrain.json")
	terrain = t.get("types", {})
	fire = t.get("fire", {})
	terrain_by_id.resize(terrain.size())
	for name in terrain.keys():
		var spec: Dictionary = terrain[name]
		spec["name"] = name
		terrain_by_id[int(spec["id"])] = spec
	for letter in LETTERS.keys():
		terrain_by_letter[letter] = terrain[LETTERS[letter]]
	units = _load_json("res://data/units.json").get("types", {})

func color_of(spec: Dictionary) -> Color:
	var c: Array = spec["color"]
	return Color8(int(c[0]), int(c[1]), int(c[2]))

func _load_json(path: String) -> Dictionary:
	var f := FileAccess.open(path, FileAccess.READ)
	if f == null:
		push_error("missing %s" % path)
		return {}
	var parsed = JSON.parse_string(f.get_as_text())
	return parsed if parsed is Dictionary else {}
