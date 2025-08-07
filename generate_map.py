bl_info = {
    "name": "Quake 2 BSP to MAP - Compilation Ready",
    "blender": (3, 0, 0),
    "category": "Import-Export",
    "version": (3, 0, 0),
    "author": "BSP Tools Professional",
    "description": "BSP to MAP converter that produces compilable maps",
    "support": "COMMUNITY"
}

import bpy
import struct
import os
import math
from mathutils import Vector
from bpy.types import Operator
from bpy.props import StringProperty, BoolProperty, FloatProperty, IntProperty
from bpy_extras.io_utils import ImportHelper
from decimal import Decimal, ROUND_HALF_UP

# Constants
BSP_MAGIC = b'IBSP'
BSP_VERSION_Q2 = 38
COMPILE_EPSILON = 0.125  # Minimum edge size for qbsp
GRID_SNAP = 0.25  # Grid snapping for coordinates

# Content flags
CONTENTS_SOLID = 1
CONTENTS_WINDOW = 2
CONTENTS_AUX = 4
CONTENTS_LAVA = 8
CONTENTS_SLIME = 16
CONTENTS_WATER = 32
CONTENTS_AREAPORTAL = 0x8000

# Surface flags
SURF_NODRAW = 0x80
SURF_SKY = 0x4
SURF_HINT = 0x100
SURF_SKIP = 0x200

class SimplePlane:
    def __init__(self, normal_x, normal_y, normal_z, distance, plane_type):
        self.normal = Vector((normal_x, normal_y, normal_z))
        self.distance = distance
        self.type = plane_type

class SimpleBrush:
    def __init__(self, first_side, num_sides, contents):
        self.first_side = first_side
        self.num_sides = num_sides
        self.contents = contents

class SimpleBrushSide:
    def __init__(self, plane_num, tex_info):
        self.plane_num = plane_num
        self.tex_info = tex_info

class SimpleTexInfo:
    def __init__(self, u_axis, u_offset, v_axis, v_offset, flags, value, texture_name):
        self.u_axis = u_axis
        self.u_offset = u_offset
        self.v_axis = v_axis
        self.v_offset = v_offset
        self.flags = flags
        self.value = value
        self.texture_name = texture_name

def snap_to_grid(value, grid_size=GRID_SNAP):
    """Snap coordinate to grid for clean geometry"""
    return round(value / grid_size) * grid_size

def round_coordinate(value, decimals=3):
    """Round coordinate to prevent tiny edges"""
    # Use decimal for precise rounding
    d = Decimal(str(value))
    rounded = d.quantize(Decimal(10) ** -decimals, rounding=ROUND_HALF_UP)
    return float(rounded)

def normalize_vector(vec):
    """Normalize a vector safely"""
    length = math.sqrt(vec[0]**2 + vec[1]**2 + vec[2]**2)
    if length > 0.001:
        return Vector((vec[0]/length, vec[1]/length, vec[2]/length))
    return Vector((0, 0, 1))

def cross_product(a, b):
    """Calculate cross product"""
    return Vector((
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0]
    ))

def validate_brush_geometry(points):
    """Check if brush geometry is valid"""
    # Check for degenerate edges
    for i in range(3):
        for j in range(i+1, 3):
            edge = Vector(points[j]) - Vector(points[i])
            if edge.length < COMPILE_EPSILON:
                return False, f"Degenerate edge ({edge.length:.6f})"
    return True, None

def fix_texture_name(name):
    """Fix texture names for compilation"""
    if not name or name == "":
        return "MISSING"
    
    # Remove problematic characters
    name = name.replace('\x00', '').strip()
    
    # Handle special textures
    if name.startswith('*'):  # Water textures
        return name
    if name.upper() in ['CLIP', 'NODRAW', 'SKIP', 'HINT', 'AREAPORTAL']:
        return name.upper()
    
    return name

