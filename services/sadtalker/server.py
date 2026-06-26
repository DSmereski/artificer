"""
SadTalker Flask API Server
Generates talking head videos from a source image and audio file.
"""

import os
import sys
import uuid
import subprocess
from pathlib import Path

from flask import Flask, request, jsonify, send_file
from PIL import Image

app = Flask(__name__)

# Add SadTalker to path
SADTALKER_PATH = Path("/app/SadTalker")
sys.path.insert(0, str(SADTALKER_PATH))

# Directories
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "/output"))
AVATARS_DIR = Path(os.environ.get("AVATARS_DIR", "/avatars"))
WORKSPACE_DIR = Path("/workspace")
CHECKPOINTS_DIR = SADTALKER_PATH / "checkpoints"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
AVATARS_DIR.mkdir(parents=True, exist_ok=True)
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return jsonify({"status": "healthy", "service": "sadtalker"})


@app.route("/avatars", methods=["GET"])
def list_avatars():
    """List available avatar images."""
    avatars = []
    for ext in ["*.jpg", "*.jpeg", "*.png"]:
        for img_path in AVATARS_DIR.glob(ext):
            avatars.append({
                "name": img_path.stem,
                "filename": img_path.name,
                "path": str(img_path)
            })

    return jsonify({
        "avatars": avatars,
        "avatars_directory": str(AVATARS_DIR)
    })


@app.route("/avatars/upload", methods=["POST"])
def upload_avatar():
    """Upload a new avatar image."""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "Empty filename"}), 400

    # Validate image
    try:
        img = Image.open(file.stream)
        img.verify()
        file.stream.seek(0)
    except Exception as e:
        return jsonify({"error": f"Invalid image: {str(e)}"}), 400

    # Save avatar - sanitize name to prevent path traversal
    import re as _re
    name = request.form.get("name", Path(file.filename).stem)
    name = _re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:50]
    ext = Path(file.filename).suffix or ".png"
    if ext.lower() not in (".jpg", ".jpeg", ".png", ".gif"):
        return jsonify({"error": "Invalid file extension"}), 400
    filename = f"{name}{ext}"
    save_path = AVATARS_DIR / filename
    if not str(save_path.resolve()).startswith(str(AVATARS_DIR.resolve())):
        return jsonify({"error": "Invalid filename"}), 400

    file.save(str(save_path))

    return jsonify({
        "success": True,
        "name": name,
        "filename": filename,
        "path": str(save_path)
    })


@app.route("/generate", methods=["POST"])
def generate():
    """
    Generate talking head video from source image and audio.

    Request (multipart form or JSON):
    - source_image: Image file or path to avatar
    - audio: Audio file or path (.wav, .mp3)
    - avatar_name: Name of pre-uploaded avatar (alternative to source_image)
    - preprocess: 'crop', 'extcrop', 'resize', 'full', 'extfull' (default: 'crop')
    - still: Boolean - reduce head motion (default: False)
    - enhancer: 'gfpgan', 'RestoreFormer', None (default: None)

    Returns: Video file or JSON with file path
    """
    try:
        if request.content_type and "multipart/form-data" in request.content_type:
            return _handle_multipart_request()
        else:
            return _handle_json_request()
    except Exception as e:
        import traceback
        import traceback as tb
        tb.print_exc()
        return jsonify({"error": "Generation failed. Check server logs for details."}), 500


def _handle_multipart_request():
    """Handle multipart form upload."""
    # Get source image
    if "source_image" in request.files:
        source_file = request.files["source_image"]
        source_path = WORKSPACE_DIR / f"source_{uuid.uuid4()}.png"
        source_file.save(str(source_path))
    elif "avatar_name" in request.form:
        avatar_name = request.form["avatar_name"]
        source_path = _find_avatar(avatar_name)
        if not source_path:
            return jsonify({"error": f"Avatar '{avatar_name}' not found"}), 404
    else:
        return jsonify({"error": "No source image provided"}), 400

    # Get audio file
    if "audio" not in request.files:
        return jsonify({"error": "No audio file provided"}), 400

    audio_file = request.files["audio"]
    audio_ext = Path(audio_file.filename).suffix or ".wav"
    audio_path = WORKSPACE_DIR / f"audio_{uuid.uuid4()}{audio_ext}"
    audio_file.save(str(audio_path))

    # Get options
    preprocess = request.form.get("preprocess", "crop")
    still = request.form.get("still", "false").lower() == "true"
    enhancer = request.form.get("enhancer", None)
    return_file = request.form.get("return_file", "true").lower() == "true"

    return _generate_video(str(source_path), str(audio_path), preprocess, still, enhancer, return_file)


