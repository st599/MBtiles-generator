import math
import sqlite3
import requests
import io
import argparse
import sys
import time
import logging
import os
import xml.etree.ElementTree as ET
from xml.dom import minidom
from PIL import Image

# --- LOGGING CONFIGURATION ---
# Configured to output to stdout for real-time monitoring
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Primary tile servers. 
# Using the openstreetmap.fr mirror for cyclosm as it is generally more script-friendly.
SERVERS = {
    'osm': 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    'cyclosm': 'https://{s}.tile.openstreetmap.fr/cyclosm/{z}/{x}/{y}.png',
    'hiking': 'https://tile.waymarkedtrails.org/hiking/{z}/{x}/{y}.png'
}

def deg2num(lat_deg, lon_deg, zoom):
    """
    Converts Latitude and Longitude to Slippy Map tile coordinates (X, Y).
    
    The Y coordinate calculation uses the Mercator projection logic to account 
    for the curvature of the Earth on a flat map.
    
    Args:
        lat_deg (float): Latitude in decimal degrees.
        lon_deg (float): Longitude in decimal degrees.
        zoom (int): The zoom level.
        
    Returns:
        tuple: (xtile, ytile) as integers.
    """
    lat_rad = math.radians(lat_deg)
    n = 2.0 ** zoom
    xtile = int((lon_deg + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.log(math.tan(lat_rad) + (1 / math.cos(lat_rad))) / math.pi) / 2.0 * n)
    return (xtile, ytile)

def create_teasi_metadata_xml(bbox, zoom_range, output_name, server_name):
    """
    Generates the .xml file required by Teasi/Tahuna devices for map indexing.
    
    Args:
        bbox (list): [min_lat, min_lon, max_lat, max_lon].
        zoom_range (range): Range of zoom levels.
        output_name (str): Filename of the .mbtiles file.
        server_name (str): Name of the map style provider.
    """
    xml_path = os.path.splitext(output_name)[0] + ".xml"
    root = ET.Element("MapMetadata")
    ET.SubElement(root, "Name").text = os.path.basename(output_name)
    ET.SubElement(root, "Description").text = f"Cycling map from {server_name}"
    
    bounds = ET.SubElement(root, "Bounds")
    ET.SubElement(bounds, "MinLat").text = str(bbox[0])
    ET.SubElement(bounds, "MinLon").text = str(bbox[1])
    ET.SubElement(bounds, "MaxLat").text = str(bbox[2])
    ET.SubElement(bounds, "MaxLon").text = str(bbox[3])
    
    zooms = ET.SubElement(root, "ZoomLevels")
    ET.SubElement(zooms, "Min").text = str(min(zoom_range))
    ET.SubElement(zooms, "Max").text = str(max(zoom_range))
    
    xml_str = minidom.parseString(ET.tostring(root)).toprettyxml(indent="   ")
    with open(xml_path, "w", encoding="utf-8") as f:
        f.write(xml_str)
    logger.info(f"Metadata XML created: {xml_path}")

