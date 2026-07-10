bl_info = {
    "name": "Import .PSD as Mesh Planes",
    "author": "byebyeLAN",
    "version": (1, 0, 2),
    "blender": (4, 2, 0),
    "location": "File > Import > Import .PSD as Mesh Planes",
    "description": "Import .PSD with layers as image mesh planes.",
    "category": "Import-Export",
}

import bpy
import struct
import os
from bpy.props import StringProperty, BoolProperty, FloatProperty, IntProperty
from bpy_extras.io_utils import ImportHelper

PSD_SIGNATURE = b"8BPS"
PIXEL_SIZE = 0.001

class PSDParseError(Exception):
    pass

# read binary of big-endian .psd files
class Reader:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def read(self, n):
        chunk = self.data[self.pos:self.pos+n]
        if len(chunk) != n:
            raise PSDParseError("Unexpected EOF")
        self.pos += n
        return chunk

    def u16(self): return struct.unpack(">H", self.read(2))[0]
    def s16(self): return struct.unpack(">h", self.read(2))[0]
    def u32(self): return struct.unpack(">I", self.read(4))[0]
    def s32(self): return struct.unpack(">i", self.read(4))[0]
    def skip(self, n): self.pos += n
    def tell(self): return self.pos
    def seek(self, pos): self.pos = pos

# psd decoder
def decode_packbits(data, expected):
    out = bytearray()
    i = 0
    ln = len(data)
    while i < ln and len(out) < expected:
        n = struct.unpack("b", data[i:i+1])[0]
        i += 1
        if 0 <= n <= 127:
            count = n + 1
            out.extend(data[i:i+count])
            i += count
        elif -127 <= n <= -1:
            count = 1 - n
            b = data[i]
            i += 1
            out.extend([b] * count)
    if len(out) < expected:
        out.extend([0] * (expected - len(out)))
    return bytes(out[:expected])

# pascal name parser
def parse_pascal_name(r):
    ln = r.read(1)[0]
    raw = r.read(ln)
    pad = (4 - ((ln + 1) % 4)) % 4
    if pad:
        r.skip(pad)
    try:
        return raw.decode("macroman")
    except:
        return "Layer"

# 0 1 2 -1 correspond to rgba respectively
def build_rgba(w, h, chan_data, opacity):
    red_ch = chan_data.get(0, bytes([0]) * (w * h))
    grn_ch = chan_data.get(1, bytes([0]) * (w * h))
    blu_ch = chan_data.get(2, bytes([0]) * (w * h))
    alp_ch = chan_data.get(-1, bytes([255]) * (w * h))

    # store px as array for ram efficiency
    from array import array
    px = array('f')

    img_opacity = opacity / 255.0

    append = px.append

    # flip/reverse the x and y (or h and w) texture coordinates
    for y in reversed(range(h)):
        row_start = y * w

        for x in (range(w)):
            i = row_start + x

            append(red_ch[i] / 255.0)
            append(grn_ch[i] / 255.0)
            append(blu_ch[i] / 255.0)
            append((alp_ch[i] / 255.0) * img_opacity)

    return px

# psd file parser
def parse_psd(filepath):
    with open(filepath, "rb") as f:
        data = f.read()

    r = Reader(data)

    if r.read(4) != PSD_SIGNATURE:
        raise PSDParseError("Not a .PSD file.")
    if r.u16() != 1:
        raise PSDParseError("Unsupported .PSD version.")

    r.skip(6)
    channels = r.u16()
    height = r.u32()
    width = r.u32()
    depth = r.u16()
    color_mode = r.u16()

    if depth != 8 or color_mode != 3:
        raise PSDParseError("Only 8-bit RGB .PSD supported.")

    # skip color mode data section
    r.skip(r.u32())

    # skip image resources section
    r.skip(r.u32())

    # layer and mask info section
    layer_mask_len = r.u32()
    layers = []

    if layer_mask_len > 0:
        layer_info_len = r.u32()

        if layer_info_len > 0:
            layer_count = abs(r.s16())
            records = []

            for _ in range(layer_count):
                top = r.s32()
                left = r.s32()
                bottom = r.s32()
                right = r.s32()

                ch_count = r.u16()
                chs = []
                for _ in range(ch_count):
                    cid = r.s16()
                    clen = r.u32()
                    chs.append((cid, clen))

                r.read(8)

                opacity = r.read(1)[0]
                clipping = r.read(1)[0]
                flags = r.read(1)[0]
                r.read(1)

                extra_len = r.u32()
                extra_start = r.tell()

                mask_len = r.u32()
                r.skip(mask_len)
                blend_len = r.u32()
                r.skip(blend_len)

                name = parse_pascal_name(r)

                r.seek(extra_start + extra_len)

                records.append({
                    "top": top,
                    "left": left,
                    "bottom": bottom,
                    "right": right,
                    "channels": chs,
                    "opacity": opacity,
                    "flags": flags,
                    "name": name,
                })

            # read actual channel pixel payloads
            for rec in records:
                w = max(0, rec["right"] - rec["left"])
                h = max(0, rec["bottom"] - rec["top"])
                chan_data = {}

                for cid, clen in rec["channels"]:
                    if w == 0 or h == 0:
                        r.skip(clen)
                        continue

                    compression = r.u16()

                    if compression == 0:
                        payload = r.read(w * h)

                    elif compression == 1:
                        row_lengths = [r.u16() for _ in range(h)]
                        payload = bytearray()
                        for rl in row_lengths:
                            payload.extend(decode_packbits(r.read(rl), w))
                        payload = bytes(payload)

                    else:
                        # ZIP etc not implemented
                        r.skip(clen - 2)
                        payload = bytes([0] * (w * h))

                    chan_data[cid] = payload

                rec["pixels"] = build_rgba(w, h, chan_data, rec["opacity"])
                rec["width"] = w
                rec["height"] = h
                layers.append(rec)

    return width, height, layers

