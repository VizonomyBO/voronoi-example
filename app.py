import json
import os

import pyproj
from flask import Flask, jsonify, render_template, request
from shapely import voronoi_polygons
from shapely.geometry import GeometryCollection, MultiPoint, Point, mapping, shape
from shapely.ops import transform

# Constants
WGS84_CRS = pyproj.crs.CRS('epsg:4326')
DEFAULT_PORT = 8080
BUFFER_ENVELOPE_SIZE = 5000
ADDITIONAL_RADIUS = 5

app = Flask(__name__)

def calculate_circle_radius(ha, additional_radius=ADDITIONAL_RADIUS):
    """
    Estimate the circle radius in meters based on hectares.
    
    :param ha: Area in hectares
    :param additional_radius: Additional radius to add to the calculated radius
    :return: Estimated radius in meters
    """
    m2 = ha * 10000
    radius = (m2 / 3.1415) ** 0.5
    return radius + additional_radius

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/process', methods=['POST'])
def process_geojson():
    """
    Process the uploaded GeoJSON file to generate buffered Voronoi polygons.
    
    :return: JSON response with the processed GeoJSON data or an error message
    """
    file = request.files.get('file')
    if not file:
        return "Missing file", 400

    try:
        geojson_data = json.load(file)
    except json.JSONDecodeError:
        return "Invalid GeoJSON file", 400

    features = geojson_data.get('features', [])
    if not features:
        return "No features found in the GeoJSON file", 400

    multipoint, transformed_points, buffered_points = process_features(features)
    voronoi_regions = generate_voronoi_polygons(multipoint, transformed_points)
    output_geojson = create_output_geojson(features, transformed_points, buffered_points, voronoi_regions)

    return jsonify(output_geojson)

def process_features(features):
    """
    Process the features from the GeoJSON file.
    
    :param features: List of features from the GeoJSON file
    :return: A tuple containing MultiPoint, list of transformed points, and list of buffered points
    """
    multipoint = MultiPoint([shape(f['geometry']) for f in features])
    center = multipoint.centroid
    proj_str = f"+ellps=WGS84 +proj=tmerc +lat_0={center.y} +lon_0={center.x} +units=m +no_defs"
    crs_dst = pyproj.crs.CRS(proj_str)
    to_tmp_tmerc = pyproj.Transformer.from_crs(WGS84_CRS, crs_dst).transform

    transformed_points = []
    buffered_points = []
    for feature in features:
        point = shape(feature['geometry'])
        est_area = feature['properties'].get('est_area', 0)
        buffer_distance = calculate_circle_radius(est_area)
        transformed_point = transform(to_tmp_tmerc, Point(point.x, point.y))
        transformed_points.append(transformed_point)
        buffered_points.append(transformed_point.buffer(buffer_distance))

    return multipoint, transformed_points, buffered_points

def generate_voronoi_polygons(multipoint, transformed_points):
    """
    Generate Voronoi polygons based on transformed points.
    
    :param multipoint: MultiPoint object of the original points
    :param transformed_points: List of transformed points
    :return: Voronoi polygons
    """
    envelope = GeometryCollection(transformed_points).envelope.buffer(BUFFER_ENVELOPE_SIZE)
    return voronoi_polygons(MultiPoint(transformed_points), extend_to=envelope)

def create_output_geojson(features, transformed_points, buffered_points, voronoi_regions):
    """
    Create the output GeoJSON data.
    
    :param features: Original features from the GeoJSON file
    :param transformed_points: List of transformed points
    :param buffered_points: List of buffered points
    :param voronoi_regions: Voronoi polygons
    :return: Output GeoJSON data as a dictionary
    """
    to_wgs84 = pyproj.Transformer.from_crs(voronoi_regions.crs, WGS84_CRS).transform

    voronoi_polygons_per_point = [0] * len(transformed_points)
    for i, point in enumerate(transformed_points):
        for region in voronoi_regions.geoms:
            if point.intersects(region):
                voronoi_polygons_per_point[i] = region

    output_features = []
    for i, (voronoi_polygon, buffered_point, feature) in enumerate(zip(voronoi_polygons_per_point, buffered_points, features)):
        intersection_region = buffered_point.intersection(voronoi_polygon)
        if intersection_region.is_valid and not intersection_region.is_empty:
            region_in_wgs84 = transform(to_wgs84, intersection_region)
            output_features.append({
                'type': 'Feature',
                'geometry': mapping(region_in_wgs84),
                'properties': feature['properties']
            })

    return {
        'type': 'FeatureCollection',
        'features': output_features
    }

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', DEFAULT_PORT)))
