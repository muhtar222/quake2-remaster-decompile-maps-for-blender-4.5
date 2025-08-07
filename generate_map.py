bl_info = {
    "name": "Quake 2 BSP to MAP - Final Clean",
    "blender": (3, 0, 0),
    "category": "Import-Export",
    "version": (4, 0, 0),
    "author": "BSP Tools",
    "description": "Complete BSP to MAP converter",
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
COMPILE_EPSILON = 0.125
GRID_SNAP = 0.25

# Content flags
CONTENTS_SOLID = 1
CONTENTS_WINDOW = 2
CONTENTS_WATER = 32
CONTENTS_AREAPORTAL = 0x8000

# Surface flags  
SURF_NODRAW = 0x80
SURF_SKY = 0x4

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
    """Snap coordinate to grid"""
    return round(value / grid_size) * grid_size

def round_coordinate(value, decimals=3):
    """Round coordinate precisely"""
    d = Decimal(str(value))
    rounded = d.quantize(Decimal(10) ** -decimals, rounding=ROUND_HALF_UP)
    return float(rounded)

def normalize_vector(vec):
    """Normalize vector safely"""
    length = math.sqrt(vec[0]**2 + vec[1]**2 + vec[2]**2)
    if length > 0.001:
        return Vector((vec[0]/length, vec[1]/length, vec[2]/length))
    return Vector((0, 0, 1))

def cross_product(a, b):
    """Cross product"""
    return Vector((
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0]
    ))

def validate_brush_geometry(points):
    """Check if brush geometry is valid"""
    for i in range(3):
        for j in range(i+1, 3):
            edge = Vector(points[j]) - Vector(points[i])
            if edge.length < COMPILE_EPSILON:
                return False, f"Degenerate edge ({edge.length:.6f})"
    return True, None

