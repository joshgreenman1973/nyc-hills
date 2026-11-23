# Uphill

Every walkable block in New York City colored by how steep it is, measured from a federal lidar survey, with a router that finds the flattest walk between any two points.

- Streets: NYC Street Centerline (CSCL), NYC Open Data inkn-q76z
- Elevation: USGS 3D Elevation Program, one-meter bare-earth model from the 2013–14 New York CMGP Sandy lidar

To rebuild:

```bash
curl -s -o raw/centerline.geojson "https://data.cityofnewyork.us/resource/inkn-q76z.geojson?\$limit=200000&\$select=the_geom,physicalid,rw_type,nonped,status,from_level_code,to_level_code,boroughcode,stname_label,full_street_name,segmentlength,trafdir,segment_type"
python3 fetch_dem.py
python3 build.py
```

How every number is computed: [methodology.md](methodology.md).
