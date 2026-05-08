**SharedFileOut.h**

**SharedMemoryPhysics** \= "Local\\acevo\_pmf\_physics"; // from Local\\acpmf\_physics 

**SharedMemoryGraphic** \= "Local\\acevo\_pmf\_graphics"; // from Local\\acpmf\_graphics 

**SharedMemoryStatic** \= "Local\\acevo\_pmf\_static"; // from Local\\acpmf\_static

**Change log**

- \[2026-03-31\] inner structures have fixed size (see the section structure name \[size\])  
- \[2026-03-31\] **\!\!\!** display\_current\_page\_index is now an array of 16 items (it was 9 before)  
- \[2026-03-31\] the example solution has been updated

# **Enumerations**

## **ACEVO\_STATUS**

*Current operational state of the simulator.*

| Value | Meaning |
| :---- | :---- |
| AC\_OFF (0) | Simulator is not running / no session active |
| AC\_REPLAY (1) | A replay is currently being played back |
| AC\_LIVE (2) | Live driving session is active |
| AC\_PAUSE (3) | Session is paused |

## **ACEVO\_SESSION\_TYPE**

*Type of racing session currently loaded.*

| Value | Meaning |
| :---- | :---- |
| AC\_UNKNOWN (-1) | Session type not yet determined |
| AC\_TIME\_ATTACK (0) | Time attack / qualifying session |
| AC\_RACE (1) | Race session |
| AC\_HOT\_STINT (2) | Hot-stint practice |
| AC\_CRUISE (3) | Untimed cruise |

## **ACEVO\_FLAG\_TYPE**

*Race flag currently shown to the driver.*

| Value | Meaning |
| :---- | :---- |
| AC\_NO\_FLAG (0) | No flag displayed |
| AC\_WHITE\_FLAG (1) | Slow vehicle ahead on track |
| AC\_GREEN\_FLAG (2) | Track clear — racing resumed |
| AC\_RED\_FLAG (3) | Session stopped due to incident or hazard |
| AC\_BLUE\_FLAG (4) | Lapped car must yield to the race leader |
| AC\_YELLOW\_FLAG (5) | Hazard present — no overtaking |
| AC\_BLACK\_FLAG (6) | Driver disqualified / must pit immediately |
| AC\_BLACK\_WHITE\_FLAG (7) | Warning for unsportsmanlike behaviour |
| AC\_CHECKERED\_FLAG (8) | Session or race has ended |
| AC\_ORANGE\_CIRCLE\_FLAG (9) | Mechanical problem — car must pit |
| AC\_RED\_YELLOW\_STRIPES\_FLAG (10) | Slippery surface ahead on track |

## **ACEVO\_CAR\_LOCATION**

*Where on the circuit the car is currently positioned.*

| Value | Meaning |
| :---- | :---- |
| ACEVO\_UNASSIGNED (0) | Position not yet determined |
| ACEVO\_PITLANE (1) | Car is inside the pit lane |
| ACEVO\_PITENTRY (2) | Car is at the pit-lane entry |
| ACEVO\_PITEXIT (3) | Car is at the pit-lane exit |
| ACEVO\_TRACK (4) | Car is on the racing circuit |

## **ACEVO\_ENGINE\_TYPE**

*Powertrain type of the player car.*

| Value | Meaning |
| :---- | :---- |
| ACEVO\_INTERNAL\_COMBUSTION (0) | Traditional petrol/diesel internal combustion engine |
| ACEVO\_ELECTRIC\_MOTOR (1) | Fully electric powertrain |

## **ACEVO\_STARTING\_GRIP**

*Initial grip conditions at session start.*

| Value | Meaning |
| :---- | :---- |
| ACEVO\_GREEN (0) | Track grip at minimum |
| ACEVO\_FAST (1) | Track grip in advanced (fast) stage |
| ACEVO\_OPTIMUM (2) | Track conditions starting at optimum grip |

# **Structs**

## **SPageFilePhysics**

*Raw physics telemetry updated every simulation step. Contains all low-level vehicle dynamics data.*

