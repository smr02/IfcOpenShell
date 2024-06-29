# IfcPatch - IFC patching utiliy
# Copyright (C) 2020, 2021 Dion Moult <dion@thinkmoult.com>
#
# This file is part of IfcPatch.
#
# IfcPatch is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# IfcPatch is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with IfcPatch.  If not, see <http://www.gnu.org/licenses/>.

import numpy as np
import ifcopenshell
import ifcopenshell.util.unit
import ifcopenshell.util.element
import ifcopenshell.util.geolocation
from ifcpatch.recipes.SetFalseOrigin import Patcher as SetFalseOrigin
from typing import Union
from logging import Logger


class Patcher:
    def __init__(self, src: str, file: ifcopenshell.file, logger: Logger, filepath: Union[str, ifcopenshell.file]):
        """Merge two IFC models into one

        Note that other than combining the two IfcProject elements into one, no
        further processing will be done. This means that you may end up with
        duplicate spatial hierarchies (i.e. 2 sites, 2 buildings, etc).

        Will automatically convert length units in the second model to the main
        model's unit before merging.

        :param filepath: The filepath of the second IFC model to merge into the
            first. The first model is already specified as the input to
            IfcPatch.

        Example:

        .. code:: python

            ifcpatch.execute({"input": "input.ifc", "file": model, "recipe": "MergeProject", "arguments": ["/path/to/model2.ifc"]})
        """
        self.src = src
        self.file = file
        self.logger = logger
        self.filepath = filepath

    def patch(self):
        if isinstance(self.filepath, ifcopenshell.file):
            other = self.filepath
        else:
            other = ifcopenshell.open(self.filepath)

        if (main_unit := self.get_unit_name(self.file)) != self.get_unit_name(other):
            other = ifcopenshell.util.unit.convert_file_length_units(other, main_unit)

        existing_enh = np.array(
            ifcopenshell.util.geolocation.auto_xyz2enh(self.file, 0, 0, 0, should_return_in_map_units=False)
        )
        other_enh = np.array(
            ifcopenshell.util.geolocation.auto_xyz2enh(other, 0, 0, 0, should_return_in_map_units=False)
        )

        if not np.allclose(existing_enh, other_enh):
            x, y, z = ifcopenshell.util.geolocation.auto_enh2xyz(other, *existing_enh, is_specified_in_map_units=False)
            e, n, h = existing_enh
            # For now don't handle rotation because my brain is going to explode
            SetFalseOrigin("", other, self.logger, name="", x=x, y=y, z=z, e=e, n=n, h=h).patch()

        self.existing_contexts: list[ifcopenshell.entity_instance] = self.file.by_type(
            "IfcGeometricRepresentationContext"
        )
        self.added_contexts: set[ifcopenshell.entity_instance] = set()

        original_project = self.file.by_type("IfcProject")[0]
        merged_project = self.file.add(other.by_type("IfcProject")[0])

        for element in other.by_type("IfcGeometricRepresentationContext"):
            new = self.file.add(element)
            self.added_contexts.add(new)

        for element in other:
            self.file.add(element)

        for inverse in self.file.get_inverse(merged_project):
            ifcopenshell.util.element.replace_attribute(inverse, merged_project, original_project)
        self.file.remove(merged_project)

        self.reuse_existing_contexts()

    def get_unit_name(self, ifc_file: ifcopenshell.file) -> str:
        length_unit = ifcopenshell.util.unit.get_project_unit(ifc_file, "LENGTHUNIT")
        return ifcopenshell.util.unit.get_full_unit_name(length_unit)

    def reuse_existing_contexts(self):
        to_delete = set()

        for added_context in self.added_contexts:
            equivalent_existing_context = self.get_equivalent_existing_context(added_context)
            if equivalent_existing_context:
                for inverse in self.file.get_inverse(added_context):
                    if self.file.schema != "IFC2X3":
                        if inverse.is_a("IfcCoordinateOperation"):
                            to_delete.add(inverse.id())
                            continue
                    ifcopenshell.util.element.replace_attribute(inverse, added_context, equivalent_existing_context)
                to_delete.add(added_context.id())

        for element_id in to_delete:
            try:
                ifcopenshell.util.element.remove_deep2(self.file, self.file.by_id(element_id))
            except:
                pass

    def get_equivalent_existing_context(
        self, added_context: ifcopenshell.entity_instance
    ) -> Union[ifcopenshell.entity_instance, None]:
        for context in self.existing_contexts:
            if context.is_a() != added_context.is_a():
                continue
            if context.is_a("IfcGeometricRepresentationSubContext"):
                if (
                    context.ContextType == added_context.ContextType
                    and context.ContextIdentifier == added_context.ContextIdentifier
                    and context.TargetView == added_context.TargetView
                ):
                    return context
            elif (
                context.ContextType == added_context.ContextType
                and context.ContextIdentifier == added_context.ContextIdentifier
            ):
                return context
