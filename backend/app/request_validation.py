"""Reject text PostgreSQL cannot store, without inspecting binary uploads."""

import json

from fastapi import HTTPException, Request
from starlette.datastructures import UploadFile


def reject_nul(value):
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str) and "\x00" in item:
            raise HTTPException(422, "Текстовые поля не могут содержать нулевой символ")
        if isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend(item)


async def validate_request_text(request: Request):
    # FastAPI has already parsed route bodies before resolving dependencies;
    # Request caches JSON/forms. Files remain binary and are not read here.
    reject_nul(request.path_params)
    reject_nul(request.query_params.multi_items())
    reject_nul(list(request.headers.items()))
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    # FastAPI also treats bodies without Content-Type as JSON.
    if not content_type or content_type == "application/json" or content_type.endswith("+json"):
        if not await request.body():
            return
        try:
            value = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as error:
            raise HTTPException(422, "Некорректный формат JSON") from error
        reject_nul(value)
    elif content_type in {"multipart/form-data", "application/x-www-form-urlencoded"}:
        form = await request.form()
        for key, value in form.multi_items():
            reject_nul((key, value.filename if isinstance(value, UploadFile) else value))