| Type | Field | Meaning |
| :---- | :---- | :---- |
| int | packetId | Incrementing counter — detect new data packets by comparing to previous value |
| float | gas | Throttle pedal position (0.0 \= released, 1.0 \= full throttle) |
| float | brake | Brake pedal position (0.0 \= released, 1.0 \= full brake) |
| float | fuel | Remaining fuel in litres |
| int | gear | Engaged gear: 0 \= reverse, 1 \= neutral, 2+ \= forward gears |
| int | rpms | Engine speed in revolutions per minute |
| float | steerAngle | Normalised steering angle (−1.0 \= full left, \+1.0 \= full right) |
| float | speedKmh | Vehicle speed in km/h |
| float\[3\] | velocity | World-space velocity vector \[X, Y, Z\] in m/s |
| float\[3\] | accG | Acceleration in G \[lateral X, longitudinal Y, vertical Z\] |
| float\[4\] | wheelSlip | Tyre slip value per wheel \[FL, FR, RL, RR\] |
| float\[4\] | wheelLoad | Vertical tyre load in Newtons \[FL, FR, RL, RR\] |
| float\[4\] | wheelsPressure | Tyre inflation pressure in PSI \[FL, FR, RL, RR\] |
| float\[4\] | wheelAngularSpeed | Wheel rotational speed in rad/s \[FL, FR, RL, RR\] |
| float\[4\] | tyreWear | Tyre wear level (0.0 \= new, 1.0 \= fully worn) \[FL, FR, RL, RR\] |
| float\[4\] | tyreDirtyLevel | Amount of dirt / debris on each tyre surface \[FL, FR, RL, RR\] |
| float\[4\] | tyreCoreTemperature | Core temperature of each tyre in °C \[FL, FR, RL, RR\] |
| float\[4\] | camberRAD | Wheel camber angle in radians per corner \[FL, FR, RL, RR\] |
| float\[4\] | suspensionTravel | Suspension compression travel in metres \[FL, FR, RL, RR\] |
| float | drs | DRS flap state (0.0 \= closed, 1.0 \= fully open) |
| float | tc | Traction control cut intensity (0.0 \= inactive, 1.0 \= maximum) |
| float | heading | Vehicle heading relative to world north in radians |
| float | pitch | Chassis pitch angle in radians (positive \= nose up) |
| float | roll | Chassis roll angle in radians (positive \= right side down) |
| float | cgHeight | Height of the centre of gravity above the ground in metres |
| float\[5\] | carDamage | Damage level per body zone \[front, rear, left, right, centre\] (0.0–1.0) |
| int | numberOfTyresOut | Number of tyres currently outside track limits |
| int | pitLimiterOn | Pit-speed limiter active (0 \= off, 1 \= on) |
| float | abs | ABS intervention intensity (0.0 \= inactive, 1.0 \= fully active) |
| float | kersCharge | KERS/ERS battery state of charge (0.0–1.0) |
| float | kersInput | KERS/ERS power delivery level currently being deployed (0.0–1.0) |
| int | autoShifterOn | Automatic gearshift aid active (0 \= manual, 1 \= auto) |
| float\[2\] | rideHeight | Ride height at front and rear axle in metres \[front, rear\] |
| float | turboBoost | Current turbo boost pressure in bar |
| float | ballast | Additional ballast added to the car in kg |
| float | airDensity | Ambient air density in kg/m³ |
| float | airTemp | Ambient air temperature in °C |
| float | roadTemp | Road surface temperature in °C |
| float\[3\] | localAngularVel | Angular velocity in the car's local frame \[pitch, yaw, roll\] in rad/s |
| float | finalFF | Final force-feedback torque value sent to the wheel (Nm) |
| float | performanceMeter | Real-time delta vs. best lap (positive \= ahead of reference) |
| int | engineBrake | Engine-braking setting level (higher \= more engine braking) |
| int | ersRecoveryLevel | ERS energy-recovery intensity level |
| int | ersPowerLevel | ERS power-deployment level |
| int | ersHeatCharging | ERS heat-charging mode active (0 \= off, 1 \= on) |
| int | ersIsCharging | ERS currently recovering energy (0 \= deploying, 1 \= charging) |
| float | kersCurrentKJ | Energy stored in the KERS/ERS battery in kilojoules |
| int | drsAvailable | DRS can be activated (0 \= no, 1 \= yes) |
| int | drsEnabled | DRS is open and active (0 \= closed, 1 \= open) |
| float\[4\] | brakeTemp | Brake disc temperature per corner in °C \[FL, FR, RL, RR\] |
| float | clutch | Clutch pedal position (0.0 \= engaged, 1.0 \= fully disengaged) |
| float\[4\] | tyreTempI | Tyre inner-edge temperature per wheel in °C \[FL, FR, RL, RR\] |
| float\[4\] | tyreTempM | Tyre mid-tread temperature per wheel in °C \[FL, FR, RL, RR\] |
| float\[4\] | tyreTempO | Tyre outer-edge temperature per wheel in °C \[FL, FR, RL, RR\] |
| int | isAIControlled | Car is driven by AI (0 \= player, 1 \= AI) |
| float\[4\]\[3\] | tyreContactPoint | 3-D world-space contact point of each tyre with the road \[FL,FR,RL,RR\]\[X,Y,Z\] |
| float\[4\]\[3\] | tyreContactNormal | Road-surface normal vector at each tyre contact point \[FL,FR,RL,RR\]\[X,Y,Z\] |
| float\[4\]\[3\] | tyreContactHeading | Heading vector at each tyre contact point \[FL,FR,RL,RR\]\[X,Y,Z\] |
| float | brakeBias | Front brake-bias ratio (e.g. 0.56 \= 56 % front) |
| float\[3\] | localVelocity | Velocity in the car's local reference frame \[X, Y, Z\] in m/s |
| int | P2PActivations | Remaining Push-to-Pass activations |
| int | P2PStatus | Push-to-Pass status (0 \= inactive, 1 \= active) |
| int | currentMaxRpm | Current rev-limiter ceiling in RPM |
| float\[4\] | mz | Self-aligning tyre torque (Mz) per wheel \[FL, FR, RL, RR\] in Nm |
| float\[4\] | fx | Longitudinal tyre force (Fx) per wheel \[FL, FR, RL, RR\] in N |
| float\[4\] | fy | Lateral tyre force (Fy) per wheel \[FL, FR, RL, RR\] in N |
| float\[4\] | slipRatio | Longitudinal slip ratio per tyre \[FL, FR, RL, RR\] |
| float\[4\] | slipAngle | Lateral slip angle per tyre in radians \[FL, FR, RL, RR\] |
| int | tcinAction | Traction control currently cutting power (0 \= no, 1 \= yes) |
| int | absInAction | ABS currently modulating brakes (0 \= no, 1 \= yes) |
| float\[4\] | suspensionDamage | Suspension structural damage per corner (0.0–1.0) \[FL, FR, RL, RR\] |
| float\[4\] | tyreTemp | Representative tyre surface temperature per wheel in °C \[FL, FR, RL, RR\] |
| float | waterTemp | Engine coolant temperature in °C |
| float\[4\] | brakeTorque | Braking torque at each wheel in Nm \[FL, FR, RL, RR\] |
| int | frontBrakeCompound | Front brake-pad compound identifier |
| int | rearBrakeCompound | Rear brake-pad compound identifier |
| float\[4\] | padLife | Brake-pad remaining life per corner (0.0–1.0) \[FL, FR, RL, RR\] |
| float\[4\] | discLife | Brake-disc remaining life per corner (0.0–1.0) \[FL, FR, RL, RR\] |
| int | ignitionOn | Ignition switch state (0 \= off, 1 \= on) |
| int | starterEngineOn | Starter motor currently cranking (0 \= no, 1 \= yes) |
| int | isEngineRunning | Engine is running (0 \= stopped, 1 \= running) |
| float | kerbVibration | Vibration intensity transmitted from kerb strikes |
| float | slipVibrations | Vibration intensity caused by tyre slip |
| float | roadVibrations | Vibration intensity from road surface texture |
| float | absVibrations | Vibration intensity generated by ABS pulsing |