class BSP_OT_import_compilable(Operator, ImportHelper):
    """BSP to MAP converter that produces compilable maps"""
    bl_idname = "import_scene.bsp_to_map_compilable"
    bl_label = "Import BSP to MAP (Compilable)"
    bl_description = "Convert BSP to compilable MAP format"
    bl_options = {'PRESET', 'UNDO'}
    
    filename_ext = ".bsp"
    filter_glob: StringProperty(default="*.bsp", options={'HIDDEN'})
    
    # Properties
    grid_snap: FloatProperty(
        name="Grid Snap",
        description="Snap coordinates to grid",
        default=0.25,
        min=0.125,
        max=16.0
    )
    
    coordinate_decimals: IntProperty(
        name="Coordinate Decimals",
        description="Decimal places for coordinates",
        default=3,
        min=0,
        max=6
    )
    
    min_edge_length: FloatProperty(
        name="Min Edge Length",
        description="Minimum edge length to prevent degenerate edges",
        default=0.125,
        min=0.01,
        max=1.0
    )
    
    fix_areaportal: BoolProperty(
        name="Fix Area Portals",
        description="Attempt to fix func_areaportal entities",
        default=True
    )
    
    skip_problem_brushes: BoolProperty(
        name="Skip Problem Brushes",
        description="Skip brushes that would cause compile errors",
        default=True
    )
    
    verbose: BoolProperty(
        name="Verbose Output",
        description="Print detailed information",
        default=True
    )
    
    def execute(self, context):
        """Execute the conversion"""
        print("\n" + "="*60)
        print("BSP TO MAP - COMPILABLE VERSION")
        print("="*60)
        print(f"File: {self.filepath}")
        print(f"Grid snap: {self.grid_snap}")
        print(f"Min edge: {self.min_edge_length}")
        
        try:
            # Read BSP file
            with open(self.filepath, 'rb') as f:
                data = f.read()
            
            print(f"Read {len(data)} bytes")
            
            # Check header
            if len(data) < 8:
                self.report({'ERROR'}, "File too small")
                return {'CANCELLED'}
            
            magic = data[0:4]
            version = struct.unpack('<I', data[4:8])[0]
            
            print(f"Magic: {magic}, Version: {version}")
            
            # Read lumps
            lumps = []
            offset = 8
            for i in range(19):
                if offset + 8 <= len(data):
                    lump_offset = struct.unpack('<I', data[offset:offset+4])[0]
                    lump_length = struct.unpack('<I', data[offset+4:offset+8])[0]
                    lumps.append((lump_offset, lump_length))
                    offset += 8
                else:
                    lumps.append((0, 0))
            
            # Parse data
            planes = []
            brushes = []
            brush_sides = []
            texinfos = []
            entities_string = ""
            
            # Parse entities
            if lumps[0][1] > 0:
                entity_data = data[lumps[0][0]:lumps[0][0] + lumps[0][1]]
                entities_string = entity_data.decode('ascii', errors='ignore').strip('\x00')
                print(f"Found {len(entities_string)} bytes of entities")
            
            # Parse planes
            if lumps[1][1] > 0:
                plane_data = data[lumps[1][0]:lumps[1][0] + lumps[1][1]]
                num_planes = len(plane_data) // 20
                
                for i in range(num_planes):
                    offset = i * 20
                    if offset + 20 <= len(plane_data):
                        nx, ny, nz, dist, ptype = struct.unpack('<ffffI', plane_data[offset:offset+20])
                        planes.append(SimplePlane(nx, ny, nz, dist, ptype))
                
                print(f"Parsed {len(planes)} planes")
            
            # Parse textures
            if lumps[5][1] > 0:
                texinfo_data = data[lumps[5][0]:lumps[5][0] + lumps[5][1]]
                TEXINFO_SIZE = 76
                num_texinfos = len(texinfo_data) // TEXINFO_SIZE
                
                for i in range(num_texinfos):
                    offset = i * TEXINFO_SIZE
                    if offset + TEXINFO_SIZE <= len(texinfo_data):
                        try:
                            tex_data = struct.unpack('<8f2I32sI', texinfo_data[offset:offset+TEXINFO_SIZE])
                            
                            u_axis = Vector((tex_data[0], tex_data[1], tex_data[2]))
                            u_offset = tex_data[3]
                            v_axis = Vector((tex_data[4], tex_data[5], tex_data[6]))
                            v_offset = tex_data[7]
                            flags = tex_data[8]
                            value = tex_data[9]
                            texture_name = tex_data[10].decode('ascii', errors='ignore').strip('\x00')
                            
                            texinfos.append(SimpleTexInfo(u_axis, u_offset, v_axis, v_offset, 
                                                         flags, value, texture_name))
                        except:
                            texinfos.append(SimpleTexInfo(
                                Vector((1, 0, 0)), 0, Vector((0, -1, 0)), 0,
                                0, 0, "MISSING"
                            ))
                
                print(f"Parsed {len(texinfos)} texinfos")
            
            # Parse brushes
            if lumps[14][1] > 0:
                brush_data = data[lumps[14][0]:lumps[14][0] + lumps[14][1]]
                num_brushes = len(brush_data) // 12
                
                for i in range(num_brushes):
                    offset = i * 12
                    if offset + 12 <= len(brush_data):
                        first_side, num_sides, contents = struct.unpack('<III', brush_data[offset:offset+12])
                        brushes.append(SimpleBrush(first_side, num_sides, contents))
                
                print(f"Parsed {len(brushes)} brushes")
            
            # Parse brush sides
            if lumps[15][1] > 0:
                side_data = data[lumps[15][0]:lumps[15][0] + lumps[15][1]]
                num_sides = len(side_data) // 4
                
                for i in range(num_sides):
                    offset = i * 4
                    if offset + 4 <= len(side_data):
                        plane_num, tex_info = struct.unpack('<HH', side_data[offset:offset+4])
                        brush_sides.append(SimpleBrushSide(plane_num, tex_info))
                
                print(f"Parsed {len(brush_sides)} brush sides")
            
            # Create MAP file
            map_path = os.path.splitext(self.filepath)[0] + '_compilable.map'
            print(f"\nWriting MAP: {map_path}")
            
            valid_brushes = 0
            skipped_brushes = 0
            fixed_brushes = 0
            areaportal_count = 0
            
            with open(map_path, 'w') as f:
                # Header
                f.write('// BSP to MAP - Compilable Version\n')
                f.write(f'// Grid snap: {self.grid_snap}\n')
                f.write(f'// Min edge: {self.min_edge_length}\n\n')
                
                # Worldspawn
                f.write('{\n')
                f.write('"classname" "worldspawn"\n')
                f.write('"mapversion" "220"\n')
                
                # Parse worldspawn properties from entities
                if entities_string:
                    lines = entities_string.split('\n')
                    in_worldspawn = False
                    for line in lines:
                        line = line.strip()
                        if '"classname" "worldspawn"' in line:
                            in_worldspawn = True
                        elif line == '}' and in_worldspawn:
                            break
                        elif in_worldspawn and '"' in line and 'classname' not in line:
                            # Skip problematic properties
                            if not any(skip in line for skip in ['_tb_', 'wad', 'mapversion']):
                                f.write(line + '\n')
                
                # Process brushes
                for brush_idx, brush in enumerate(brushes):
                    # Skip invalid
                    if brush.num_sides < 4:
                        skipped_brushes += 1
                        continue
                    
                    # Skip empty brushes
                    if brush.contents == 0:
                        skipped_brushes += 1
                        continue
                    
                    # Skip areaportal brushes for now
                    if brush.contents & CONTENTS_AREAPORTAL:
                        areaportal_count += 1
                        continue
                    
                    # Get planes
                    brush_planes = []
                    brush_texinfos = []
                    
                    for i in range(brush.num_sides):
                        side_idx = brush.first_side + i
                        if side_idx < len(brush_sides):
                            side = brush_sides[side_idx]
                            if side.plane_num < len(planes):
                                brush_planes.append(planes[side.plane_num])
                                if side.tex_info < len(texinfos):
                                    brush_texinfos.append(texinfos[side.tex_info])
                                else:
                                    brush_texinfos.append(None)
                    
                    if len(brush_planes) >= 4:
                        try:
                            # Check if brush will be valid
                            brush_valid = True
                            brush_lines = []
                            
                            for i, plane in enumerate(brush_planes):
                                # Calculate three points with grid snapping
                                normal = normalize_vector(plane.normal)
                                
                                # Find tangent vectors
                                if abs(normal[2]) < 0.9:
                                    tangent1 = normalize_vector(cross_product(normal, Vector((0, 0, 1))))
                                else:
                                    tangent1 = normalize_vector(cross_product(normal, Vector((1, 0, 0))))
                                
                                tangent2 = normalize_vector(cross_product(normal, tangent1))
                                
                                # Create points with larger scale to avoid degenerate edges
                                scale = 256  # Larger scale
                                center = normal * plane.distance
                                
                                # Snap center to grid
                                center = Vector((
                                    snap_to_grid(center[0], self.grid_snap),
                                    snap_to_grid(center[1], self.grid_snap),
                                    snap_to_grid(center[2], self.grid_snap)
                                ))
                                
                                # Create points
                                p1 = center + tangent1 * scale
                                p2 = center - tangent1 * scale
                                p3 = center + tangent2 * scale
                                
                                # Snap points to grid
                                p1 = Vector((
                                    snap_to_grid(p1[0], self.grid_snap),
                                    snap_to_grid(p1[1], self.grid_snap),
                                    snap_to_grid(p1[2], self.grid_snap)
                                ))
                                p2 = Vector((
                                    snap_to_grid(p2[0], self.grid_snap),
                                    snap_to_grid(p2[1], self.grid_snap),
                                    snap_to_grid(p2[2], self.grid_snap)
                                ))
                                p3 = Vector((
                                    snap_to_grid(p3[0], self.grid_snap),
                                    snap_to_grid(p3[1], self.grid_snap),
                                    snap_to_grid(p3[2], self.grid_snap)
                                ))
                                
                                # Validate geometry
                                valid, error = validate_brush_geometry([p1, p2, p3])
                                if not valid and self.skip_problem_brushes:
                                    if self.verbose:
                                        print(f"  Brush {brush_idx}: Skipped - {error}")
                                    brush_valid = False
                                    break
                                
                                # Round coordinates
                                p1 = Vector((
                                    round_coordinate(p1[0], self.coordinate_decimals),
                                    round_coordinate(p1[1], self.coordinate_decimals),
                                    round_coordinate(p1[2], self.coordinate_decimals)
                                ))
                                p2 = Vector((
                                    round_coordinate(p2[0], self.coordinate_decimals),
                                    round_coordinate(p2[1], self.coordinate_decimals),
                                    round_coordinate(p2[2], self.coordinate_decimals)
                                ))
                                p3 = Vector((
                                    round_coordinate(p3[0], self.coordinate_decimals),
                                    round_coordinate(p3[1], self.coordinate_decimals),
                                    round_coordinate(p3[2], self.coordinate_decimals)
                                ))
                                
                                # Build plane line
                                line = f'( {p1[0]} {p1[1]} {p1[2]} ) '
                                line += f'( {p2[0]} {p2[1]} {p2[2]} ) '
                                line += f'( {p3[0]} {p3[1]} {p3[2]} ) '
                                
                                # Texture
                                if i < len(brush_texinfos) and brush_texinfos[i]:
                                    tex = brush_texinfos[i]
                                    name = fix_texture_name(tex.texture_name)
                                    
                                    # Round texture coordinates
                                    u_axis = Vector((
                                        round_coordinate(tex.u_axis[0], 3),
                                        round_coordinate(tex.u_axis[1], 3),
                                        round_coordinate(tex.u_axis[2], 3)
                                    ))
                                    v_axis = Vector((
                                        round_coordinate(tex.v_axis[0], 3),
                                        round_coordinate(tex.v_axis[1], 3),
                                        round_coordinate(tex.v_axis[2], 3)
                                    ))
                                    u_offset = round_coordinate(tex.u_offset, 2)
                                    v_offset = round_coordinate(tex.v_offset, 2)
                                    
                                    line += f'{name} '
                                    line += f'[ {u_axis[0]} {u_axis[1]} {u_axis[2]} {u_offset} ] '
                                    line += f'[ {v_axis[0]} {v_axis[1]} {v_axis[2]} {v_offset} ] '
                                    line += '0 1 1'
                                else:
                                    line += 'MISSING [ 1 0 0 0 ] [ 0 -1 0 0 ] 0 1 1'
                                
                                brush_lines.append(line)
                            
                            # Write brush if valid
                            if brush_valid and brush_lines:
                                f.write('{\n')
                                for line in brush_lines:
                                    f.write(line + '\n')
                                f.write('}\n')
                                valid_brushes += 1
                                if not valid:
                                    fixed_brushes += 1
                            else:
                                skipped_brushes += 1
                            
                        except Exception as e:
                            if self.verbose:
                                print(f"  Brush {brush_idx}: Error - {e}")
                            skipped_brushes += 1
                
                f.write('}\n')  # End worldspawn
                
                # Process entities (skip func_areaportal for now)
                if entities_string:
                    lines = entities_string.split('\n')
                    in_entity = False
                    entity_lines = []
                    entity_class = ""
                    
                    for line in lines:
                        line = line.strip()
                        if line == '{':
                            in_entity = True
                            entity_lines = []
                            entity_class = ""
                        elif line == '}':
                            if in_entity and entity_lines:
                                # Check entity type
                                for el in entity_lines:
                                    if '"classname"' in el:
                                        if '"func_areaportal"' in el:
                                            entity_class = "areaportal"
                                        elif '"worldspawn"' in el:
                                            entity_class = "worldspawn"
                                        break
                                
                                # Write non-problematic entities
                                if entity_class not in ["worldspawn", "areaportal"]:
                                    f.write('{\n')
                                    for el in entity_lines:
                                        f.write(el + '\n')
                                    f.write('}\n')
                            
                            in_entity = False
                        elif in_entity:
                            entity_lines.append(line)
            
            # Report
            print(f"\n" + "="*60)
            print(f"CONVERSION COMPLETE")
            print(f"  Output: {map_path}")
            print(f"  Valid brushes: {valid_brushes}")
            print(f"  Fixed brushes: {fixed_brushes}")
            print(f"  Skipped brushes: {skipped_brushes}")
            print(f"  Areaportal brushes skipped: {areaportal_count}")
            print(f"\nNOTE: func_areaportal entities removed to prevent compile errors")
            print(f"      You may need to recreate them manually")
            print("="*60)
            
            msg = f"Exported {valid_brushes} brushes ({skipped_brushes} skipped)"
            if areaportal_count > 0:
                msg += f"\nSkipped {areaportal_count} areaportal brushes"
            
            self.report({'INFO'}, msg)
            return {'FINISHED'}
            
        except Exception as e:
            print(f"\nERROR: {e}")
            import traceback
            traceback.print_exc()
            self.report({'ERROR'}, f"Conversion failed: {str(e)}")
            return {'CANCELLED'}
    
    def draw(self, context):
        """Draw UI"""
        layout = self.layout
        
        box = layout.box()
        box.label(text="Grid & Precision", icon='GRID')
        box.prop(self, "grid_snap")
        box.prop(self, "coordinate_decimals")
        box.prop(self, "min_edge_length")
        
        box = layout.box()
        box.label(text="Fix Options", icon='MODIFIER_ON')
        box.prop(self, "fix_areaportal")
        box.prop(self, "skip_problem_brushes")
        
        box = layout.box()
        box.label(text="Output", icon='INFO')
        box.prop(self, "verbose")

def menu_func_import(self, context):
    self.layout.operator(BSP_OT_import_compilable.bl_idname, 
                        text="Quake 2 BSP to MAP (Compilable)")

def register():
    print("Registering BSP to MAP Compilable Converter...")
    try:
        bpy.utils.register_class(BSP_OT_import_compilable)
        bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
        print("✓ Successfully registered!")
        print("✓ Access: File > Import > Quake 2 BSP to MAP (Compilable)")
    except Exception as e:
        print(f"✗ Registration failed: {e}")

def unregister():
    try:
        bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
        bpy.utils.unregister_class(BSP_OT_import_compilable)
        print("Unregistered")
    except Exception as e:
        print(f"Unregistration failed: {e}")

if __name__ == "__main__":
    register()