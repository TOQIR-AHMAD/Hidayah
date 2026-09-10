ARABIC RECITATION GOES HERE
===========================

Put one file per ayah, named exactly like this:

    001.mp3    <-  bismillah ir-rahman ir-raheem
    002.mp3
    003.mp3
    004.mp3
    005.mp3
    006.mp3
    007.mp3

.wav, .m4a, .ogg and .flac work too - keep the 001...007 names.


ONE COMPLETE RECORDING INSTEAD
------------------------------

If you have a single file of the whole surah, put it here as:

    al_fatihah.mp3

and write down where each ayah starts and ends, in config.yaml:

    audio:
      recitation_mode: "full_surah"
      full_surah_timings:
        - {ayah: 1, start: 0.00, end: 6.42}
        - ...

Measure those numbers yourself, in Audacity or any player that shows
timecodes. `python run.py validate` checks that they fit the recording.


IMPORTANT
---------

This must be a real recording of a real reciter. Do not use text-to-speech
or a voice clone for Quranic Arabic.

Use only recordings you own, have permission for, or that are offered under
a licence permitting reuse - then record who the reciter is in the
"reciter:" block of config.yaml. Nothing is ever downloaded for you.


CHECK YOUR FILES

    python run.py validate
