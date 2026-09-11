"""Expand aliases, add new tracks with _confidence: 'estimated', and add confidence per corner.

Phase 1b: Expand aliases for better game-log matching
Phase 1c: Add ~10 new tracks with estimated corner windows
"""

import json
from pathlib import Path

CATALOG_PATH = Path(__file__).parent.parent / "data" / "track_catalog.json"

with open(CATALOG_PATH, "r", encoding="utf-8") as f:
    catalog = json.load(f)

# ── Phase 1b: Expand aliases for existing tracks ──

ALIAS_EXTENSIONS = {
    "brands_hatch": {"aliases": ["brands_hatch", "brands", "brands-hatch", "brands hatch", "brands hatch gp"]},
    "circuit_de_spa_francorchamps": {
        "aliases": [
            "circuit_de_spa_francorchamps",
            "spa",
            "spa-francorchamps",
            "circuit_de_spa_",
            "spa francorchamps",
            "circuit de spa-francorchamps",
        ]
    },
    "circuit_of_the_americas": {"aliases": ["circuit_of_the_americas", "cota", "circuit-of-the-americas", "americas"]},
    "donington_park_national": {
        "aliases": ["donington_park_national", "donington_park", "donington", "donington park"]
    },
    "fuji_speedway": {"aliases": ["fuji_speedway", "fuji", "fuji-speedway", "fuji speedway"]},
    "imola": {"aliases": ["imola", "autodromo-enzo-e-dino-ferrari", "enzo e dino ferrari"]},
    "laguna_seca": {
        "aliases": [
            "laguna_seca",
            "laguna",
            "laguna-seca",
            "mazda-raceway",
            "weathertech-raceway",
            "laguna seca",
            "mazda raceway laguna seca",
        ]
    },
    "monza": {"aliases": ["monza", "autodromo nazionale di monza"]},
    "mount_panorama": {"aliases": ["mount_panorama", "bathurst", "mount-panorama", "mount panorama"]},
    "nurburgring_nordschleife": {
        "aliases": [
            "nurburgring_nordschleife",
            "nordschleife",
            "nurburgring-nordschleife",
            "nurburgring_touristenfahrten",
            "nurburgring_24h",
            "nürburgring",
            "nurburgring",
            "nordschleife",
            "nürburgring nordschleife",
        ]
    },
    "oulton_park_international": {"aliases": ["oulton_park_international", "oulton park", "oulton"]},
    "red_bull_ring": {"aliases": ["red_bull_ring", "red-bull-ring", "rbr", "red bull ring", "spielberg"]},
    "suzuka": {"aliases": ["suzuka", "suzuka-circuit", "suzuka_circuit", "suzuka circuit"]},
    "watkins_glen_international": {
        "aliases": [
            "watkins_glen_international",
            "watkins_glen_internati",
            "watkins glen",
            "watkins glen international",
        ]
    },
}

for key, ext in ALIAS_EXTENSIONS.items():
    if key in catalog:
        existing_aliases = set(a.lower() for a in catalog[key].get("aliases", []))
        for new_alias in ext["aliases"]:
            if new_alias.lower() not in existing_aliases:
                catalog[key]["aliases"].append(new_alias)
                existing_aliases.add(new_alias.lower())

# ── Phase 1c: Add ~10 new tracks with _confidence: "estimated" ──