def get_tile_with_retry(url, zoom, x, y):
    """
    Attempts to download a single map tile using browser-mimicking headers.
    
    Args:
        url (str): The template URL from SERVERS.
        zoom, x, y (int): Tile coordinates.
        
    Returns:
        bytes: Raw image data or None if unsuccessful.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
        'Referer': 'https://www.cyclosm.org/'
    }
    
    subdomains = ['a', 'b', 'c'] if '{s}' in url else ['']
    for sub in subdomains:
        try:
            target_url = url.replace("{s}", sub).format(z=zoom, x=x, y=y)
            r = requests.get(target_url, headers=headers, timeout=15)
            if r.status_code == 200:
                return r.content
        except Exception:
            continue
    return None

def verify_mbtiles(db_path):
    """
    Checks the SQLite metadata and tile health after generation.
    Displays average tile size to verify compression efficiency.
    """
    if not os.path.exists(db_path): return
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT name, value FROM metadata")
        logger.info("--- Internal Metadata Check ---")
        for name, value in cursor.fetchall():
            logger.info(f"{name}: {value}")
            
        cursor.execute("SELECT COUNT(*), SUM(LENGTH(tile_data)) FROM tiles")
        count, total_bytes = cursor.fetchone()
        if count > 0:
            avg_kb = (total_bytes / count) / 1024
            logger.info(f"--- Tile Summary: {count} tiles, Avg {avg_kb:.2f} KB ---")
    finally:
        conn.close()

def main():
    """
    Orchestrates the map generation process: cleanup, grid calculation,
    downloading, and SQLite storage with proper MBTiles metadata.
    """
    parser = argparse.ArgumentParser(description="TeasiOne MBTiles Generator")
    parser.add_argument("--bbox", nargs=4, type=float, required=True, help="min_lat min_lon max_lat max_lon")
    parser.add_argument("--output", default="map.mbtiles", help="Output filename")
    parser.add_argument("--server", choices=SERVERS.keys(), default="osm", help="Map style")
    parser.add_argument("--zooms", nargs=2, type=int, default=[13, 16], help="Min Max zoom")
    parser.add_argument("--quality", type=int, default=80, help="JPG quality (1-100)")
    parser.add_argument("--dry-run", action="store_true", help="Estimate only")
    
    args = parser.parse_args()
    min_lat, min_lon, max_lat, max_lon = args.bbox
    zoom_range = range(args.zooms[0], args.zooms[1] + 1)
    
    # Clean start: Remove existing files to avoid SQLite conflicts
    if not args.dry_run:
        xml_file = os.path.splitext(args.output)[0] + ".xml"
        for f in [args.output, xml_file]:
            if os.path.exists(f):
                logger.info(f"Deleting existing file: {f}")
                os.remove(f)

    # Calculate tile grid requirements
    total_expected = 0
    zoom_stats = {}
    for z in zoom_range:
        x1, y1 = deg2num(max_lat, min_lon, z) 
        x2, y2 = deg2num(min_lat, max_lon, z)
        count = (abs(x2 - x1) + 1) * (abs(y2 - y1) + 1)
        total_expected += count
        zoom_stats[z] = (x1, x2, y1, y2, count)

    logger.info(f"Plan: {total_expected} tiles. Est. size: ~{(total_expected*18)/1024:.2f} MB")
    if args.dry_run: return

    create_teasi_metadata_xml(args.bbox, zoom_range, args.output, args.server)
    
    conn = sqlite3.connect(args.output)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE metadata (name text, value text);")
    cursor.execute("CREATE TABLE tiles (zoom_level integer, tile_column integer, tile_row integer, tile_data blob);")
    
    # Essential MBTiles metadata for MapTiler and GPS device compatibility
    cnt_lon, cnt_lat = (min_lon + max_lon) / 2, (min_lat + max_lat) / 2
    meta_info = [
        ('name', os.path.basename(args.output)),
        ('format', 'jpg'),
        ('bounds', f"{min_lon},{min_lat},{max_lon},{max_lat}"), # Standard WGS84
        ('center', f"{cnt_lon},{cnt_lat},{max(zoom_range)}"),
        ('type', 'baselayer'),
        ('version', '1.1'),
        ('description', f'Teasi cycling map: {args.server}'),
        ('projection', 'EPSG:3857') # Confirms tiles are Web Mercator
    ]
    cursor.executemany("INSERT INTO metadata VALUES (?, ?)", meta_info)

    base_url = SERVERS[args.server]
    try:
        for zoom in zoom_range:
            x1, x2, y1, y2, count = zoom_stats[zoom]
            logger.info(f"Zoom {zoom}: Downloading {count} tiles...")
            processed = 0
            for x in range(min(x1, x2), max(x1, x2) + 1):
                for y in range(min(y1, y2), max(y1, y2) + 1):
                    tile_data = get_tile_with_retry(base_url, zoom, x, y)
                    if tile_data:
                        # Convert to JPG to save space on SD card
                        img = Image.open(io.BytesIO(tile_data)).convert("RGB")
                        buf = io.BytesIO()
                        img.save(buf, format="JPEG", quality=args.quality)
                        
                        # TMS flip: MBTiles counts Y from bottom up
                        tms_y = (pow(2, zoom) - 1) - y
                        cursor.execute("INSERT INTO tiles VALUES (?, ?, ?, ?)", 
                                     (zoom, x, tms_y, sqlite3.Binary(buf.getvalue())))
                    
                    processed += 1
                    if processed % 50 == 0: logger.info(f"Zoom {zoom}: {processed}/{count}...")
                    time.sleep(0.1) # Be polite to community servers
                conn.commit()
    except KeyboardInterrupt:
        logger.warning("Download interrupted.")
    finally:
        conn.close()
        verify_mbtiles(args.output)

if __name__ == "__main__":
    main()
