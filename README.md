# Ohio Regional Parcel Exporter 🗺️

> A production-grade ArcGIS Pro Python Toolbox (`.pyt`) for extracting Ohio
> Statewide Parcel data by any geographic boundary — with zero manual layer
> loading required.

---

## The Problem

The [Ohio Statewide Parcels (Public View)](https://geohio.maps.arcgis.com/home/item.html?id=26ab5fad8d5d4258a7492a14de83bc0e)
hosted feature service contains **millions of records** covering all 88 Ohio counties.
When working with this dataset in ArcGIS Pro:

- The **Export Features** button in the UI is greyed out (disabled by the data owner to prevent server overload)
- Standard **Feature Class to Shapefile** tools fail due to the 2 GB shapefile size limit and timeout errors
- Manually downloading the entire state when you only need one county or MPO area wastes significant time and storage

---

## The Solution

This toolbox connects directly to the GeoOhio FeatureServer REST API using the
user's **active ArcGIS Pro portal credentials** — the same sign-in used when
pasting a URL into *Insert > Add Data > Data From Path*. It then applies
server-side attribute or spatial filtering before streaming only the records
you need into your project's default geodatabase.

Results appear automatically in the **Contents pane**, ready to use.

---

## Filter Modes

| Mode | How it works | Speed |
|---|---|---|
| **Statewide** | Exports all Ohio parcel records | Slowest |
| **ODOT District** | SQL query: `COUNTY IN (...)` using built-in district–county mapping | Fast |
| **MPO Area** | True spatial intersect against a user-supplied MPO boundary layer | Medium |
| **County** | SQL query: `COUNTY = 'NAME'` — no boundary layer needed | Fastest |
| **Specific Boundary** | Spatial intersect against any polygon (shapefile, feature class, etc.) | Medium |

### Edge Parcel Behaviour
For spatial filters (MPO Area and Specific Boundary), the tool uses
`SelectLayerByLocation (INTERSECT)` and exports **full parcel geometry** — it
does **not** clip parcels at the boundary edge. Any parcel that touches or
overlaps the boundary is included in its entirety. This is intentional: a
half-parcel is not a useful planning unit.

---

## Technical Architecture

### Why `addDataFromPath()` instead of `MakeFeatureLayer(url)`
`MakeFeatureLayer` hits the REST endpoint directly and needs its own
authentication handshake, which fails unless the user is signed in at the
arcpy level. `addDataFromPath()` borrows the credentials the user already
established when signing into ArcGIS Pro — the same ones that allow pasting
the URL manually.

### Why the output goes to the project default GDB
`aprx.defaultGeodatabase` is where ArcGIS Pro sends all built-in geoprocessing
outputs (Buffer, Clip, etc.). Coworkers already know where it is, it is always
writable, and it keeps data organized inside the project.

### Why edge parcels are never clipped
`arcpy.analysis.Clip` would trim parcels to the boundary line, producing partial
polygon geometries that are inaccurate for any parcel-level analysis. The tool
uses `CopyFeatures` on the INTERSECT selection instead, preserving full parcel
geometry at boundary edges.

### The CRS problem and the two-step spatial fix
`SelectLayerByLocation` sends the clip geometry to the ArcGIS REST server.
The server only accepts WGS84 (EPSG:4326). If the boundary shapefile is in
a projected CRS (Ohio State Plane, UTM, NAD83, etc.) the server returns
**Error 400: Bad syntax in request**.

The tool detects and handles this automatically:

```
Step 1 — Inspect input CRS
         • Projected CRS → reproject to WGS84 in memory
         • Already WGS84 → skip reprojection
         • Undefined CRS → warn user, attempt with raw coordinates
         • Multiple features → dissolve to single polygon first

Step 2 — SelectLayerByLocation against the service (WGS84 geometry)
         Server receives geometry it understands → no Error 400

Step 3 — CopyFeatures exports selected records locally
         Full parcel geometry preserved, no clipping
```

This means the tool works with **any** shapefile a user provides, regardless
of its coordinate system.

---

## How to Install

1. Download or clone this repository
2. Open your ArcGIS Pro project
3. In the **Catalog pane**, right-click **Toolboxes**
4. Select **Add Toolbox** and browse to `ParcelExporter.pyt`
5. The **"Regional Parcel Exporter"** tool will appear under the toolbox

> ⚠️ Make sure `ParcelExporter.pyt` and `ParcelExporter.pyt.xml` are in the
> **same folder** — the `.xml` file provides the tooltip help text inside ArcGIS Pro.

---

## How to Use

### County (fastest)
1. Open the tool
2. Set **Filter Area By** → `County`
3. Select a county from the dropdown (all 88 Ohio counties listed)
4. Set your **Output Feature Class Name**
5. Click **Run**

### ODOT District
1. Set **Filter Area By** → `ODOT District`
2. Select a District (1–12)
3. The tool automatically queries all counties in that district

### MPO Area
1. Add an Ohio MPO Boundaries polygon layer to your map
   *(source: ODOT TIMS portal at transportation.ohio.gov)*
2. Set **Filter Area By** → `MPO Area`
3. Select the **MPO Name** from the dropdown
4. Select the **MPO Boundary Layer** from your Contents pane
5. Select the **MPO Name Field** (the field containing MPO names, e.g. `MPO_NAME`)

### Specific Boundary (any shapefile)
1. Add your polygon layer to the ArcGIS Pro map
2. Set **Filter Area By** → `Specific Boundary (Shapefile/Layer)`
3. Select your polygon layer
4. Click **Run** — any coordinate system is handled automatically

---

## ODOT District Reference

| District | Counties |
|---|---|
| 1 | Allen, Defiance, Hancock, Hardin, Paulding, Putnam, Van Wert, Wyandot |
| 2 | Fulton, Henry, Lucas, Ottawa, Sandusky, Seneca, Williams, Wood |
| 3 | Ashland, Crawford, Erie, Huron, Lorain, Medina, Richland, Wayne |
| 4 | Ashtabula, Mahoning, Portage, Stark, Summit, Trumbull |
| 5 | Coshocton, Fairfield, Guernsey, Knox, Licking, Muskingum, Perry |
| 6 | Delaware, Fayette, Franklin, Madison, Marion, Morrow, Pickaway, Union |
| 7 | Auglaize, Champaign, Clark, Darke, Logan, Mercer, Miami, Montgomery, Shelby |
| 8 | Butler, Clermont, Clinton, Greene, Hamilton, Preble, Warren |
| 9 | Adams, Brown, Highland, Jackson, Lawrence, Pike, Ross, Scioto |
| 10 | Athens, Gallia, Hocking, Meigs, Monroe, Morgan, Noble, Vinton, Washington |
| 11 | Belmont, Carroll, Columbiana, Harrison, Holmes, Jefferson, Tuscarawas |
| 12 | Cuyahoga, Geauga, Lake |

---

## Repository Structure

```
Ohio_Parcel_Toolbox/
├── ParcelExporter.pyt       # Python Toolbox — all tool logic and UI
├── ParcelExporter.pyt.xml   # Metadata — tooltip help text for ArcGIS Pro
└── README.md                # This file
```

---

## Skills Demonstrated

- **ArcPy Python Toolbox (`.pyt`)** development with dynamic UI validation
- **ArcGIS REST API** integration via active portal credential routing
- **Coordinate system handling** — automatic CRS detection and reprojection
- **Server-side spatial filtering** — attribute queries and spatial intersect
  against hosted feature services
- **Error handling and user messaging** — meaningful warnings for every failure mode
- **GIS data engineering** — large-scale vector data extraction to File Geodatabase

---

## Data Source

**Ohio Statewide Parcels Public View**
Managed by OGRIP (Ohio Geographically Referenced Information Program)
Item ID: `26ab5fad8d5d4258a7492a14de83bc0e`
Portal: [GeoOhio](https://geohio.maps.arcgis.com)

---

## Requirements

- ArcGIS Pro 2.9 or later
- Active portal sign-in with access to the GeoOhio parcel service
- Network access to `services2.arcgis.com`
- Spatial Analyst or Advanced license not required — Standard license sufficient
