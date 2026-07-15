# import-psd-as-mesh-planes

Allows importing of .psd files as image planes in Blender.\
Check it out in Blender Extensions Platform! 
[https://extensions.blender.org/import-psd-as-mesh-planes/](https://extensions.blender.org/add-ons/import-psd-as-mesh-planes/)

You may support me here: [Gumroad link](https://byebyelan.gumroad.com/l/import-psd-as-mesh-planes)

Known Issues / Limitations:
 - Only supports RGB 8-bit .psd files.
 - Support for RGB 16-bit and 8/16-bit CYMK are planned for the future.
 - Layer Groups and Heirarchy are not imported, the layers are only sorted in Blender's Outliner alphabetically.
 - Layer Masks, Effects, and Clipping Masks are not respected upon importing, and are instead treated similarly as regular Paint Layers.