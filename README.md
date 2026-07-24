# import-psd-as-mesh-planes

Allows importing of .psd files as image planes in Blender.\
Check it out in Blender Extensions Platform! 
[https://extensions.blender.org/import-psd-as-mesh-planes/](https://extensions.blender.org/add-ons/import-psd-as-mesh-planes/)

Credits to:
 - Koga Yamaguchi (@kyamagu) for the amazing [PSD-Tools Python Library](https://github.com/psd-tools/psd-tools)
 - Hynek Schlawak (attrs Python Library)

You may support me here: [Gumroad link](https://byebyelan.gumroad.com/l/import-psd-as-mesh-planes)

Known Issues / Limitations:
 - PSD files come in different types, only 8/16 bit RGB and CMYK files are officially supported, the other types may or may not fail.
 - 32-bit RGB/CMYK files are not supported.
 - PSD files allow for multiple layers having the same name, but Blender forcibly adds an incrementing .001 suffix to all duplicate names.
 - Blender only operates in RGBA, and so CMYK .psd files are converted to RGB, which may cause color inaccuracy issues.
 - Layer Masks, Effects, Blending Modes, and Clipping Masks are treated similarly as regular Paint Layers.
 - Imported Layer mesh planes are automatically ordered alphabetically in Blender's Outliner.