## **SMEvoTyreState \[256 bytes\]**

*Complete state of a single tyre corner. Embedded four times in SPageFileGraphicEvo (lf, rf, lr, rr).*

| Type | Field | Meaning |
| :---- | :---- | :---- |
| float | slip | Combined tyre slip magnitude |
| bool | lock | Tyre is locked under braking (true \= locking) |
| float | tyre\_pression | Tyre inflation pressure (PSI) |
| float | tyre\_temperature\_c | Average tyre carcass temperature in °C |
| float | brake\_temperature\_c | Brake disc temperature in °C |
| float | brake\_pressure | Hydraulic brake pressure applied at this corner |
| float | tyre\_temperature\_left | Inner-edge tyre temperature in °C |
| float | tyre\_temperature\_center | Centre-tread tyre temperature in °C |
| float | tyre\_temperature\_right | Outer-edge tyre temperature in °C |
| char\[33\] | tyre\_compound\_front | Name of the compound fitted on the front axle |
| char\[33\] | tyre\_compound\_rear | Name of the compound fitted on the rear axle |
| float | tyre\_normalized\_pressure | Pressure as a 0–1 fraction of the target range |
| float | tyre\_normalized\_temperature\_left | Inner-edge temperature as a 0–1 fraction of optimal range |
| float | tyre\_normalized\_temperature\_center | Centre temperature as a 0–1 fraction of optimal range |
| float | tyre\_normalized\_temperature\_right | Outer-edge temperature as a 0–1 fraction of optimal range |
| float | brake\_normalized\_temperature | Brake temperature as a 0–1 fraction of optimal operating range |
| float | tyre\_normalized\_temperature\_core | Core tyre temperature as a 0–1 fraction of optimal range |