class BSPtoMAPConverter(Operator, ImportHelper):
    """Convert BSP to MAP format"""
    bl_idname = "import_scene.bsp_map_final"
    bl_label = "Import BSP to MAP"
    bl_description = "Convert BSP to MAP"
    bl_options = {'PRESET', 'UNDO'}
    
    filename_ext = ".bsp"
    filter_glob: StringProperty(default="*.bsp", options={'HIDDEN'})
    
    # Properties
    grid_snap: FloatProperty(
        name="Grid Snap",
        description="Snap to grid",
        default=0.25,
        min=0.125,
        max=16.0
    )
    
    coordinate_decimals: IntProperty(
        name="Decimals",
        description="Coordinate precision",
        default=3,
        min=0,
        max=6
    )
    
    min_edge_length: FloatProperty(
        name="Min Edge",
        description="Minimum edge length",
        default=0.125,
        min=0.01,
        max=1.0
    )
    
    default_texture: StringProperty(
        name="Default Texture",
        description="Default texture name",
        default="e1u1/metal1_2"
    )
    
    fix_textures: BoolProperty(
        name="Fix Textures",
        description="Fix texture paths",
        default=True
    )
    
    skip_problems: BoolProperty(
        name="Skip Problems",
        description="Skip problem brushes",
        default=True
    )
    
    show_info: BoolProperty(
        name="Show Info",
        description="Show detailed info",
        default=True
    )
    
    def fix_texture_name(self, name):
        """Fix texture name"""
        if not name or name == "" or name == "MISSING":
            return self.default_texture
        
        name = name.replace('\x00', '').strip()
        
        if not name:
            return self.default_texture
        
        # Special textures
        if name.startswith('*'):
            return name
        if name.upper() in ['CLIP', 'NODRAW', 'SKIP', 'HINT', 'AREAPORTAL']:
            return name.upper()
        
        # Add texture path if missing
        if self.fix_textures and '/' not in name and not name.startswith('*'):
            name_lower = name.lower()
            
            if 'metal' in name_lower:
                return f"e1u1/{name}"
            elif 'wall' in name_lower:
                return f"e1u2/{name}"
            elif 'floor' in name_lower or 'flr' in name_lower:
                return f"e1u3/{name}"
            elif 'crate' in name_lower:
                return f"e2u3/{name}"
            elif 'rock' in name_lower:
                return f"e3u1/{name}"
            else:
                return f"e1u1/{name}"
        
        return name
    
    def execute(self, context):
        """Execute conversion"""
        print("\n" + "="*60)
        print("BSP TO MAP CONVERTER - FINAL")
        print("="*60)
        
        try:
            # Read file
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
            
            # Entities
            if lumps[0][1] > 0:
                entity_data = data[lumps[0][0]:lumps[0][0] + lumps[0][1]]
                entities_string = entity_data.decode('ascii', errors='ignore').strip('\x00')
                print(f"Found {len(entities_string)} bytes of entities")
            
            # Planes
            if lumps[1][1] > 0:
                plane_data = data[lumps[1][0]:lumps[1][0] + lumps[1][1]]
                for i in range(0, len(plane_data), 20):
                    if i + 20 <= len(plane_data):
                        nx, ny, nz, dist, ptype = struct.unpack('<ffffI', plane_data[i:i+20])
                        planes.append(SimplePlane(nx, ny, nz, dist, ptype))
                print(f"Parsed {len(planes)} planes")
            
            # Textures
            if lumps[5][1] > 0:
                texinfo_data = data[lumps[5][0]:lumps[5][0] + lumps[5][1]]
                TEXINFO_SIZE = 76
                for i in range(0, len(texinfo_data), TEXINFO_SIZE):
                    if i + TEXINFO_SIZE <= len(texinfo_data):
                        try:
                            tex_data = struct.unpack('<8f2I32sI', texinfo_data[i:i+TEXINFO_SIZE])
                            u_axis = Vector((tex_data[0], tex_data[1], tex_data[2]))
                            u_offset = tex_data[3]
                            v_axis = Vector((tex_data[4], tex_data[5], tex_data[6]))
                            v_offset = tex_data[7]
                            flags = tex_data[8]
                            value = tex_data[9]
                            texture_name = tex_data[10].decode('ascii', errors='ignore').strip('\x00')
                            
                            if not texture_name:
                                texture_name = self.default_texture
                            
                            texinfos.append(SimpleTexInfo(u_axis, u_offset, v_axis, v_offset,
                                                         flags, value, texture_name))
                        except:
                            texinfos.append(SimpleTexInfo(
                                Vector((1, 0, 0)), 0, Vector((0, -1, 0)), 0,
                                0, 0, self.default_texture
                            ))
                print(f"Parsed {len(texinfos)} texinfos")
            
            # Brushes
            if lumps[14][1] > 0:
                brush_data = data[lumps[14][0]:lumps[14][0] + lumps[14][1]]
                for i in range(0, len(brush_data), 12):
                    if i + 12 <= len(brush_data):
                        first_side, num_sides, contents = struct.unpack('<III', brush_data[i:i+12])
                        brushes.append(SimpleBrush(first_side, num_sides, contents))
                print(f"Parsed {len(brushes)} brushes")
            
            # Brush sides
            if lumps[15][1] > 0:
                side_data = data[lumps[15][0]:lumps[15][0] + lumps[15][1]]
                for i in range(0, len(side_data), 4):
                    if i + 4 <= len(side_data):
                        plane_num, tex_info = struct.unpack('<HH', side_data[i:i+4])
                        brush_sides.append(SimpleBrushSide(plane_num, tex_info))
                print(f"Parsed {len(brush_sides)} brush sides")
            
            # Create MAP
            map_path = os.path.splitext(self.filepath)[0] + '_final.map'
            print(f"\nWriting MAP: {map_path}")
            
            valid_brushes = 0
            skipped_brushes = 0
            texture_stats = {}
            
            with open(map_path, 'w') as f:
                # Header
                f.write('// BSP to MAP Conversion\n\n')
                
                # Worldspawn
                f.write('{\n')
                f.write('"classname" "worldspawn"\n')
                f.write('"mapversion" "220"\n')
                
                # Process brushes
                for brush_idx, brush in enumerate(brushes):
                    # Skip invalid
                    if brush.num_sides < 4:
                        skipped_brushes += 1
                        continue
                    
                    if brush.contents == 0:
                        skipped_brushes += 1
                        continue
                    
                    # Skip areaportal
                    if brush.contents & CONTENTS_AREAPORTAL:
                        skipped_brushes += 1
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
                                elif len(texinfos) > 0:
                                    brush_texinfos.append(texinfos[0])
                                else:
                                    brush_texinfos.append(SimpleTexInfo(
                                        Vector((1, 0, 0)), 0, Vector((0, -1, 0)), 0,
                                        0, 0, self.default_texture
                                    ))
                    
                    if len(brush_planes) >= 4:
                        try:
                            brush_valid = True
                            brush_lines = []
                            
                            for i, plane in enumerate(brush_planes):
                                # Calculate points
                                normal = normalize_vector(plane.normal)
                                
                                if abs(normal[2]) < 0.9:
                                    tangent1 = normalize_vector(cross_product(normal, Vector((0, 0, 1))))
                                else:
                                    tangent1 = normalize_vector(cross_product(normal, Vector((1, 0, 0))))
                                
                                tangent2 = normalize_vector(cross_product(normal, tangent1))
                                
                                scale = 256
                                center = normal * plane.distance
                                
                                # Snap to grid
                                center = Vector((
                                    snap_to_grid(center[0], self.grid_snap),
                                    snap_to_grid(center[1], self.grid_snap),
                                    snap_to_grid(center[2], self.grid_snap)
                                ))
                                
                                p1 = center + tangent1 * scale
                                p2 = center - tangent1 * scale
                                p3 = center + tangent2 * scale
                                
                                # Snap points
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
                                
                                # Validate
                                valid, error = validate_brush_geometry([p1, p2, p3])
                                if not valid and self.skip_problems:
                                    brush_valid = False
                                    break
                                
                                # Round
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
                                
                                # Build line
                                line = f'( {p1[0]} {p1[1]} {p1[2]} ) '
                                line += f'( {p2[0]} {p2[1]} {p2[2]} ) '
                                line += f'( {p3[0]} {p3[1]} {p3[2]} ) '
                                
                                # Texture
                                if i < len(brush_texinfos) and brush_texinfos[i]:
                                    tex = brush_texinfos[i]
                                    name = self.fix_texture_name(tex.texture_name)
                                    
                                    if name not in texture_stats:
                                        texture_stats[name] = 0
                                    texture_stats[name] += 1
                                    
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
                                    
                                    if u_axis.length < 0.01:
                                        u_axis = Vector((1, 0, 0))
                                    if v_axis.length < 0.01:
                                        v_axis = Vector((0, -1, 0))
                                    
                                    u_offset = round_coordinate(tex.u_offset, 2)
                                    v_offset = round_coordinate(tex.v_offset, 2)
                                    
                                    line += f'{name} '
                                    line += f'[ {u_axis[0]} {u_axis[1]} {u_axis[2]} {u_offset} ] '
                                    line += f'[ {v_axis[0]} {v_axis[1]} {v_axis[2]} {v_offset} ] '
                                    line += '0 1 1'
                                else:
                                    default_tex = self.default_texture
                                    if default_tex not in texture_stats:
                                        texture_stats[default_tex] = 0
                                    texture_stats[default_tex] += 1
                                    
                                    line += f'{default_tex} [ 1 0 0 0 ] [ 0 -1 0 0 ] 0 1 1'
                                
                                brush_lines.append(line)
                            
                            # Write brush
                            if brush_valid and brush_lines:
                                f.write('{\n')
                                for line in brush_lines:
                                    f.write(line + '\n')
                                f.write('}\n')
                                valid_brushes += 1
                            else:
                                skipped_brushes += 1
                        
                        except Exception as e:
                            if self.show_info:
                                print(f"  Brush {brush_idx}: Error - {e}")
                            skipped_brushes += 1
                
                f.write('}\n')  # End worldspawn
                
                # Add other entities
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
                                for el in entity_lines:
                                    if '"classname"' in el:
                                        if '"func_areaportal"' in el:
                                            entity_class = "areaportal"
                                        elif '"worldspawn"' in el:
                                            entity_class = "worldspawn"
                                        break
                                
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
            print(f"COMPLETE!")
            print(f"  Output: {map_path}")
            print(f"  Valid brushes: {valid_brushes}")
            print(f"  Skipped brushes: {skipped_brushes}")
            
            if self.show_info and texture_stats:
                print(f"\nTop textures:")
                sorted_tex = sorted(texture_stats.items(), key=lambda x: x[1], reverse=True)
                for i, (tex, count) in enumerate(sorted_tex[:5]):
                    print(f"  {tex}: {count}")
            
            print("="*60)
            
            self.report({'INFO'}, f"Exported {valid_brushes} brushes")
            return {'FINISHED'}
            
        except Exception as e:
            print(f"\nERROR: {e}")
            import traceback
            traceback.print_exc()
            self.report({'ERROR'}, f"Failed: {str(e)}")
            return {'CANCELLED'}
    
    def draw(self, context):
        layout = self.layout
        
        box = layout.box()
        box.label(text="Grid", icon='GRID')
        box.prop(self, "grid_snap")
        box.prop(self, "coordinate_decimals")
        box.prop(self, "min_edge_length")
        
        box = layout.box()
        box.label(text="Textures", icon='TEXTURE')
        box.prop(self, "default_texture")
        box.prop(self, "fix_textures")
        
        box = layout.box()
        box.label(text="Options", icon='SETTINGS')
        box.prop(self, "skip_problems")
        box.prop(self, "show_info")

def menu_func_import(self, context):
    self.layout.operator(BSPtoMAPConverter.bl_idname, 
                        text="Quake 2 BSP to MAP (Final)")

def register():
    print("Registering BSP to MAP Final...")
    try:
        bpy.utils.register_class(BSPtoMAPConverter)
        bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
        print("✓ Ready!")
    except Exception as e:
        print(f"✗ Failed: {e}")

def unregister():
    try:
        bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
        bpy.utils.unregister_class(BSPtoMAPConverter)
    except:
        pass

if __name__ == "__main__":
    register()
