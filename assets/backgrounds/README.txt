BACKGROUND
==========

The shipped theme (theme.style: "glass") is DESIGNED AROUND ARTWORK YOU PUT
HERE. It sets its text straight over the picture in frosted capsules, and
draws no backdrop of its own. Put your plate in this folder:

    assets/backgrounds/fatihah.jpg

Use the PLAIN plate - the artwork with NO text on it. The project draws
"SURAH 001", the surah name, the ayah number and every ayah itself. A plate
that already has text on it will end up with two sets.

With this folder empty the project falls back to drawing its own backdrop,
and `render` logs a warning saying so.

FILE TYPES
----------
    Images  .jpg .jpeg .png .webp .bmp
            Cropped to 16:9 (never stretched), given a slow push-in and drift.

    Video   .mp4 .mov .mkv .webm
            Looped if shorter than the video, cropped to 16:9.

The first file found is used, so if more than one file is here, name the one
you want in config.yaml:

    background:
      source: "fatihah.png"

That is how it currently ships, deliberately. This folder also holds the
composed reference design (the version with the text already burnt onto it).
Naming the plate explicitly stops "auto" grabbing the composed one, which
would put a second set of text on screen. If you delete the composed file you
can go back to source: "auto".

To force the generated backdrop even with a file here:

    background:
      source: "generated"

COMPOSITION
-----------
The text is centred, so leave the middle of the frame relatively quiet.
Detail belongs at the edges. A plate around 1920x1080 or larger is ideal;
anything smaller is upscaled.

If the text is hard to read over your plate, raise background.dim (it ships
at 0.12) or strengthen theme.text_shadow_opacity.

Use only images and footage you are licensed to use. Nothing is ever
downloaded for you.