## **SMEvoDamageState \[128 bytes\]**

*Structural damage level for each body zone of the car (0.0 \= undamaged, 1.0 \= destroyed).*

| Type | Field | Meaning |
| :---- | :---- | :---- |
| float | damage\_front | Damage on the front body / nose |
| float | damage\_rear | Damage on the rear body / diffuser |
| float | damage\_left | Damage on the left side of the body |
| float | damage\_right | Damage on the right side of the body |
| float | damage\_center | Damage on the central / underfloor area |
| float | damage\_suspension\_lf | Damage on the front-left suspension |
| float | damage\_suspension\_rf | Damage on the front-right suspension |
| float | damage\_suspension\_lr | Damage on the rear-left suspension |
| float | damage\_suspension\_rr | Damage on the rear-right suspension |

## **SMEvoPitInfo \[64 bytes\]**

*Status of each pit-stop service action. −1 \= will not perform, 0 \= completed, 1 \= in progress.*

| Type | Field | Meaning |
| :---- | :---- | :---- |
| int8\_t | damage | Body-repair action state |
| int8\_t | fuel | Refuelling action state |
| int8\_t | tyres\_lf | Front-left tyre change state |
| int8\_t | tyres\_rf | Front-right tyre change state |
| int8\_t | tyres\_lr | Rear-left tyre change state |
| int8\_t | tyres\_rr | Rear-right tyre change state |

## **SMEvoElectronics \[128 bytes\]**

*All driver-adjustable electronic aid and setup settings.*

| Type | Field | Meaning |
| :---- | :---- | :---- |
| int8\_t | tc\_level | Traction-control level (0 \= off, higher \= more aggressive) |
| int8\_t | tc\_cut\_level | TC throttle-cut aggressiveness level |
| int8\_t | abs\_level | ABS intervention level (0 \= off) |
| int8\_t | esc\_level | Electronic stability-control level (0 \= off) |
| int8\_t | ebb\_level | Electronic brake-balance adjustment level |
| float | brake\_bias | Front brake-bias ratio (e.g. 0.56 \= 56 % front) |
| int8\_t | engine\_map\_level | Engine map / power mode selection |
| float | turbo\_level | Turbo wastegate or boost target setting |
| int8\_t | ers\_deployment\_map | ERS power-deployment strategy map |
| float | ers\_recharge\_map | ERS recharge aggressiveness setting |
| bool | is\_ers\_heat\_charging\_on | ERS heat-based charging is enabled |
| bool | is\_ers\_overtake\_mode\_on | ERS overtake (maximum-deploy) mode is active |
| bool | is\_drs\_open | DRS flap is currently open |
| int8\_t | diff\_power\_level | Differential lock level under power |
| int8\_t | diff\_coast\_level | Differential lock level on lift / coast |
| int8\_t | front\_bump\_damper\_level | Front bump (compression) damper stiffness level |
| int8\_t | front\_rebound\_damper\_level | Front rebound damper stiffness level |
| int8\_t | rear\_bump\_damper\_level | Rear bump (compression) damper stiffness level |
| int8\_t | rear\_rebound\_damper\_level | Rear rebound damper stiffness level |
| bool | is\_ignition\_on | Ignition switch is on |
| bool | is\_pitlimiter\_on | Pit-speed limiter is active |
| int8\_t | active\_performance\_mode | Selected vehicle performance / power mode index |

## **SMEvoInstrumentation \[128 bytes\]**