def _handle_json_request():
    """Handle JSON request with file paths."""
    data = request.get_json()

    if not data:
        return jsonify({"error": "No JSON data provided"}), 400

    # Get source image path
    if "source_image" in data:
        source_path = data["source_image"]
    elif "avatar_name" in data:
        source_path = _find_avatar(data["avatar_name"])
        if not source_path:
            return jsonify({"error": f"Avatar '{data['avatar_name']}' not found"}), 404
        source_path = str(source_path)
    else:
        return jsonify({"error": "No source image provided"}), 400

    # Get audio path
    if "audio" not in data:
        return jsonify({"error": "No audio path provided"}), 400

    audio_path = data["audio"]

    # Get options
    preprocess = data.get("preprocess", "crop")
    still = data.get("still", False)
    enhancer = data.get("enhancer", None)
    return_file = data.get("return_file", True)

    return _generate_video(source_path, audio_path, preprocess, still, enhancer, return_file)


def _find_avatar(name: str) -> Path:
    """Find avatar by name."""
    import re as _re
    name = _re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    for ext in [".jpg", ".jpeg", ".png"]:
        avatar_path = AVATARS_DIR / f"{name}{ext}"
        if str(avatar_path.resolve()).startswith(str(AVATARS_DIR.resolve())) and avatar_path.exists():
            return avatar_path
    return None


def _generate_video(source_path: str, audio_path: str, preprocess: str = "crop",
                   still: bool = False, enhancer: str = None, return_file: bool = True):
    """Generate the talking head video using SadTalker."""
    # Validate inputs
    if not Path(source_path).exists():
        return jsonify({"error": f"Source image not found: {source_path}"}), 404
    if not Path(audio_path).exists():
        return jsonify({"error": f"Audio file not found: {audio_path}"}), 404

    # Generate output path
    video_id = str(uuid.uuid4())
    result_dir = WORKSPACE_DIR / f"result_{video_id}"
    result_dir.mkdir(parents=True, exist_ok=True)

    # Build command
    cmd = [
        "python3", "inference.py",
        "--driven_audio", audio_path,
        "--source_image", source_path,
        "--result_dir", str(result_dir),
    ]

    if still:
        cmd.append("--still")

    if enhancer:
        cmd.extend(["--enhancer", enhancer])

    # Run SadTalker
    result = subprocess.run(
        cmd,
        cwd=str(SADTALKER_PATH),
        capture_output=True,
        text=True,
        timeout=600
    )

    if result.returncode != 0:
        return jsonify({
            "error": f"SadTalker failed: {result.stderr}",
            "stdout": result.stdout
        }), 500

    # Find generated video
    generated_videos = list(result_dir.glob("**/*.mp4"))
    if not generated_videos:
        return jsonify({"error": "No video was generated", "stdout": result.stdout}), 500

    generated_video = generated_videos[0]

    # Move to output directory (use shutil.move for cross-device support)
    import shutil
    output_filename = f"talking_{video_id}.mp4"
    output_path = OUTPUT_DIR / output_filename
    shutil.move(str(generated_video), str(output_path))

    # Cleanup temp directory
    shutil.rmtree(result_dir, ignore_errors=True)

    if return_file:
        return send_file(
            str(output_path),
            mimetype="video/mp4",
            as_attachment=True,
            download_name=output_filename
        )
    else:
        duration = _get_video_duration(str(output_path))
        return jsonify({
            "success": True,
            "file_path": str(output_path),
            "filename": output_filename,
            "duration_seconds": duration
        })


def _get_video_duration(video_path: str) -> float:
    """Get video duration in seconds."""
    try:
        result = subprocess.run([
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path
        ], capture_output=True, text=True)
        return float(result.stdout.strip())
    except:
        return 0.0


@app.route("/download_models", methods=["POST"])
def download_models():
    """Download SadTalker pretrained models."""
    try:
        # Run the download script
        cmd = ["bash", "scripts/download_models.sh"]
        result = subprocess.run(
            cmd,
            cwd=str(SADTALKER_PATH),
            capture_output=True,
            text=True,
            timeout=1800
        )

        if result.returncode != 0:
            return jsonify({
                "error": "Model download failed",
                "stderr": result.stderr,
                "stdout": result.stdout
            }), 500

        return jsonify({
            "success": True,
            "message": "Models downloaded successfully"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8085, debug=False)
