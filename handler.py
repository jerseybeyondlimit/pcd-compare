import os
import shutil
import subprocess
import tempfile
import uuid
import numpy as np
import json
import laspy
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sklearn.neighbors import NearestNeighbors
from fastapi.responses import JSONResponse
from src.proto.inno_event_pb2 import BackgroundStaticMap
from fastapi.responses import RedirectResponse, FileResponse

app = FastAPI(
    title="PCD Compare API",
    description="Upload two .pcd files with the BackgroundStaticMap structure, compare for extra points, and return URLs to LAS files that can be loaded by Potree.",
    version="1.0.0"
)

# Allow cross-origin requests from any origin (can be restricted later as needed).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"]
)

# Mount the /static directory so the frontend can directly access the generated .las files.
# Note: Make sure the static/processed directory exists under the project root.
app.mount(
    "/static",
    StaticFiles(directory="static"),
    name="static"
)
app.mount(
    "/frontend",
    StaticFiles(directory="frontend", html=True),
    name="frontend"
)


def write_las(
    path: str,
    points: np.ndarray,
    color: tuple = None,
    compressed: bool = False
):
    """
    Write an N×3 NumPy array to a LAS or LAZ file.
    - path: File path ending with .las or .laz.
    - points: An N×3 float array representing (x, y, z) coordinates.
    - color: If not None, should be an (r, g, b) tuple with values in the range [0, 1]. This will be expanded to an N×3 array.
    - compressed: If True, output as .laz; otherwise, output as .las.
    Note: Since LAS RGB attributes are stored as uint16 (0–65535), floating-point color values in the range 0–1 will be scaled accordingly to 0–65535.
    """
    # Determine compression based on the file extension
    ext = os.path.splitext(path)[1].lower()
    if ext not in [".las", ".laz"]:
        raise RuntimeError(f"The LAS file path must end with .las or .laz. If not, raise an error.: {path}")

    N = points.shape[0]
    # Create the LAS header(LAS version 1.2, point format 3, which includes RGB fields)
    header = laspy.LasHeader(point_format=3, version="1.2")

    # laspy requires scaled coordinates when writing
    # Default header.scales = (0.01, 0.01, 0.01), which can be adjusted if needed
    las = laspy.LasData(header)

    # Assign point data directly to las.x, las.y, and las.z，will automatically be quantized using header.scales and header.offsets
    las.x = points[:, 0]
    las.y = points[:, 1]
    las.z = points[:, 2]

    # Handle color
    if color is None:
        # If no color is provided, default to white: (1.0, 1.0, 1.0)
        rgb_arr = np.ones((N, 3), dtype=np.uint16) * 65535
    else:
        # If a color is provided as a float triplet (r, g, b) in the range [0, 1]
        # Expand it to an N×3 array (same row for all points)，Convert to uint16 by multiplying by 65535
        r255 = int(np.clip(color[0], 0.0, 1.0) * 65535)
        g255 = int(np.clip(color[1], 0.0, 1.0) * 65535)
        b255 = int(np.clip(color[2], 0.0, 1.0) * 65535)
        rgb_arr = np.zeros((N, 3), dtype=np.uint16)
        rgb_arr[:, 0] = r255
        rgb_arr[:, 1] = g255
        rgb_arr[:, 2] = b255

    las.red = rgb_arr[:, 0]
    las.green = rgb_arr[:, 1]
    las.blue = rgb_arr[:, 2]

    # Write to disk
    if compressed:
        las.write(path)  # If the path ends in .laz, laspy will automatically write a compressed file
    else:
        las.write(path.replace(".laz", ".las"))


@app.get("/")
async def root():
    return RedirectResponse(url="/frontend/index.html")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse("../venv/lib/python3.10/site-packages/dash/favicon.ico")


