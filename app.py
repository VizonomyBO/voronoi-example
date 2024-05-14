import json
import os

import pyproj
import shapely
from flask import Flask, jsonify, render_template, request
from shapely.geometry import GeometryCollection, MultiPoint, Point, mapping, shape
from shapely.ops import transform

app = Flask(__name__)

def circle_radius_estimate_in_meters(ha, additional_radius=5):
    m2 = ha * 10000
    r = (m2 / 3.1415) ** 0.5
    return r + additional_radius

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/process', methods=['POST'])
def process_geojson():
    if 'file' not in request.files or 'target_area_ha' not in request.form:
        return "Missing file or target_area_ha", 400

    file = request.files['file']
    target_area_ha = float(request.form['target_area_ha'])

    try:
        geojson_data = json.load(file)
    except json.JSONDecodeError:
        return "Invalid GeoJSON file", 400

    features = geojson_data['features']

    multipoint = MultiPoint([shape(f['geometry']) for f in features])
    center = multipoint.centroid

    crs_wgs84 = pyproj.crs.CRS('epsg:4326')
    proj_str = f"+ellps=WGS84 +proj=tmerc +lat_0={center.y} +lon_0={center.x} +units=m +no_defs"
    crs_dst = pyproj.crs.CRS(proj_str)
    to_tmp_tmerc = pyproj.Transformer.from_crs(crs_wgs84, crs_dst).transform
    to_wgs84 = pyproj.Transformer.from_crs(crs_dst, crs_wgs84).transform

    buffer_distance = circle_radius_estimate_in_meters(target_area_ha)
    ltm_points = []
    buffered = []
    for pt in [shape(f['geometry']) for f in features]:
        geom_reproj = transform(to_tmp_tmerc, Point(pt.x, pt.y))
        ltm_points.append(geom_reproj)
        buffered.append(geom_reproj.buffer(buffer_distance))

    envelope = GeometryCollection(ltm_points).envelope.buffer(5000)
    vp = shapely.voronoi_polygons(MultiPoint(ltm_points), extend_to=envelope)

    vpp = [0 for x in ltm_points]
    for i, pt in enumerate(ltm_points):
        for v in vp.geoms:
            if pt.intersects(v):
                vpp[i] = v

    output_features = []
    for i, (v, b, f) in enumerate(zip(vpp, buffered, features)):
        region = b.intersection(v)
        if region.is_valid and not region.is_empty:
            region_in_wgs84 = transform(to_wgs84, region)
            output_features.append({
                'type': 'Feature',
                'geometry': mapping(region_in_wgs84),
                'properties': f['properties']
            })

    output_geojson = {
        'type': 'FeatureCollection',
        'features': output_features
    }

    return jsonify(output_geojson)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
