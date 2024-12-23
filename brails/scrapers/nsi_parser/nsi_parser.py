# Copyright (c) 2024 The Regents of the University of California
#
# This file is part of BRAILS++.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
# this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
# this list of conditions and the following disclaimer in the documentation
# and/or other materials provided with the distribution.
#
# 3. Neither the name of the copyright holder nor the names of its contributors
# may be used to endorse or promote products derived from this software without
# specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS 'AS IS'
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
#
# You should have received a copy of the BSD 3-Clause License along with
# BRAILS++. If not, see <http://www.opensource.org/licenses/>.
#
# Contributors:
# Barbaros Cetiner
# Frank McKenna
#
#
# Last updated:
# 11-13-2024

"""
This module defines a class for scraping data from NSI.

.. autosummary::

      NSI_Parser
"""

import requests
from requests.adapters import HTTPAdapter, Retry
import sys
import json
from shapely.geometry import Point
from brails.utils import GeoTools
from brails.types.asset_inventory import Asset, AssetInventory
from brails.types.region_boundary import RegionBoundary


class NSI_Parser:

    def __init__(self):
        self.attributes = {}
        self.brails2r2dmap = {'lon': 'Longitude', 'lat': 'Latitude',
                              'fparea': 'PlanArea',
                              'numstories': 'NumberOfStories',
                              'erabuilt': 'YearBuilt',
                              'repaircost': 'ReplacementCost',
                              'constype': 'StructureType',
                              'occupancy': 'OccupancyClass', 'fp': 'Footprint'}
        self.footprints = {}
        self.nsi2brailsmap = {'x': 'lon', 'y': 'lat', 'sqft': 'fparea',
                              'num_story': 'numstories',
                              'med_yr_blt': 'erabuilt',
                              'val_struct': 'repaircost',
                              'bldgtype': 'constype',
                              'occtype': 'occupancy', 'fd_id': 'fd_id',
                              'occtype_extended': ['splitlevel','basement']} # extended features will be provided only when get_extended_features option is selected

        
    def __get_bbox(self, footprints: list) -> tuple:
        """
        Get the bounding box for a footprint.

        Method that determines the extent of the area covered by the
        footprints as a tight-fit rectangular bounding box

        Args:
            footprints (list) List of footprint data defined as a list of
                lists of coordinates in EPSG 4326, i.e., [[vert1],....
                [vertlast]]. Vertices are defined in [longitude,latitude]
                fashion.
        Returns:
            tuple: containing the minimum and maximum latitude and
                longitude values.
        """
        minlat = footprints[0][0][1]
        minlon = footprints[0][0][0]
        maxlat = footprints[0][0][1]
        maxlon = footprints[0][0][0]
        for fp in footprints:
            for vert in fp:
                if vert[1] > maxlat:
                    maxlat = vert[1]
                if vert[0] > maxlon:
                    maxlon = vert[0]
                if vert[1] < minlat:
                    minlat = vert[1]
                if vert[0] < minlon:
                    minlon = vert[0]
        return (minlat, minlon, maxlat, maxlon)

    def __get_nbi_data(self, bbox: tuple) -> dict:
        """
        Get the NBI data for a bounding box entry.

        Args:
            bbox (tuple) Tuple containing the minimum and maximum latitude and
                longitude values.
        Returns:
            dict: Dictionary containing extracted NBI data keyed using the
                NBI point coordinates
        """
        # Unpack the bounding box coordinates:
        (minlat, minlon, maxlat, maxlon) = bbox

        # Construct the query URL for the bounding box input
        baseurl = "https://nsi.sec.usace.army.mil/nsiapi/structures?bbox="
        bboxstr = (f'{minlon:.5f},{minlat:.5f},{minlon:.5f},{maxlat:.5f},'
                   f'{maxlon:.5f},{maxlat:.5f},{maxlon:.5f},{minlat:.5f},'
                   f'{minlon:.5f},{minlat:.5f}')
        url = baseurl + bboxstr

        # Define a retry stratey for common error codes to use in downloading
        # NBI data:
        s = requests.Session()
        retries = Retry(total=5,
                        backoff_factor=0.1,
                        status_forcelist=[500, 502, 503, 504])
        s.mount('https://', HTTPAdapter(max_retries=retries))

        # Download NBI data using the defined retry strategy, read downloaded
        # GeoJSON data into a list:
        print('\nGetting National Structure Inventory (NSI) building data'
              ' for the entered location input...')
        response = s.get(url)
        datalist = response.json()['features']

        # Write the data in datalist into a dictionary for better data access,
        # and filtering the duplicate entries:
        datadict = {}
        for data in datalist:
            pt = Point(data['geometry']['coordinates'])
            datadict[pt] = data['properties']
        return datadict

    def GetRawDataROI(self, roi, outfile: str) -> None:
        """
        Get NSI data within an ROI/area of footprints and write to GeoJSON.

        Method that reads NSI buildings data finds the points within a
        bounding polygon or matches a set of footprints, then writes the
        extracted data into a GeoJSON file.

        Args:
            roi: Either 1) a list of footprint data defined as a list of
                lists of coordinates in EPSG 4326, i.e., [[vert1],....
                [vertlast]]. Vertices are defined in [longitude,latitude]
                fashion, or 2) a shapely polygon or multipolygon for the
                boundary polygon of the region of interest.

                outfile: string containing the name of the output file
                Currently, only GeoJSON format is supported
        """
        if type(roi) is list:
            # Determine the coordinates of the bounding box including the
            # footprints:
            bbox = self.__get_bbox(roi)
            footprints = roi.copy()
            bpoly = None
        else:
            try:
                roitype = roi.geom_type
                bpoly = roi
                # If ROI is defined as a Shapely polygon or multipolygon,
                # determine the coordinates of the bounding box for the
                # bounding polygon:
                if roitype in ['MultiPolygon', 'Polygon', 'POLYGON']:
                    bbox = bpoly.bounds
                    bbox = (bbox[1], bbox[0], bbox[3], bbox[2])
                # Otherwise exit with a system error:
            except:
                sys.exit('Unimplemented region of interest data type: ',
                         roitype)

        # Get NBI data for the computed bounding box:
        datadict = self.__get_nbi_data(bbox)

        points = list(datadict.keys())
        if bpoly is None:
            # Find the NBI points that are enclosed in each footprint:
            pointsKeep, _, points_keep_footprints_index = \
                GeoTools.match_points_to_polygons(points, footprints)
        else:
            # Find the NBI points contained in bpoly:
            pointsKeep = set()
            for pt in points:
                if bpoly.contains(pt):
                    pointsKeep.add(pt)
            pointsKeep = list(pointsKeep)
            points_keep_footprints_index = []

        # Display the number of NSI points that are within roi:
        print(f'Found a total of {len(pointsKeep)} building points in'
              ' NSI that are within the entered region of interest')

        if len(pointsKeep) != 0:

            # Write the extracted NSI data in a GeoJSON file:
            attrkeys = list(datadict[pointsKeep[0]].keys())
            geojson = {'type': 'FeatureCollection',
                       "crs": {"type": "name", "properties":
                               {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
                       'features': []}
            for pt in pointsKeep:
                feature = {'type': 'Feature',
                           'properties': {},
                           'geometry': {'type': 'Point',
                                        'coordinates': []}}
                feature['geometry']['coordinates'] = [pt.x, pt.y]
                attributes = datadict[pt]
                for key in attrkeys:
                    attr = attributes[key]
                    feature['properties'][key] = 'NA' if attr is None else attr
                geojson['features'].append(feature)

            return geojson, points_keep_footprints_index

        else:
            return {}

    def GetNSIData(self, footprints: list,
                   lengthUnit: str = 'ft',
                   outfile: str = '',
                   get_extended_features: bool=False,
                   add_features: list = []):
        """
        Match NSI buildings points to a set of footprints.

        Method that reads NSI buildings points and matches the data to a set
        of footprints and writes the data in a BRAILS-compatible format

        Args:
            footprints: List of footprint data defined as a list of lists of
                coordinates in EPSG 4326, i.e., [[vert1],....[vertlast]].
                Vertices are defined in [longitude,latitude] fashion.

            lengthUnit: The units for the data to be returned in, 'm' or 'ft'

            outfile: string containing the name of the output file
                Currently, only GeoJSON format is supported

            get_extended_features: additionally get split level and gararge

            add_features: list of features to be included

        Output:
            dict: Attribute dictionary for the footprints containing the
                attributes: 1)latitude, 2)longitude, 3)building plan area,
                4)number of floors, 5)year of construction, 6)replacement cost,
                7)structure type, 8)occupancy class, 9)footprint polygon
                If "get_extended_features" option is selected, output additionally includes 
                10) split level and 11) gararge
        """

        def get_attr_from_datadict(datadict, footprints, nsi2brailsmap, get_extended_features=False, add_features=[]):
            # Parsers for building and occupancy types:
            def bldgtype_parser(bldgtype):
                bldgtype = bldgtype + '1'
                if bldgtype == 'M1':
                    bldgtype = 'RM1'
                return bldgtype

            def occ_parser(occtype):
                if '-' in occtype:
                    occtype = occtype.split('-')[0]
                return occtype


            def split_parser(occtype):
                # TODO-ADAM: by default no
                split = "No"
                if "RES1-SL" in occtype:
                    split = "Yes"
                return split

            def base_parser(occtype):
                # TODO-ADAM: By default what?
                base = "NA"
                if "RES1" in occtype:
                    if "NB" in occtype:
                        base = "Yes"
                    elif "WB" in occtype:
                        base = "No"

                return base

            # Extract the NSI points from the data dictionary input:
            points = list(datadict.keys())

            # Match NBI data to footprints:
            _, fp2ptmap, points_keep_footprint_index = \
                GeoTools.match_points_to_polygons(points, footprints)

            # Write the matched footprints to a list:
            footprintsMatched = fp2ptmap.keys()

            # Initialize the attributes dictionary:
            attributes = {'fp': [], 'fp_json': []}
            nsikeys = list(nsi2brailsmap.keys())
            brailskeys = list(nsi2brailsmap.values())

            basic_indices = [i for i, s in enumerate(nsikeys) if '_extended' not in s]
            extended_indices = [i for i, s in enumerate(nsikeys) if '_extended' in s]

            for key in [brailskeys[i] for i in basic_indices]:
                attributes[key] = []

            for key in add_features:
                attributes[key] = []

            if get_extended_features:
                for key in [brailskeys[i] for i in extended_indices]:
                    for subkey in key:
                        attributes[subkey] = []

            # Extract NSI attributes corresponding to each building footprint
            # and write them in attributes dictionary:
            for fpstr in footprintsMatched:
                fp = json.loads(fpstr)
                pt = fp2ptmap[fpstr]
                ptres = datadict[pt]
                attributes['fp'].append(fp)
                attributes['fp_json'].append(
                    ('{"type":"Feature","geometry":' +
                     '{"type":"Polygon","coordinates":[' +
                     f"""{fp}""" +
                     ']},"properties":{}}'
                     )
                )
                for key in nsikeys:
                    # Get Basic features
                    if key == 'bldgtype':
                        attributes[nsi2brailsmap[key]].append(
                            bldgtype_parser(ptres[key]))
                    elif key == 'occtype':
                        attributes[nsi2brailsmap[key]].append(
                            occ_parser(ptres[key]))
                    elif key == 'occtype_extended':
                        if get_extended_features: 
                            key_org = key.split('_')[0]
                            attributes[nsi2brailsmap[key][0]].append(split_parser(ptres[key_org]))
                            attributes[nsi2brailsmap[key][1]].append(base_parser(ptres[key_org]))
                        else:
                            pass
                    else:
                        attributes[nsi2brailsmap[key]].append(ptres[key])

                    # Sy - User provided features
                    if key in add_features:
                        attributes[key].append(ptres[key])



            # Display the number of footprints that can be matched to NSI
            # points:
            print(f'Found a total of {len(footprintsMatched)} building points'
                  ' in NSI that match the footprint data.')

            return attributes, points_keep_footprint_index

        # Determine the coordinates of the bounding box including the
        # footprints:
        bbox = self.__get_bbox(footprints)

        # Get the NBI data for computed bounding box:
        datadict = self.__get_nbi_data(bbox)

        # Create a footprint-merged building inventory from extracted NBI data:
        attributes, points_keep_footprint_index = get_attr_from_datadict(
            datadict, footprints, self.nsi2brailsmap, get_extended_features = get_extended_features, add_features=add_features)

        # Convert to footprint areas to sqm if needed:
        if lengthUnit == 'm':
            fparea = [area/(3.28084**2) for area in attributes['fparea']]
            attributes.update({'fparea': fparea})

        self.attributes = attributes.copy()

        """
        if outfile:
            if 'csv' in outfile.lower(): 
                # Write the extracted features into a Pandas dataframe:
                brailskeys = list(self.brails2r2dmap.keys())
                inventory = pd.DataFrame(pd.Series(attributes[brailskeys[0]],
                                                   name=self.brails2r2dmap[brailskeys[0]]))        
                for key in brailskeys[1:]:
                    inventory[self.brails2r2dmap[key]] = attributes[key]
    
                # Write the created inventory in R2D-compatible CSV format:
                n = inventory.columns[-1]
                inventory.drop(n, axis = 1, inplace = True)
                inventory[n] = attributes['fp_json']
                inventory.to_csv(outfile, index=True, index_label='id') 
            else:
                # Write the extracted features into a GeoJSON file:
                footprints = attributes['fp'].copy()
                del attributes['fp_json']
                del attributes['fp']
                fpHandler = FootprintHandler()
                fpHandler._FootprintHandler__write_fp2geojson(footprints,
                                                              attributes,
                                                              outfile,
                                                              convertKeys=True)
                outfile = outfile.split('.')[0] + '.geojson'        
            print(f'\nFinal inventory data available in {os.getcwd()}/{outfile}')
        """

        # Reformat the class variables to make them compatible with the
        # InventoryGenerator:
        self.footprints = self.attributes['fp'].copy()
        del self.attributes['fp']
        del self.attributes['fp_json']

        geojson = {'type': 'FeatureCollection',
                   "crs": {"type": "name", "properties": {
                       "name": "urn:ogc:def:crs:OGC:1.3:CRS84"}
                   },
                   'features': []}

        for ind, fp in enumerate(self.footprints):
            feature = {'id': str(ind),
                       'type': 'Feature',
                       'properties': {},
                       'geometry': {'type': 'Polygon',
                                    'coordinates': []}}
            feature['geometry']['coordinates'] = fp
            attrkeys = list(attributes.keys())
            for key in attrkeys:
                if key != 'fp' and key != 'fp_json':
                    attr = attributes[key][ind]
                    # if convertKeys:
                    #    keyout = attrmap[key]
                    # else:
                    keyout = key
                    feature['properties'][keyout] = 'NA' if attr is None else attr
            feature['properties']['type'] = 'Building'
            geojson['features'].append(feature)

        return geojson, points_keep_footprint_index

    def get_raw_data_given_boundary(self,
                                    region_boundary: RegionBoundary,
                                    length_unit: str = 'ft',
                                    outfile: str = '') -> AssetInventory:
        """
        Method that gets nsi data given a region boundary

        Input:  RegionBoundary
                   The region boundary
                length unit
                   The units data to be returned in, 'm' or 'ft'
                outfile
                   Optional string

        Output: AssetInventory
                  an AssetInventory
        """

        geojson, _ = self.GetRawDataROI(
            region_boundary.get_boundary()[0], outfile)
        inventory = AssetInventory()

        for feature in geojson['features']:
            # Access geometry and properties
            # make a list with just 1 enntry
            coordinates = [feature['geometry']['coordinates']]
            properties = feature['properties']
            properties['type'] = 'Building'
            id = properties['fd_id']
            asset = Asset(id, coordinates, properties)
            inventory.add_asset(id, asset)

        # return self.GetRawDataROI(region_boundary.get_boundary()[0], outfile)
        return inventory

    def get_raw_data_given_inventory(self,
                                     inventory: AssetInventory,
                                     length_unit: str = 'ft',
                                     outfile: str = '') -> AssetInventory:
        """
        Method that gets nsi data given a region boundary

        Input:  AssetInventory
                   The Asset inventory whose footprints we use to obtain NSI data
                length unit
                   The units data to be returned in, 'm' or 'ft'
                outfile
                   Optional string

        Output: AssetInventory
                   The inputed AssetInventory is updated and returned. returned for clarity of code
        """

        footprints, asset_keys = inventory.get_coordinates()
        geojson, footprints_index = self.GetRawDataROI(
            footprints,  length_unit)

        #print('keys: ', asset_keys, ' index   ', footprints_index)

        features = geojson['features']
        for index, feature in enumerate(geojson['features']):
            properties = feature['properties']
            properties['type'] = 'Building'
            asset_id = asset_keys[footprints_index[index]]
            inventory.add_asset_features(asset_id, properties)

        #
        # now update original inventory
        #

        # print(geojson)

        return inventory

    def get_filtered_data_given_inventory(self,
                                          inventory: AssetInventory,
                                          length_unit: str = 'ft',
                                          outfile: str = '',
                                          get_extended_features: bool=False,
                                          add_features: list = []):
        """
        Get NSI data corresponding to an AssetInventory.

        Input:  RegionBoundary
                   The region boundary
                length unit
                   The units data to be returned in, 'm' or 'ft'
                outfile
                   Optional string
                get_extended_features
                   additionally get split level and gararge
                add_features
                   Optional list of strings. Use this if you want to include additional features in the filtered inventory

        Output: dict
                Attribute dictionary for the footprints containing the attributes:
                1)latitude, 2)longitude, 3)building plan area, 4)number of 
                floors, 5)year of construction, 6)replacement cost, 7)structure
                type, 8)occupancy class, 9)footprint polygon
                If "get_extended_features" option is selected, output additionally includes 
                10) split level and 11) gararge
        """

        footprints, asset_keys = inventory.get_coordinates()
        geojson, footprints_index = self.GetNSIData(footprints, length_unit, get_extended_features = get_extended_features, add_features=add_features)

        # print('keys: ', asset_keys, ' index   ', footprints_index)
        # print(geojson)

        #
        # now update original inventory
        #

        features = geojson['features']
        for index, feature in enumerate(geojson['features']):
            # Access geometry and properties
            properties = feature['properties']
            properties['type'] = 'Building'
            asset_id = asset_keys[footprints_index[index]]
            inventory.add_asset_features(asset_id, properties)
            # print(asset_id, properties)

        return inventory
        # return self.GetNSIData(footprints(),  length_unit)
