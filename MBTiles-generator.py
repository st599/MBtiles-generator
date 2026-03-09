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

# --- LOGGING CONFIGURATION ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Server definitions
# 'osm' is used as the base layer for all composites.
SERVERS = {
    'osm': 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    'cyclosm': 'https://{s}.tile.openstreetmap.fr/cyclosm/{z}/{x}/{y}.png',
    'hiking': 'https://tile.waymarkedtrails.org/hiking/{z}/{x}/{y}.png'
}

def print_gpl_header():
    """
    Prints the GPLv3 license notice to the console at startup.
    Ensures users are aware of their rights and the lack of warranty.
    """
    header = """
MBTiles Generator - Custom Map Generator
Copyright (C) 2026  <Your Name/Organization>

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License version 3.
This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY.
    """
    print(header)

def deg2num(lat_deg, lon_deg, zoom):
    """
    Converts Latitude and Longitude to Slippy Map tile coordinates (X, Y).
    
    Uses the spherical Mercator projection. Note that the Y axis in OSM 
    tile systems starts at 0 at the North Pole and increases Southward.
    
    Args:
        lat_deg (float): Latitude.
        lon_deg (float): Longitude.
        zoom (int): Zoom level.
        
    Returns:
        tuple: (xtile, ytile)
    """
    lat_rad = math.radians(lat_deg)
    n = 2.0 ** zoom
    xtile = int((lon_deg + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.log(math.tan(lat_rad) + (1 / math.cos(lat_rad))) / math.pi) / 2.0 * n)
    return (xtile, ytile)

def get_tile_with_retry(url, zoom, x, y):
    """
    Fetches a map tile with browser-mimicking headers and subdomain rotation.
    
    Args:
        url (str): Template URL from SERVERS.
        zoom, x, y (int): Tile coordinates.
        
    Returns:
        bytes: Raw tile data or None if download fails.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Referer': 'https://www.openstreetmap.org/'
    }
    subdomains = ['a', 'b', 'c'] if '{s}' in url else ['']
    for sub in subdomains:
        try:
            target_url = url.replace("{s}", sub).format(z=zoom, x=x, y=y)
            r = requests.get(target_url, headers=headers, timeout=10)
            if r.status_code == 200:
                return r.content
        except Exception:
            continue
    return None

def main():
    """
    Orchestrates map generation: calculates the grid, downloads tiles, 
    and composites overlays onto a base map if necessary.
    """
    print_gpl_header()
    parser = argparse.ArgumentParser(description="MBTiles Generator")
    parser.add_argument("--bbox", nargs=4, type=float, required=True, help="min_lat min_lon max_lat max_lon")
    parser.add_argument("--output", default="map.mbtiles", help="Output filename")
    parser.add_argument("--server", choices=SERVERS.keys(), default="osm", help="Map style")
    parser.add_argument("--zooms", nargs=2, type=int, default=[13, 16], help="Min Max zoom")
    parser.add_argument("--quality", type=int, default=80, help="JPG quality (1-100)")
    
    args = parser.parse_args()
    min_lat, min_lon, max_lat, max_lon = args.bbox
    zoom_range = range(args.zooms[0], args.zooms[1] + 1)

    # Cleanup: Ensure we start with fresh files
    xml_file = os.path.splitext(args.output)[0] + ".xml"
    for f in [args.output, xml_file]:
        if os.path.exists(f):
            logger.info(f"Removing existing file: {f}")
            os.remove(f)

    # XML Metadata: Required for the Teasi/Tahuna indexing system
    root = ET.Element("MapMetadata")
    ET.SubElement(root, "Name").text = os.path.basename(args.output)
    bounds_xml = ET.SubElement(root, "Bounds")
    for k, v in zip(["MinLat", "MinLon", "MaxLat", "MaxLon"], args.bbox):
        ET.SubElement(bounds_xml, k).text = str(v)
    
    with open(xml_file, "w") as f:
        f.write(minidom.parseString(ET.tostring(root)).toprettyxml(indent="   "))
    logger.info(f"Created XML: {xml_file}")

    # Database: Setup MBTiles schema
    conn = sqlite3.connect(args.output)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE metadata (name text, value text);")
    cursor.execute("CREATE TABLE tiles (zoom_level integer, tile_column integer, tile_row integer, tile_data blob);")
    
    meta = [
        ('name', args.output), ('format', 'jpg'), ('projection', 'EPSG:3857'),
        ('bounds', f"{min_lon},{min_lat},{max_lon},{max_lat}")
    ]
    cursor.executemany("INSERT INTO metadata VALUES (?, ?)", meta)

    try:
        for zoom in zoom_range:
            # Map coordinates to the tile grid
            x1, y1 = deg2num(max_lat, min_lon, zoom)
            x2, y2 = deg2num(min_lat, max_lon, zoom)
            
            logger.info(f"Zoom {zoom}: Processing tiles...")
            for x in range(min(x1, x2), max(x1, x2) + 1):
                for y in range(min(y1, y2), max(y1, y2) + 1):
                    # 1. Fetch OSM Base Layer
                    # Even for Hiking/CyclOSM, we need the base map to avoid a transparent result.
                    base_data = get_tile_with_retry(SERVERS['osm'], zoom, x, y)
                    if not base_data: continue
                    
                    # Open base and convert to RGBA for alpha blending
                    base_img = Image.open(io.BytesIO(base_data)).convert("RGBA")
                    
                    # 2. Layer Compositing
                    # If an overlay server is selected, download and paste it on top of the base.
                    if args.server != 'osm':
                        overlay_data = get_tile_with_retry(SERVERS[args.server], zoom, x, y)
                        if overlay_data:
                            overlay_img = Image.open(io.BytesIO(overlay_data)).convert("RGBA")
                            base_img.alpha_composite(overlay_img)
                    
                    # 3. Finalize for MBTiles
                    # TeasiOne requires RGB JPEGs. Converting from RGBA to RGB flattens the image.
                    final_img = base_img.convert("RGB")
                    buf = io.BytesIO()
                    final_img.save(buf, format="JPEG", quality=args.quality)
                    
                    # TMS flip: MBTiles counts Y from the South Pole up.
                    tms_y = (pow(2, zoom) - 1) - y
                    cursor.execute("INSERT INTO tiles VALUES (?, ?, ?, ?)", (zoom, x, tms_y, sqlite3.Binary(buf.getvalue())))
                
                # Commit every column to preserve progress
                conn.commit()
    except KeyboardInterrupt:
        logger.warning("Interrupted by user. Progress saved.")
    finally:
        conn.close()
        logger.info(f"Map generation finished: {args.output}")

if __name__ == "__main__":
    main()