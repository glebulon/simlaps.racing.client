# Laguna Seca profile calibration evidence

Date: 2026-09-12<br>
ACE source: controlled ACE 0.9.1 release.6 build, identified from the same-session ACE log header<br>
Report source: one HTML report with five captured laps, of which laps 4 and 5 were valid

This is a sanitized geometry cross-check for the catalog's Laguna Seca full profile. The
HTML report was parsed from its embedded `const DATA` payload. Valid-lap track points were
taken directly from the renderer's already-resampled approximately 200 point traces, with
the recorded absolute `lap_progress` retained. Rounded coordinates, global chord-angle
geometry, and speed landmarks were compared before checking the catalog windows.

`G` is the global chord-angle landmark and `S` is the smoothed speed minimum.
Values separated by `/` are laps 4 and 5; comma-separated values are multiple
landmarks in one lap.

| Corner | Catalog window | G (lap 4 / lap 5) | S (lap 4 / lap 5) | Evidence note |
| ---: | :---: | ---: | ---: | --- |
| 1 | 0.040–0.075 | 0.0704 / 0.0653 | — / — | Full throttle; no speed trough required |
| 2 | 0.080–0.145 | 0.1357 / 0.1357 | 0.1256 / 0.1307 | Secondary trough at 0.1457 is just outside the window |
| 3 | 0.185–0.235 | 0.2161 / 0.2161 | 0.2312 / 0.2111, 0.2261 | Speed and geometry are somewhat ambiguous |
| 4 | 0.265–0.325 | 0.2864 / 0.2915 | 0.2864 / 0.2814, 0.3015 | Speed and geometry are somewhat ambiguous |
| 5 | 0.390–0.455 | 0.4271 / 0.4271 | 0.4271 / 0.4322 | Geometry and speed landmark |
| 6 | 0.500–0.565 | 0.5427 / 0.5427 | 0.5427 / 0.5427 | Geometry and speed landmark |
| 7 | 0.580–0.635 | 0.6131 / 0.6181 | — / — | Full throttle; no speed trough required |
| 8 | 0.645–0.715 | 0.6784, 0.7035 / 0.6784, 0.7035 | 0.6834 / 0.6884 | Corkscrew 8/8A pair in both laps |
| 9 | 0.715–0.790 | 0.7588 / 0.7588 | 0.7638 / 0.7638 | Geometry and speed landmark |
| 10 | 0.800–0.860 | 0.8291 / 0.8291 | 0.8291 / 0.8291 | Geometry and speed landmark |
| 11 | 0.885–0.950 | 0.9095 / 0.9095 | 0.9146 / 0.9146 | Geometry and speed landmark |

The table supports the existing eleven catalog windows. It does not promote their
confidence: the profile remains `estimated` because this source is a lossy HTML report
trace rather than a raw shared-memory capture, and it provides no per-car apex-speed
expectations. The largest discontinuity near 0.377 and its recovery to approximately
0.407 were excluded from landmark evidence; the T5 landmarks occur later, around 0.427.
No player identity, car UUID, personal path, raw trace, expected speed, or lap time is
retained here.

Reference layout: [WeatherTech Raceway Laguna Seca track information](https://weathertechraceway.com/pages/track-information)<br>
Reference racing lines: [WeatherTech Raceway Laguna Seca racing lines flyer](https://cdn.shopify.com/s/files/1/0678/1914/3354/files/WeatherTech_Raceway_Laguna_Seca_Racing_Lines_Flyer.pdf?v=1733112124)
