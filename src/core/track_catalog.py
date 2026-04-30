"""Static track catalog and profile selection helpers for telemetry analysis."""

import os


TRACK_CATALOG = {
    "brands_hatch": {
        "name": "Brands Hatch",
        "aliases": ["brands_hatch", "brands", "brands-hatch"],
        "default_config": "gp",
        "configs": {
            "gp": {
                "name": "GP",
                "aliases": ["gp"],
                "corners": [
                    {"id": 1, "name": "Paddock Hill Bend", "start": 0.020, "end": 0.070},
                    {"id": 2, "name": "Druids Bend", "start": 0.145, "end": 0.205},
                    {"id": 3, "name": "Graham Hill Bend", "start": 0.255, "end": 0.315},
                    {"id": 4, "name": "Surtees", "start": 0.405, "end": 0.455},
                    {"id": 5, "name": "Hawthorn Bend", "start": 0.520, "end": 0.575},
                    {"id": 6, "name": "Westfield", "start": 0.610, "end": 0.650},
                    {"id": 7, "name": "Sheene Curve", "start": 0.685, "end": 0.730},
                    {"id": 8, "name": "Stirlings", "start": 0.760, "end": 0.820},
                    {"id": 9, "name": "Clark Curve", "start": 0.920, "end": 0.990},
                ],
            },
            "indy": {
                "name": "Indy",
                "aliases": ["indy"],
                "corners": [
                    {"id": 1, "name": "Paddock Hill Bend", "start": 0.020, "end": 0.080},
                    {"id": 2, "name": "Druids Bend", "start": 0.180, "end": 0.250},
                    {"id": 3, "name": "Graham Hill Bend", "start": 0.350, "end": 0.450},
                    {"id": 4, "name": "Surtees", "start": 0.600, "end": 0.650},
                    {"id": 5, "name": "McLaren", "start": 0.650, "end": 0.700},
                    {"id": 6, "name": "Clearways", "start": 0.850, "end": 0.950},
                    {"id": 7, "name": "Clark Curve", "start": 0.970, "end": 0.990},
                ],
            },
        },
    },
    "brands_hatch_indy": {
        "name": "Brands Hatch Indy",
        "aliases": ["brands_hatch_indy", "brands-hatch-indy"],
        "default_config": "indy",
        "configs": {
            "indy": {
                "name": "Indy",
                "aliases": ["indy"],
                "corners": [
                    {"id": 1, "name": "Paddock Hill Bend", "start": 0.020, "end": 0.080},
                    {"id": 2, "name": "Druids Bend", "start": 0.180, "end": 0.250},
                    {"id": 3, "name": "Graham Hill Bend", "start": 0.350, "end": 0.450},
                    {"id": 4, "name": "Surtees", "start": 0.600, "end": 0.650},
                    {"id": 5, "name": "McLaren", "start": 0.650, "end": 0.700},
                    {"id": 6, "name": "Clearways", "start": 0.850, "end": 0.950},
                    {"id": 7, "name": "Clark Curve", "start": 0.970, "end": 0.990},
                ],
            },
        },
    },
    "circuit_de_spa_francorchamps": {
        "name": "Circuit de Spa-Francorchamps",
        "aliases": ["circuit_de_spa_francorchamps", "spa", "spa-francorchamps", "circuit_de_spa_"],
        "default_config": "current",
        "configs": {
            "current": {
                "name": "Current",
                "aliases": ["current", "gp", "full"],
                "corners": [
                    {"id": 1, "name": "La Source", "start": 0.070, "end": 0.100},
                    {"id": 2, "name": "Eau Rouge", "start": 0.100, "end": 0.108},
                    {"id": 3, "name": "Raidillon Left", "start": 0.108, "end": 0.117},
                    {"id": 4, "name": "Raidillon Right", "start": 0.117, "end": 0.126},
                    {"id": 5, "name": "Les Combes 1", "start": 0.128, "end": 0.145},
                    {"id": 6, "name": "Les Combes 2", "start": 0.145, "end": 0.160},
                    {"id": 7, "name": "Les Combes 3", "start": 0.190, "end": 0.208},
                    {"id": 8, "name": "Bruxelles", "start": 0.208, "end": 0.236},
                    {"id": 9, "name": "No Name", "start": 0.330, "end": 0.352},
                    {"id": 10, "name": "Pouhon 1", "start": 0.418, "end": 0.442},
                    {"id": 11, "name": "Pouhon 2", "start": 0.442, "end": 0.472},
                    {"id": 12, "name": "Fagnes 1", "start": 0.488, "end": 0.498},
                    {"id": 13, "name": "Fagnes 2", "start": 0.498, "end": 0.508},
                    {"id": 14, "name": "Campus", "start": 0.710, "end": 0.722},
                    {"id": 15, "name": "Paul Frere", "start": 0.722, "end": 0.752},
                    {"id": 16, "name": "Blanchimont 1", "start": 0.820, "end": 0.832},
                    {"id": 17, "name": "Blanchimont 2", "start": 0.832, "end": 0.842},
                    {"id": 18, "name": "Bus Stop 1", "start": 0.928, "end": 0.942},
                    {"id": 19, "name": "Bus Stop 2", "start": 0.942, "end": 0.955},
                ],
            },
        },
    },
    "circuit_of_the_americas": {
        "name": "Circuit of the Americas",
        "aliases": ["circuit_of_the_americas", "cota", "circuit-of-the-americas"],
        "default_config": "gp",
        "configs": {
            "gp": {
                "name": "GP",
                "aliases": ["gp", "full"],
                "corners": [
                    {"id": 1, "name": "Big Red", "start": 0.010, "end": 0.060},
                    {"id": 2, "name": "Turn 2", "start": 0.080, "end": 0.110},
                    {"id": 3, "name": "Esses 1", "start": 0.130, "end": 0.150},
                    {"id": 4, "name": "Esses 2", "start": 0.150, "end": 0.170},
                    {"id": 5, "name": "Esses 3", "start": 0.170, "end": 0.190},
                    {"id": 6, "name": "Esses 4", "start": 0.190, "end": 0.210},
                    {"id": 7, "name": "Turn 7", "start": 0.250, "end": 0.280},
                    {"id": 8, "name": "Turn 8", "start": 0.280, "end": 0.310},
                    {"id": 9, "name": "Turn 9", "start": 0.310, "end": 0.340},
                    {"id": 10, "name": "Turn 10", "start": 0.380, "end": 0.400},
                    {"id": 11, "name": "The Bobby Pin", "start": 0.420, "end": 0.470},
                    {"id": 12, "name": "Turn 12", "start": 0.550, "end": 0.600},
                    {"id": 13, "name": "Turn 13", "start": 0.650, "end": 0.680},
                    {"id": 14, "name": "Turn 14", "start": 0.680, "end": 0.710},
                    {"id": 15, "name": "Turn 15", "start": 0.710, "end": 0.750},
                    {"id": 16, "name": "Triple Apex 1", "start": 0.800, "end": 0.830},
                    {"id": 17, "name": "Triple Apex 2", "start": 0.830, "end": 0.860},
                    {"id": 18, "name": "Triple Apex 3", "start": 0.860, "end": 0.890},
                    {"id": 19, "name": "Turn 19", "start": 0.910, "end": 0.940},
                    {"id": 20, "name": "The Andretti", "start": 0.960, "end": 0.990},
                ],
            },
        },
    },
    "circuit_of_the_americas_national": {
        "name": "Circuit of the Americas National",
        "aliases": ["circuit_of_the_americas_national", "cota-national"],
        "default_config": "national",
        "configs": {
            "national": {
                "name": "National",
                "aliases": ["national"],
                "corners": [
                    {"id": 1, "name": "Big Red", "start": 0.020, "end": 0.060},
                    {"id": 2, "name": "Turn 2", "start": 0.080, "end": 0.110},
                    {"id": 3, "name": "Esses 1", "start": 0.130, "end": 0.150},
                    {"id": 4, "name": "Esses 2", "start": 0.150, "end": 0.170},
                    {"id": 5, "name": "Esses 3", "start": 0.170, "end": 0.190},
                    {"id": 6, "name": "Esses 4", "start": 0.190, "end": 0.210},
                    {"id": 7, "name": "Turn 7", "start": 0.250, "end": 0.280},
                    {"id": 8, "name": "Turn 8", "start": 0.280, "end": 0.310},
                    {"id": 9, "name": "Turn 9", "start": 0.310, "end": 0.340},
                    {"id": 10, "name": "Turn 10", "start": 0.380, "end": 0.400},
                    {"id": 11, "name": "The Bobby Pin", "start": 0.420, "end": 0.470},
                    {"id": 12, "name": "Turn 12", "start": 0.550, "end": 0.600},
                    {"id": 13, "name": "Turn 13", "start": 0.650, "end": 0.680},
                    {"id": 14, "name": "Turn 14", "start": 0.680, "end": 0.710},
                    {"id": 15, "name": "Turn 15", "start": 0.710, "end": 0.750},
                ],
            },
        },
    },
    "donington_park_national": {
        "name": "Donington Park National",
        "aliases": ["donington_park_national", "donington_park"],
        "default_config": "national",
        "configs": {
            "national": {
                "name": "National",
                "aliases": ["national"],
                "corners": [
                    {"id": 1, "name": "Redgate", "start": 0.020, "end": 0.080},
                    {"id": 2, "name": "Hollywood", "start": 0.150, "end": 0.180},
                    {"id": 3, "name": "Craner Curves", "start": 0.180, "end": 0.250},
                    {"id": 4, "name": "Old Hairpin", "start": 0.320, "end": 0.380},
                    {"id": 5, "name": "Schwantz Curve", "start": 0.450, "end": 0.480},
                    {"id": 6, "name": "McLean's", "start": 0.480, "end": 0.520},
                    {"id": 7, "name": "Coppice", "start": 0.650, "end": 0.720},
                    {"id": 8, "name": "Goddard's 1", "start": 0.850, "end": 0.880},
                    {"id": 9, "name": "Goddard's 2", "start": 0.880, "end": 0.920},
                ],
            },
        },
    },
    "fuji_speedway": {
        "name": "Fuji Speedway",
        "aliases": ["fuji_speedway", "fuji", "fuji-speedway"],
        "default_config": "full",
        "configs": {
            "full": {
                "name": "Full",
                "aliases": ["full"],
                "corners": [
                    {"id": 1, "name": "TGR Corner", "start": 0.020, "end": 0.080},
                    {"id": 2, "name": "Coca-Cola 1", "start": 0.120, "end": 0.150},
                    {"id": 3, "name": "Coca-Cola 2", "start": 0.150, "end": 0.180},
                    {"id": 4, "name": "100R", "start": 0.220, "end": 0.280},
                    {"id": 5, "name": "Turn 5", "start": 0.350, "end": 0.380},
                    {"id": 6, "name": "Hairpin", "start": 0.380, "end": 0.420},
                    {"id": 7, "name": "Turn 7", "start": 0.420, "end": 0.450},
                    {"id": 8, "name": "300R", "start": 0.550, "end": 0.600},
                    {"id": 9, "name": "Dunlop 1", "start": 0.650, "end": 0.680},
                    {"id": 10, "name": "Dunlop 2", "start": 0.680, "end": 0.710},
                    {"id": 11, "name": "Turn 11", "start": 0.750, "end": 0.780},
                    {"id": 12, "name": "GR Supra 1", "start": 0.820, "end": 0.850},
                    {"id": 13, "name": "GR Supra 2", "start": 0.850, "end": 0.880},
                    {"id": 14, "name": "Panasonic 1", "start": 0.900, "end": 0.930},
                    {"id": 15, "name": "Panasonic 2", "start": 0.930, "end": 0.960},
                    {"id": 16, "name": "Final Corner", "start": 0.960, "end": 0.990},
                ],
            },
        },
    },
    "fuji_speedway_gp_short": {
        "name": "Fuji Speedway GP Short",
        "aliases": ["fuji_speedway_gp_short"],
        "default_config": "gp_short",
        "configs": {
            "gp_short": {
                "name": "GP Short",
                "aliases": ["gp_short"],
                "corners": [
                    {"id": 1, "name": "TGR Corner", "start": 0.020, "end": 0.080},
                    {"id": 2, "name": "Coca-Cola 1", "start": 0.120, "end": 0.150},
                    {"id": 3, "name": "Coca-Cola 2", "start": 0.150, "end": 0.180},
                    {"id": 4, "name": "100R", "start": 0.220, "end": 0.280},
                    {"id": 5, "name": "Turn 5", "start": 0.350, "end": 0.380},
                    {"id": 6, "name": "300R", "start": 0.420, "end": 0.470},
                    {"id": 7, "name": "Dunlop 1", "start": 0.570, "end": 0.600},
                    {"id": 8, "name": "Dunlop 2", "start": 0.600, "end": 0.630},
                    {"id": 9, "name": "Turn 9", "start": 0.670, "end": 0.700},
                    {"id": 10, "name": "Turn 10", "start": 0.700, "end": 0.730},
                    {"id": 11, "name": "GR Supra 1", "start": 0.780, "end": 0.810},
                    {"id": 12, "name": "GR Supra 2", "start": 0.810, "end": 0.840},
                    {"id": 13, "name": "Panasonic", "start": 0.890, "end": 0.940},
                    {"id": 14, "name": "Final Corner", "start": 0.940, "end": 0.990},
                ],
            },
        },
    },
    "imola": {
        "name": "Imola",
        "aliases": ["imola", "autodromo-enzo-e-dino-ferrari"],
        "default_config": "full",
        "configs": {
            "full": {
                "name": "Full",
                "aliases": ["full"],
                "corners": [
                    {"id": 1, "name": "Turn 1 (Start Straight Kink)", "start": 0.010, "end": 0.030},
                    {"id": 2, "name": "Variante Tamburello 1", "start": 0.050, "end": 0.080},
                    {"id": 3, "name": "Variante Tamburello 2", "start": 0.080, "end": 0.110},
                    {"id": 4, "name": "Variante Tamburello 3", "start": 0.110, "end": 0.140},
                    {"id": 5, "name": "Variante Villeneuve 1", "start": 0.180, "end": 0.210},
                    {"id": 6, "name": "Variante Villeneuve 2", "start": 0.210, "end": 0.240},
                    {"id": 7, "name": "Tosa", "start": 0.280, "end": 0.320},
                    {"id": 8, "name": "Piratella Kink", "start": 0.360, "end": 0.380},
                    {"id": 9, "name": "Piratella", "start": 0.380, "end": 0.420},
                    {"id": 10, "name": "Acque Minerali Kink", "start": 0.460, "end": 0.480},
                    {"id": 11, "name": "Acque Minerali 1", "start": 0.480, "end": 0.510},
                    {"id": 12, "name": "Acque Minerali 2", "start": 0.510, "end": 0.540},
                    {"id": 13, "name": "Acque Minerali 3", "start": 0.540, "end": 0.570},
                    {"id": 14, "name": "Variante Alta 1", "start": 0.620, "end": 0.650},
                    {"id": 15, "name": "Variante Alta 2", "start": 0.650, "end": 0.680},
                    {"id": 16, "name": "Rivazza Kink", "start": 0.720, "end": 0.740},
                    {"id": 17, "name": "Rivazza 1", "start": 0.740, "end": 0.780},
                    {"id": 18, "name": "Rivazza 2", "start": 0.780, "end": 0.820},
                    {"id": 19, "name": "Finish Straight Kink", "start": 0.960, "end": 0.980},
                ],
            },
        },
    },
    "laguna_seca": {
        "name": "Laguna Seca",
        "aliases": ["laguna_seca", "laguna", "laguna-seca", "mazda-raceway", "weathertech-raceway"],
        "default_config": "full",
        "configs": {
            "full": {
                "name": "Full",
                "aliases": ["full", "11-corner", "11_corner"],
                "corners": [
                    {"id": 1, "name": "Turn 1", "start": 0.020, "end": 0.045},
                    {"id": 2, "name": "Andretti Hairpin", "start": 0.070, "end": 0.125},
                    {"id": 3, "name": "Turn 3", "start": 0.185, "end": 0.225},
                    {"id": 4, "name": "Turn 4", "start": 0.285, "end": 0.325},
                    {"id": 5, "name": "Turn 5", "start": 0.380, "end": 0.425},
                    {"id": 6, "name": "Turn 6", "start": 0.475, "end": 0.520},
                    {"id": 7, "name": "Turn 7", "start": 0.555, "end": 0.590},
                    {"id": 8, "name": "Corkscrew", "start": 0.590, "end": 0.645},
                    {"id": 9, "name": "Rainey Curve", "start": 0.700, "end": 0.745},
                    {"id": 10, "name": "Turn 10", "start": 0.810, "end": 0.850},
                    {"id": 11, "name": "Turn 11", "start": 0.900, "end": 0.960},
                ],
            },
        },
    },
    "monza": {
        "name": "Monza",
        "aliases": ["monza"],
        "default_config": "v0_4",
        "configs": {
            "v0_4": {
                "name": "v0.4",
                "aliases": ["v0.4", "v0_4", "full", "gp"],
                "corners": [
                    {"id": 1, "name": "Variante del Rettifilo (T1) Right", "start": 0.145, "end": 0.170},
                    {"id": 2, "name": "Variante del Rettifilo (T2) Left", "start": 0.170, "end": 0.195},
                    {"id": 3, "name": "Curva Grande (T3)", "start": 0.220, "end": 0.285},
                    {"id": 4, "name": "Variante della Roggia (T4) Left", "start": 0.345, "end": 0.372},
                    {"id": 5, "name": "Variante della Roggia (T5) Right", "start": 0.372, "end": 0.402},
                    {"id": 6, "name": "Lesmo 1 (T6)", "start": 0.455, "end": 0.505},
                    {"id": 7, "name": "Lesmo 2 (T7)", "start": 0.515, "end": 0.565},
                    {"id": 8, "name": "Variante Ascari (T8) Left", "start": 0.635, "end": 0.665},
                    {"id": 9, "name": "Variante Ascari (T9) Right", "start": 0.665, "end": 0.695},
                    {"id": 10, "name": "Variante Ascari (T10) Left", "start": 0.695, "end": 0.720},
                    {"id": 11, "name": "Curva Parabolica (T11)", "start": 0.875, "end": 0.930},
                ],
            },
        },
    },
    "mount_panorama": {
        "name": "Mount Panorama",
        "aliases": ["mount_panorama", "bathurst", "mount-panorama"],
        "default_config": "full",
        "configs": {
            "full": {
                "name": "Full",
                "aliases": ["full"],
                "corners": [
                    {"id": 1, "name": "Hell Corner", "start": 0.015, "end": 0.045},
                    {"id": 2, "name": "Griffins Bend", "start": 0.185, "end": 0.220},
                    {"id": 3, "name": "The Cutting 1", "start": 0.245, "end": 0.275},
                    {"id": 4, "name": "The Cutting 2", "start": 0.275, "end": 0.300},
                    {"id": 5, "name": "Quarry Corner", "start": 0.315, "end": 0.340},
                    {"id": 6, "name": "Reid Park Right", "start": 0.355, "end": 0.385},
                    {"id": 7, "name": "Reid Park Left", "start": 0.385, "end": 0.415},
                    {"id": 8, "name": "Sulman Park", "start": 0.430, "end": 0.470},
                    {"id": 9, "name": "McPhillamy Park", "start": 0.485, "end": 0.520},
                    {"id": 10, "name": "Brock's Skyline", "start": 0.530, "end": 0.550},
                    {"id": 11, "name": "The Esses", "start": 0.550, "end": 0.570},
                    {"id": 12, "name": "The Dipper", "start": 0.570, "end": 0.590},
                    {"id": 13, "name": "Forrest's Elbow", "start": 0.590, "end": 0.620},
                    {"id": 14, "name": "The Chase Kink", "start": 0.900, "end": 0.920},
                    {"id": 15, "name": "The Chase Left", "start": 0.920, "end": 0.945},
                    {"id": 16, "name": "The Chase Right", "start": 0.945, "end": 0.965},
                    {"id": 17, "name": "Murray's Corner", "start": 0.985, "end": 0.998},
                ],
            },
        },
    },
    "nurburgring_nordschleife": {
        "name": "Nurburgring Nordschleife",
        "aliases": ["nurburgring_nordschleife", "nordschleife", "nurburgring-nordschleife", "nurburgring_touristenfahrten", "nurburgring_24h"],
        "default_config": "24h",
        "configs": {
            "24h": {
                "name": "24H",
                "aliases": ["24h", "touristenfahrten"],
                "corners": [
                    {"id": 1, "name": "Antoniusbuche", "start": 0.010, "end": 0.020},
                    {"id": 2, "name": "Tiergarten 1", "start": 0.020, "end": 0.030},
                    {"id": 3, "name": "Tiergarten 2", "start": 0.030, "end": 0.040},
                    {"id": 4, "name": "Hohenrain-Schikane 1", "start": 0.040, "end": 0.045},
                    {"id": 5, "name": "Hohenrain-Schikane 2", "start": 0.045, "end": 0.050},
                    {"id": 6, "name": "Hohenrain-Schikane 3", "start": 0.050, "end": 0.055},
                    {"id": 7, "name": "T13", "start": 0.055, "end": 0.060},
                    {"id": 8, "name": "Sabine-Schmitz-Kurve", "start": 0.060, "end": 0.065},
                    {"id": 9, "name": "Hatzenbach Bogen", "start": 0.065, "end": 0.070},
                    {"id": 10, "name": "Hatzenbach 1", "start": 0.070, "end": 0.075},
                    {"id": 11, "name": "Hatzenbach 2", "start": 0.075, "end": 0.080},
                    {"id": 12, "name": "Hatzenbach 3", "start": 0.080, "end": 0.085},
                    {"id": 13, "name": "Hatzenbach 4", "start": 0.085, "end": 0.090},
                    {"id": 14, "name": "Hatzenbach 5", "start": 0.090, "end": 0.095},
                    {"id": 15, "name": "Hatzenbach 6", "start": 0.095, "end": 0.100},
                    {"id": 16, "name": "Hatzenbach 7", "start": 0.100, "end": 0.105},
                    {"id": 17, "name": "Hoheichen 1", "start": 0.105, "end": 0.110},
                    {"id": 18, "name": "Hoheichen 2", "start": 0.110, "end": 0.115},
                    {"id": 19, "name": "Quiddelbacher Höhe 1", "start": 0.115, "end": 0.120},
                    {"id": 20, "name": "Quiddelbacher Höhe 2", "start": 0.120, "end": 0.125},
                    {"id": 21, "name": "Quiddelbacher Höhe 3", "start": 0.125, "end": 0.130},
                    {"id": 22, "name": "Flugplatz", "start": 0.130, "end": 0.135},
                    {"id": 23, "name": "Kottenborn", "start": 0.135, "end": 0.140},
                    {"id": 24, "name": "Schwedenkreuz", "start": 0.140, "end": 0.145},
                    {"id": 25, "name": "Aremberg", "start": 0.145, "end": 0.150},
                    {"id": 26, "name": "Fuchsröhre 1", "start": 0.150, "end": 0.155},
                    {"id": 27, "name": "Fuchsröhre 2", "start": 0.155, "end": 0.160},
                    {"id": 28, "name": "Fuchsröhre 3", "start": 0.160, "end": 0.165},
                    {"id": 29, "name": "Adenauer Forst 1", "start": 0.165, "end": 0.170},
                    {"id": 30, "name": "Adenauer Forst 2", "start": 0.170, "end": 0.175},
                    {"id": 31, "name": "Adenauer Forst 3", "start": 0.175, "end": 0.180},
                    {"id": 32, "name": "Adenauer Forst 4", "start": 0.180, "end": 0.185},
                    {"id": 33, "name": "Metzgesfeld 1", "start": 0.185, "end": 0.190},
                    {"id": 34, "name": "Metzgesfeld 2", "start": 0.190, "end": 0.195},
                    {"id": 35, "name": "Kallenhard", "start": 0.195, "end": 0.200},
                    {"id": 36, "name": "Spiegelkurve 1", "start": 0.200, "end": 0.205},
                    {"id": 37, "name": "Spiegelkurve 2", "start": 0.205, "end": 0.210},
                    {"id": 38, "name": "Miss-hit-miss 1", "start": 0.210, "end": 0.215},
                    {"id": 39, "name": "Miss-hit-miss 2", "start": 0.215, "end": 0.220},
                    {"id": 40, "name": "Miss-hit-miss 3", "start": 0.220, "end": 0.225},
                    {"id": 41, "name": "Wehrseifen 1", "start": 0.225, "end": 0.230},
                    {"id": 42, "name": "Wehrseifen 2", "start": 0.230, "end": 0.235},
                    {"id": 43, "name": "Wehrseifen 3", "start": 0.235, "end": 0.240},
                    {"id": 44, "name": "Breidscheid 1", "start": 0.240, "end": 0.245},
                    {"id": 45, "name": "Breidscheid 2", "start": 0.245, "end": 0.250},
                    {"id": 46, "name": "Ex-Mühle", "start": 0.250, "end": 0.255},
                    {"id": 47, "name": "Lauda Links", "start": 0.255, "end": 0.260},
                    {"id": 48, "name": "Bergwerk", "start": 0.260, "end": 0.265},
                    {"id": 49, "name": "Kesselchen", "start": 0.265, "end": 0.270},
                    {"id": 50, "name": "Klostertal", "start": 0.270, "end": 0.275},
                    {"id": 51, "name": "Mutkurve", "start": 0.275, "end": 0.280},
                    {"id": 52, "name": "Steilstrecke", "start": 0.280, "end": 0.285},
                    {"id": 53, "name": "Carraciola-Karussell", "start": 0.285, "end": 0.295},
                    {"id": 54, "name": "Hohe Acht", "start": 0.295, "end": 0.300},
                    {"id": 55, "name": "Hedgwigshöhe", "start": 0.300, "end": 0.305},
                    {"id": 56, "name": "Wippermann", "start": 0.305, "end": 0.310},
                    {"id": 57, "name": "Eschbach 1", "start": 0.310, "end": 0.315},
                    {"id": 58, "name": "Eschbach 2", "start": 0.315, "end": 0.320},
                    {"id": 59, "name": "Brünnchen 1", "start": 0.320, "end": 0.325},
                    {"id": 60, "name": "Brünnchen 2", "start": 0.325, "end": 0.330},
                    {"id": 61, "name": "Eiskurve 1", "start": 0.330, "end": 0.335},
                    {"id": 62, "name": "Eiskurve 2", "start": 0.335, "end": 0.340},
                    {"id": 63, "name": "Pflanzgarten I", "start": 0.340, "end": 0.345},
                    {"id": 64, "name": "Pflanzgarten II", "start": 0.345, "end": 0.350},
                    {"id": 65, "name": "Stefan-Bellof-S 1", "start": 0.350, "end": 0.355},
                    {"id": 66, "name": "Stefan-Bellof-S 2", "start": 0.355, "end": 0.360},
                    {"id": 67, "name": "Stefan-Bellof-S 3", "start": 0.360, "end": 0.365},
                    {"id": 68, "name": "Schwalbenschwanz 1", "start": 0.365, "end": 0.370},
                    {"id": 69, "name": "Schwalbenschwanz 2", "start": 0.370, "end": 0.375},
                    {"id": 70, "name": "Kleine Karussell", "start": 0.375, "end": 0.380},
                    {"id": 71, "name": "Galgenkopf 1", "start": 0.380, "end": 0.390},
                    {"id": 72, "name": "Galgenkopf 2", "start": 0.390, "end": 0.400},
                ],
            },
        },
    },
    "oulton_park_fosters": {
        "name": "Oulton Park Fosters",
        "aliases": ["oulton_park_fosters"],
        "default_config": "fosters",
        "configs": {
            "fosters": {
                "name": "Fosters",
                "aliases": ["fosters"],
                "corners": [
                    {"id": 1, "name": "Old Hall", "start": 0.030, "end": 0.100},
                    {"id": 2, "name": "Cascades", "start": 0.180, "end": 0.300},
                    {"id": 3, "name": "Fosters", "start": 0.365, "end": 0.420},
                    {"id": 4, "name": "Knickerbrook", "start": 0.580, "end": 0.650},
                    {"id": 5, "name": "Druids", "start": 0.790, "end": 0.870},
                    {"id": 6, "name": "Lodge Corner", "start": 0.935, "end": 0.980},
                    {"id": 7, "name": "Deer Leap", "start": 0.980, "end": 0.997},
                ],
            },
        },
    },
    "oulton_park_international": {
        "name": "Oulton Park International",
        "aliases": ["oulton_park_international"],
        "default_config": "international",
        "configs": {
            "international": {
                "name": "International",
                "aliases": ["international"],
                "corners": [
                    {"id": 1, "name": "Old Hall", "start": 0.020, "end": 0.080},
                    {"id": 2, "name": "Denton's", "start": 0.110, "end": 0.135},
                    {"id": 3, "name": "Cascades", "start": 0.135, "end": 0.205},
                    {"id": 4, "name": "Island Bend", "start": 0.280, "end": 0.340},
                    {"id": 5, "name": "Shell Oils", "start": 0.420, "end": 0.485},
                    {"id": 6, "name": "Britten's 1", "start": 0.550, "end": 0.565},
                    {"id": 7, "name": "Britten's 2", "start": 0.565, "end": 0.580},
                    {"id": 8, "name": "Britten's 3", "start": 0.580, "end": 0.595},
                    {"id": 9, "name": "Britten's 4", "start": 0.595, "end": 0.610},
                    {"id": 10, "name": "Hislop's 1", "start": 0.700, "end": 0.725},
                    {"id": 11, "name": "Hislop's 2", "start": 0.725, "end": 0.755},
                    {"id": 12, "name": "Knickerbrook", "start": 0.800, "end": 0.850},
                    {"id": 13, "name": "Clay Hill", "start": 0.865, "end": 0.880},
                    {"id": 14, "name": "Water Tower", "start": 0.880, "end": 0.895},
                    {"id": 15, "name": "Druids", "start": 0.895, "end": 0.940},
                    {"id": 16, "name": "Lodge Corner", "start": 0.965, "end": 0.988},
                    {"id": 17, "name": "Deer Leap", "start": 0.988, "end": 0.998},
                ],
            },
        },
    },
    "red_bull_ring": {
        "name": "Red Bull Ring",
        "aliases": ["red_bull_ring", "red-bull-ring", "rbr"],
        "default_config": "full",
        "configs": {
            "full": {
                "name": "Full",
                "aliases": ["full", "gp"],
                "corners": [
                    {"id": 1, "name": "T1 - Niki Lauda Turn", "start": 0.015, "end": 0.070},
                    {"id": 2, "name": "T2 - Münzer Turn", "start": 0.135, "end": 0.165},
                    {"id": 3, "name": "T3", "start": 0.190, "end": 0.255},
                    {"id": 4, "name": "T4 - Rauch Turn", "start": 0.345, "end": 0.395},
                    {"id": 5, "name": "T5", "start": 0.495, "end": 0.520},
                    {"id": 6, "name": "T6", "start": 0.545, "end": 0.585},
                    {"id": 7, "name": "T7 - Graz Turn", "start": 0.595, "end": 0.645},
                    {"id": 8, "name": "T8", "start": 0.735, "end": 0.770},
                    {"id": 9, "name": "T9 - Jochen Rindt Turn", "start": 0.800, "end": 0.855},
                    {"id": 10, "name": "T10", "start": 0.900, "end": 0.955},
                ],
            },
            "national": {
                "name": "National",
                "aliases": ["national"],
                "corners": [
                    {"id": 1, "name": "Turn 3", "start": 0.200, "end": 0.260},
                    {"id": 2, "name": "Turn 4", "start": 0.350, "end": 0.400},
                    {"id": 3, "name": "Turn 5", "start": 0.500, "end": 0.520},
                    {"id": 4, "name": "Turn 6", "start": 0.550, "end": 0.600},
                    {"id": 5, "name": "Turn 7", "start": 0.600, "end": 0.650},
                    {"id": 6, "name": "Turn 8", "start": 0.750, "end": 0.800},
                    {"id": 7, "name": "Turn 9", "start": 0.850, "end": 0.900},
                ],
            },
        },
    },
    "suzuka": {
        "name": "Suzuka Circuit",
        "aliases": ["suzuka", "suzuka-circuit", "suzuka_circuit"],
        "default_config": "full",
        "configs": {
            "full": {
                "name": "Full",
                "aliases": ["full", "gp"],
                "corners": [
                    {"id": 1, "name": "Turn 1", "start": 0.010, "end": 0.038},
                    {"id": 2, "name": "Turn 2", "start": 0.038, "end": 0.072},
                    {"id": 3, "name": "Turn 3", "start": 0.095, "end": 0.118},
                    {"id": 4, "name": "Turn 4", "start": 0.118, "end": 0.143},
                    {"id": 5, "name": "Turn 5", "start": 0.143, "end": 0.170},
                    {"id": 6, "name": "Turn 6", "start": 0.170, "end": 0.205},
                    {"id": 7, "name": "Dunlop Curve", "start": 0.205, "end": 0.280},
                    {"id": 8, "name": "Degner 1", "start": 0.305, "end": 0.330},
                    {"id": 9, "name": "Degner 2", "start": 0.330, "end": 0.360},
                    {"id": 10, "name": "Turn 10 (Bridge Curve)", "start": 0.395, "end": 0.410},
                    {"id": 11, "name": "Hairpin", "start": 0.425, "end": 0.460},
                    {"id": 12, "name": "Turn 12 (200R)", "start": 0.540, "end": 0.560},
                    {"id": 13, "name": "Spoon 1", "start": 0.615, "end": 0.650},
                    {"id": 14, "name": "Spoon 2", "start": 0.650, "end": 0.695},
                    {"id": 15, "name": "130R", "start": 0.835, "end": 0.870},
                    {"id": 16, "name": "Casio Triangle Right", "start": 0.925, "end": 0.950},
                    {"id": 17, "name": "Casio Triangle Left", "start": 0.950, "end": 0.970},
                    {"id": 18, "name": "Final Right", "start": 0.970, "end": 0.990},
                ],
            },
        },
    },
    "suzuka_east": {
        "name": "Suzuka East",
        "aliases": ["suzuka_east", "suzuka-east"],
        "default_config": "east",
        "configs": {
            "east": {
                "name": "East",
                "aliases": ["east"],
                "corners": [
                    {"id": 1, "name": "Turn 1", "start": 0.010, "end": 0.040},
                    {"id": 2, "name": "Turn 2", "start": 0.040, "end": 0.070},
                    {"id": 3, "name": "Turn 3", "start": 0.100, "end": 0.130},
                    {"id": 4, "name": "Turn 4", "start": 0.130, "end": 0.160},
                    {"id": 5, "name": "Turn 5", "start": 0.160, "end": 0.190},
                    {"id": 6, "name": "Turn 6", "start": 0.190, "end": 0.220},
                    {"id": 7, "name": "Dunlop Curve", "start": 0.220, "end": 0.280},
                ],
            },
        },
    },
    "suzuka_west": {
        "name": "Suzuka West",
        "aliases": ["suzuka_west", "suzuka-west"],
        "default_config": "west",
        "configs": {
            "west": {
                "name": "West",
                "aliases": ["west"],
                "corners": [
                    {"id": 1, "name": "Turn 1", "start": 0.010, "end": 0.040},
                    {"id": 2, "name": "Turn 2", "start": 0.040, "end": 0.070},
                    {"id": 3, "name": "Turn 3", "start": 0.100, "end": 0.130},
                    {"id": 4, "name": "Turn 4", "start": 0.130, "end": 0.160},
                    {"id": 5, "name": "Turn 5", "start": 0.160, "end": 0.190},
                    {"id": 6, "name": "Turn 6", "start": 0.190, "end": 0.220},
                    {"id": 7, "name": "130R West", "start": 0.220, "end": 0.280},
                    {"id": 8, "name": "Casio Chicane 1", "start": 0.280, "end": 0.310},
                    {"id": 9, "name": "Casio Chicane 2", "start": 0.310, "end": 0.340},
                    {"id": 10, "name": "Turn 10", "start": 0.340, "end": 0.370},
                ],
            },
        },
    },
    "watkins_glen_international": {
        "name": "Watkins Glen International",
        "aliases": ["watkins_glen_international", "watkins_glen_internati"],
        "default_config": "full",
        "configs": {
            "full": {
                "name": "Full",
                "aliases": ["full"],
                "corners": [
                    {"id": 1, "name": "Turn 1", "start": 0.020, "end": 0.075},
                    {"id": 2, "name": "Esses 1", "start": 0.130, "end": 0.170},
                    {"id": 3, "name": "Esses 2", "start": 0.170, "end": 0.225},
                    {"id": 4, "name": "Esses 3", "start": 0.225, "end": 0.275},
                    {"id": 5, "name": "Inner Loop", "start": 0.420, "end": 0.505},
                    {"id": 6, "name": "Outer Loop", "start": 0.610, "end": 0.700},
                    {"id": 7, "name": "Toe", "start": 0.730, "end": 0.775},
                    {"id": 8, "name": "Boot Right Hairpin", "start": 0.790, "end": 0.845},
                    {"id": 9, "name": "Boot Crest Right", "start": 0.855, "end": 0.895},
                    {"id": 10, "name": "Heel", "start": 0.905, "end": 0.945},
                    {"id": 11, "name": "Turn 10", "start": 0.955, "end": 0.980},
                    {"id": 12, "name": "Turn 11", "start": 0.985, "end": 0.998},
                ],
            },
        },
    },
    "watkins_glen_international_short": {
        "name": "Watkins Glen International Short",
        "aliases": ["watkins_glen_international_short"],
        "default_config": "short",
        "configs": {
            "short": {
                "name": "Short",
                "aliases": ["short"],
                "corners": [
                    {"id": 1, "name": "Turn 1", "start": 0.020, "end": 0.075},
                    {"id": 2, "name": "Turn 2", "start": 0.130, "end": 0.170},
                    {"id": 3, "name": "Turn 3", "start": 0.170, "end": 0.225},
                    {"id": 4, "name": "Turn 4", "start": 0.225, "end": 0.275},
                    {"id": 5, "name": "Inner Loop", "start": 0.420, "end": 0.505},
                    {"id": 6, "name": "Turn 5 (Carousel)", "start": 0.610, "end": 0.715},
                    {"id": 7, "name": "Turn 6", "start": 0.845, "end": 0.895},
                    {"id": 8, "name": "Turn 7", "start": 0.930, "end": 0.985},
                ],
            },
        },
    },
    "watkins_glen_international_gp_inner_loop": {
        "name": "Watkins Glen International GP Inner Loop",
        "aliases": ["watkins_glen_international_gp_inner_loop"],
        "default_config": "gp_inner_loop",
        "configs": {
            "gp_inner_loop": {
                "name": "GP Inner Loop",
                "aliases": ["gp_inner_loop"],
                "corners": [
                    {"id": 1, "name": "Turn 1", "start": 0.020, "end": 0.080},
                    {"id": 2, "name": "Esses 1", "start": 0.120, "end": 0.165},
                    {"id": 3, "name": "Esses 2", "start": 0.165, "end": 0.220},
                    {"id": 4, "name": "Esses 3", "start": 0.220, "end": 0.270},
                    {"id": 5, "name": "Inner Loop", "start": 0.420, "end": 0.505},
                    {"id": 6, "name": "Outer Loop", "start": 0.610, "end": 0.715},
                    {"id": 7, "name": "Turn 6", "start": 0.845, "end": 0.895},
                    {"id": 8, "name": "Turn 7", "start": 0.930, "end": 0.985},
                ],
            },
        },
    },
    "watkins_glen_international_short_inner_loop": {
        "name": "Watkins Glen International Short Inner Loop",
        "aliases": ["watkins_glen_international_short_inner_loop"],
        "default_config": "short_inner_loop",
        "configs": {
            "short_inner_loop": {
                "name": "Short Inner Loop",
                "aliases": ["short_inner_loop"],
                "corners": [
                    {"id": 1, "name": "Turn 1", "start": 0.020, "end": 0.075},
                    {"id": 2, "name": "Turn 2", "start": 0.130, "end": 0.170},
                    {"id": 3, "name": "Turn 3", "start": 0.170, "end": 0.225},
                    {"id": 4, "name": "Turn 4", "start": 0.225, "end": 0.275},
                    {"id": 5, "name": "Inner Loop", "start": 0.420, "end": 0.505},
                    {"id": 6, "name": "Turn 5 (Carousel)", "start": 0.610, "end": 0.715},
                    {"id": 7, "name": "Turn 6", "start": 0.845, "end": 0.895},
                    {"id": 8, "name": "Turn 7", "start": 0.930, "end": 0.985},
                ],
            },
        },
    },
}


