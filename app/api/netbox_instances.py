import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.db import NetBoxInstance
from app.templates_config import templates

router = APIRouter()


@router.get("/netbox-instances", response_class=HTMLResponse)
def list_instances(request: Request, db: Session = Depends(get_db)):
    instances = db.execute(
        select(NetBoxInstance).order_by(NetBoxInstance.name)
    ).scalars().all()
    return templates.TemplateResponse("netbox_instances.html", {
        "request": request,
        "instances": instances,
    })


@router.post("/netbox-instances", response_class=HTMLResponse)
def create_instance(
    request: Request,
    name: Annotated[str, Form()],
    url: Annotated[str, Form()],
    token: Annotated[str, Form()],
    is_default: Annotated[bool, Form()] = False,
    db: Session = Depends(get_db),
):
    # Clear existing default if this one is being set as default
    if is_default:
        db.execute(
            NetBoxInstance.__table__.update().values(is_default=False)
        )

    instance = NetBoxInstance(
        id=uuid.uuid4(),
        name=name,
        url=url.rstrip("/"),
        token=token,
        is_default=is_default,
    )
    db.add(instance)
    db.commit()
    return RedirectResponse(url="/netbox-instances", status_code=303)


@router.post("/netbox-instances/{instance_id}/delete", response_class=HTMLResponse)
def delete_instance(instance_id: uuid.UUID, db: Session = Depends(get_db)):
    instance = db.get(NetBoxInstance, instance_id)
    if not instance:
        raise HTTPException(404, "Instance not found")
    db.delete(instance)
    db.commit()
    return RedirectResponse(url="/netbox-instances", status_code=303)


@router.post("/netbox-instances/{instance_id}/set-default", response_class=HTMLResponse)
def set_default(instance_id: uuid.UUID, db: Session = Depends(get_db)):
    db.execute(NetBoxInstance.__table__.update().values(is_default=False))
    instance = db.get(NetBoxInstance, instance_id)
    if not instance:
        raise HTTPException(404, "Instance not found")
    instance.is_default = True
    db.commit()
    return RedirectResponse(url="/netbox-instances", status_code=303)
