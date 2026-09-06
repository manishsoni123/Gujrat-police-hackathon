# cameras_enrichment.csv — how the camera locations were derived

`media/cameras.json` (supplied by the hackathon organiser) contains only an `id`
(`cam01`..`cam30`) and a free-text `name` for each camera. It carries **no
coordinates, district, city, police station or owning department**.

Everything in `media/cameras_enrichment.csv` beyond `external_id` and `name`
was **inferred by the team from the camera name alone** using public
geocoders and web sources (OpenStreetMap Nominatim/Overpass, Mappls/Justdial
listings, news items). None of it was provided or verified by Gujarat Police.
Treat the values as a working assumption for the GIS map and gap analysis, not
as ground truth. Anything marked `guess` should be confirmed with the organiser
before it is used for an operational decision.

## Columns

| column | meaning |
|---|---|
| `external_id` | `id` from `cameras.json` (join key) |
| `name` | `name` from `cameras.json`, byte-for-byte (numeric prefixes and typos kept) |
| `lat`, `lon` | WGS84 decimal degrees. Never blank: when only a district could be established the district headquarters is used |
| `district`, `city` | Gujarat district and town/locality the camera is believed to be in. `(district HQ placeholder)` in `city` means the point is the HQ fallback, not the real site |
| `police_station` | Nearest police station we could identify quickly (OSM-mapped station or a well-known station for that town). Best effort, not the official jurisdiction; blank when unsure |
| `department_code` | Team's guess at the owning department: `POLICE`, `GSRTC`, `PANCHAYAT`, `MUNICIPAL`, `HEALTH`, `RTO`, `EDUCATION`, `UNASSIGNED`. Reasoning is in `notes` |
| `camera_type_guess` | `bullet`, `dome`, `ptz`, `anpr` or `rlvd`, guessed from the kind of site (gate/toll = ANPR, RLVD in name = RLVD, circle = PTZ, etc.) |
| `location_confidence` | see legend below |
| `location_source` | where the coordinate came from (`osm-overpass`, `osm-nominatim`, `mappls-listing`, `tripadvisor-listing`, `derived`, `district-hq-fallback`) |
| `notes` | how the name was interpreted, alternatives considered, and why the department was chosen |

## Confidence legend

| value | meaning |
|---|---|
| `exact` | The specific landmark in the name was found in OpenStreetMap (bridge, toll booth, school, bus station, railway station, named circle). Expect the true pole to be within ~100 m |
| `approx` | The town, suburb, village or junction area was identified but not the exact pole; the point is the area/junction centre or a nearby listed landmark. Expect within ~1 km |
| `guess` | The name could not be matched to a specific place with confidence. The point is either the district headquarters (`district-hq-fallback`) or the most plausible candidate we could find, with alternatives listed in `notes`. May be tens of km off, or a different place altogether |

Summary of the current file: 7 `exact`, 16 `approx`, 7 `guess`.

## Names that needed interpretation (please confirm with the organiser)

- **14 Delight RLVD** — no place called "Delight" exists; read as *Delhi Gate* (Delhi Darwaja) junction, Ahmedabad. Could equally be Delhi Gate, Surat.
- **15 Suvidha park** — placed at Suvidha Crossroads, Paldi (fits the other Paldi/Ambawadi cameras); there are also housing societies called "Suvidha Park" in east Ahmedabad.
- **30 kheram** — read as Khergam (Navsari); Kharel (Gandevi taluka) is the other candidate.
- **20 Mohanpura** and **28 BK Mervada tran Rasta** — only tiny villages of these names were found (Idar taluka, Sabarkantha; Palanpur taluka, Banaskantha) and neither is in OSM, so the district HQ coordinates are used.
- **07 hero-showroom-gir-somnath** and **18 Rajkot CCTV** — the name only gives a district/city; the town centre is used.
- **10 char-chowk-road-2-junagadh** — "Char Chowk" exists on the Junagadh highway at Keshod (Junagadh district), not inside Junagadh city.
- **36/37/38 bilimora** — three identical names; assigned to the railway station, the GSRTC depot and the main road so they render as distinct points.
- **33 dehgam** — taken as Dehgam, Gandhinagar district (there is also a Degam village in Navsari).

## Regenerating / correcting

The file is hand-curated. To correct a row, edit the CSV (keep `name` identical
to `cameras.json`), update `location_confidence`/`location_source`, and note the
evidence in `notes`. If the organiser later supplies real coordinates or
department ownership, replace the inferred values and set `location_source` to
`organiser`.
