bl_info = {
    "name": "Import .PSD as Mesh Planes",
    "author": "byebyeLAN",
    "version": (2, 4, 7),
    "blender": (4, 2, 0),
    "location": "File > Import > Import .PSD as Mesh Planes",
    "description": "Import .PSD layers as image mesh planes.",
    "category": "Import-Export",
}

import bpy
import os
import numpy as np
import time
from bpy.props import StringProperty, BoolProperty, FloatProperty
from bpy_extras.io_utils import ImportHelper
from psd_tools import PSDImage


# CMYK to RGB conversion
def cmyk_to_rgb(C, M, Y, K):
    R = (1.0 - C) * (1.0 - K)
    G = (1.0 - M) * (1.0 - K)
    B = (1.0 - Y) * (1.0 - K)
    return R, G, B


# Normalise raw layer to RGBA top‑down float array (CMYK inversion)
def _raw_to_rgba_topdown(array, color_mode, opacity):
    """Convert a raw numpy array (H,W,C) to RGBA float32, top‑down."""
    if array.dtype == np.uint16:
        array = array.astype(np.float32) / 65535.0
    elif array.dtype == np.uint8:
        array = array.astype(np.float32) / 255.0
    elif array.dtype != np.float32:
        array = array.astype(np.float32)

    # Detect CMYK (or any 5‑channel array)
    if str(color_mode).lower() == 'cmyk' or array.shape[2] == 5:
        num_ch = array.shape[2]
        if num_ch == 4:
            C, M, Y, K = array[:, :, 0], array[:, :, 1], array[:, :, 2], array[:, :, 3]
            A = np.ones_like(C)
        elif num_ch == 5:
            C, M, Y, K = array[:, :, 0], array[:, :, 1], array[:, :, 2], array[:, :, 3]
            A = array[:, :, 4]
        else:
            # Fallback: use first 4 channels as CMYK
            array = array[:, :, :4]
            C, M, Y, K = array[:, :, 0], array[:, :, 1], array[:, :, 2], array[:, :, 3]
            A = np.ones_like(C)

        # Invert CMYK (0=black, 255=white)
        C = 1.0 - C
        M = 1.0 - M
        Y = 1.0 - Y
        K = 1.0 - K
        R, G, B = cmyk_to_rgb(C, M, Y, K)
        array = np.stack([R, G, B, A], axis=-1)

    elif array.shape[2] == 3:   # RGB without alpha → add opaque alpha
        alpha = np.ones((array.shape[0], array.shape[1], 1), dtype=np.float32)
        array = np.concatenate([array, alpha], axis=2)

    # Guarantee exactly 4 channels
    if array.shape[2] != 4:
        if array.shape[2] > 4:
            array = array[:, :, :4]
        else:
            alpha = np.ones((array.shape[0], array.shape[1], 1), dtype=np.float32)
            array = np.concatenate([array, alpha], axis=2)

    # Apply layer opacity
    opacity_factor = opacity / 255.0
    array[:, :, 3] *= opacity_factor
    return array


# Create a plane with its own mesh and material
def create_plane(name, image, location, scale):
    verts = [(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)]
    faces = [(0, 1, 2, 3)]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()

    uv_layer = mesh.uv_layers.new(name=name)
    uvs = [(0, 0), (1, 0), (1, 1), (0, 1)]
    for i, uv in enumerate(uvs):
        uv_layer.data[i].uv = uv

    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    obj.scale.x = image.size[0] * scale * 0.5
    obj.scale.y = image.size[1] * scale * 0.5

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

    output.location = (0, 0)
    mix.location = (-150, 0)
    transparent.location = (-350, -100)
    emission.location = (-350, -200)
    tex.location = (-650, 0)

    obj.data.materials.append(mat)
    return obj


