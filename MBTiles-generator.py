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

# --- LOGGING ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

SERVERS = {
    'osm': 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    'cyclosm': 'https://{s}.tile.cyclosm.openstreetmap.fr/cyclosm/{z}/{x}/{y}.png',
    'hiking': 'https://tile.waymarkedtrails.org/hiking/{z}/{x}/{y}.png'
}

def deg2num(lat_deg, lon_deg, zoom):
    """
    Standard OpenStreetMap slippy map conversion.
    x = longitude math, y = latitude math
    """
    lat_rad = math.radians(lat_deg)
    n = 2.0 ** zoom
    xtile = int((lon_deg + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.log(math.tan(lat_rad) + (1 / math.cos(lat_rad))) / math.pi) / 2.0 * n)
    return (xtile, ytile)

def create_teasi_metadata_xml(bbox, zoom_range, output_name, server_name):
    xml_path = os.path.splitext(output_name)[0] + ".xml"
    root = ET.Element("MapMetadata")
    ET.SubElement(root, "Name").text = os.path.basename(output_name)
    ET.SubElement(root, "Description").text = f"Cycling map from {server_name}"
    
    bounds = ET.SubElement(root, "Bounds")
    # Teasi XML usually expects: MinLat, MinLon, MaxLat, MaxLon
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

def main():
    parser = argparse.ArgumentParser(description="TeasiOne Map Generator (Fix: Lat/Lon aware)")
    # Clarifying input: Latitude usually comes first in GPS strings, but Lon is X.
    parser.add_argument("--bbox", nargs=4, type=float, required=True, 
                        help="Format: min_lat min_lon max_lat max_lon (e.g., 52.3 13.3 52.6 13.6)")
    parser.add_argument("--output", default="map.mbtiles", help="Output filename")
    parser.add_argument("--server", choices=SERVERS.keys(), default="osm", help="Map style")
    parser.add_argument("--zooms", nargs=2, type=int, default=[13, 16], help="Min Max zoom")
    parser.add_argument("--quality", type=int, default=80, help="JPG quality")
    parser.add_argument("--dry-run", action="store_true")
    
    args = parser.parse_args()
    min_lat, min_lon, max_lat, max_lon = args.bbox
    zoom_range = range(args.zooms[0], args.zooms[1] + 1)
    
    # Pre-flight check
    total_expected = 0
    for z in zoom_range:
        x_start, y_start = deg2num(max_lat, min_lon, z) # Top Left
        x_end, y_end = deg2num(min_lat, max_lon, z)     # Bottom Right
        total_expected += (abs(x_end - x_start) + 1) * (abs(y_end - y_start) + 1)
    
    logger.info(f"Targeting area: Lat({min_lat} to {max_lat}), Lon({min_lon} to {max_lon})")
    logger.info(f"Total tiles: {total_expected}. Est. Size: ~{(total_expected*15)/1024:.2f} MB")

    if args.dry_run: return

    create_teasi_metadata_xml(args.bbox, zoom_range, args.output, args.server)
    base_url = SERVERS[args.server].replace("{s}", "a") 
    conn = sqlite3.connect(args.output)
    cursor = conn.cursor()
    
    # Initialize DB (Same logic as before)
    cursor.execute("CREATE TABLE IF NOT EXISTS metadata (name text, value text);")
    cursor.execute("CREATE TABLE IF NOT EXISTS tiles (zoom_level integer, tile_column integer, tile_row integer, tile_data blob);")

    try:
        for zoom in zoom_range:
            # Map coordinates to tile indices
            # Note: y_start is typically smaller than y_end in tile coords because y=0 is North
            x_min, y_min = deg2num(max_lat, min_lon, zoom) 
            x_max, y_max = deg2num(min_lat, max_lon, zoom)
            
            logger.info(f"Zoom {zoom}: Downloading grid X({x_min}-{x_max}) Y({y_min}-{y_max})")
            
            for x in range(min(x_min, x_max), max(x_min, x_max) + 1):
                for y in range(min(y_min, y_max), max(y_min, y_max) + 1):
                    try:
                        r = requests.get(base_url.format(z=zoom, x=x, y=y), headers={'User-Agent': 'TeasiGen/1.0'}, timeout=10)
                        if r.status_code == 200:
                            img = Image.open(io.BytesIO(r.content)).convert("RGB")
                            buf = io.BytesIO()
                            img.save(buf, format="JPEG", quality=args.quality)
                            
                            # TMS flip for MBTiles
                            tms_y = (pow(2, zoom) - 1) - y
                            cursor.execute("INSERT OR REPLACE INTO tiles VALUES (?, ?, ?, ?)", (zoom, x, tms_y, sqlite3.Binary(buf.getvalue())))
                        time.sleep(0.05)
                    except Exception as e:
                        logger.error(f"Error at {zoom}/{x}/{y}: {e}")
                conn.commit()
    finally:
        conn.close()
        logger.info("Done.")

if __name__ == "__main__":
    main()
