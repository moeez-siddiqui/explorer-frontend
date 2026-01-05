from fastapi import FastAPI, HTTPException, status, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl
import os
import subprocess
import shutil
import uuid
from pathlib import Path
import httpx
import asyncio
from typing import Optional
from playwright.async_api import async_playwright # NEW IMPORT
import json # NEW IMPORT - useful for handling template_colors

app = FastAPI(
    title="Quran Verse Card Video Generator API",
    description="API to generate dynamic verse cards with audio recitation, based on user input.",
    version="0.1.0"
)

# --- Configuration: IMPORTANT! Please update these paths ---
FFMPEG_PATH = "/usr/bin/ffmpeg"
FFPROBE_PATH = "/usr/bin/ffprobe"

# Arabic Font Path: Ensure this font is installed on your WSL system
# If you installed Amiri, update this path accordingly.
# Example: ARABIC_FONT_PATH = "/usr/share/fonts/opentype/amiri/amiri-regular.ttf"
ARABIC_FONT_PATH = "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf"

# English Font Path: Ensure this font is installed on your WSL system
# Example: ENGLISH_FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
ENGLISH_FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

# Temporary directory for all generated files
TEMP_BASE_DIR = Path("temp_generated_videos")
TEMP_BASE_DIR.mkdir(parents=True, exist_ok=True)

# --- CORS Configuration ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        # "https://your-netlify-domain.netlify.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Pydantic Model for Request Body ---
class VerseCardRequest(BaseModel):
    surah_name: str
    verse_number: int
    arabic_text: str
    translation_text: str
    audio_url: Optional[HttpUrl] = None
    custom_duration: Optional[float] = None
    selected_template: str = "gradient" # To receive the template name
    template_colors: dict # To receive the actual color values from the frontend

# --- Helper Function to get audio duration using ffprobe ---
async def get_audio_duration(audio_path: Path) -> float:
    command = [
        FFPROBE_PATH, "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)
    ]
    process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        print(f"FFprobe Error Stdout: {stdout.decode()}")
        print(f"FFprobe Error Stderr: {stderr.decode()}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get audio duration using ffprobe: {stderr.decode()}"
        )
    try:
        return float(stdout.decode().strip())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to parse audio duration from ffprobe output. Is the audio file valid?"
        )