def import_psd_streaming(context, filepath, flatten, skip_hidden, pack_images,
                         scale_factor, plane_spacing, root_collection):
    psd = PSDImage.open(filepath)
    doc_w = psd.width
    doc_h = psd.height
    color_mode = psd.color_mode

    if psd.depth == 32:
        raise RuntimeError("Unsupported bit depth.")

    start_time = time.time()
    total_layers = [0]
    z_offset = 0.0

    def process_layer(layer, parent_col, parent_hidden=False):
        nonlocal z_offset
        if skip_hidden and (parent_hidden or not layer.visible):
            return

        array = layer.numpy()
        if array is None:
            return

        h, w = array.shape[:2]
        if w <= 0 or h <= 0:
            return

        rgba = _raw_to_rgba_topdown(array, color_mode, layer.opacity)
        pixels = np.flipud(rgba).ravel().tolist()
        del rgba, array

        img = bpy.data.images.new(layer.name, width=w, height=h, alpha=True)
        img.pixels = pixels
        if pack_images:
            img.pack()

        cx = ((layer.left + w / 2) - doc_w / 2) * 0.001 * scale_factor
        cy = -(((layer.top + h / 2) - doc_h / 2) * 0.001 * scale_factor)
        location = (cx, cy, z_offset)

        obj = create_plane(layer.name, img, location, 0.001 * scale_factor)
        parent_col.objects.link(obj)

        z_offset += plane_spacing
        total_layers[0] += 1

    def walk(container, parent_col, parent_hidden=False):
        for layer in container:
            if layer.is_group():
                if skip_hidden and (parent_hidden or not layer.visible):
                    continue
                group_col = bpy.data.collections.new(layer.name)
                parent_col.children.link(group_col)
                walk(layer, group_col, parent_hidden=parent_hidden)
            else:
                if skip_hidden and (parent_hidden or not layer.visible):
                    continue
                process_layer(layer, parent_col)

        # Reverse sub-collections for File Order
        sub_cols = list(parent_col.children)
        for col in sub_cols:
            parent_col.children.unlink(col)
        for col in reversed(sub_cols):
            parent_col.children.link(col)

    if flatten:
        def collect_visible(container, parent_hidden=False):
            items = []
            for layer in container:
                if layer.is_group():
                    # If the group itself is hidden (and skip_hidden is on), skip the whole group
                    if skip_hidden and (parent_hidden or not layer.visible):
                        continue
                    items.extend(collect_visible(layer, parent_hidden=parent_hidden))
                else:
                    if skip_hidden and (parent_hidden or not layer.visible):
                        continue
                    if layer.kind != 'pixel':
                        continue
                    if layer.width > 0 and layer.height > 0:
                        items.append(layer)
            return items

        all_layers = collect_visible(psd)
        result = np.zeros((doc_h, doc_w, 4), dtype=np.float32)

        for layer in all_layers:
            array = layer.numpy()
            if array is None:
                continue
            rgba = _raw_to_rgba_topdown(array, color_mode, layer.opacity)
            x1 = layer.left
            y1 = layer.top
            x2 = x1 + rgba.shape[1]
            y2 = y1 + rgba.shape[0]
            x1 = max(0, x1)
            y1 = max(0, y1)
            x2 = min(doc_w, x2)
            y2 = min(doc_h, y2)
            dest_region = result[y1:y2, x1:x2, :]
            src_h = y2 - y1
            src_w = x2 - x1
            if src_h <= 0 or src_w <= 0:
                continue
            src_region = rgba[:src_h, :src_w, :]
            src_alpha = src_region[:, :, 3:4]
            dest_region[:, :, :3] = (src_region[:, :, :3] * src_alpha +
                                     dest_region[:, :, :3] * (1.0 - src_alpha))
            dest_region[:, :, 3] = src_alpha[:, :, 0] + dest_region[:, :, 3] * (1.0 - src_alpha[:, :, 0])

        pixels = np.flipud(result).ravel().tolist()
        del result

        name = f"{os.path.splitext(os.path.basename(filepath))[0]}_Merged"
        img = bpy.data.images.new(name, width=doc_w, height=doc_h, alpha=True)
        img.pixels = pixels
        if pack_images:
            img.pack()

        obj = create_plane(name, img, (0, 0, 0), 0.001 * scale_factor)
        root_collection.objects.link(obj)
        total_layers[0] = 1
    else:
        walk(psd, root_collection)

    elapsed = time.time() - start_time
    return doc_w, doc_h, total_layers[0], elapsed

class IMPORT_OT_psd_layers(bpy.types.Operator, ImportHelper):
    bl_idname = "import_scene.psd_layers"
    bl_label = "Import .PSD as Mesh Planes"
    bl_description = "Import .PSD file with layers as mesh planes."
    filename_ext = ".psd"

    filter_glob: StringProperty(default="*.psd", options={'HIDDEN'})
    scale_factor: FloatProperty(name="Scale", description="Image scale based on overall .psd canvas size.", default=1.0, min=0.0001)
    plane_spacing: FloatProperty(name="Z Offset", description="Offsets each mesh plane by the Z-axis.", default=0.001, min=0.0, precision=3)
    flatten_image: BoolProperty(name="Flatten Image", description="Flattens all .psd layers into one mesh plane.", default=False)
    skip_hidden: BoolProperty(name="Skip Hidden", description="Skips importing hidden layers.", default=True)
    pack_images: BoolProperty(name="Pack Images", description="Packs all imported images to this file.", default=True)

    def execute(self, context):
        try:
            psd_filename = os.path.splitext(os.path.basename(self.filepath))[0]
            root_coll = bpy.data.collections.new(psd_filename)
            context.scene.collection.children.link(root_coll)

            doc_w, doc_h, total, elapsed = import_psd_streaming(
                context, self.filepath,
                flatten=self.flatten_image,
                skip_hidden=self.skip_hidden,
                pack_images=self.pack_images,
                scale_factor=self.scale_factor,
                plane_spacing=self.plane_spacing,
                root_collection=root_coll,
            )

            if total == 0:
                self.report({'WARNING'}, "No layers found.")
                return {'CANCELLED'}

            self.report({'INFO'}, f"Imported {total} planes in {elapsed:.3f} seconds.")
            print(f"Imported {total} planes in {elapsed:.3f} seconds.")
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