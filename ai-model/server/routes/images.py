from fastapi import APIRouter
from fastapi.responses import FileResponse
import os

router = APIRouter()

@router.get("/images/{filename}")
def get_image(filename: str):
    path = f"logs/images/{filename}"
    if os.path.exists(path):
        return FileResponse(path)
    return {"error": "Image not found"}