@app.post("/compare", summary="Upload two .pcd files and return the relative URLs of the four generated .las files.")
async def compare_pcd(
    base_pcd: UploadFile = File(...),
    gen_pcd: UploadFile = File(...)
):
    """
    Accept two serialized .pcd files of type BackgroundStaticMap:
    1. Save them to a temporary directory
    2. Deserialize them into BackgroundStaticMap objects and extract the points field
    3. Convert the points into NumPy arrays pts_base and pts_gen (with coordinate order [z, -y, x])
    4. Use a KD-tree to compute extra_in_gen and extra_in_base
    5. Write out the four point sets as .las files
    6. Return a JSON containing the four relative URLs：
          return JSONResponse({
            "base_dir":       base_octree_url,
            "gen_dir":        gen_octree_url,
            "extra_base_dir": extra_base_octree_url,
            "extra_gen_dir":  extra_gen_octree_url
        })
    """
    # 1. Write the two uploaded files into a temporary directory.
    try:
        tmp_dir = tempfile.TemporaryDirectory()
        base_path = os.path.join(tmp_dir.name, "base.pcd")
        gen_path = os.path.join(tmp_dir.name, "gen.pcd")

        with open(base_path, "wb") as f:
            f.write(await base_pcd.read())
        with open(gen_path, "wb") as f:
            f.write(await gen_pcd.read())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save the uploaded file.: {e}")

    # 2. Deserialize the files into BackgroundStaticMap objects.
    try:
        bsm_base = BackgroundStaticMap()
        with open(base_path, "r") as f:
            read_hex = f.read()
            raw_binary = bytes.fromhex(read_hex)
            bsm_base.ParseFromString(raw_binary)

        bsm_gen = BackgroundStaticMap()
        with open(gen_path, "r") as f:
            read_hex = f.read()
            raw_binary = bytes.fromhex(read_hex)
            bsm_gen.ParseFromString(raw_binary)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to deserialize the .pcd file, please verify the file format: {e}")

    # 3. Extract the XYZ fields from the Protobuf data and convert them into NumPy arrays, in the order [z, -y, x]. 
    #    Assume each BackgroundStaticPoint has x, y, and z fields.
    try:
        pts_base_list = []
        for i in range(bsm_base.points.size):
            pts_base_list.append([bsm_base.points.z[i], -bsm_base.points.y[i], bsm_base.points.x[i]])
        pts_base = np.array(pts_base_list, dtype=np.float64)

        pts_gen_list = []
        for i in range(bsm_gen.points.size):
            pts_gen_list.append([bsm_gen.points.z[i], -bsm_gen.points.y[i], bsm_gen.points.x[i]])
        pts_gen = np.array(pts_gen_list, dtype=np.float64)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Extracting points from Protobuf failed: {e}")

    # 4. Use a KD-tree to identify "extra" points
    epsilon = 0.5  # Threshold (in meters, adjustable based on point cloud density)
    try:
        # 4.1 Points present in Gen but not in Base (extra points in Gen relative to Base)
        nbrs1 = NearestNeighbors(n_neighbors=1, algorithm='kd_tree').fit(pts_base)
        distances, _ = nbrs1.kneighbors(pts_gen)
        mask_extra_in_gen = distances.flatten() > epsilon
        extra_pts_gen = pts_gen[mask_extra_in_gen]

        # 4.2 Points present in Base but not in Gen (extra points in Base relative to Gen)
        nbrs2 = NearestNeighbors(n_neighbors=1, algorithm='kd_tree').fit(pts_gen)
        distances1, _ = nbrs2.kneighbors(pts_base)
        mask_extra_in_base = distances1.flatten() > epsilon
        extra_pts_base = pts_base[mask_extra_in_base]

        # 4.3 Remove the extra points and retain only the "common" or "normal" points
        pts_gen_non_extra = pts_gen[~mask_extra_in_gen]
        pts_base_non_extra = pts_base[~mask_extra_in_base]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Calculating extra points failed: {e}")

    try:
        # To ensure the existance of directory processed
        processed_dir = os.path.join(os.getcwd(), "static", "processed")
        os.makedirs(processed_dir, exist_ok=True)
        if os.path.isdir(processed_dir) and os.listdir(processed_dir):
            for entry in os.listdir(processed_dir):
                entry_path = os.path.join(processed_dir, entry)
                if os.path.isdir(entry_path):
                    shutil.rmtree(entry_path)
                else:
                    os.remove(entry_path)

        # Generate a unique filename.
        def make_unique_filename(prefix: str, ext: str = ".las") -> str:
            """生成唯一的文件名，如 'base_abcdef123456.las'"""
            return f"{prefix}_{uuid.uuid4().hex}{ext}"

        # 5.1 Write out all points from Base
        fn_base = make_unique_filename("base", ".las")
        las_base_path = os.path.join(processed_dir, fn_base)
        if pts_base_non_extra.shape[0] > 0:
            # Color points from Base：Light Grey (0.7, 0.7, 0.7)
            write_las(las_base_path, pts_base_non_extra, color=(0.7, 0.7, 0.7), compressed=False)
        else:
            # Also write a minimal LAS file (with N=0) for empty point sets.
            write_las(las_base_path, np.zeros((0, 3), dtype=np.float32), color=(1.0, 1.0, 1.0), compressed=False)

        # 5.2 Write out all points from Gen
        fn_gen = make_unique_filename("gen", ".las")
        las_gen_path = os.path.join(processed_dir, fn_gen)
        if pts_gen_non_extra.shape[0] > 0:
            write_las(las_gen_path, pts_gen_non_extra, color=(0.5, 0.5, 0.5), compressed=False)
        else:
            write_las(las_gen_path, np.zeros((0, 3), dtype=np.float32), color=(1.0, 1.0, 1.0), compressed=False)

        # 5.3 Write out "Extra in Base" points (in red)
        fn_extra_base = make_unique_filename("extra_base", ".las")
        las_extra_base_path = os.path.join(processed_dir, fn_extra_base)
        if extra_pts_base.shape[0] > 0:
            write_las(las_extra_base_path, extra_pts_base, color=(1.0, 0.0, 0.0), compressed=False)
        else:
            write_las(las_extra_base_path, np.zeros((0, 3), dtype=np.float32), color=(1.0, 1.0, 1.0), compressed=False)

        # 5.4 Write out "Extra in Gen" points (in blue)
        fn_extra_gen = make_unique_filename("extra_gen", ".las")
        las_extra_gen_path = os.path.join(processed_dir, fn_extra_gen)
        if extra_pts_gen.shape[0] > 0:
            write_las(las_extra_gen_path, extra_pts_gen, color=(0.0, 0.0, 1.0), compressed=False)
        else:
            write_las(las_extra_gen_path, np.zeros((0, 3), dtype=np.float32), color=(1.0, 1.0, 1.0), compressed=False)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Writing LAS file failed: {e}")
    
    # 6. Invoke PotreeConverter to convert each LAS file into an octree structure
    output_root = os.path.join("static", "processed")

    def fix_metadata_to_binary(octree_dir: str):
        """
        Automatically modify the encoding field in octree_dir/metadata.json from DEFAULT to BINARY
        """
        meta_path = os.path.join(octree_dir, "metadata.json")
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        # If the encoding field exists and is set to DEFAULT, update it to BINARY
        if meta.get("encoding", "") == "DEFAULT":
            meta["encoding"] = "BINARY"
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, separators=(",", ":"))
    
    def convert_one(las_path: str, output_dir_name: str):
        project_root = os.path.dirname(__file__)
        converter_dir = os.path.join(project_root, "bin", "PotreeConverter_linux_x64")
        converter_bin = os.path.join(converter_dir, "PotreeConverter")

        if not os.path.isfile(converter_bin):
            raise HTTPException(status_code=500, detail=f"PotreeConverter execution file do not exist: {converter_bin}")

        # Ensure the converter has execution permission
        try:
            os.chmod(converter_bin, 0o755)
        except Exception:
            pass

        out_dir_full = os.path.join(output_root, output_dir_name)
        if os.path.isdir(out_dir_full):
            shutil.rmtree(out_dir_full)
        os.makedirs(out_dir_full, exist_ok=True)

        cmd = [
            converter_bin,
            las_path,
            "-o", out_dir_full,
            "--generate-page", "no",
            "--output-format", "POTREE"
        ]
        
        # Output debugging information
        print(f"Running PotreeConverter: {' '.join(cmd)}")

        env = os.environ.copy()
        existing = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = f"{converter_dir}:{existing}"

        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True, env=env)
        except subprocess.CalledProcessError as e:
            error_msg = (
                f"PotreeConverter execution failed，returncode={e.returncode}\n"
                f"stdout:\n{e.stdout}\n"
                f"stderr:\n{e.stderr}"
            )
            raise HTTPException(status_code=500, detail=error_msg)

        metadata_dir = str(os.path.join(out_dir_full, 'pointclouds/no'))
        fix_metadata_to_binary(octree_dir=metadata_dir)
        
        # Return relative paths in the format: /static/processed/<dirname>
        return f"/static/processed/{output_dir_name}"

    try:
        def maybe_convert(pts: np.ndarray, las_path: str, prefix: str):
            if pts.shape[0] > 0:
                return convert_one(las_path, prefix)
            else:
                return ""
        
        base_octree_url = maybe_convert(pts_base_non_extra, las_base_path, fn_base.split(".")[0])
        gen_octree_url = maybe_convert(pts_gen_non_extra, las_gen_path, fn_gen.split(".")[0])
        extra_base_octree_url = maybe_convert(extra_pts_base, las_extra_base_path, fn_extra_base.split(".")[0])
        extra_gen_octree_url = maybe_convert(extra_pts_gen, las_extra_gen_path, fn_extra_gen.split(".")[0])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PotreeConverter failed in converting process: {e}")

    # 7. Clean up the temporary directory
    tmp_dir.cleanup()

    # 8. Return a JSON containing the URLs of the four octree directories
    extra_gen_num = extra_pts_gen.shape[0]
    extra_percent = round((extra_gen_num / pts_base.shape[0]) * 100, 2)
    return JSONResponse({
        "base_dir":       base_octree_url,
        "gen_dir":        gen_octree_url,
        "extra_base_dir": extra_base_octree_url,
        "extra_gen_dir":  extra_gen_octree_url,
        "extra_gen_num": extra_gen_num,
        "extra_percent": str(extra_percent) + "%"
    })
