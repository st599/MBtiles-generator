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
    lat_rad = math.radians(lat_deg)
    n = 2.0 ** zoom
    xtile = int((lon_deg + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return (xtile, ytile)

def create_teasi_metadata_xml(bbox, zoom_range, output_name, server_name):
    """Generates the metadata.xml file required by some Tahuna/Teasi versions."""
    xml_path = os.path.splitext(output_name)[0] + ".xml"
    
    root = ET.Element("MapMetadata")
    ET.SubElement(root, "Name").text = os.path.basename(output_name)
    ET.SubElement(root, "Description").text = f"Cycling map generated from {server_name}"
    ET.SubElement(root, "Provider").text = "OpenStreetMap/TeasiGen"
    
    bounds = ET.SubElement(root, "Bounds")
    ET.SubElement(bounds, "MinLat").text = str(bbox[0])
    ET.SubElement(bounds, "MinLon").text = str(bbox[1])
    ET.SubElement(bounds, "MaxLat").text = str(bbox[2])
    ET.SubElement(bounds, "MaxLon").text = str(bbox[3])
    
    zooms = ET.SubElement(root, "ZoomLevels")
    ET.SubElement(zooms, "Min").text = str(min(zoom_range))
    ET.SubElement(zooms, "Max").text = str(max(zoom_range))
    
    # Pretty print the XML
    xml_str = minidom.parseString(ET.tostring(root)).toprettyxml(indent="   ")
    with open(xml_path, "w", encoding="utf-8") as f:
        f.write(xml_str)
    
    logger.info(f"Metadata XML created: {xml_path}")

def setup_db(conn, bbox, zoom_range, name):
    cursor = conn.cursor()
    cursor.execute("DROP TABLE IF EXISTS metadata;")
    cursor.execute("DROP TABLE IF EXISTS tiles;")
    cursor.execute("CREATE TABLE metadata (name text, value text);")
    cursor.execute("CREATE TABLE tiles (zoom_level integer, tile_column integer, tile_row integer, tile_data blob);")
    
    meta = [
        ('name', name), ('type', 'baselayer'), ('version', '1.1'),
        ('description', f'Cycling map'), ('format', 'jpg'),
        ('bounds', f"{bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]}"),
        ('minzoom', str(min(zoom_range))), ('maxzoom', str(max(zoom_range)))
    ]
    cursor.executemany("INSERT INTO metadata VALUES (?, ?)", meta)
    conn.commit()

def validate_mbtiles(file_path):
    logger.info("--- Final Validation ---")
    if not os.path.exists(file_path):
        return
    
    conn = sqlite3.connect(file_path)
    cursor = conn.cursor()
    try:
        cursor.execute("PRAGMA integrity_check;")
        if cursor.fetchone()[0] == "ok":
            logger.info("Database Integrity: OK")
        
        size_mb = os.path.getsize(file_path) / (1024 * 1024)
        logger.info(f"Final Size: {size_mb:.2f} MB")
    finally:
        conn.close()

def main():
    parser = argparse.ArgumentParser(description="TeasiOne Map & Metadata Generator")
    parser.add_argument("--bbox", nargs=4, type=float, required=True, help="min_lat min_lon max_lat max_lon")
    parser.add_argument("--output", default="map.mbtiles", help="Output filename")
    parser.add_argument("--server", choices=SERVERS.keys(), default="osm", help="Map style")
    parser.add_argument("--zooms", nargs=2, type=int, default=[13, 16], help="Min Max zoom")
    parser.add_argument("--quality", type=int, default=80, help="JPG quality")
    parser.add_argument("--dry-run", action="store_true", help="Calculate count and exit")
    
    args = parser.parse_args()
    zoom_range = range(args.zooms[0], args.zooms[1] + 1)
    
    # Pre-flight calculation
    total_expected = 0
    for z in zoom_range:
        x_min, y_max = deg2num(args.bbox[0], args.bbox[1], z)
        x_max, y_min = deg2num(args.bbox[2], args.bbox[3], z)
        total_expected += (x_max - x_min + 1) * (y_max - y_min + 1)
    
    logger.info(f"Target: {total_expected} tiles. Est. Size: ~{(total_expected*15)/1024:.2f} MB")

    if args.dry_run:
        return

    # Create Metadata XML
    create_teasi_metadata_xml(args.bbox, zoom_range, args.output, args.server)

    # Download Loop
    base_url = SERVERS[args.server].replace("{s}", "a") 
    conn = sqlite3.connect(args.output)
    setup_db(conn, args.bbox, zoom_range, args.output)
    cursor = conn.cursor()

    try:
        for zoom in zoom_range:
            x_min, y_max = deg2num(args.bbox[0], args.bbox[1], zoom)
            x_max, y_min = deg2num(args.bbox[2], args.bbox[3], zoom)
            
            logger.info(f"Zoom {zoom}: Downloading...")
            for x in range(x_min, x_max + 1):
                for y in range(y_min, y_max + 1):
                    try:
                        r = requests.get(base_url.format(z=zoom, x=x, y=y), headers={'User-Agent': 'TeasiGen/1.0'}, timeout=10)
                        if r.status_code == 200:
                            img = Image.open(io.BytesIO(r.content)).convert("RGB")
                            buf = io.BytesIO()
                            img.save(buf, format="JPEG", quality=args.quality)
                            tms_y = (pow(2, zoom) - 1) - y
                            cursor.execute("INSERT INTO tiles VALUES (?, ?, ?, ?)", (zoom, x, tms_y, sqlite3.Binary(buf.getvalue())))
                        time.sleep(0.05)
                    except Exception as e:
                        logger.error(f"Error at {zoom}/{x}/{y}: {e}")
                conn.commit()
    except KeyboardInterrupt:
        logger.warning("Interrupted.")
    finally:
        conn.close()
        validate_mbtiles(args.output)

if __name__ == "__main__":
    main()
