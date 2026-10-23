#!/bin/zsh
# Double-click to start Slicer with the OpenDICOM viewer workarounds.
set -eu
script_directory="${0:A:h}"
if (( $# > 0 )); then
    slicer_application="$1"
else
    slicer_candidates=(/Applications/*Slicer*.app(N))
    if (( ${#slicer_candidates} != 1 )); then
        print -u2 'Could not select a single Slicer installation in /Applications.'
        print -u2 'Run this launcher with the path to the desired Slicer .app as its argument.'
        exit 1
    fi
    slicer_application="${slicer_candidates[1]}"
fi
slicer_executable="$slicer_application/Contents/MacOS/Slicer"
if [[ ! -x "$slicer_executable" ]]; then
    print -u2 'The selected application does not contain a Slicer executable.'
    exit 1
fi
exec "$slicer_executable" --python-script "$script_directory/slicer_startup_compat.py"