*Cockpit light, display, and instrumentation panel states.*

| Type | Field | Meaning |
| :---- | :---- | :---- |
| int8\_t | main\_light\_stage | Main exterior light stage (0 \= off) |
| int8\_t | special\_light\_stage | Auxiliary / special lights level |
| int8\_t | cockpit\_light\_stage | Interior cockpit illumination level |
| int8\_t | wiper\_level | Windscreen wiper speed (0 \= off) |
| bool | rain\_lights | Rear rain light is on |
| bool | direction\_light\_left | Left turn indicator is active |
| bool | direction\_light\_right | Right turn indicator is active |
| bool | flashing\_lights | Flashing lights are active |
| bool | warning\_lights | Hazard lights are illuminated |
| int8\_t | selected\_display\_index | Index of the currently focused display device |
| int8\_t | display\_current\_page\_index\[16\] | Active page index on displays |
| bool | are\_headlights\_visible | Headlights are on and visible to other drivers |

## **SMEvoSessionState \[256 bytes\]**

*Server-side session lifecycle information.*

| Type | Field | Meaning |
| :---- | :---- | :---- |
| char\[33\] | phase\_name | Name of the current session phase (e.g. 'Race', 'Qualify') |
| char\[15\] | time\_left | Formatted remaining session time (HH:MM:SS) |
| int32\_t | time\_left\_ms | Remaining session time in milliseconds |
| char\[15\] | wait\_time | Formatted wait time before session start |
| int32\_t | total\_lap | Total laps scheduled for this session |
| int32\_t | current\_lap | Current lap number being driven |
| int32\_t | lights\_on | Number of starting lights currently illuminated |
| int32\_t | lights\_mode | Starting-light sequence mode identifier |
| float | lap\_length\_km | Track lap length in kilometres |
| int32\_t | end\_session\_flag | Non-zero when the session is ending |
| char\[15\] | time\_to\_next\_session | Formatted countdown to the next session |
| bool | disconnected\_from\_server | Player has lost connection to the game server |
| bool | restart\_season\_enabled | Season restart option is available to the player |
| bool | ui\_enable\_drive | Drive button is enabled in the UI |
| bool | ui\_enable\_setup | Setup screen is accessible from the UI |
| bool | is\_ready\_to\_next\_blinking | Ready-to-proceed indicator is blinking |
| bool | show\_waiting\_for\_players | Waiting-for-players lobby screen is shown |

## **SMEvoTimingState \[256 bytes\]**

*Lap timing and delta values displayed on the HUD.*

| Type | Field | Meaning |
| :---- | :---- | :---- |
| char\[15\] | current\_laptime | Current lap time as a formatted string |
| char\[15\] | delta\_current | Delta vs. current reference lap (formatted) |
| int32\_t | delta\_current\_p | Sign of delta\_current: \+1 slower, −1 faster, 0 \= hidden |
| char\[15\] | last\_laptime | Last completed lap time as a formatted string |
| char\[15\] | delta\_last | Delta vs. last lap (formatted) |
| int32\_t | delta\_last\_p | Sign of delta\_last: \+1 slower, −1 faster, 0 \= hidden |
| char\[15\] | best\_laptime | Personal best lap time as a formatted string |
| char\[15\] | ideal\_laptime | Theoretical best lap (sum of best sectors) as a formatted string |
| char\[15\] | total\_time | Total elapsed session time as a formatted string |
| bool | is\_invalid | Current lap has been invalidated (track-limits violation, etc.) |

## **SMEvoAssistsState \[64 bytes\]**

*Driver-assist settings currently active for the player car.*

| Type | Field | Meaning |
| :---- | :---- | :---- |
| uint8\_t | auto\_gear | Automatic gearshift aid level (0 \= off) |
| uint8\_t | auto\_blip | Automatic throttle blip on downshift (0 \= off) |
| uint8\_t | auto\_clutch | Automatic clutch management (0 \= off) |
| uint8\_t | auto\_clutch\_on\_start | Automatic clutch during the rolling start (0 \= off) |
| uint8\_t | manual\_ignition\_e\_start | Manual ignition and electric start required (0 \= automatic) |
| uint8\_t | auto\_pit\_limiter | Pit-speed limiter activates automatically (0 \= manual) |
| uint8\_t | standing\_start\_assist | Standing-start launch assistance active (0 \= off) |
| float | auto\_steer | Auto-steer correction strength (0.0 \= off, 1.0 \= maximum) |
| float | arcade\_stability\_control | Arcade-style stability aid level (0.0 \= off, 1.0 \= maximum) |

