# ACE `.carsetup` snapshot format

ACE logs a user preset as two consecutive messages:

```text
Load preset <display name>
Opening non-packed file "...\Saved Games\ACE\Car Setups\...\<name>.carsetup"
```

The file is a protobuf-wire message without a published `.proto`. The client
reads only files below `%USERPROFILE%\Saved Games\ACE\Car Setups`, requires the
`.carsetup` extension, and limits reads to 1 MiB.

## Validated fields

| Top field | Repetition / child | Meaning |
| --- | --- | --- |
| 1 | child 1, packed float32 pair | Front/rear anti-roll bar values |
| 1 | child 2 | Steering ratio |
| 1 | child 3.1 / 3.2 | Front brake bias / brake power |
| 2 | FL, FR, RL, RR; child 1 | Spring rate |
| 2 | child 2.1 / 2.2 | Bump-stop range / rate |
| 2 | child 3.1 / 3.2 | Packer range / rate |
| 3 | FL, FR, RL, RR; child 1 / 3 | Bump / rebound setting |
| 4 | FL, FR, RL, RR; child 1 / 2 / 3 | Pressure / camber / toe |
| 5 | children 1, 2, 3, 5 | Aero setup values (protocol-numbered) |
| 6 | child 2 / 3 | Front/rear ride height |
| 7 | child 1 | Fuel load |
| 9 | UTF-8 string | Car, mechanical preset, and visual preset IDs |

Scalar zero values may be omitted by protobuf. For a present wheel record,
missing toe child 3 therefore means a zero toe setting. Other unknown fields
remain ignored until they can be validated against ACE output.

The mechanical configuration string has this shape:

```text
<car>_<mechanical-preset>_<visual-preset>
```

For example, Mazda MX-5 ND Cup configuration
`preset_mx5ndcup_mech_1` is displayed as `ND2`, while
`preset_mx5ndcup_mech_2` is displayed as `ND1`. The raw mechanical preset ID
is retained as the stable protocol value.
