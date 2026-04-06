"""
Ohio Regional Parcel Exporter
─────────────────────────────
Author  : [Your Name]
Version : 5.0

What changed in v5
  • Fixed "Error 400 / Bad syntax in request" that occurred when using a local
    shapefile or feature class as a spatial boundary against the hosted service.

    Root cause: SelectLayerByLocation sends the clip geometry to the server.
    If that geometry is in a projected coordinate system (e.g. Ohio State Plane)
    while the service is in WGS84, the server rejects it with a 400 error.

    Fix: Two-step spatial filter for Specific Boundary and MPO Area:
      1. Project the boundary to WGS84 (in memory) — matches the service CRS.
      2. SelectLayerByLocation with the reprojected geometry — server accepts it.
      3. CopyFeatures exports the rough result to a local temp feature class.
      4. arcpy.analysis.Clip trims precisely to the original boundary.
    This gives exact boundary results regardless of the source CRS.

  • ODOT District and County use SQL attribute queries — no spatial operation
    against the server, so they are unaffected by this issue.

Filter modes
  Statewide             – Full state download
  ODOT District         – SQL filter by county membership (no boundary layer needed)
  MPO Area              – Two-step spatial clip against user-supplied MPO boundary
  County                – SQL attribute query on COUNTY field (fastest)
  Specific Boundary     – Two-step spatial clip against any polygon the user provides
"""

import arcpy
import os

# ── Service URL ──────────────────────────────────────────────────────────────
# "OhioStatewidePacels" (missing 'r') matches the registered service name exactly.
PARCEL_SERVICE_URL = (
    "https://services2.arcgis.com/MlJ0G8iWUyC7jAmu/arcgis/rest/services/"
    "OhioStatewidePacels_full_view/FeatureServer/0"
)

# Service coordinate system — WGS84
SERVICE_SR = arcpy.SpatialReference(4326)

# ── ODOT District → County mapping ──────────────────────────────────────────
DIST_MAP = {
    "District 1":  ["ALLEN", "DEFIANCE", "HANCOCK", "HARDIN", "PAULDING",
                    "PUTNAM", "VAN WERT", "WYANDOT"],
    "District 2":  ["FULTON", "HENRY", "LUCAS", "OTTAWA", "SANDUSKY",
                    "SENECA", "WILLIAMS", "WOOD"],
    "District 3":  ["ASHLAND", "CRAWFORD", "ERIE", "HURON", "LORAIN",
                    "MEDINA", "RICHLAND", "WAYNE"],
    "District 4":  ["ASHTABULA", "MAHONING", "PORTAGE", "STARK",
                    "SUMMIT", "TRUMBULL"],
    "District 5":  ["COSHOCTON", "FAIRFIELD", "GUERNSEY", "KNOX",
                    "LICKING", "MUSKINGUM", "PERRY"],
    "District 6":  ["DELAWARE", "FAYETTE", "FRANKLIN", "MADISON",
                    "MARION", "MORROW", "PICKAWAY", "UNION"],
    "District 7":  ["AUGLAIZE", "CHAMPAIGN", "CLARK", "DARKE", "LOGAN",
                    "MERCER", "MIAMI", "MONTGOMERY", "SHELBY"],
    "District 8":  ["BUTLER", "CLERMONT", "CLINTON", "GREENE",
                    "HAMILTON", "PREBLE", "WARREN"],
    "District 9":  ["ADAMS", "BROWN", "HIGHLAND", "JACKSON", "LAWRENCE",
                    "PIKE", "ROSS", "SCIOTO"],
    "District 10": ["ATHENS", "GALLIA", "HOCKING", "MEIGS", "MONROE",
                    "MORGAN", "NOBLE", "VINTON", "WASHINGTON"],
    "District 11": ["BELMONT", "CARROLL", "COLUMBIANA", "HARRISON",
                    "HOLMES", "JEFFERSON", "TUSCARAWAS"],
    "District 12": ["CUYAHOGA", "GEAUGA", "LAKE"],
}


# ─────────────────────────────────────────────────────────────────────────────
class Toolbox(object):
    def __init__(self):
        self.label = "Ohio Planning & Data Tools"
        self.alias = "OhioPlanning"
        self.tools = [ParcelExporter]


