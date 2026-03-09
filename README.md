# TeasiGen: Advanced Map Generator for TeasiOne

TeasiGen is a professional-grade Python utility designed to create high-performance offline maps for TeasiOne and Tahuna GPS devices. It handles tile downloading, coordinate projection math, and packages data into an optimized SQLite (`.mbtiles`) database and companion `metadata.xml`.

## Key Features

* **MapTiler Compatible:** Strictly formatted metadata using WGS84 ($EPSG:4326$) bounds to ensure maps align correctly in pro GIS tools.
* **Stealth Headers:** Mimics modern browsers to bypass 403/Forbidden errors from tile servers.
* **Automatic Cleanup:** Deletes existing files before starting to prevent database corruption.
* **Integrity Verification:** Post-generation summary shows average tile size and storage health.

## Installation

Ensure you have Python 3.8+ and the necessary libraries:

```bash
pip install requests pillow requests

```

## Usage

```bash
python map_gen.py --bbox MIN_LAT MIN_LON MAX_LAT MAX_LON [OPTIONS]

```

### Example: Richmond Park & Kingston (London)

```bash
python map_gen.py --bbox 51.416552 -0.313797 51.463205 -0.215608 --server cyclosm --output southwest_london.mbtiles

```

---

## CLI Options

| Option | Argument | Description |
| --- | --- | --- |
| `--bbox` | `LAT LON LAT LON` | **Required.** Format: `min_lat min_lon max_lat max_lon`. |
| `--output` | `filename.mbtiles` | Output filename. Existing files with this name will be deleted first. |
| `--server` | `osm`, `cyclosm`, `hiking` | Tile provider. `cyclosm` uses the stable French mirror for cycling data. |
| `--zooms` | `MIN MAX` | Recommend **13 16** for standard use, **17** for extreme trail detail. |
| `--quality` | `1-100` | JPG quality. **80** is recommended for Teasi hardware. |
| `--dry-run` | *(Flag)* | Runs coordinate math and estimates file size without downloading. |

---

## Geography & Projections

TeasiGen handles the complex translation between different coordinate systems:

1. **Input:** User provides GPS coordinates in Decimal Degrees ($WGS84$).
2. **Processing:** Script converts these to Web Mercator ($EPSG:3857$) for tile fetching.
3. **Storage:** Tiles are flipped to the **TMS (Tile Map Service)** standard required by MBTiles.

---

## Installation on Teasi/Tahuna Devices

1. **Generate:** Run the script to create your `.mbtiles` and `.xml` files.
2. **Connect:** Plug your device into your computer via USB.
3. **Deploy:** Copy both files to the **`Maps`** or **`Import`** folder on your SD card.
4. **Rename (Optional):** If the device does not see the map, rename the file extension from `.mbtiles` to **`.mbt`**. Keep the `.xml` filename identical to the map filename.

## Troubleshooting

* **Blue/Empty Map:** Usually caused by inverted coordinates. Ensure you follow `min_lat min_lon max_lat max_lon`.
* **Map "Off Center":** If the map appears in the wrong location, MapTiler might be reading cached metadata. Use the updated script version to force $EPSG:4326$ bounds.

---

