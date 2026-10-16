"""KTH graphical profile for matplotlib figures (colours from cohm/kth-doc-templates).

Figtree ships as a variable font on most systems, and matplotlib renders a variable
font only at its default (light) instance. The static Regular/SemiBold files in fonts/
(SIL Open Font License, fonts/OFL.txt) give real 400/600 weights.
"""
from pathlib import Path

import matplotlib
from matplotlib import font_manager

FONTS = Path(__file__).resolve().parent.parent / "fonts"


def use_kth_fonts() -> None:
    for ttf in sorted(FONTS.glob("Figtree-*.ttf")):
        font_manager.fontManager.addfont(str(ttf))
    matplotlib.rcParams["font.family"] = ["Figtree", "Helvetica Neue", "Arial", "sans-serif"]