ESTIMATED_TRACKS = {
    "silverstone": {
        "name": "Silverstone Circuit",
        "aliases": ["silverstone", "silverstone-circuit", "silverstone_circuit", "silverstone gp"],
        "default_config": "gp",
        "configs": {
            "gp": {
                "name": "GP",
                "aliases": ["gp", "full"],
                "corners": [
                    {"id": 1, "name": "Abbey", "start": 0.00, "end": 0.035},
                    {"id": 2, "name": "Farm Curve", "start": 0.035, "end": 0.055},
                    {"id": 3, "name": "Village", "start": 0.070, "end": 0.095},
                    {"id": 4, "name": "The Loop", "start": 0.095, "end": 0.125},
                    {"id": 5, "name": "Aintree", "start": 0.125, "end": 0.150},
                    {"id": 6, "name": "Wellington Straight Kink", "start": 0.185, "end": 0.195},
                    {"id": 7, "name": "Brooklands", "start": 0.220, "end": 0.250},
                    {"id": 8, "name": "Luffield", "start": 0.260, "end": 0.295},
                    {"id": 9, "name": "Woodcote", "start": 0.315, "end": 0.330},
                    {"id": 10, "name": "Copse", "start": 0.375, "end": 0.410},
                    {"id": 11, "name": "Maggots", "start": 0.430, "end": 0.455},
                    {"id": 12, "name": "Becketts", "start": 0.455, "end": 0.490},
                    {"id": 13, "name": "Chapel", "start": 0.490, "end": 0.510},
                    {"id": 14, "name": "Hangar Straight Kink", "start": 0.540, "end": 0.550},
                    {"id": 15, "name": "Stowe", "start": 0.610, "end": 0.650},
                    {"id": 16, "name": "Vale", "start": 0.700, "end": 0.730},
                    {"id": 17, "name": "Club", "start": 0.730, "end": 0.765},
                    {"id": 18, "name": "Hamilton Straight Kink", "start": 0.820, "end": 0.845},
                ],
            }
        },
    },
    "nurburgring_gp": {
        "name": "Nurburgring GP",
        "aliases": ["nurburgring_gp", "nurburgring-gp", "nurburgring grand prix", "nürburgring gp"],
        "default_config": "gp",
        "configs": {
            "gp": {
                "name": "GP",
                "aliases": ["gp", "full"],
                "corners": [
                    {"id": 1, "name": "T1 (Castrol)", "start": 0.01, "end": 0.05},
                    {"id": 2, "name": "T2", "start": 0.08, "end": 0.12},
                    {"id": 3, "name": "T3 (Falken)", "start": 0.20, "end": 0.27},
                    {"id": 4, "name": "T4 (Dunlop)", "start": 0.32, "end": 0.37},
                    {"id": 5, "name": "T5 (Shell)", "start": 0.40, "end": 0.44},
                    {"id": 6, "name": "T6 (RTL)", "start": 0.48, "end": 0.53},
                    {"id": 7, "name": "T7 (Bit)", "start": 0.58, "end": 0.62},
                    {"id": 8, "name": "T8 (Veedol)", "start": 0.65, "end": 0.70},
                    {"id": 9, "name": "T9 (NGK)", "start": 0.75, "end": 0.79},
                    {"id": 10, "name": "T10 (Mobil 1)", "start": 0.82, "end": 0.87},
                    {"id": 11, "name": "T11 (Aral)", "start": 0.92, "end": 0.96},
                    {"id": 12, "name": "T12 (Michael Schumacher S)", "start": 0.96, "end": 0.99},
                ],
            }
        },
    },
    "barcelona": {
        "name": "Circuit de Barcelona-Catalunya",
        "aliases": ["barcelona", "catalunya", "barcelona-catalunya", "circuit de barcelona-catalunya"],
        "default_config": "gp",
        "configs": {
            "gp": {
                "name": "GP",
                "aliases": ["gp", "full"],
                "corners": [
                    {"id": 1, "name": "T1 (Elf)", "start": 0.02, "end": 0.06},
                    {"id": 2, "name": "T2", "start": 0.08, "end": 0.12},
                    {"id": 3, "name": "T3", "start": 0.12, "end": 0.15},
                    {"id": 4, "name": "T4 (Repsol)", "start": 0.18, "end": 0.23},
                    {"id": 5, "name": "T5 (Seat)", "start": 0.28, "end": 0.33},
                    {"id": 6, "name": "T6", "start": 0.38, "end": 0.42},
                    {"id": 7, "name": "T7", "start": 0.42, "end": 0.45},
                    {"id": 8, "name": "T8", "start": 0.45, "end": 0.49},
                    {"id": 9, "name": "T9 (Campsa)", "start": 0.52, "end": 0.58},
                    {"id": 10, "name": "T10 (La Caixa)", "start": 0.62, "end": 0.67},
                    {"id": 11, "name": "T11", "start": 0.72, "end": 0.77},
                    {"id": 12, "name": "T12", "start": 0.82, "end": 0.86},
                    {"id": 13, "name": "T13 (European)", "start": 0.90, "end": 0.94},
                    {"id": 14, "name": "T14 (New Holland)", "start": 0.94, "end": 0.97},
                ],
            }
        },
    },
    "hungaroring": {
        "name": "Hungaroring",
        "aliases": ["hungaroring", "budapest", "hungary"],
        "default_config": "gp",
        "configs": {
            "gp": {
                "name": "GP",
                "aliases": ["gp", "full"],
                "corners": [
                    {"id": 1, "name": "T1", "start": 0.03, "end": 0.08},
                    {"id": 2, "name": "T2", "start": 0.10, "end": 0.15},
                    {"id": 3, "name": "T3", "start": 0.20, "end": 0.24},
                    {"id": 4, "name": "T4", "start": 0.27, "end": 0.35},
                    {"id": 5, "name": "T5", "start": 0.40, "end": 0.44},
                    {"id": 6, "name": "T6", "start": 0.48, "end": 0.55},
                    {"id": 7, "name": "T7", "start": 0.58, "end": 0.62},
                    {"id": 8, "name": "T8", "start": 0.65, "end": 0.69},
                    {"id": 9, "name": "T9", "start": 0.72, "end": 0.76},
                    {"id": 10, "name": "T10", "start": 0.80, "end": 0.84},
                    {"id": 11, "name": "T11", "start": 0.86, "end": 0.90},
                    {"id": 12, "name": "T12", "start": 0.92, "end": 0.96},
                    {"id": 13, "name": "T13", "start": 0.96, "end": 0.99},
                ],
            }
        },
    },
    "zandvoort": {
        "name": "Circuit Zandvoort",
        "aliases": ["zandvoort", "circuit-zandvoort", "zandvoort circuit"],
        "default_config": "gp",
        "configs": {
            "gp": {
                "name": "GP",
                "aliases": ["gp", "full"],
                "corners": [
                    {"id": 1, "name": "T1 (Tarzan)", "start": 0.02, "end": 0.08},
                    {"id": 2, "name": "T2 (Gerlach)", "start": 0.12, "end": 0.16},
                    {"id": 3, "name": "T3 (Hugenholtz)", "start": 0.20, "end": 0.28},
                    {"id": 4, "name": "T4 (Hunserug)", "start": 0.32, "end": 0.38},
                    {"id": 5, "name": "T5 (Slotemaker)", "start": 0.42, "end": 0.46},
                    {"id": 6, "name": "T6", "start": 0.48, "end": 0.52},
                    {"id": 7, "name": "T7", "start": 0.55, "end": 0.60},
                    {"id": 8, "name": "T8 (Masters)", "start": 0.65, "end": 0.70},
                    {"id": 9, "name": "T9 (Renee)", "start": 0.72, "end": 0.76},
                    {"id": 10, "name": "T10 (Bocht 10)", "start": 0.80, "end": 0.85},
                    {"id": 11, "name": "T11 (Arie Luyendijk)", "start": 0.88, "end": 0.92},
                    {"id": 12, "name": "T12 (Chicane)", "start": 0.95, "end": 0.99},
                ],
            }
        },
    },
    "portimao": {
        "name": "Algarve International Circuit",
        "aliases": ["portimao", "algarve", "autodromo internacional do algarve"],
        "default_config": "gp",
        "configs": {
            "gp": {
                "name": "GP",
                "aliases": ["gp", "full"],
                "corners": [
                    {"id": 1, "name": "T1", "start": 0.02, "end": 0.06},
                    {"id": 2, "name": "T2", "start": 0.08, "end": 0.12},
                    {"id": 3, "name": "T3", "start": 0.18, "end": 0.22},
                    {"id": 4, "name": "T4", "start": 0.25, "end": 0.30},
                    {"id": 5, "name": "T5 (Kink)", "start": 0.35, "end": 0.38},
                    {"id": 6, "name": "T6", "start": 0.42, "end": 0.48},
                    {"id": 7, "name": "T7", "start": 0.52, "end": 0.56},
                    {"id": 8, "name": "T8", "start": 0.58, "end": 0.62},
                    {"id": 9, "name": "T9", "start": 0.65, "end": 0.70},
                    {"id": 10, "name": "T10", "start": 0.75, "end": 0.80},
                    {"id": 11, "name": "T11", "start": 0.83, "end": 0.87},
                    {"id": 12, "name": "T12", "start": 0.90, "end": 0.94},
                    {"id": 13, "name": "T13", "start": 0.94, "end": 0.97},
                    {"id": 14, "name": "T14", "start": 0.97, "end": 0.99},
                ],
            }
        },
    },
    "kyalami": {
        "name": "Kyalami Grand Prix Circuit",
        "aliases": ["kyalami", "kyalami-gp", "kyalami circuit"],
        "default_config": "gp",
        "configs": {
            "gp": {
                "name": "GP",
                "aliases": ["gp", "full"],
                "corners": [
                    {"id": 1, "name": "T1", "start": 0.03, "end": 0.08},
                    {"id": 2, "name": "T2", "start": 0.12, "end": 0.18},
                    {"id": 3, "name": "T3", "start": 0.22, "end": 0.26},
                    {"id": 4, "name": "T4", "start": 0.30, "end": 0.34},
                    {"id": 5, "name": "T5", "start": 0.38, "end": 0.42},
                    {"id": 6, "name": "T6 (Sunset)", "start": 0.48, "end": 0.52},
                    {"id": 7, "name": "T7 (Jukskei)", "start": 0.55, "end": 0.62},
                    {"id": 8, "name": "T8 (Esses 1)", "start": 0.65, "end": 0.68},
                    {"id": 9, "name": "T9 (Esses 2)", "start": 0.68, "end": 0.72},
                    {"id": 10, "name": "T10 (Coca-Cola)", "start": 0.75, "end": 0.82},
                    {"id": 11, "name": "T11 (Kink)", "start": 0.88, "end": 0.91},
                    {"id": 12, "name": "T12", "start": 0.94, "end": 0.98},
                ],
            }
        },
    },
    "road_america": {
        "name": "Road America",
        "aliases": ["road_america", "road-america", "road america", "elkhart lake"],
        "default_config": "full",
        "configs": {
            "full": {
                "name": "Full",
                "aliases": ["full", "gp"],
                "corners": [
                    {"id": 1, "name": "T1", "start": 0.02, "end": 0.07},
                    {"id": 2, "name": "T2", "start": 0.12, "end": 0.16},
                    {"id": 3, "name": "T3", "start": 0.20, "end": 0.25},
                    {"id": 4, "name": "T4", "start": 0.28, "end": 0.35},
                    {"id": 5, "name": "T5 (Canada Corner)", "start": 0.38, "end": 0.44},
                    {"id": 6, "name": "T6", "start": 0.50, "end": 0.55},
                    {"id": 7, "name": "T7 (The Bend)", "start": 0.58, "end": 0.64},
                    {"id": 8, "name": "T8 (Carousel)", "start": 0.68, "end": 0.74},
                    {"id": 9, "name": "T9 (Kettle Bottle)", "start": 0.78, "end": 0.82},
                    {"id": 10, "name": "T10", "start": 0.84, "end": 0.88},
                    {"id": 11, "name": "T11 (Bill Mitchell)", "start": 0.90, "end": 0.95},
                    {"id": 12, "name": "T12 (Victory)", "start": 0.96, "end": 0.99},
                ],
            }
        },
    },
    "misano": {
        "name": "Misano World Circuit",
        "aliases": ["misano", "misano-world-circuit", "marco simoncelli"],
        "default_config": "full",
        "configs": {
            "full": {
                "name": "Full",
                "aliases": ["full", "gp"],
                "corners": [
                    {"id": 1, "name": "T1 (Quercia)", "start": 0.03, "end": 0.08},
                    {"id": 2, "name": "T2", "start": 0.12, "end": 0.16},
                    {"id": 3, "name": "T3", "start": 0.20, "end": 0.25},
                    {"id": 4, "name": "T4 (Tramonto)", "start": 0.30, "end": 0.35},
                    {"id": 5, "name": "T5", "start": 0.38, "end": 0.42},
                    {"id": 6, "name": "T6", "start": 0.45, "end": 0.50},
                    {"id": 7, "name": "T7", "start": 0.52, "end": 0.56},
                    {"id": 8, "name": "T8 (Carro)", "start": 0.60, "end": 0.68},
                    {"id": 9, "name": "T9", "start": 0.72, "end": 0.76},
                    {"id": 10, "name": "T10", "start": 0.78, "end": 0.82},
                    {"id": 11, "name": "T11 (Del Rio)", "start": 0.85, "end": 0.90},
                    {"id": 12, "name": "T12 (Curvone)", "start": 0.92, "end": 0.96},
                    {"id": 13, "name": "T13 (Variante)", "start": 0.96, "end": 0.99},
                ],
            }
        },
    },
    "valencia": {
        "name": "Circuit Ricardo Tormo",
        "aliases": ["valencia", "ricardo tormo", "circuit-ricardo-tormo", "cheste"],
        "default_config": "gp",
        "configs": {
            "gp": {
                "name": "GP",
                "aliases": ["gp", "full"],
                "corners": [
                    {"id": 1, "name": "T1", "start": 0.02, "end": 0.07},
                    {"id": 2, "name": "T2", "start": 0.10, "end": 0.15},
                    {"id": 3, "name": "T3", "start": 0.20, "end": 0.25},
                    {"id": 4, "name": "T4", "start": 0.30, "end": 0.35},
                    {"id": 5, "name": "T5", "start": 0.38, "end": 0.42},
                    {"id": 6, "name": "T6", "start": 0.48, "end": 0.55},
                    {"id": 7, "name": "T7", "start": 0.58, "end": 0.62},
                    {"id": 8, "name": "T8", "start": 0.65, "end": 0.68},
                    {"id": 9, "name": "T9", "start": 0.72, "end": 0.77},
                    {"id": 10, "name": "T10", "start": 0.82, "end": 0.86},
                    {"id": 11, "name": "T11", "start": 0.88, "end": 0.92},
                    {"id": 12, "name": "T12", "start": 0.94, "end": 0.98},
                    {"id": 13, "name": "T13", "start": 0.98, "end": 0.99},
                ],
            }
        },
    },
}