## **SPageFileGraphicEvo**

*Main HUD and graphics telemetry page. Updated each rendered frame. Contains embedded sub-structs for tyres, damage, electronics, timing, and session state.*

| Type | Field | Meaning |
| :---- | :---- | :---- |
| int | packetId | Incrementing counter — detect new frames by comparing to previous value |
| ACEVO\_STATUS | status | Current simulator operational state (see ACEVO\_STATUS) |
| uint64\_t | focused\_car\_id\_a | Unique ID of the car currently shown by the camera |
| uint64\_t | focused\_car\_id\_b |  |
| uint64\_t | player\_car\_id\_a | Unique ID of the player's own car |
| uint64\_t | player\_car\_id\_b |  |
| unsigned short | rpm | Engine speed in RPM for HUD display |
| bool | is\_rpm\_limiter\_on | Rev limiter is cutting fuel / ignition (bouncing off limiter) |
| bool | is\_change\_up\_rpm | Engine RPM is in the upshift window |
| bool | is\_change\_down\_rpm | Engine RPM is in the downshift window |
| bool | tc\_active | Traction control is actively intervening this frame |
| bool | abs\_active | ABS is actively modulating brake pressure this frame |
| bool | esc\_active | Electronic stability control is intervening this frame |
| bool | launch\_active | Launch control system is engaged |
| bool | is\_ignition\_on | Ignition switch is on |
| bool | is\_engine\_running | Engine is running |
| bool | kers\_is\_charging | KERS/ERS battery is currently being charged |
| bool | is\_wrong\_way | Car is travelling in the wrong direction on track |
| bool | is\_drs\_available | DRS activation is permitted in this section |
| bool | battery\_is\_charging | High-voltage battery pack is in charging state |
| bool | is\_max\_kj\_per\_lap\_reached | Maximum ERS deployment energy for this lap has been consumed |
| bool | is\_max\_charge\_kj\_per\_lap\_reached | Maximum ERS charge energy for this lap has been stored |
| short | display\_speed\_kmh | Displayed speed in km/h |
| short | display\_speed\_mph | Displayed speed in mph |
| short | display\_speed\_ms | Displayed speed in m/s |
| float | pitspeeding\_delta | Speed delta vs. pit-lane limit (negative \= under limit) |
| short | gear\_int | Current gear as an integer (same encoding as physics gear) |
| float | rpm\_percent | Engine RPM as a fraction of redline (0.0–1.0) |
| float | gas\_percent | Throttle pedal position as a fraction (0.0–1.0) |
| float | brake\_percent | Brake pressure as a fraction (0.0–1.0) |
| float | handbrake\_percent | Handbrake engagement as a fraction (0.0–1.0) |
| float | clutch\_percent | Clutch disengagement as a fraction (1.0–0.0) |
| float | steering\_percent | Steering wheel position (−1.0 \= full left, \+1.0 \= full right) |
| float | ffb\_strength | Global force-feedback output strength |
| float | car\_ffb\_mupliplier | Per-car force-feedback gain multiplier |
| float | water\_temperature\_percent | Coolant temperature as a fraction of optimal operating range |
| float | water\_pressure\_bar | Coolant system pressure in bar |
| float | fuel\_pressure\_bar | Fuel system pressure in bar |
| int8\_t | water\_temperature\_c | Coolant temperature in °C |
| int8\_t | air\_temperature\_c | Ambient air temperature in °C |
| float | oil\_temperature\_c | Engine oil temperature in °C |
| float | oil\_pressure\_bar | Engine oil pressure in bar |
| float | exhaust\_temperature\_c | Exhaust gas temperature in °C |
| float | g\_forces\_x | Lateral G-force (positive \= rightward) |
| float | g\_forces\_y | Longitudinal G-force (positive \= under acceleration) |
| float | g\_forces\_z | Vertical G-force (positive \= upward) |
| float | turbo\_boost | Absolute turbo boost pressure in bar |
| float | turbo\_boost\_level | Current boost stage or map level |
| float | turbo\_boost\_perc | Turbo boost as a fraction of maximum (0.0–1.0) |
| int32\_t | steer\_degrees | Steering wheel rotation in degrees from centre |
| float | current\_km | Distance driven in the current session in km |
| uint32\_t | total\_km | Total odometer / career distance in km |
| uint32\_t | total\_driving\_time\_s | Total driving time accumulated in seconds |
| int32\_t | time\_of\_day\_hours | In-game time of day — hours (0–23) |
| int32\_t | time\_of\_day\_minutes | In-game time of day — minutes (0–59) |
| int32\_t | time\_of\_day\_seconds | In-game time of day — seconds (0–59) |
| int32\_t | delta\_time\_ms | Delta vs. reference lap in milliseconds (signed) |
| int32\_t | current\_lap\_time\_ms | Current lap time in milliseconds |
| int32\_t | predicted\_lap\_time\_ms | Predicted final lap time in milliseconds |
| float | fuel\_liter\_current\_quantity | Fuel remaining in the tank in litres |
| float | fuel\_liter\_current\_quantity\_percent | Fuel remaining as a fraction of tank capacity |
| float | fuel\_liter\_per\_km | Average fuel consumption rate in litres per km |
| float | km\_per\_fuel\_liter | Average fuel economy in km per litre |
| float | current\_torque | Engine output torque in Nm |
| int32\_t | current\_bhp | Engine output power in brake horsepower |
| SMEvoTyreState | tyre\_lf | Full tyre state for the front-left corner |
| SMEvoTyreState | tyre\_rf | Full tyre state for the front-right corner |
| SMEvoTyreState | tyre\_lr | Full tyre state for the rear-left corner |
| SMEvoTyreState | tyre\_rr | Full tyre state for the rear-right corner |
| float | npos | Normalised track position (0.0 \= start/finish line, 1.0 \= one full lap) |
| float | kers\_charge\_perc | KERS/ERS charge level as a fraction (0.0–1.0) |
| float | kers\_current\_perc | KERS/ERS power currently being deployed as a fraction |
| float | control\_lock\_time | Seconds driver input remains locked (e.g. after collision penalty) |
| SMEvoDamageState | car\_damage | Damage levels for each body zone of the car |
| ACEVO\_CAR\_LOCATION | car\_location | Current track zone the car occupies (see ACEVO\_CAR\_LOCATION) |
| SMEvoPitInfo | pit\_info | Status of each pit-stop service item |
| float | fuel\_liter\_used | Fuel consumed since session start in litres |
| float | fuel\_liter\_per\_lap | Average fuel consumed per lap in litres |
| float | laps\_possible\_with\_fuel | Estimated number of laps achievable with remaining fuel |
| float | battery\_temperature | High-voltage battery temperature in °C |
| float | battery\_voltage | High-voltage battery pack voltage in V |
| float | instantaneous\_fuel\_liter\_per\_km | Instantaneous fuel consumption in litres per km |
| float | instantaneous\_km\_per\_fuel\_liter | Instantaneous fuel economy in km per litre |
| float | gear\_rpm\_window | How well current RPM suits the engaged gear (1.0 \= ideal window) |
| SMEvoInstrumentation | instrumentation | Current state of all cockpit lights and displays |
| SMEvoInstrumentation | instrumentation\_min\_limit | Minimum allowed setting for each instrumentation item |
| SMEvoInstrumentation | instrumentation\_max\_limit | Maximum allowed setting for each instrumentation item |
| SMEvoElectronics | electronics | Current electronic aid and setup values |
| SMEvoElectronics | electronics\_min\_limit | Minimum allowed value for each electronics setting |
| SMEvoElectronics | electronics\_max\_limit | Maximum allowed value for each electronics setting |
| SMEvoElectronics | electronics\_is\_modifiable | Flags which electronics fields the driver can adjust in-session |
| int32\_t | total\_lap\_count | Total laps completed in the session |
| uint32\_t | current\_pos | Current race position (1 \= leader) |
| uint32\_t | total\_drivers | Total number of cars in the session |
| int32\_t | last\_laptime\_ms | Last completed lap time in milliseconds |
| int32\_t | best\_laptime\_ms | Personal best lap time in milliseconds |
| ACEVO\_FLAG\_TYPE | flag | Flag shown specifically to this driver |
| ACEVO\_FLAG\_TYPE | global\_flag | Flag shown to all drivers on track |
| uint32\_t | max\_gears | Number of forward gears the car has |
| ACEVO\_ENGINE\_TYPE | engine\_type | Powertrain type of the car (see ACEVO\_ENGINE\_TYPE) |
| bool | has\_kers | Car is equipped with a KERS/ERS system |
| bool | is\_last\_lap | This is the final scheduled lap of the race |
| char\[33\] | performance\_mode\_name | Display name of the active vehicle performance / power mode |
| float | diff\_coast\_raw\_value | Raw differential coast-lock value from setup |
| float | diff\_power\_raw\_value | Raw differential power-lock value from setup |
| int32\_t | race\_cut\_gained\_time\_ms | Cumulative time penalty from track-limit cuts in ms |
| int32\_t | distance\_to\_deadline | Distance to the penalty trigger in metres |
| float | race\_cut\_current\_delta | Running delta time accrued from track-limit violations |
| SMEvoSessionState | session\_state | Session lifecycle and countdown information |
| SMEvoTimingState | timing\_state | HUD lap times and delta display values |
| int32\_t | player\_ping | Network round-trip ping to the server in ms |
| int32\_t | player\_latency | Measured network latency in ms |
| int32\_t | player\_cpu\_usage | Client CPU usage in percent |
| int32\_t | player\_cpu\_usage\_avg | Average client CPU usage in percent |
| int32\_t | player\_qos | Network Quality-of-Service score |
| int32\_t | player\_qos\_avg | Average QoS score over the session |
| int32\_t | player\_fps | Current rendered frames per second |
| int32\_t | player\_fps\_avg | Average FPS over the session |
| char\[33\] | driver\_name | Driver's first name |
| char\[33\] | driver\_surname | Driver's surname |
| char\[33\] | car\_model | Identifier or display name of the car model |
| bool | is\_in\_pit\_box | Car is stationary inside its assigned pit box |
| bool | is\_in\_pit\_lane | Car is anywhere within the pit lane |
| bool | is\_valid\_lap | Current lap is valid and counts for timing |
| float\[60\]\[3\] | car\_coordinates | World-space position of up to 60 cars \[car\_index\]\[X, Y, Z\] |
| float | gap\_ahead | Time gap to the car immediately ahead in seconds |
| float | gap\_behind | Time gap to the car immediately behind in seconds |
| uint8\_t | active\_cars | Number of cars actively participating in the session |
| float | fuel\_per\_lap | Target fuel consumption per lap in litres |
| float | fuel\_estimated\_laps | Estimated laps remaining with current fuel |
| SMEvoAssistsState | assists\_state | All driver-assist levels currently active |
| float | max\_fuel | Maximum fuel tank capacity of the car in litres |
| float | max\_turbo\_boost | Maximum turbo boost pressure in bar |
| bool | use\_single\_compound | Car is restricted to a single tyre compound for both axles |