def build_track_profile(track_key: str, config_key: str) -> dict:
    """Build a track profile dictionary."""
    track = TRACK_CATALOG[track_key]
    config = track["configs"][config_key]
    return {
        "track_key": track_key,
        "track_name": track["name"],
        "config_key": config_key,
        "config_name": config["name"],
        "display_name": f"{track['name']} ({config['name']})",
        "corners": config.get("corners", []),
    }


def select_track_profile(
    path: str = None,
    track_name: str = None,
    config_name: str = None
) -> tuple:
    """Select a track profile based on path, track name, or config name.

    Returns:
        tuple: (track_key, track_profile) or (None, None) if not found
    """
    if track_name:
        # Direct match first
        if track_name in TRACK_CATALOG:
            track = TRACK_CATALOG[track_name]
            return track_name, build_track_profile(track_name, track["default_config"])

        # Search aliases
        for track_key, track in TRACK_CATALOG.items():
            labels = [track_key, *track.get("aliases", [])]
            if track_name.lower() in [l.lower() for l in labels]:
                if config_name:
                    for config_key, config in track["configs"].items():
                        config_labels = [config_key, *config.get("aliases", [])]
                        if config_name.lower() in [l.lower() for l in config_labels]:
                            return track_key, build_track_profile(track_key, config_key)
                return track_key, build_track_profile(track_key, track["default_config"])
        return None, None

    if path:
        path_l = os.path.normpath(path).lower()
        for track_key, track in TRACK_CATALOG.items():
            if any(alias in path_l for alias in track.get("aliases", [])):
                for config_key, config in track["configs"].items():
                    if any(alias in path_l for alias in config.get("aliases", [])):
                        return track_key, build_track_profile(track_key, config_key)
                return track_key, build_track_profile(track_key, track["default_config"])

    return None, None




def find_track_by_name(track_name: str) -> tuple:
    """Find a track by name or alias, return (key, profile)."""
    if not track_name:
        return None, None

    # Direct key match
    if track_name in TRACK_CATALOG:
        return track_name, build_track_profile(track_name, TRACK_CATALOG[track_name]["default_config"])

    # Search in aliases (case-insensitive)
    track_name_lower = track_name.lower().replace("_", "-").replace(" ", "-")
    for track_key, track in TRACK_CATALOG.items():
        for alias in track.get("aliases", []):
            alias_normalized = alias.lower().replace("_", "-").replace(" ", "-")
            if track_name_lower == alias_normalized or track_name_lower.startswith(alias_normalized):
                return track_key, build_track_profile(track_key, track["default_config"])

    return None, None