# Add _confidence to EVERY existing corner
for _track_key, track in catalog.items():
    for _config_key, config in track.get("configs", {}).items():
        for corner in config.get("corners", []):
            corner["_confidence"] = "profiled"

# Add estimated tracks with _confidence: "estimated" on each corner
for key, track_data in ESTIMATED_TRACKS.items():
    for _config_key, config in track_data["configs"].items():
        for corner in config.get("corners", []):
            corner["_confidence"] = "estimated"
    catalog[key] = track_data

with open(CATALOG_PATH, "w", encoding="utf-8") as f:
    json.dump(catalog, f, indent=2, ensure_ascii=False)

print(f"Updated catalog with {len(catalog)} tracks total")
new_tracks = len(ESTIMATED_TRACKS)
existing_tracks = len(catalog) - new_tracks
print(f"  - Existing tracks updated: {existing_tracks}")
print(f"  - New estimated tracks added: {new_tracks}")

# Count confidence types
profiled = sum(
    1
    for t in catalog.values()
    for c in t.get("configs", {}).values()
    for _ in c.get("corners", [])
    if _.get("_confidence") == "profiled"
)
estimated = sum(
    1
    for t in catalog.values()
    for c in t.get("configs", {}).values()
    for _ in c.get("corners", [])
    if _.get("_confidence") == "estimated"
)
print(f"  - Profiled corners: {profiled}")
print(f"  - Estimated corners: {estimated}")
