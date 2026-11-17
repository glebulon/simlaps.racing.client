# Laguna Seca corner calibration

The Laguna Seca Full profile uses `Corkscrew` `0.645..0.715` and `Rainey Curve` `0.715..0.790`.

The bounds were calibrated from controlled ACE `0.9.1+release.6` observations (revision `4375debd69b77e448235afb7013b498a9f372079`) on two valid BMW M4 GT3 laps. The Corkscrew trace showed the approach near `0.612`, a direction-change sequence around `0.666..0.715`, the paired heading/yaw landmarks around `0.682..0.695`, and the raw speed minimum near `0.684..0.686`. The unchanged canonical resampling and median filter can report an apex just before that raw trough, around `0.682`. A separate trace placed the Rainey geometry around `0.752..0.764`. In corrected replay, canonical C8 apex speeds were about `59.5` and `53.5` km/h across the two laps, while C9 was about `124.9` and `127.6` km/h; these are validation observations, not catalog constants. Official corner naming and circuit context follow [WeatherTech Raceway Laguna Seca](https://weathertechraceway.com/pages/track-information/).

These are controlled live-observation calibrations of the in-game normalized-progress signal, not an official shared-memory specification or a substitute for a full track survey. They should be rechecked if ACE changes the track mesh, progress mapping, or car/build behavior.