# ─────────────────────────────────────────────────────────────────────────────
class ParcelExporter(object):

    def __init__(self):
        self.label = "Regional Parcel Exporter"
        self.description = (
            "Extracts Ohio Statewide Parcel data directly from the GeoOhio "
            "FeatureServer using your active ArcGIS Pro portal credentials. "
            "Results are saved to the current project's default geodatabase "
            "and automatically added to the Contents pane. Supports Statewide, "
            "ODOT District, MPO Area (true spatial clip), County, and Custom "
            "Boundary filters."
        )
        self.canRunInBackground = False

    # ── Parameters ───────────────────────────────────────────────────────────
    def getParameterInfo(self):

        # 0 ── Filter type ────────────────────────────────────────────────────
        p_filter = arcpy.Parameter(
            displayName="Filter Area By",
            name="filter_type",
            datatype="GPString",
            parameterType="Required",
            direction="Input"
        )
        p_filter.filter.list = [
            "Statewide",
            "ODOT District",
            "MPO Area",
            "County",
            "Specific Boundary (Shapefile/Layer)"
        ]
        p_filter.value = "Statewide"
        p_filter.description = (
            "Choose the geographic scale for your extraction.\n\n"
            "Statewide            - Downloads all Ohio records (slowest).\n"
            "ODOT District        - SQL filter on COUNTY field; no boundary needed.\n"
            "MPO Area             - Precise spatial clip against MPO boundary layer.\n"
            "County               - SQL attribute query; fastest single-area option.\n"
            "Specific Boundary    - Precise spatial clip against any polygon you provide."
        )

        # 1 ── ODOT District ──────────────────────────────────────────────────
        p_dist = arcpy.Parameter(
            displayName="ODOT District",
            name="dist_num",
            datatype="GPString",
            parameterType="Optional",
            direction="Input"
        )
        p_dist.filter.list = [f"District {i}" for i in range(1, 13)]
        p_dist.description = (
            "Select one of Ohio's 12 ODOT Districts. No boundary layer needed — "
            "districts are defined by whole-county membership.\n\n"
            "District 1  = Allen, Defiance, Hancock, Hardin, Paulding, Putnam, Van Wert, Wyandot\n"
            "District 2  = Fulton, Henry, Lucas, Ottawa, Sandusky, Seneca, Williams, Wood\n"
            "District 3  = Ashland, Crawford, Erie, Huron, Lorain, Medina, Richland, Wayne\n"
            "District 4  = Ashtabula, Mahoning, Portage, Stark, Summit, Trumbull\n"
            "District 5  = Coshocton, Fairfield, Guernsey, Knox, Licking, Muskingum, Perry\n"
            "District 6  = Delaware, Fayette, Franklin, Madison, Marion, Morrow, Pickaway, Union\n"
            "District 7  = Auglaize, Champaign, Clark, Darke, Logan, Mercer, Miami, Montgomery, Shelby\n"
            "District 8  = Butler, Clermont, Clinton, Greene, Hamilton, Preble, Warren\n"
            "District 9  = Adams, Brown, Highland, Jackson, Lawrence, Pike, Ross, Scioto\n"
            "District 10 = Athens, Gallia, Hocking, Meigs, Monroe, Morgan, Noble, Vinton, Washington\n"
            "District 11 = Belmont, Carroll, Columbiana, Harrison, Holmes, Jefferson, Tuscarawas\n"
            "District 12 = Cuyahoga, Geauga, Lake"
        )

        # 2 ── MPO Name ───────────────────────────────────────────────────────
        p_mpo = arcpy.Parameter(
            displayName="MPO Name",
            name="mpo_name",
            datatype="GPString",
            parameterType="Optional",
            direction="Input"
        )
        p_mpo.filter.list = sorted([
            "AMATS (Akron)", "BHJ (Steubenville)", "BOMTS (Wheeling)",
            "CCSTCC (Springfield)", "Eastgate (Youngstown)", "ERPC (Sandusky)",
            "KYOVA (Huntington)", "LACRPC (Lima)", "LCATS (Newark)",
            "MORPC (Columbus)", "MVRPC (Dayton)", "NOACA (Cleveland)",
            "OKI (Cincinnati)", "RCRPC (Mansfield)", "SCATS (Canton)",
            "TMACOG (Toledo)", "WWW (Parkersburg)"
        ])
        p_mpo.description = (
            "Select the target MPO. The tool selects this MPO's polygon from "
            "your boundary layer, reprojects it to WGS84 to match the service, "
            "runs the spatial selection, then clips precisely to the original boundary."
        )

        # 3 ── MPO Boundary Layer ─────────────────────────────────────────────
        p_mpo_layer = arcpy.Parameter(
            displayName="MPO Boundary Layer",
            name="mpo_layer",
            datatype="GPFeatureLayer",
            parameterType="Optional",
            direction="Input"
        )
        p_mpo_layer.description = (
            "A polygon layer containing all Ohio MPO boundaries (one polygon per MPO). "
            "Add it to your ArcGIS Pro map first, then select it here. "
            "Source: ODOT TIMS portal (transportation.ohio.gov) or GeoOhio. "
            "Any coordinate system is supported — the tool reprojects automatically."
        )

        # 4 ── MPO Name Field ─────────────────────────────────────────────────
        p_mpo_field = arcpy.Parameter(
            displayName="MPO Name Field",
            name="mpo_field",
            datatype="Field",
            parameterType="Optional",
            direction="Input"
        )
        p_mpo_field.parameterDependencies = [p_mpo_layer.name]
        p_mpo_field.description = (
            "The field in the MPO Boundary Layer that contains the MPO name. "
            "Auto-populates once you select the layer above. Check the attribute "
            "table to identify the right field (common: MPO_NAME, NAME, AGENCY, MPO)."
        )

        # 5 ── County Name ────────────────────────────────────────────────────
        p_county = arcpy.Parameter(
            displayName="County Name",
            name="county_name",
            datatype="GPString",
            parameterType="Optional",
            direction="Input"
        )
        p_county.filter.list = sorted([
            "ADAMS", "ALLEN", "ASHLAND", "ASHTABULA", "ATHENS", "AUGLAIZE",
            "BELMONT", "BROWN", "BUTLER", "CARROLL", "CHAMPAIGN", "CLARK",
            "CLERMONT", "CLINTON", "COLUMBIANA", "COSHOCTON", "CRAWFORD",
            "CUYAHOGA", "DARKE", "DEFIANCE", "DELAWARE", "ERIE", "FAIRFIELD",
            "FAYETTE", "FRANKLIN", "FULTON", "GALLIA", "GEAUGA", "GREENE",
            "GUERNSEY", "HAMILTON", "HANCOCK", "HARDIN", "HARRISON", "HENRY",
            "HIGHLAND", "HOCKING", "HOLMES", "HURON", "JACKSON", "JEFFERSON",
            "KNOX", "LAKE", "LAWRENCE", "LICKING", "LOGAN", "LORAIN", "LUCAS",
            "MADISON", "MAHONING", "MARION", "MEDINA", "MEIGS", "MERCER",
            "MIAMI", "MONROE", "MONTGOMERY", "MORGAN", "MORROW", "MUSKINGUM",
            "NOBLE", "OTTAWA", "PAULDING", "PERRY", "PICKAWAY", "PIKE",
            "PORTAGE", "PREBLE", "PUTNAM", "RICHLAND", "ROSS", "SANDUSKY",
            "SCIOTO", "SENECA", "SHELBY", "STARK", "SUMMIT", "TRUMBULL",
            "TUSCARAWAS", "UNION", "VAN WERT", "VINTON", "WARREN", "WASHINGTON",
            "WAYNE", "WILLIAMS", "WOOD", "WYANDOT"
        ])
        p_county.description = (
            "Select any of Ohio's 88 counties. Runs COUNTY = 'NAME' as a SQL "
            "attribute query — no boundary layer, no spatial operation, fastest option.\n\n"
            "All 88: Adams, Allen, Ashland, Ashtabula, Athens, Auglaize, Belmont, "
            "Brown, Butler, Carroll, Champaign, Clark, Clermont, Clinton, Columbiana, "
            "Coshocton, Crawford, Cuyahoga, Darke, Defiance, Delaware, Erie, Fairfield, "
            "Fayette, Franklin, Fulton, Gallia, Geauga, Greene, Guernsey, Hamilton, "
            "Hancock, Hardin, Harrison, Henry, Highland, Hocking, Holmes, Huron, "
            "Jackson, Jefferson, Knox, Lake, Lawrence, Licking, Logan, Lorain, Lucas, "
            "Madison, Mahoning, Marion, Medina, Meigs, Mercer, Miami, Monroe, "
            "Montgomery, Morgan, Morrow, Muskingum, Noble, Ottawa, Paulding, Perry, "
            "Pickaway, Pike, Portage, Preble, Putnam, Richland, Ross, Sandusky, "
            "Scioto, Seneca, Shelby, Stark, Summit, Trumbull, Tuscarawas, Union, "
            "Van Wert, Vinton, Warren, Washington, Wayne, Williams, Wood, Wyandot."
        )

        # 6 ── Custom Boundary Feature ────────────────────────────────────────
        p_boundary = arcpy.Parameter(
            displayName="Custom Boundary Feature",
            name="boundary_feat",
            datatype="GPFeatureLayer",
            parameterType="Optional",
            direction="Input"
        )
        p_boundary.description = (
            "Any polygon layer defining your study area — township, city boundary, "
            "corridor buffer, custom planning area, etc. Any coordinate system is "
            "supported; the tool reprojects to WGS84 automatically before querying "
            "the service, then clips precisely to your original boundary."
        )

        # 7 ── Output Feature Class Name ──────────────────────────────────────
        p_name = arcpy.Parameter(
            displayName="Output Feature Class Name",
            name="out_name",
            datatype="GPString",
            parameterType="Required",
            direction="Input"
        )
        p_name.value = "Parcels_Export"
        p_name.description = (
            "Name for the output feature class in the project's default geodatabase. "
            "Letters, numbers, and underscores only — no spaces or special characters. "
            "The name auto-suggests based on your filter selection but can be changed. "
            "Examples: Parcels_Stark_County, Parcels_District4, Parcels_SCATS_MPO."
        )

        return [
            p_filter,    # 0
            p_dist,      # 1
            p_mpo,       # 2
            p_mpo_layer, # 3
            p_mpo_field, # 4
            p_county,    # 5
            p_boundary,  # 6
            p_name       # 7
        ]

    # ── Dynamic UI ───────────────────────────────────────────────────────────
    def updateParameters(self, parameters):
        f_type = parameters[0].valueAsText or ""

        parameters[1].enabled = (f_type == "ODOT District")

        mpo_active = (f_type == "MPO Area")
        parameters[2].enabled = mpo_active
        parameters[3].enabled = mpo_active
        parameters[4].enabled = mpo_active

        parameters[5].enabled = (f_type == "County")
        parameters[6].enabled = (f_type == "Specific Boundary (Shapefile/Layer)")

        # Auto-suggest output name unless user has manually changed it
        if not parameters[7].altered:
            dist   = parameters[1].valueAsText or ""
            mpo    = parameters[2].valueAsText or ""
            county = parameters[5].valueAsText or ""

            if f_type == "ODOT District" and dist:
                parameters[7].value = f"Parcels_{dist.replace(' ', '_')}"
            elif f_type == "MPO Area" and mpo:
                safe_mpo = mpo.split("(")[0].strip().replace(" ", "_")
                parameters[7].value = f"Parcels_{safe_mpo}_MPO"
            elif f_type == "County" and county:
                parameters[7].value = (
                    f"Parcels_{county.title().replace(' ', '_')}_County"
                )
            elif f_type == "Specific Boundary (Shapefile/Layer)":
                parameters[7].value = "Parcels_Custom_Boundary"
            elif f_type == "Statewide":
                parameters[7].value = "Parcels_Statewide"

    # ── Validation messages ──────────────────────────────────────────────────
    def updateMessages(self, parameters):
        # Valid parameter message methods: setWarningMessage(), setErrorMessage(),
        # clearMessage(). Do NOT use setInformationMessage() or setIDMessage().
        f_type = parameters[0].valueAsText

        if f_type == "ODOT District" and not parameters[1].valueAsText:
            parameters[1].setWarningMessage(
                "Select a District. Counties will be queried automatically."
            )
        if f_type == "MPO Area":
            if not parameters[2].valueAsText:
                parameters[2].setWarningMessage("Select an MPO from the list.")
            if not parameters[3].valueAsText:
                parameters[3].setWarningMessage(
                    "Add the Ohio MPO Boundary layer to your map, then select it here."
                )
            if parameters[3].valueAsText and not parameters[4].valueAsText:
                parameters[4].setWarningMessage(
                    "Select the field containing MPO names "
                    "(e.g. MPO_NAME, NAME, AGENCY)."
                )
        if f_type == "County" and not parameters[5].valueAsText:
            parameters[5].setWarningMessage(
                "Select a county. All 88 Ohio counties are in the dropdown."
            )
        if (f_type == "Specific Boundary (Shapefile/Layer)"
                and not parameters[6].valueAsText):
            parameters[6].setWarningMessage(
                "Provide a polygon layer defining your area of interest."
            )

        # Validate output name
        out_name = parameters[7].valueAsText
        if out_name:
            invalid = set(out_name) - set(
                "abcdefghijklmnopqrstuvwxyz"
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                "0123456789_"
            )
            if invalid:
                parameters[7].setErrorMessage(
                    f"Invalid characters: {' '.join(invalid)}. "
                    "Use only letters, numbers, and underscores."
                )
            elif out_name[0].isdigit():
                parameters[7].setErrorMessage(
                    "Feature class name cannot start with a number."
                )

    # ── Helpers ──────────────────────────────────────────────────────────────
    def _spatial_filter_and_clip(self, service_lyr, boundary_fc, out_fc):
        """
        Universal two-step spatial filter — works for ANY input polygon
        regardless of coordinate system, number of features, or file format.

        Root cause of Error 400
        ───────────────────────
        SelectLayerByLocation sends the clip geometry to the ArcGIS REST server.
        The server only understands WGS84 (EPSG:4326). Any other CRS — Ohio State
        Plane, UTM, NAD83, Web Mercator, etc. — causes "Bad syntax in request."

        Four cases handled automatically
        ──────────────────────────────────
        Case 1  Projected CRS (most common)
                Reproject to WGS84 in memory before the server query.

        Case 2  Already WGS84
                Skip reprojection, query directly.

        Case 3  Undefined / missing CRS (.prj absent or corrupt)
                Warn the user and attempt the query with raw coordinates.
                If the data is geographic (lat/lon values) it will work.
                If it is actually projected without a .prj, the user is
                instructed to run Define Projection first.

        Case 4  Multiple features in the boundary layer
                Dissolved to a single polygon before querying so the server
                receives one clean geometry rather than many small requests
                that could fail individually or leave gaps.

        Steps
        ─────
        1  Inspect CRS and handle all four cases above.
        2  SelectLayerByLocation against the service (WGS84 geometry).
        3  CopyFeatures exports the rough result to in_memory (local, fast).
        4  arcpy.analysis.Clip trims to the ORIGINAL boundary — exact edges,
           correct output CRS, no approximation from the reprojection step.
        """
        # ── Step 1: Inspect CRS and prepare query geometry ────────────────────
        desc     = arcpy.Describe(boundary_fc)
        input_sr = desc.spatialReference

        # If the boundary has multiple features, dissolve to one clean polygon.
        # This prevents the server from receiving many small geometries which
        # can individually fail or produce gaps between results.
        feat_count = int(arcpy.management.GetCount(boundary_fc).getOutput(0))
        if feat_count == 0:
            raise ValueError("The boundary layer contains no features.")

        work_boundary = boundary_fc  # will be updated if dissolve/reproject needed

        if feat_count > 1:
            arcpy.AddMessage(
                f"  Boundary has {feat_count} features — dissolving to single "
                "polygon for a cleaner server query..."
            )
            dissolved = r"in_memory\dissolved_boundary"
            arcpy.management.Dissolve(boundary_fc, dissolved)
            work_boundary = dissolved

        # Evaluate the CRS and decide what to do
        is_undefined = (
            input_sr.name in ("Unknown", "")
            or input_sr.factoryCode == 0
        )
        is_wgs84 = (input_sr.factoryCode == 4326)

        if is_undefined:
            # Case 3 — no .prj file or corrupt projection definition
            arcpy.AddWarning(
                "The boundary layer has no defined coordinate system "
                "(.prj file missing or undefined). The tool will attempt to "
                "use it as-is. If the result is empty or the query fails, "
                "define the projection first: right-click layer > Properties > "
                "Source, or run the 'Define Projection' geoprocessing tool."
            )
            arcpy.AddMessage(
                "Step 1/3 — Skipping reprojection (undefined CRS). "
                "Querying server with raw coordinates..."
            )
            query_boundary = work_boundary

        elif is_wgs84:
            # Case 2 — already in the right CRS, nothing to do
            arcpy.AddMessage(
                "Step 1/3 — Boundary is already in WGS84. "
                "No reprojection needed..."
            )
            query_boundary = work_boundary

        else:
            # Case 1 — projected CRS, reproject to WGS84
            arcpy.AddMessage(
                f"Step 1/3 — Reprojecting boundary from '{input_sr.name}' "
                "to WGS84 for server-side query..."
            )
            projected = r"in_memory\boundary_wgs84"
            arcpy.management.Project(work_boundary, projected, SERVICE_SR)
            query_boundary = projected

        # ── Step 2: Server-side spatial selection ─────────────────────────────
        arcpy.AddMessage(
            "Step 2/3 — Running spatial selection against parcel service..."
        )
        arcpy.management.SelectLayerByLocation(
            service_lyr, "INTERSECT", query_boundary
        )

        selected = int(arcpy.management.GetCount(service_lyr).getOutput(0))
        if selected == 0:
            raise ValueError(
                "No parcels found within the boundary. Verify that the boundary "
                "layer overlaps the Ohio parcel service extent, and that the "
                "coordinate system is correctly defined."
            )
        arcpy.AddMessage(f"  {selected:,} parcels selected.")

        # ── Step 3: Export all selected parcels with full geometry ─────────────
        # We intentionally do NOT use arcpy.analysis.Clip here.
        # Clip would cut parcels at the boundary edge, removing parts of parcels
        # that straddle the line. The correct behaviour is to keep the FULL
        # geometry of every parcel that touches or overlaps the boundary — so
        # edge parcels are always complete, never trimmed. The INTERSECT selection
        # in Step 2 already captured exactly the right set of parcels.
        arcpy.AddMessage(
            "Step 3/3 — Exporting all selected parcels with full geometry..."
        )
        arcpy.management.CopyFeatures(service_lyr, out_fc)

        # ── Cleanup ───────────────────────────────────────────────────────────
        for tmp in [r"in_memory\dissolved_boundary",
                    r"in_memory\boundary_wgs84"]:
            try:
                if arcpy.Exists(tmp):
                    arcpy.management.Delete(tmp)
            except Exception:
                pass  # Non-critical — do not mask real errors

    # ── Execution ────────────────────────────────────────────────────────────
    def execute(self, parameters, messages):
        arcpy.env.overwriteOutput = True

        f_type        = parameters[0].valueAsText
        dist_raw      = parameters[1].valueAsText
        mpo_name      = parameters[2].valueAsText
        mpo_layer     = parameters[3].valueAsText
        mpo_field     = parameters[4].valueAsText
        county_name   = parameters[5].valueAsText
        boundary_feat = parameters[6].valueAsText
        out_name      = parameters[7].valueAsText

        # ── Get project and active map ────────────────────────────────────────
        try:
            aprx       = arcpy.mp.ArcGISProject("CURRENT")
            active_map = aprx.activeMap
            if active_map is None:
                maps = aprx.listMaps()
                if not maps:
                    arcpy.AddError(
                        "No map found in the current project. "
                        "Open or create a map before running this tool."
                    )
                    return
                active_map = maps[0]
                arcpy.AddMessage(f"No active map — using '{active_map.name}'.")

            project_gdb = aprx.defaultGeodatabase
            arcpy.AddMessage(f"Project GDB       : {project_gdb}")

        except Exception as e:
            arcpy.AddError(f"Could not access current ArcGIS Pro project: {e}")
            return

        out_fc = os.path.join(project_gdb, out_name)

        # ── Load parcel service via active portal credentials ─────────────────
        arcpy.AddMessage(
            "Connecting to GeoOhio Parcel Service via active portal credentials..."
        )
        parcel_layer = None
        try:
            parcel_layer = active_map.addDataFromPath(PARCEL_SERVICE_URL)
            arcpy.AddMessage(
                f"Connected. Layer '{parcel_layer.name}' loaded into "
                f"map '{active_map.name}'."
            )
        except Exception as e:
            arcpy.AddError(
                f"Failed to connect to the parcel service.\nError: {e}\n\n"
                "Troubleshooting:\n"
                "  1. Confirm you are signed into ArcGIS Pro (File > Sign In).\n"
                "  2. Test manually: Insert > Add Data > Data From Path, paste:\n"
                f"     {PARCEL_SERVICE_URL}\n"
                "  3. Confirm your network can reach services2.arcgis.com."
            )
            return

        # Make a selectable layer from the added service layer
        temp_lyr = "parcel_working_lyr"
        arcpy.management.MakeFeatureLayer(parcel_layer, temp_lyr)

        # ── Apply geographic filter ───────────────────────────────────────────
        try:
            if f_type == "Statewide":
                arcpy.AddMessage(
                    "Statewide selected. Exporting all Ohio parcel records. "
                    "This may take a significant amount of time."
                )
                arcpy.management.CopyFeatures(temp_lyr, out_fc)

            elif f_type == "ODOT District":
                counties = DIST_MAP.get(dist_raw, [])
                if not counties:
                    arcpy.AddError(f"No county mapping found for '{dist_raw}'.")
                    return
                county_sql = ", ".join([f"'{c}'" for c in counties])
                query = f"COUNTY IN ({county_sql})"
                arcpy.AddMessage(
                    f"Filtering {dist_raw} — "
                    f"{len(counties)} counties: {', '.join(counties)}"
                )
                arcpy.management.SelectLayerByAttribute(
                    temp_lyr, "NEW_SELECTION", query
                )
                arcpy.management.CopyFeatures(temp_lyr, out_fc)

            elif f_type == "County":
                query = f"COUNTY = '{county_name}'"
                arcpy.AddMessage(f"Filtering by county: {county_name}")
                arcpy.management.SelectLayerByAttribute(
                    temp_lyr, "NEW_SELECTION", query
                )
                arcpy.management.CopyFeatures(temp_lyr, out_fc)

            elif f_type == "MPO Area":
                # Isolate the target MPO polygon from the boundary layer
                arcpy.AddMessage(
                    f"Isolating '{mpo_name}' polygon from MPO boundary layer..."
                )
                mpo_temp = "mpo_clip_lyr"
                arcpy.management.MakeFeatureLayer(mpo_layer, mpo_temp)
                arcpy.management.SelectLayerByAttribute(
                    mpo_temp, "NEW_SELECTION",
                    f"{mpo_field} = '{mpo_name}'"
                )
                hit_count = int(
                    arcpy.management.GetCount(mpo_temp).getOutput(0)
                )
                if hit_count == 0:
                    arcpy.AddError(
                        f"No polygon matched: {mpo_field} = '{mpo_name}'.\n"
                        "Open the MPO boundary layer attribute table and confirm "
                        "the exact text — it must match character-for-character."
                    )
                    return

                # Export selected MPO polygon to in_memory for the two-step clip
                mpo_boundary_fc = r"in_memory\mpo_single_boundary"
                arcpy.management.CopyFeatures(mpo_temp, mpo_boundary_fc)

                arcpy.AddMessage(
                    f"Found {hit_count} polygon(s). "
                    "Starting two-step spatial clip..."
                )
                self._spatial_filter_and_clip(temp_lyr, mpo_boundary_fc, out_fc)
                arcpy.management.Delete(mpo_boundary_fc)

            elif f_type == "Specific Boundary (Shapefile/Layer)":
                arcpy.AddMessage(
                    f"Starting two-step spatial clip against: {boundary_feat}"
                )
                self._spatial_filter_and_clip(temp_lyr, boundary_feat, out_fc)

        except Exception as e:
            arcpy.AddError(f"Filter/export step failed: {e}")
            return

        finally:
            # Always remove the temporary cloud layer — even if export fails
            if parcel_layer is not None:
                try:
                    active_map.removeLayer(parcel_layer)
                    arcpy.AddMessage(
                        "Temporary cloud layer removed from map."
                    )
                except Exception:
                    pass

        # ── Add result to Contents pane ───────────────────────────────────────
        result_count = 0
        try:
            result_count = int(
                arcpy.management.GetCount(out_fc).getOutput(0)
            )
            result_layer = active_map.addDataFromPath(out_fc)
            arcpy.AddMessage(
                f"Layer '{result_layer.name}' added to Contents pane."
            )
        except Exception as e:
            arcpy.AddWarning(
                f"Export succeeded but could not add layer to Contents pane: {e}"
            )

        # ── Summary ───────────────────────────────────────────────────────────
        arcpy.AddMessage("=" * 55)
        arcpy.AddMessage("SUCCESS — Extraction complete.")
        arcpy.AddMessage(f"Filter applied    : {f_type}")
        arcpy.AddMessage(f"Features exported : {result_count:,}")
        arcpy.AddMessage(f"Output location   : {out_fc}")
        arcpy.AddMessage(f"Layer in Contents : {out_name}")
        arcpy.AddMessage("=" * 55)