## **SPageFileStaticEvo**

*Static session metadata. Written once when a session loads and does not change while driving.*

| Type | Field | Meaning |
| :---- | :---- | :---- |
| char\[15\] | sm\_version | Shared-memory interface version string |
| char\[15\] | ac\_evo\_version | AC Evo game build version string |
| ACEVO\_SESSION\_TYPE | session | Type of the current session (see ACEVO\_SESSION\_TYPE) |
| char\[33\] | session\_name | Human-readable session name (e.g. 'Race 1') |
| uint8\_t | event\_id | Unique identifier of the event within the championship |
| uint8\_t | session\_id | Unique identifier of this session within the event |
| ACEVO\_STARTING\_GRIP | starting\_grip | Tyre grip condition at session start (see ACEVO\_STARTING\_GRIP) |
| float | starting\_ambient\_temperature\_c | Ambient air temperature at session start in °C |
| float | starting\_ground\_temperature\_c | Road surface temperature at session start in °C |
| bool | is\_static\_weather | Weather is fixed and will not change during the session |
| bool | is\_timed\_race | Session ends by elapsed time rather than lap count |
| bool | is\_online | Session is an online multiplayer event |
| int | number\_of\_sessions | Total sessions in this event (e.g. 3 \= practice \+ qualify \+ race) |
| char\[33\] | nation | Country / nation name associated with the event or track |
| float | longitude | Geographic longitude of the track location in decimal degrees |
| float | latitude | Geographic latitude of the track location in decimal degrees |
| char\[33\] | track | Track identifier or name |
| char\[33\] | track\_configuration | Track layout variant or configuration name |
| float | track\_length\_m | Total lap length of the track in metres |

