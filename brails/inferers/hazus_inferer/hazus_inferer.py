# -*- coding: utf-8 -*-
#
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
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
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
# BRAILS. If not, see <http://www.opensource.org/licenses/>.
#
# Contributors:
# Sang-ri Yi
#
# Last updated:
# 11-12-2024

import time

import os
import sys
import copy
import json

import numpy as np
from copy import deepcopy
import logging

from brails.types.asset_inventory import AssetInventory
from brails.inferers.inferenceEngine import InferenceEngine
from brails.inferers.hazus_inferer.hazus_rulesets import get_hazus_occ_type_mapping, get_hazus_state_region_mapping, get_hazus_height_classes, get_hazus_year_classes

from itertools import product

import reverse_geocode # sy - note this may not be the most accurate package but it's fast

# Configure logging:
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class HazusInferer(InferenceEngine):
    """
    Imputes dataset based on k-nearest neighbors in the feature-agmented space. Sequentially generate inventory

    Attributes:
        n_pw (int):
                The number of possible worlds (i.e. samples or realizations)
        seed (int):
                For reproducibility

    Methods:


    """

    def __init__(self):
        pass

    def infer(
        self,
        input_inventory: AssetInventory,
        n_possible_worlds=1,
        include_features=[],
        seed=1,
        overwirte_existing = True,
        year_key = 'erabuilt', # TODO: to be updated
        occ_key = 'occupancy',
        strtype_key = 'constype2',
        nstory_key = 'numstories',
        strtype_options = []
    ) -> AssetInventory:

        # ['no_urm', 'allow_mh_only_for_res2', 'res3_AB_to_res1']

        #
        # Determine n_pw
        #
        elapseStart = time.time()
        existing_worlds = input_inventory.get_n_pw()

        if existing_worlds is None:
            msg  = f"ERROR: All assets should have same number of possible worlds to run the inference."
            raise Exception(msg)

        if existing_worlds == 1:
            n_pw = n_possible_worlds # if zero, it will give the most likely value
        else: 
            if (n_possible_worlds ==0):
                pass
            elif (n_possible_worlds ==1) or (n_possible_worlds ==1) or (n_possible_worlds==existing_worlds):
                logger.warning(f"Existing {existing_worlds} worlds detacted. {existing_worlds} samples will generated per feature")
                n_pw = 1 # n_pw per exisitng pw
            else:
                msg = f"ERROR: the number of possible worlds {n_possible_worlds} should be the same as the existing possible worlds. Choose {existing_worlds} or 0 (to get only the most likely value) to run the inference."
                raise Exception(msg)

        #
        # set some variables : TODO: move to the constructer
        #
        self.n_pw = n_possible_worlds
        self.seed = seed
        self.overwirte_existing = overwirte_existing
        self.skip_buildings_if_needed = False
        output_inventory = copy.deepcopy(input_inventory)

        #
        # set seed
        #

        np.random.seed(self.seed)

        #
        # run the first world
        #

        # occupancy type
        input_inventory_subset = input_inventory.get_world_realization(0)

        occ_runable = self.check_keys(needed_keys = [year_key, occ_key, nstory_key], target_key=occ_key, inventory= input_inventory_subset)
        if occ_runable:
            occ_prop, inventory_realization_df = self.get_str_from_occ(input_inventory_subset, year_key, occ_key, strtype_key, nstory_key, n_pw)

        #
        # loop over the second ~ n_pw worlds if needed 
        #
        # TODO: Note that there may be inefficiency. Even if you have a probablistic inventory, if year, occ, nstory is non-probablistic, you really don't need to run it 10 times.
        #

        for nw in range(1,existing_worlds):

            # get inventory realization
            inventory_realization = input_inventory.get_world_realization(nw)

            # occupancy type
            if occ_runable:
                acc_prop_tmp, inventory_realization_df = self.get_str_from_occ(inventory_realization, year_key, occ_key, strtype_key, nstory_key, n_pw)
                occ_prop = self.merge_two_json(acc_prop_tmp, occ_prop, shrink=(nw == existing_worlds-1))

        #
        # update features
        #

        updated = False
            
        # occupancy type
        for index, feature in occ_prop.items():
            output_inventory.add_asset_features(index, feature, overwrite=True)
            updated = True

        #
        # Return the valuee
        #

        if not updated:
            logger.warning(f"Nothing happened to the inventory.")   


        elapseEnd = (time.time() - elapseStart) / 60
        print("Done inference. It took {:.2f} mins".format(elapseEnd))

        return output_inventory

    def merge_two_json(self,A,B,shrink=False):

        if A=={}:
            return B

        if B=={}:
            return A

        C={}

        for key in A:
            # Initialize the merged values for the current key
            merged_values = {}

            # Loop through each feature in A and B
            for feature in A[key]:
                # Get the value from A, ensure it's a list
                value_a = A[key][feature]
                if not isinstance(value_a, list):
                    value_a = [value_a]

                # Get the value from B, ensure it's a list
                value_b = B[key].get(feature, [])
                if not isinstance(value_b, list):
                    value_b = [value_b]

                # Merge the two lists

                if shrink==False:
                    merged_values[feature] = value_a + value_b
                else:
                    val = value_a + value_b
                    if isinstance(val, list) and (len(set(val)) == 1):
                        merged_values[feature] = val[0]
                    else:
                        merged_values[feature] = val

            # Assign the merged values for the current key to C
            C[key] = merged_values

        return C


    def check_keys(self,needed_keys,target_key,inventory):

        #
        # Convert inventory to df
        #

        bldg_properties_df, bldg_geometries_df, nbldg = inventory.get_dataframe()
        bldg_properties_df = bldg_properties_df.replace("NA", np.nan, inplace=False) # missing
        bldg_properties_df = bldg_properties_df.replace("", np.nan, inplace=False) # missing
        provided_keys = bldg_properties_df.columns

        #
        # check if the dataframe column has the keys
        #

        # Check if needed_keys is a subset of provided_keys
        if set(needed_keys).issubset(set(provided_keys)):
            pass
        else:
            # Find elements in needed_keys that are not in provided_keys
            not_in_provided_keys = [item for item in needed_keys if item not in provided_keys]
            logger.warning(f"The keys needed to estimate {target_key} is not there: ", not_in_provided_keys)
            logger.warning(f"Skipping hazus inference of {target_key}.")
            return False

        #
        # Check if input is missing
        #

        ready = True
        for key in needed_keys:
            my_col = bldg_properties_df[key]

            if bldg_properties_df[key].isnull().any():  # if null found
                missing_values_index = my_col[my_col.isnull()].index.tolist()

                if self.skip_buildings_if_needed:
                    pass
                    # TODO: remove dataframe rows that have missing data and ready is true, return dataframe

                else:
                    if len(missing_values_index)>10:
                        print(f"The feature {key} is missing in many buildings including: ", missing_values_index[0:10])
                    else:
                        print(f"The feature {key} is missing in following buildings: ", missing_values_index)
                    ready = False
            

        if ready==False:
            print(f"Skipping hazus inference of {target_key}. If you still want to perform the inference, run imputer first.")
            return False

        #
        # warning message for overwritting
        #

        if target_key in provided_keys:

            avail_percentage = 100-sum(bldg_properties_df[key].isnull())/len(bldg_properties_df[key])*100

            if self.overwirte_existing and (avail_percentage<100):
                logger.warning(f"the feature {target_key} available for {avail_percentage} % of inventories. They will be overwritten, unless otherwise specified.")
            elif (not self.overwirte_existing) and (avail_percentage<100):
                logger.warning(f"the feature {target_key} available for {avail_percentage} % of inventories. The feature will be inferred only for the missing inventories, unless otherwise specified.")
            elif (not self.overwirte_existing) and (avail_percentage==100):
                logger.warning(f"the feature {target_key} is already complete. If you still want to perform the inference, please turn on the option to overwrite")

        #
        # Count the existing worlds
        #

        # if set(needed_keys) & set(inventory.get_multi_keys()):
        #     # "There is at least one overlapping element."
        #     existing_worlds = inventory.get_n_pw 
        # else:
        #     existing_worlds = 1 
            

        return True

    def get_str_from_occ(self, input_inventory, year_key, occ_key, strtype_key, nstory_key, n_pw):

        #
        # convert inventory to df
        #

        bldg_properties_df, bldg_geometries_df, nbldg = input_inventory.get_dataframe()

        #
        # get hazus rulesets
        #

        states_to_region = get_hazus_state_region_mapping()
        height_classes = get_hazus_height_classes()
        year_classes = get_hazus_year_classes()
        type_lists, type_weights = get_hazus_occ_type_mapping()

        #
        # Add "state" and "region" columns
        #

        geo_locs = reverse_geocode.search([(row[0],row[1]) for i,row in enumerate(bldg_geometries_df.values)] )
        states_list = [bldg["state"] for bldg in geo_locs]
        region_list = [states_to_region[state]["RegionGroup"] for state in states_list]
        bldg_properties_df["state"]=states_list
        bldg_properties_df["region"]=region_list

        #
        # Add "height_class" column 
        #

        bldg_properties_df["height_class"] = ""
        for height_class, story_list in height_classes.items():
            in_class_index = bldg_properties_df[nstory_key].isin(story_list)
            if sum(in_class_index)>0:
                bldg_properties_df.loc[in_class_index, 'height_class'] = height_class

        #
        # Add "year_class" column
        #

        bldg_properties_df["year_class"] = ""
        for year_class, year_list in year_classes.items():
            in_class_index = bldg_properties_df[year_key].isin(year_list)
            if sum(in_class_index)>0:
                bldg_properties_df.loc[in_class_index, 'year_class'] = year_class

        #
        # Clean occupancy class and add as a new column
        #

        bldg_properties_df[f'{occ_key}_clean'] = bldg_properties_df[occ_key].apply(self.modulate_occ)
        occ_key = f'{occ_key}_clean'


        #
        # Get all cases of interest
        #

        region_list = list(set(bldg_properties_df['region']))
        occ_list = list(set(bldg_properties_df[occ_key]))
        height_list = height_classes.keys()
        state_list = list(set(bldg_properties_df['state']))
        classes_in_inventory = list(product(region_list,occ_list,height_list))


        #
        # Run inference
        #

        new_prop = {}

        for region, occ, height in classes_in_inventory: # for all regions that appear at least once in inventory

            
            subset_inventory = bldg_properties_df[(bldg_properties_df['region']==region) & (bldg_properties_df[occ_key]==occ) & (bldg_properties_df['height_class']==height)]
            nbldg_subset = len(subset_inventory)

            
            if nbldg_subset==0:
                # no instance found
                continue
                
            if region=="West Coast":

                # year built is considered only in west coast
                
                for year_class in year_classes:
                    
                    subset_inventory2 = subset_inventory[(subset_inventory['year_class']==year_class)] # inventory with specific region, occ, height, year
                    nbldg_subset2 = len(subset_inventory2)
                    
                    if nbldg_subset2==0:
                        # no instance found
                        continue

                    if occ =="RES1":
                        #print(f"{occ} {year_class} {nbldg_subset2}")

                        for state in state_list:
                            subset_inventory3 = subset_inventory2[(subset_inventory2['state']==state)] # inventory with specific region, occ, height, year, state
                            nbldg_subset3 = len(subset_inventory2)

                            weights = np.array(type_weights[region][occ][year_class][state])/100.
                            structure_types = np.array(type_lists[region]["RES1"])
                            weights, structure_types = self.modulate_weights(weights, structure_types, region, occ, year_class, height)

                            if len(weights)==0:
                                logger.warning(f"HAZUS does not provide structural type information for {region}-{occ}-{height}-{year_class}-{state}. {strtype_key} will be missing in id={subset_inventory2.index.tolist()}")

                            new_prop = self.add_features_to_asset(new_prop, strtype_key, structure_types, weights, n_pw, nbldg_subset3, global_asset_indices=subset_inventory3.index)

                    else:
                        weights = np.array(type_weights[region][occ][height][year_class])/100.
                        structure_types = np.array(type_lists[region][height])
                        weights, structure_types = self.modulate_weights(weights, structure_types, region, occ, year_class, height)
                    
                        if len(weights)==0:
                            logger.warning(f" HAZUS does not provide structural type information for {region}-{occ}-{height}-{year_class}. {strtype_key} will be missing in id={subset_inventory2.index.tolist()}")
                        
                        new_prop = self.add_features_to_asset(new_prop, strtype_key, structure_types, weights, n_pw, nbldg_subset2, global_asset_indices=subset_inventory2.index)

            elif (region=="Mid-West") or (region=="East Coast"):
                    
                if occ =="RES1":
                    #print(f"{occ} {year_class} {nbldg_subset2}")

                    for state in state_list:
                        subset_inventory3 = subset_inventory[(subset_inventory['state']==state)] # inventory with specific region, occ, height, year, state
                        nbldg_subset3 = len(subset_inventory)

                        weights = np.array(type_weights[region][occ][state])/100.
                        structure_types = np.array(type_lists[region]["RES1"])
                        weights, structure_types = self.modulate_weights(weights, structure_types, region, occ, year_class, height)

                        if len(weights)==0:
                            logger.warning(f"HAZUS does not provide structural type information for {region}-{occ}-{height}-{year_class}-{state}. {strtype_key} will be missing in id={subset_inventory2.index.tolist()}")

                        new_prop = self.add_features_to_asset(new_prop, strtype_key, structure_types, weights, n_pw, nbldg_subset3, global_asset_indices=subset_inventory3.index)
                else:
                    # define prob and categories
                    weights = np.array(type_weights[region][occ][height])/100.
                    structure_types = np.array(type_lists[region][height])
                    weights, structure_types = self.modulate_weights(weights, structure_types, region, occ, year_class, height)

                    if len(weights)==0:
                        logger.warning(f"HAZUS does not provide structural type information for {region}-{occ}-{height}. {strtype_key} will be missing in id={subset_inventory.index.tolist()}")
                        
                    # add assets
                    new_prop = self.add_features_to_asset(new_prop, strtype_key, structure_types, weights, n_pw, nbldg_subset, global_asset_indices=subset_inventory.index)

        return new_prop, bldg_properties_df

    # Clean up occ classes
    def modulate_occ(self, s):
        # if s="RES1A", return s="RES1"
        if not s[-1].isdigit():  # Check if the last character is not a digit
            return s[:-1]  # Remove the last character
        return s  # Return the string unchanged if it ends with a number

    def flatten_array(self, arr):
        if len(arr) == 1:
            return arr[0]  # Flatten to string
        else:
            return arr  # Leave the array as is or process differently

    def add_features_to_asset(self, new_prop, strtype_key, structure_types, weights, n_pw, n_bldg_subset, global_asset_indices):
        if len(weights)==0:
           for count, index in enumerate(global_asset_indices):     
                #new_prop[index] = {strtype_key: "NOT IN HAZUS" } 
               new_prop[index] = {strtype_key: np.nan} 
           return new_prop
        
        if n_pw==0:
            # most likely struct
            struct_pick = [structure_types[np.argmax(weights)]]*n_bldg_subset            
        else:                
            # sample nbldg x n_pw          
            struct_pick = np.random.choice(structure_types, size=[n_bldg_subset, n_pw], replace=True, p=weights ).tolist()
      
        for count, index in enumerate(global_asset_indices):   

            # shrinks to a scalar value if same.
            val_vec = struct_pick[count]
            if not isinstance(val_vec, list):
                val = val_vec[0]
            elif len(set(val_vec)) == 1:
                val = val_vec[0]
            else:
                val = val_vec

            new_prop[index] = {strtype_key: val} # if #elem in list is 1, convert it to integer

            #new_prop[index] = {strtype_key: self.flatten_array(struct_pick[count])} # if #elem in list is 1, convert it to integer

        return new_prop


    def modulate_weights(self, weights, structure_types, region, occ, year_class, height):

        pass

        return weights, structure_types