# create planes
def create_plane(context, name, image, x, y, z, scale):
    bpy.ops.mesh.primitive_plane_add(location=(x, y, z))
    obj = context.active_object
    obj.name = name

    obj.scale.x = image.size[0] * scale * 0.5
    obj.scale.y = image.size[1] * scale * 0.5

    uv_layer = obj.data.uv_layers.new(name=name)

    # create node setup for each plane
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True

    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    nodes.clear()

    output = nodes.new("ShaderNodeOutputMaterial")
    tex = nodes.new("ShaderNodeTexImage")
    tex.image = image
    emission = nodes.new("ShaderNodeEmission")
    transparent = nodes.new("ShaderNodeBsdfTransparent")
    mix = nodes.new("ShaderNodeMixShader")

    links.new(tex.outputs["Color"], emission.inputs["Color"])
    links.new(tex.outputs["Alpha"], mix.inputs["Fac"])
    links.new(transparent.outputs[0], mix.inputs[1])
    links.new(emission.outputs[0], mix.inputs[2])
    links.new(mix.outputs[0], output.inputs["Surface"])

    # move nodes
    output.location = (0, 0)
    mix.location = (-150, 0)
    transparent.location = (-350, -100)
    emission.location = (-350, -200)
    tex.location = (-650, 0)

    obj.data.materials.append(mat)

    return obj

class IMPORT_OT_psd_layers(bpy.types.Operator, ImportHelper):
    bl_idname = "import_scene.psd_layers"
    bl_label = "Import .PSD as Mesh Planes"
    bl_description = "Import .PSD file with layers as mesh planes."
    filename_ext = ".psd"

    filter_glob: StringProperty(default="*.psd", options={'HIDDEN'})
    scale_factor: FloatProperty(name="Scale", description="Image scale based on overall .psd canvas size." , default=1.0, min=0.0001)
    plane_spacing: FloatProperty(name="Z Offset", description="Offsets each mesh plane by the Z-axis.", default=0.001, min=0.0, precision=3)
    skip_hidden: BoolProperty(name="Skip Hidden", description="Skips importing hidden layers.", default=True)
    pack_images: BoolProperty(name="Pack Images", description="Packs all imported images to this file.", default=True)

    def execute(self, context):
        try:
            doc_w, doc_h, layers = parse_psd(self.filepath)

            # get psd filename and make it a collection to store planes
            psd_filename = os.path.splitext(os.path.basename(self.filepath))[0]
            import_collection = bpy.data.collections.new(psd_filename)
            context.scene.collection.children.link(import_collection)

            z_offset = 0.0

            # ordering psd imports by layer
            for layer in layers:

                hidden = bool(layer["flags"] & 0x02)
                if self.skip_hidden and hidden:
                    continue

                if layer["width"] == 0 or layer["height"] == 0:
                    continue

                img = bpy.data.images.new(
                    layer["name"],
                    width=layer["width"],
                    height=layer["height"],
                    alpha=True,
                )

                img.pixels = layer["pixels"]

                # ram optimisation
                import gc
                gc.collect()

                if self.pack_images:
                    img.pack()

                # PSD origin is top-left, adjust it to center at 0, 0, 0 in world space
                cx = ((layer["left"] + layer["width"] / 2) - doc_w / 2) * PIXEL_SIZE * self.scale_factor
                cy = -(((layer["top"] + layer["height"] / 2) - doc_h / 2) * PIXEL_SIZE * self.scale_factor)

                obj = create_plane(context, layer["name"], img, cx, cy, z_offset, PIXEL_SIZE * self.scale_factor)

                import_collection.objects.link(obj)

                for collection in tuple(obj.users_collection):
                    if collection != import_collection:
                        collection.objects.unlink(obj)

                z_offset += self.plane_spacing

            return {'FINISHED'}

        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

def menu_func(self, context):
    self.layout.operator(IMPORT_OT_psd_layers.bl_idname, text="Import .PSD as Mesh Planes (.psd)")

def register():
    bpy.utils.register_class(IMPORT_OT_psd_layers)
    bpy.types.TOPBAR_MT_file_import.append(menu_func)

def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func)
    bpy.utils.unregister_class(IMPORT_OT_psd_layers)

if __name__ == "__main__":
    register()
