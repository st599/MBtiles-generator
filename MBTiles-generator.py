#!/usr/bin/env python3

#
#   MBTILES GENERATOR
#
#   Filename        :   MBTiles-generator.py
#   Description     :   Playing with AI to create MBTiles suitable for bicycle computers such as TeasiOne
#   Date            :   09/03/2026
#   Author          :   Simon Thompson
#   Copyright       :   (c) Simon Thompson 2026
#   Dependencies    :   pillow, requests, python3
#

#
#   SYSTEM IMPORTS
#

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

#
# CONSTANTS
#

# --- LOGGING CONFIGURATION ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

SERVERS = {
    'osm': 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    'cyclosm': 'https://{s}.tile.openstreetmap.fr/cyclosm/{z}/{x}/{y}.png',
    'hiking': 'https://tile.waymarkedtrails.org/hiking/{z}/{x}/{y}.png'
}


# 
# FUNCTIONS 
#

def print_gpl_header():
    """Prints the GPLv3 license notice to the console at startup."""
    header = """
MBTiles Generator - Custom Map Generator
Copyright (C) 2026  Simon Thompson

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
    """
    print(header)


def deg2num(lat_deg, lon_deg, zoom):
    """
    Converts Latitude and Longitude to Slippy Map tile coordinates (X, Y).
    Uses the spherical Mercator projection.
    """
    lat_rad = math.radians(lat_deg)
    n = 2.0 ** zoom
    xtile = int((lon_deg + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.log(math.tan(lat_rad) + (1 / math.cos(lat_rad))) / math.pi) / 2.0 * n)
    return (xtile, ytile)


def create_teasi_metadata_xml(bbox, zoom_range, output_name, server_name):
    """Generates the indexing .xml file required by Teasi/Tahuna hardware."""
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
    """Fetches a tile with browser headers to bypass server blocks."""
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
    """Checks the SQLite health and prints tile storage statistics."""
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


###########################################################################################################################
###########################################################################################################################
### MAIN FUNCTION
###########################################################################################################################
###########################################################################################################################
def main():
    """Main application loop with GPLv3 startup notice."""
    print_gpl_header()
    
    parser = argparse.ArgumentParser(description="MBTiles Generator")
    parser.add_argument("--bbox", nargs=4, type=float, required=True, help="min_lat min_lon max_lat max_lon")
    parser.add_argument("--output", default="map.mbtiles", help="Output filename")
    parser.add_argument("--server", choices=SERVERS.keys(), default="osm", help="Map style")
    parser.add_argument("--zooms", nargs=2, type=int, default=[13, 16], help="Min Max zoom")
    parser.add_argument("--quality", type=int, default=80, help="JPG quality (1-100)")
    parser.add_argument("--dry-run", action="store_true", help="Estimate only")
    
    args = parser.parse_args()
    min_lat, min_lon, max_lat, max_lon = args.bbox
    zoom_range = range(args.zooms[0], args.zooms[1] + 1)
    
    if not args.dry_run:
        xml_file = os.path.splitext(args.output)[0] + ".xml"
        for f in [args.output, xml_file]:
            if os.path.exists(f):
                logger.info(f"Deleting existing file: {f}")
                os.remove(f)

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
    
    cnt_lon, cnt_lat = (min_lon + max_lon) / 2, (min_lat + max_lat) / 2
    meta_info = [
        ('name', os.path.basename(args.output)),
        ('format', 'jpg'),
        ('bounds', f"{min_lon},{min_lat},{max_lon},{max_lat}"),
        ('center', f"{cnt_lon},{cnt_lat},{max(zoom_range)}"),
        ('type', 'baselayer'),
        ('version', '1.1'),
        ('description', f'MBTiles map generator (GPLv3)'),
        ('projection', 'EPSG:3857')
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
                        img = Image.open(io.BytesIO(tile_data)).convert("RGB")
                        buf = io.BytesIO()
                        img.save(buf, format="JPEG", quality=args.quality)
                        tms_y = (pow(2, zoom) - 1) - y
                        cursor.execute("INSERT INTO tiles VALUES (?, ?, ?, ?)", 
                                     (zoom, x, tms_y, sqlite3.Binary(buf.getvalue())))
                    
                    processed += 1
                    if processed % 50 == 0: logger.info(f"Zoom {zoom}: {processed}/{count}...")
                    time.sleep(0.1)
                conn.commit()
    except KeyboardInterrupt:
        logger.warning("Download interrupted.")
    finally:
        conn.close()
        verify_mbtiles(args.output)

if __name__ == "__main__":
    main()