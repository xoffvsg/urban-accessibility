"""Walking access to essential services, scored per Census block group."""

import os
import sys
from pathlib import Path

# When the env's bin/ is on PATH without `conda activate` (e.g. a process launched by VS Code),
# conda's activate.d scripts don't run, PROJ_DATA stays unset and pyogrio/GDAL can't find proj.db.
# Point it at this interpreter's PROJ data; import urban_access before geopandas in notebooks.
_proj = Path(sys.prefix) / "share" / "proj"
if "PROJ_DATA" not in os.environ and (_proj / "proj.db").exists():
    os.environ["PROJ_DATA"] = str(_proj)