# --- NEW: Helper to generate HTML for the card ---
def generate_card_html(
    surah_name: str,
    verse_number: int,
    arabic_text: str,
    translation_text: str,
    selected_template: str,
    template_colors: dict,
    arabic_font_path: str,
    english_font_path: str
) -> str:
    # Ensure font paths are correctly formatted for file:// URLs
    # On Windows WSL, paths might need to be adjusted if Playwright runs in Windows context
    # but for pure WSL, /usr/share/fonts/ should work.
    # Replace backslashes with forward slashes for URL compatibility
    arabic_font_url = Path(arabic_font_path).as_uri()
    english_font_url = Path(english_font_path).as_uri()

    # CSS from your Vue component's style block, adapted for inline use in HTML
    # Note: Font family names should match the actual font file names or aliases.
    # 'DejaVu Sans' is a common alias for DejaVuSans.ttf.
    card_css = f"""
    @font-face {{
        font-family: 'Amiri';
        src: url('{arabic_font_url}') format('truetype');
        font-weight: normal;
        font-style: normal;
    }}
    @font-face {{
        font-family: 'DejaVu Sans';
        src: url('{english_font_url}') format('truetype');
        font-weight: normal;
        font-style: normal;
    }}

    body {{ margin: 0; padding: 0; display: flex; justify-content: center; align-items: center; min-height: 100vh; background: transparent; }}
    .preview-card {{
        padding: 3rem 2rem;
        border-radius: 16px;
        width: 400px; /* Fixed width for consistent screenshot size */
        color: {template_colors['text']};
        position: relative;
        overflow: hidden;
        box-sizing: border-box;
        display: flex;
        flex-direction: column;
        align-items: center;
        text-align: center;
        /* Background based on template_colors */
        background: linear-gradient(135deg, {template_colors['bg1']}, {template_colors['bg2']});
    }}

    .preview-card::before {{
        content: '';
        position: absolute;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        opacity: 0.1;
        pointer-events: none;
    }}

    .preview-header {{
        font-size: 1.5rem;
        font-weight: bold;
        font-family: 'DejaVu Sans', sans-serif;
        margin-bottom: 1rem;
        color: {template_colors['text']};
    }}

    .preview-divider {{
        width: 200px;
        height: 3px;
        background-color: {template_colors['accent']};
        margin-bottom: 1.5rem;
    }}

    .preview-arabic {{
        font-family: 'Amiri', serif;
        font-size: 1.75rem;
        line-height: 2;
        margin-bottom: 1.5rem;
        direction: rtl;
        text-align: right; /* Crucial for Arabic */
        width: 100%;
        padding: 0 1rem;
        box-sizing: border-box;
    }}

    .preview-translation {{
        font-family: 'DejaVu Sans', sans-serif;
        font-size: 1.125rem;
        opacity: 0.9;
        line-height: 1.6;
        margin-bottom: 1.5rem;
        text-align: left;
        width: 100%;
        padding: 0 1rem;
        box-sizing: border-box;
    }}

    .preview-footer {{
        font-family: 'DejaVu Sans', sans-serif;
        font-size: 0.875rem;
        opacity: 0.7;
        font-weight: bold;
        margin-top: 1rem;
        color: {template_colors['text']};
    }}
    """

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            {card_css}
        </style>
    </head>
    <body>
        <div class="preview-card {selected_template}">
            <div class="preview-header">
                Surah {surah_name} : Verse {verse_number}
            </div>
            <div class="preview-divider"></div>
            <div class="preview-arabic">
                {arabic_text}
            </div>
            <div class="preview-translation">
                {translation_text}
            </div>
            <div class="preview-footer">Quran Explorer</div>
        </div>
    </body>
    </html>
    """
    return html_content

# --- NEW: Helper to render HTML to image using Playwright ---
async def render_html_to_image(html_content: str, output_path: Path, temp_dir: Path, viewport_width: int, viewport_height: int) -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()

        # Set viewport to ensure the card is fully visible and rendered consistently
        await page.set_viewport_size({"width": viewport_width, "height": viewport_height})

        # Write HTML content to a temporary file for Playwright to load
        temp_html_file = temp_dir / "card_to_render.html"
        temp_html_file.write_text(html_content, encoding="utf-8")

        await page.goto(f"file://{temp_html_file.absolute()}")

        # Wait for fonts to load (important for consistent rendering, especially Arabic fonts)
        await page.evaluate("document.fonts.ready")

        # Take a screenshot of the specific element (the card)
        # This automatically crops to the bounding box of the .preview-card element.
        card_element = await page.query_selector(".preview-card")
        if not card_element:
            raise ValueError("Could not find .preview-card element in rendered HTML.")

        await card_element.screenshot(path=output_path, type="png", omit_background=True) # omit_background=True if you want transparent background outside the card
        await browser.close()

# --- Cleanup function for BackgroundTasks ---
def _cleanup_temp_dir(temp_dir_path: Path):
    if temp_dir_path.exists():
        print(f"Cleaning up temporary directory: {temp_dir_path}")
        shutil.rmtree(temp_dir_path)

# --- Main Endpoint to Generate Video ---
@app.post("/generate-verse-video", summary="Generates a verse card video with audio")
async def generate_verse_video(request: VerseCardRequest, background_tasks: BackgroundTasks):
    unique_id = uuid.uuid4()
    temp_dir = TEMP_BASE_DIR / str(unique_id)
    temp_dir.mkdir(parents=True, exist_ok=True)

    audio_file_path = temp_dir / "recitation.mp3"
    rendered_image_path = temp_dir / "rendered_card.png" # NEW: Path for the rendered image
    visual_only_video_path = temp_dir / "visual_only.mp4"
    final_video_path = temp_dir / "final_verse_card.mp4"

    video_duration = 0.0
    add_audio_track = False

    try:
        # 1. Determine video duration and if audio track is needed
        if request.audio_url:
            print(f"Downloading audio from: {request.audio_url}")
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(str(request.audio_url))
                response.raise_for_status()
                with open(audio_file_path, "wb") as f:
                    f.write(response.content)
            print(f"Audio downloaded to: {audio_file_path}")
            video_duration = await get_audio_duration(audio_file_path)
            print(f"Determined audio duration: {video_duration:.2f} seconds")
            add_audio_track = True
        elif request.custom_duration is not None and request.custom_duration > 0:
            video_duration = request.custom_duration
            print(f"Using custom video duration: {video_duration:.2f} seconds")
            add_audio_track = False
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Either 'audio_url' or a valid 'custom_duration' must be provided."
            )

        # --- Video Output Parameters ---
        video_width = 1280
        video_height = 720
        # The HTML card's CSS defines its width as 400px.
        # We'll render it at a higher resolution (e.g., 800px wide) to ensure quality when scaling up.
        # Playwright will determine the actual height based on content.
        # So, we set a generous viewport height.
        render_viewport_width = 800
        render_viewport_height = 1600 # Sufficiently tall to capture the card

        # 2. Generate HTML content for the card using the provided template colors
        html_content = generate_card_html(
            surah_name=request.surah_name,
            verse_number=request.verse_number,
            arabic_text=request.arabic_text,
            translation_text=request.translation_text,
            selected_template=request.selected_template,
            template_colors=request.template_colors, # Use colors sent from frontend
            arabic_font_path=ARABIC_FONT_PATH,
            english_font_path=ENGLISH_FONT_PATH
        )
        print(f"DEBUG: Generated HTML content for rendering.")

        # 3. Render HTML to image using Playwright
        print(f"Rendering HTML to image using Playwright...")
        await render_html_to_image(html_content, rendered_image_path, temp_dir, render_viewport_width, render_viewport_height)
        print(f"Image rendered to: {rendered_image_path}")

        # 4. Use FFmpeg to create a video from the rendered image
        visual_command = [
            FFMPEG_PATH,
            "-loop", "1", # Loop the image indefinitely
            "-i", str(rendered_image_path), # Input is the rendered image
            "-t", str(video_duration), # Video duration
            # Scale the image to fit the video frame, maintaining aspect ratio and padding with black if needed.
            # We assume the card's background is solid, so padding with the video's background color might be better.
            # For now, let's just scale and pad to fit the 1280x720 frame.
           "-vf", f"scale=w={video_width}:h={video_height}:force_original_aspect_ratio=decrease,pad={video_width}:{video_height}:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "23",
            "-y",
            str(visual_only_video_path)
        ]

        print(f"Running FFmpeg visual generation command from image...")
        visual_process = await asyncio.create_subprocess_exec(
            *visual_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        visual_stdout, visual_stderr = await visual_process.communicate()

        if visual_process.returncode != 0:
            print(f"FFmpeg Visual Error Stdout: {visual_stdout.decode()}")
            print(f"FFmpeg Visual Error Stderr: {visual_stderr.decode()}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to generate video visuals from image: {visual_stderr.decode()}"
            )
        if not visual_only_video_path.exists():
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="FFmpeg did not produce the visual-only video file from image. Check FFmpeg logs for errors."
            )
        print(f"Visual-only video generated from image: {visual_only_video_path}")

        # 5. Combine Visuals and Audio (conditional)
        if add_audio_track:
            combine_command = [
                FFMPEG_PATH,
                "-i", str(visual_only_video_path),
                "-i", str(audio_file_path),
                "-c:v", "copy",
                "-c:a", "aac",
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-shortest",
                "-y",
                str(final_video_path)
            ]

            print(f"Running FFmpeg combine command...")
            combine_process = await asyncio.create_subprocess_exec(
                *combine_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            combine_stdout, combine_stderr = await combine_process.communicate()

            if combine_process.returncode != 0:
                print(f"FFmpeg Combine Error Stdout: {combine_stdout.decode()}")
                print(f"FFmpeg Combine Error Stderr: {combine_stderr.decode()}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to combine video and audio: {combine_stderr.decode()}"
                )
            if not final_video_path.exists():
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="FFmpeg did not produce the final combined video file. Check FFmpeg logs for errors."
                )
            print(f"Final video generated with audio: {final_video_path}")
        else:
            shutil.copy(visual_only_video_path, final_video_path)
            print(f"Final video generated without audio (copied visual-only): {final_video_path}")

        # 6. Return the generated MP4 file as a FileResponse
        background_tasks.add_task(_cleanup_temp_dir, temp_dir)

        return FileResponse(
            path=final_video_path,
            media_type="video/mp4",
            filename=f"verse_card_{request.surah_name.replace(' ', '_')}_{request.verse_number}.mp4"
        )

    except httpx.HTTPStatusError as e:
        print(f"HTTP error downloading audio: {e.response.status_code} - {e.response.text}")
        background_tasks.add_task(_cleanup_temp_dir, temp_dir)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to download audio from '{request.audio_url}': {e.response.status_code}"
        )
    except Exception as e:
        print(f"An unexpected server error occurred during video generation: {e}")
        background_tasks.add_task(_cleanup_temp_dir, temp_dir)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {e}"
        )